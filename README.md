# Are You Niche or NPC?

A multiplayer social-reasoning game about understanding how other people think.

Players privately take positions on thoughtful questions, predict one another, respond to occasional challenges, and then see how the room moved after discussion. The game rewards perspective-taking rather than agreement.

## Current vertical slice

- Create a room and receive a four-letter code.
- Join from multiple devices or isolated browser profiles.
- Recover the same private seat after a refresh or browser restart.
- Preserve active rooms across server restarts with versioned snapshots.
- Begin preparing a fresh six-question Qwen pack as soon as the host creates a room.
- Show estimated progress while questions and the final recap are generated.
- Submit a private position and confidence rating.
- Encounter occasional, deterministically selected round modifiers.
- Predict the room, name a secret principle, defend another side, steelman a
  counterpart, or privately answer again after discussion.
- Reveal the room distribution after everyone answers.
- Score room predictions without rewarding ideological agreement.
- Let Qwen curate a dynamic end board from server-verified session facts.
- Expire abandoned rooms and persistently throttle room entry attempts.
- Let the host end an active game for the whole table at any time.
- Advance through the complete session.
- Return everyone home after completion without retaining stale seats.
- Deterministic, testable game rules independent of the UI.

Room state is stored as versioned snapshots: SQLite for local development and Postgres for hosted deployments. Each participant receives an unguessable reconnect credential; only its hash is stored by the server, and game actions derive the acting player from that credential. Immediately after room creation, FastAPI asks the authenticated Oracle gateway for six original agree/disagree statements or yes/no questions while players join. Starting is immediate once that pack is ready. When the final round closes, Qwen selects and describes the most interesting results from a server-calculated fact catalog. The deterministic engine still owns the modifier schedule, targets, instructions, arithmetic, and scoring.

Room snapshots and actions require participant credentials. New players join with the four-letter room code and a display name. Room create and join endpoints have database-backed rate limits, and the production container adds edge throttling.

## Architecture

```text
React + TypeScript client
          |
          | JSON/HTTP with short polling
          v
FastAPI application
      |                              |
      | atomic room mutations        | optional authenticated HTTPS
      v                              v
Repository interface         Cloudflare Tunnel
   /             \                  |
SQLite (local) Postgres (hosted)     v
      |                       Oracle gateway -> Qwen 0.6B
      v
Pure Python game engine
```

The AI boundary exposes exactly two capabilities: game-content generation and session
recap curation. It never receives room identifiers, credentials, or real player names;
recap players are sent as temporary aliases. Constrained JSON schemas and semantic
validation reject open-ended prompts and retry malformed generations. Curated questions are used only when the AI
service is genuinely unavailable. If Qwen remains reachable but repeatedly violates
the content contract, the game stays in the lobby and asks the host to retry rather
than silently substituting canned content.

## Development

On Windows, the launcher starts both services and stops them together:

```powershell
.\scripts\dev.ps1
```

To test from another device on the same Wi-Fi network:

```powershell
.\scripts\dev.ps1 -Lan
```

The launcher prints the remote-device URL. Windows may ask you to allow Node.js and Python through the private-network firewall.

Reconnect storage represents one seat per browser profile. When testing several players on one computer, use different browsers or profiles, or use one normal and one private session, so each player keeps an independent credential.

You can also run each service separately:

### Backend

```powershell
cd backend
uv sync --dev
uv run uvicorn psychology_roulette.api:app --reload --port 8000
```

The API documentation is available at `http://localhost:8000/docs`.

By default, development rooms are stored in `backend/data/psychology-roulette.sqlite3`. Set `PSYCHOLOGY_ROULETTE_DB_PATH` to use a different database file.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite development server proxies `/api` to the backend.

### Tests

```powershell
cd backend
uv run pytest
uv run ruff check .
```

See [docs/GAME_SPEC.md](docs/GAME_SPEC.md) for the current game rules and state-machine decisions.

## Hosted deployment: Vercel + Neon

The repository is ready to run as one Vercel Services project: Vite serves the web
client, FastAPI handles `/api/*`, and every function instance shares the same
Neon Postgres data. `vercel.json` contains the service routing, and
`backend/main.py` is the hosted API entrypoint.

1. Create a Neon project and use its direct (non-pooled) connection string for
   schema migrations and one-time data imports. The app does not use Neon Auth,
   Realtime, or Storage; room state and reconnect sessions remain in Postgres.
2. Run `database/schema.sql` as the database owner. Create a separate SQL-managed
   `psychology_roulette_runtime` login role with `NOINHERIT`, `NOSUPERUSER`,
   `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`, and `NOBYPASSRLS`. Set its
   password using Neon role management, then apply
   `database/neon_permissions.sql`; do not give the application owner credentials.
   Keep schema ownership, role creation, and migration credentials separate from
   the runtime user where practical.
3. Import this GitHub repository into Vercel and choose **Services** as the
   Framework Preset.
