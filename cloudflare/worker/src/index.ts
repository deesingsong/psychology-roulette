const MAX_REQUEST_BYTES = 64 * 1024;
const ORIGIN = "http://gateway:8000";
const GENERATION_PATHS = ["/v1/game-content", "/v1/session-recap"] as const;

function jsonError(status: number, detail: string): Response {
  return Response.json(
    { detail },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

async function boundedBody(request: Request): Promise<ArrayBuffer> {
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    total += result.value.byteLength;
    if (total > MAX_REQUEST_BYTES) {
      await reader.cancel();
      throw new RangeError("Request body is too large.");
    }
    chunks.push(result.value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body.buffer as ArrayBuffer;
}

function originResponse(response: Response): Response {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": response.headers.get("Content-Type") ?? "application/json",
    "X-Content-Type-Options": "nosniff",
  });
  const challenge = response.headers.get("WWW-Authenticate");
  if (challenge) headers.set("WWW-Authenticate", challenge);
  return new Response(response.body, { status: response.status, headers });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const isHealth = request.method === "GET" && url.pathname === "/health";
    const isGeneration =
      request.method === "POST" &&
      GENERATION_PATHS.some((path) => path === url.pathname);
    if (!isHealth && !isGeneration) {
      return jsonError(404, "Not found.");
    }

    try {
      if (isHealth) {
        const response = await env.ORACLE_NETWORK.fetch(`${ORIGIN}/health`, {
          signal: AbortSignal.timeout(5_000),
        });
        return originResponse(response);
      }

      const contentType = request.headers.get("Content-Type") ?? "";
      if (!contentType.toLowerCase().startsWith("application/json")) {
        return jsonError(415, "Content-Type must be application/json.");
      }
      const declaredLength = Number(request.headers.get("Content-Length") ?? "0");
      if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
        return jsonError(413, "Request body is too large.");
      }
      const body = await boundedBody(request);
      const headers = new Headers({
        Accept: "application/json",
        "Content-Type": "application/json",
      });
      const authorization = request.headers.get("Authorization");
      if (authorization) headers.set("Authorization", authorization);

      const response = await env.ORACLE_NETWORK.fetch(
        `${ORIGIN}${url.pathname}`,
        {
          method: "POST",
          headers,
          body,
          signal: AbortSignal.timeout(55_000),
        },
      );
      return originResponse(response);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "Oracle AI proxy request failed",
          path: url.pathname,
          error: error instanceof Error ? error.name : "UnknownError",
        }),
      );
      if (error instanceof RangeError) {
        return jsonError(413, "Request body is too large.");
      }
      return jsonError(502, "Oracle AI service is unavailable.");
    }
  },
} satisfies ExportedHandler<Env>;
