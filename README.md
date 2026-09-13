# Psychology Roulette

A multiplayer social-reasoning game about understanding how other people think.

Players privately take positions on thoughtful questions, predict one another, respond to occasional challenges, and then see how the room moved after discussion. The game rewards perspective-taking rather than agreement.

## Current vertical slice

- Create a room and receive a four-letter code.
- Join from multiple devices or isolated browser profiles.
- Recover the same private seat after a refresh or browser restart.
- Preserve active rooms across server restarts with versioned snapshots.
- Start a six-round game from a curated question pack.
- Submit a private position and confidence rating.
- Encounter occasional, deterministically selected round modifiers.
- Add an optional AI-written discussion angle without giving AI control of rules.
- Predict the room, name a secret principle, defend another side, steelman a
  counterpart, or privately answer again after discussion.
- Reveal the room distribution after everyone answers.
- Score room predictions without rewarding ideological agreement.
- Show cross-round table analytics and playful, non-diagnostic player titles.
- Expire abandoned rooms and persistently throttle room entry attempts.
- Let the host end an active game for the whole table at any time.
- Advance through the complete session.
- Return everyone home after completion without retaining stale seats.
- Deterministic, testable game rules independent of the UI.

Room state is stored as versioned snapshots: SQLite for local development and Postgres for hosted deployments. Each participant receives an unguessable reconnect credential; only its hash is stored by the server, and game actions derive the acting player from that credential. At game start, FastAPI can ask the authenticated Oracle gateway for short contextual discussion angles. The deterministic engine still owns the modifier schedule, targets, instructions, and scoring.

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

The AI service is optional by design. Requests contain curated question and modifier
text only, never room identifiers, participants, answers, credentials, or scores. If
Qwen is unavailable, slow, or returns invalid data, the game immediately keeps the
curated copy and remains fully playable.

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

## Hosted deployment: Vercel + Supabase

The repository is ready to run as one Vercel Services project: Vite serves the web
client, FastAPI handles `/api/*`, and every function instance shares the same
Supabase Postgres data. `vercel.json` contains the service routing, and
`backend/main.py` is the hosted API entrypoint.

1. Create a Supabase project and run
   `supabase/migrations/20260913000000_create_game_store.sql` in its SQL editor.
   If the project was created from an earlier version of that migration, also run
   `supabase/migrations/20260914000000_remove_private_invites.sql`.
2. Copy the transaction-pooler connection string from **Connect** in Supabase.
   Append `?sslmode=require` if the copied URL does not already specify SSL.
3. Import this GitHub repository into Vercel and choose **Services** as the
   Framework Preset.
4. Add these Vercel environment variables:

   ```text
   PSYCHOLOGY_ROULETTE_DATABASE_URL=<Supabase transaction-pooler URL>
   PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS=604800
   PSYCHOLOGY_ROULETTE_AI_BASE_URL=<Cloudflare Tunnel HTTPS hostname, optional>
   PSYCHOLOGY_ROULETTE_AI_TOKEN=<Oracle gateway secret, required with AI URL>
   PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS=12
   ```

5. Deploy. Later pushes to the connected branch redeploy automatically; no local
   launcher or always-on laptop is involved.

The database URL is a server-only secret and must never use a `VITE_` prefix or be
placed in frontend code. The adapter disables prepared statements and limits each
warm function instance to one pooled connection, which matches Supabase's
transaction-pooler guidance for serverless traffic. Postgres row locks serialize
concurrent changes to a room, so separate Vercel instances cannot overwrite each
other's answers or joins.

Vercel's dynamic outbound addresses do not affect this database connection because
the Supabase transaction pooler is a credential-authenticated public endpoint, not
an Oracle IP allowlist.

The Oracle deployment lives in `oracle/`. It runs the official ARM64 llama.cpp server
with Qwen3 0.6B Q8_0 behind a narrow authenticated gateway. The model has explicit
CPU and memory limits and is not published on an Oracle ingress port. The optional
Cloudflare connector reaches the gateway over the private Compose network.

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
