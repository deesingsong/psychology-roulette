# Oracle AI service

This deployment keeps game rules and arithmetic in the main FastAPI application. Qwen
has two jobs: create each game's fresh six-question content pack and curate the final
recap from server-verified facts. If this service is unavailable, game start continues
with curated questions and completion retains the deterministic statistics board.

The Compose stack runs three isolated services:

- the official ARM64 `llama.cpp` server image with Qwen3 0.6B Q8_0;
- a narrow authenticated gateway on loopback port 8787;
- an optional outbound-only Cloudflare Tunnel (the `tunnel` profile).

The model is limited to two CPUs and 1.5 GiB of memory so it can coexist with the
server's existing workload. No Oracle ingress port needs to be opened.

## Deployment

1. Copy this directory to `/home/opc/psychology-roulette-ai`.
2. Copy `.env.example` to `.env`, replace both values, and set mode `600`.
3. Start and test locally with `docker compose up -d model gateway`, then send an
   authenticated request to `http://127.0.0.1:8787/v1/game-content` or
   `http://127.0.0.1:8787/v1/session-recap`.
4. Create a remotely managed Cloudflare Tunnel, put its connector token in `.env`,
   and leave its public-ingress configuration deny-by-default.
5. Start the connector with `docker compose --profile tunnel up -d`.
6. Deploy the Worker in `cloudflare/worker/`. Its Workers VPC binding connects
   directly to this tunnel and publishes only the gateway's two supported routes on
   a stable `workers.dev` HTTPS address.
7. Set Vercel's `PSYCHOLOGY_ROULETTE_AI_BASE_URL` to that Worker URL and set
   `PSYCHOLOGY_ROULETTE_AI_TOKEN` to the same gateway secret.

Never commit `.env`, a tunnel token, or the gateway secret.
