# Psychology Roulette

A multiplayer social-reasoning game about understanding how other people think.

Players privately take positions on thoughtful questions, predict one another, respond to occasional challenges, and then see how the room moved after discussion. The game rewards perspective-taking rather than agreement.

## Current vertical slice

- Create a room and receive a four-letter code.
- Join from multiple devices or isolated browser profiles.
- Recover the same private seat after a refresh or browser restart.
- Preserve active rooms across server restarts with SQLite snapshots.
- Start a six-round game from a curated question pack.
- Submit a private position and confidence rating.
- Encounter occasional, deterministically selected round modifiers.
- Predict the room, name a secret principle, defend another side, steelman a
  counterpart, or privately answer again after discussion.
- Reveal the room distribution after everyone answers.
- Score room predictions without rewarding ideological agreement.
- Show cross-round table analytics and playful, non-diagnostic player titles.
- Share an optional 256-bit private invitation link.
- Expire abandoned rooms and persistently throttle room entry attempts.
- Advance through the complete session.
- Deterministic, testable game rules independent of the UI.

Room state is stored as versioned SQLite snapshots. Each participant receives an unguessable reconnect credential; only its hash is stored by the server, and game actions derive the acting player from that credential. Private invitation tokens are also stored only as hashes. AI-authored contextual wording and Oracle deployment are intentionally deferred until the deterministic product is settled.

Room snapshots and actions require participant credentials. Room create and join endpoints have SQLite-backed rate limits, and the production container adds edge throttling. Production mode requires the private invite URL by default; the four-letter-code-only flow remains available for local development.

## Architecture

```text
React + TypeScript client
          |
          | JSON/HTTP with short polling
          v
FastAPI application
          |
          | atomic room mutations
          v
SQLite snapshot store
          |
          v
Pure Python game engine
```

The AI service is optional by design. If Qwen is unavailable, the game remains fully playable.

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

The local API accepts a four-letter code without the private invite token. Set
`PSYCHOLOGY_ROULETTE_REQUIRE_INVITE_TOKEN=true` to exercise the production rule.

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

## Production container

The checked-in Compose stack serves the web app and API from one origin, keeps the
API off the public port, and stores SQLite data in a named volume:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Open `http://localhost:8080`, or change `PSYCHOLOGY_ROULETTE_PORT` in `.env`.
The Compose defaults require private invite links and expire rooms after seven days
without a mutation. Creation is limited to 10 attempts per 10 minutes per client;
joining is limited to 30 attempts per minute, with an additional Nginx edge limit.

Do not publish port 8000. The backend trusts forwarded client addresses only because
Compose exposes it solely to the internal web proxy. TLS and the eventual Oracle
host setup remain the final deployment step.
