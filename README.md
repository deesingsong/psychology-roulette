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
- Predict the room, name a secret principle, or defend the opposite position.
- Reveal the room distribution after everyone answers.
- Score room predictions without rewarding ideological agreement.
- Advance through the complete session.
- Deterministic, testable game rules independent of the UI.

Room state is stored as versioned SQLite snapshots. Each participant receives an unguessable reconnect credential; only its hash is stored by the server, and game actions derive the acting player from that credential. The remaining modifier families, session analytics, and optional Qwen commentary come next.

Room snapshots and actions require participant credentials. The create and four-letter join endpoints remain intentionally easy to access, so add rate limiting, room expiry, and either trusted-edge abuse controls or a higher-entropy invite secret before exposing the game directly to the public internet.

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
