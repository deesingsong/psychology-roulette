# Cloudflare edge gateway

This Worker provides a stable `workers.dev` HTTPS address without requiring a custom
domain. Its Workers VPC binding reaches the Oracle gateway through the named
Cloudflare Tunnel, so Oracle has no public AI ingress port.

The Worker accepts only `GET /health`, `POST /v1/game-content`, and
`POST /v1/session-recap`. It enforces a 64 KiB request limit, forwards only required
headers, and streams the origin response.
The Oracle gateway remains responsible for Bearer authentication and payload validation.

```powershell
cd cloudflare/worker
npm install
npm run check
npm run deploy
```

Workers VPC is currently a Cloudflare beta. A custom domain can replace the
`workers.dev` route later without changing the Oracle deployment.
