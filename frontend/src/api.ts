import type {
  RestoredRoomSessionView,
  RoomSessionView,
  RoomView,
} from "./types";

interface AuthenticatedRequestInit extends RequestInit {
  accessToken?: string;
  requestTimeoutMs?: number;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 10000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: AuthenticatedRequestInit = {},
): Promise<T> {
  const {
    accessToken,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    signal: callerSignal,
    ...fetchOptions
  } = options;
  const headers = new Headers(fetchOptions.headers);
  const controller = new AbortController();
  let timedOut = false;

  const abortForCaller = () => controller.abort();
  if (callerSignal?.aborted) {
    controller.abort();
  } else {
    callerSignal?.addEventListener("abort", abortForCaller, { once: true });
  }
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, requestTimeoutMs);

  if (fetchOptions.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", "Bearer " + accessToken);
  }

  try {
    const response = await fetch(path, {
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });

    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: unknown;
        code?: unknown;
      } | null;
      throw new ApiError(
        typeof payload?.detail === "string"
          ? payload.detail
          : "Something went wrong.",
        response.status,
        typeof payload?.code === "string" ? payload.code : undefined,
      );
    }

    return (await response.json()) as T;
  } catch (reason) {
    if (timedOut) {
      throw new ApiError(
        "The server took too long to respond.",
        408,
        "request_timeout",
      );
    }
    throw reason;
  } finally {
    window.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortForCaller);
  }
}

function roomPath(code: string, suffix = "") {
  return "/api/rooms/" + encodeURIComponent(code) + suffix;
}

export const api = {
  createRoom(hostName: string, accessToken: string, inviteToken: string) {
    return request<RoomSessionView>("/api/rooms", {
      method: "POST",
      accessToken,
      body: JSON.stringify({ host_name: hostName, invite_token: inviteToken }),
    });
  },
  joinRoom(
    code: string,
    name: string,
    accessToken: string,
    inviteToken: string | null,
  ) {
    return request<RoomSessionView>(roomPath(code, "/players"), {
      method: "POST",
      accessToken,
      body: JSON.stringify({ name, invite_token: inviteToken }),
    });
  },
  resume(accessToken: string, signal?: AbortSignal) {
    return request<RestoredRoomSessionView>("/api/session", {
      accessToken,
      signal,
    });
  },
  getRoom(code: string, accessToken: string, signal?: AbortSignal) {
    return request<RoomView>(roomPath(code), {
      accessToken,
      signal,
    });
  },
  startRoom(code: string, accessToken: string) {
    return request<RoomView>(roomPath(code, "/start"), {
      method: "POST",
      accessToken,
    });
  },
  submitAnswer(
    code: string,
    accessToken: string,
    position: number,
    confidence: number,
  ) {
    return request<RoomView>(roomPath(code, "/answers"), {
      method: "POST",
      accessToken,
      body: JSON.stringify({ position, confidence }),
    });
  },
  submitModifier(code: string, accessToken: string, value: number | string) {
    return request<RoomView>(roomPath(code, "/modifier-submissions"), {
      method: "POST",
      accessToken,
      body: JSON.stringify({ value }),
    });
  },
  reveal(code: string, accessToken: string) {
    return request<RoomView>(roomPath(code, "/reveal"), {
      method: "POST",
      accessToken,
    });
  },
  advance(code: string, accessToken: string) {
    return request<RoomView>(roomPath(code, "/advance"), {
      method: "POST",
      accessToken,
    });
  },
};