4. Add these Vercel environment variables:

   ```text
   PSYCHOLOGY_ROULETTE_DATABASE_URL=<Neon pooled runtime URL for psychology_roulette_runtime>
   PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS=604800
   PSYCHOLOGY_ROULETTE_AI_BASE_URL=<Cloudflare Worker HTTPS URL, optional>
   PSYCHOLOGY_ROULETTE_AI_TOKEN=<Oracle gateway secret, required with AI URL>
   PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS=45
   ```

5. Deploy. Later pushes to the connected branch redeploy automatically; no local
   launcher or always-on laptop is involved.

The database URL is a server-only secret and must never use a `VITE_` prefix or be
placed in frontend code. Use Neon’s pooled endpoint for Vercel serverless traffic
and its direct endpoint for migrations. The adapter disables prepared statements
and limits each warm function instance to one pooled connection. Postgres row locks
serialize concurrent changes to a room, so separate Vercel instances cannot
overwrite each other's answers or joins.

Vercel's dynamic outbound addresses do not affect this database connection because
Neon's pooled endpoint is a credential-authenticated public endpoint, not an
Oracle IP allowlist. For Supabase-to-Neon migration, first produce and protect a
snapshot using `database/export_snapshot.sql`, verify the destination schema,
then import with `PSYCHOLOGY_ROULETTE_MIGRATION_DATABASE_URL` set to Neon's direct
connection string and `python scripts/import_snapshot.py --file <protected-file>`.
The importer accepts only its exact snapshot format, requires an empty target, and
wraps inserts in a single transaction. Keep the source Supabase project intact
until the Neon deployment has been verified and you have separately decided to
retire it.

### Supabase-to-Neon cutover procedure

Treat the snapshot as sensitive: it contains room state and hashed reconnect
tokens. Keep it outside Git (the `.migration/` directory is ignored), never paste
it into chat, and remove it when the migration is verified. The export and transfer
helpers create files exclusively; on Windows they remove inherited ACLs and grant
access only to the current account, SYSTEM, and local Administrators. A rehearsal
import is not a substitute for a fresh source snapshot. The importer intentionally
refuses a non-empty destination; do not clear an existing Neon branch without
first confirming it is only disposable rehearsal data and making a backup if
needed.

1. Confirm the Supabase source can be read and the Neon runtime role has only the
   required table permissions. Use a separate Neon pooled connection for Vercel
   and direct connection for export/import or schema work.
2. Point Vercel Preview at the Neon verification branch and test room creation,
   join, read, answer, end-room, and persistence across requests. Do not test
   against the production Neon branch.
3. For the production data freeze, deploy with
   `PSYCHOLOGY_ROULETTE_MAINTENANCE_MODE=true`. The API returns 503 for all
   database-backed routes (including reads, which can prune expired rooms); the
   database-independent `/api/health` endpoint remains available. Confirm the
   maintenance deployment is serving before exporting.
4. Drain in-flight requests and confirm no other writers remain. This flag only
   protects the deployment that contains it: if users have been given older
   immutable Vercel deployment URLs, ensure those URLs cannot continue writing
   before taking the final snapshot.
5. Export one fresh source snapshot, securely copy it to the chosen empty Neon
   production target, import it once, then run `database/verify.sql` and compare
   all three table counts. Preserve the source unchanged; once Neon accepts new
   writes, a connection-string flip back to Supabase would lose those writes and
   would require a reverse reconciliation.
6. Set the Vercel Production database secret to the Neon main branch's pooled
   runtime URL and deploy the verified app. Keep maintenance enabled until the
   final production build is ready; then disable maintenance in a new deployment
   and verify the public room flow and persistence. Keep Supabase as a frozen
   source copy until you explicitly decide its future.

Changing a Vercel environment variable does not change existing deployments;
each environment change needs a new deployment. Never retrieve, print, or commit
the current production database URL to perform the switch.

The Oracle deployment lives in `oracle/`. It runs the official ARM64 llama.cpp server
with Qwen3 0.6B Q8_0 behind a narrow authenticated gateway. The model has explicit
CPU and memory limits and is not published on an Oracle ingress port. A Cloudflare
Tunnel makes an outbound-only connection, and the Worker in `cloudflare/worker/`
reaches that tunnel through a Workers VPC binding. This provides a stable
`workers.dev` endpoint without requiring a domain or exposing Oracle directly.

## Production container

The checked-in Compose stack serves the web app and API from one origin, keeps the
API off the public port, and stores SQLite data in a named volume:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Open `http://localhost:8080`, or change `PSYCHOLOGY_ROULETTE_PORT` in `.env`.
Rooms expire after seven days without a mutation. Creation is limited to 10
attempts per 10 minutes per client;
joining is limited to 30 attempts per minute, with an additional Nginx edge limit.

Do not publish port 8000. The backend trusts forwarded client addresses only because
Compose exposes it solely to the internal web proxy. TLS and the eventual Oracle
host setup remain the final deployment step.
