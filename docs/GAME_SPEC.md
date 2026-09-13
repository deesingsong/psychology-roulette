# Game specification — draft 0.1

## Product principle

The questions create discussion. The software creates the game.

Psychology Roulette is not a personality test and does not infer diagnoses or fixed traits. It creates temporary, playful observations from the answers given during one session.

## Session

- 3–8 players is the intended social range; two players are allowed during development.
- A standard session contains six rounds and should last roughly 20–35 minutes.
- Players join using a four-letter room code and a display name.
- Each seat receives a private reconnect credential that survives a browser refresh.
- The host controls starting and advancing the session, and can end the game for the whole table at any time.
- No account is required.

## Position scale

The client displays a seven-position scale from strongly disagree to strongly agree. The engine stores the following values:

```text
-100, -67, -33, 0, 33, 67, 100
```

Confidence is stored from `0` (unsure) to `100` (certain).

## Round state machine

```text
LOBBY
  -> ANSWERING
  -> MODIFIER (when a pre-reveal modifier is selected)
  -> REVEAL
  -> FOLLOW_UP (for Steelman or Change My Mind)
  -> FOLLOW_UP_REVEAL
  -> ANSWERING (next round)
  -> COMPLETE
```

Answers are private while the room is in `ANSWERING`. An authenticated room snapshot exposes only whether each player has submitted. Positions become visible in `REVEAL`. Steelman text and second positions remain private during `FOLLOW_UP`, and appear only in `FOLLOW_UP_REVEAL`.

The selected modifier remains hidden while players choose their positions. A pre-reveal modifier becomes visible only after every position is locked. Modifier submissions remain private until the reveal.

## Modifier schedule

The opening round is always plain so players can learn the basic loop. Each later eligible round has a stable 65% chance of receiving a modifier. The schedule is derived from a server-secret per-room seed, so it remains stable during play without being predictable from the public room code. If every draw misses, the final eligible round receives one, ensuring a normal multi-round session demonstrates the feature. Setting the modifier chance to zero explicitly disables this fallback.

The engine selects only from modifier templates allowed by the current question. Target selection and scoring remain deterministic server responsibilities. Tests can inject a fixed seed for exact reproducibility, but production seeds are never serialized. Targeted challenges are drawn from the least-targeted players and avoid an immediate repeat whenever another equally fair player is available. After a Steelman round is answered, the server assigns the selected writer the other player whose recorded position is furthest from theirs.

At game start, AI may generate one short contextual discussion angle inside each already-selected modifier. It cannot change the canonical instructions, invent authoritative scoring rules, select players, or block the round. Invalid, late, or unavailable AI output falls back to curated copy. Requests contain only curated question and modifier content, not room identifiers, player names, answers, credentials, or scores.

Initial modifier families:

1. **Predict the Room** — every player predicts the group average before answers appear.
2. **Secret Principle** — every player privately selects the value that mattered most to their answer.
3. **Devil's Advocate** — one eligible player is challenged after the reveal to defend a meaningfully different position; a player who chose the middle may defend either clear side.
4. **Steelman** — after discussion, one fairly selected player writes a concise good-faith restatement of the most distant submitted position. Only that player may submit, and the text stays private until the host reveals it.
5. **Change My Mind** — after discussion, every player privately chooses from the same seven-position scale again. The follow-up reveal shows the original and new position plus absolute movement.

All five use curated canonical instructions. Optional AI-authored context is separately labeled in the interface and stored in the same validated modifier structure.

## Scoring

Agreement with the room is never itself rewarded. Predict the Room awards up to 100 points:

```text
score = max(0, round(100 - abs(prediction - room_average) / 2))
```

This measures how accurately someone read the group, not whether their personal answer matched it. Change My Mind reports movement but does not award points for changing or holding. Steelman text is not automatically scored.

## Session summary

Once the final round is complete, the server derives a session summary from the stored rounds. It includes table averages, the widest round, each player's average position and confidence, average distance from the room, position span, prediction performance, and total follow-up movement. Titles such as "Room Reader" or "Open Door" are playful descriptions of this session only; they are not personality claims or diagnoses.

## Content

Questions are curated JSON records containing a category, intensity, relevant values, and allowed modifiers. Live AI-generated questions are outside the initial game loop.

## Persistence and reconnect

The server writes the complete room aggregate to a versioned SQLite snapshot after every successful mutation. The snapshot includes the original question pack, players, answers, modifier plan and private submissions, so an active session resumes exactly after a process restart even if curated content later changes.

Creating a room, joining a seat, issuing its credential, and changing game state are transactional operations. Failed rules or database writes do not leave partial rooms or players behind.

The browser generates one high-entropy participant credential for a create or join attempt and sends it as a Bearer credential. Retrying the same operation with the same credential returns the original seat, preventing a lost response from creating an unrecoverable player. The browser stores the credential locally and uses it to restore the server-authoritative player identity. The database stores only a SHA-256 digest. Room reads and actions reject missing, invalid, mismatched-reuse, or cross-room credentials, and request bodies cannot choose another player's identity.

During an active round, the client does not allow a participant to discard the only credential for a required seat. When the final summary is reached, each browser removes its saved credential automatically and offers a direct return to the home screen. The summary remains visible until the player returns home, but refreshing after completion does not restore the old seat.

## Deployment boundary

Participant credentials prevent room snapshots and actions from being read or changed with a room code alone. Room creation and joining consume persistent fixed-window rate-limit buckets keyed by a hash of the network client address. Abandoned rooms and their participant sessions are deleted after a configurable inactivity period.

Only the host participant credential can end a game. Ending early deletes the room and all of its participant sessions atomically. Other clients discover that the room has ended on their next poll, discard their local credentials, and return to the home screen.

The container edge applies an additional create/join throttle, overwrites untrusted forwarded-address headers, serves the client and API from one origin, and adds browser security headers. The API container is internal-only.

The AI gateway runs on Oracle behind an outbound-only Cloudflare Tunnel and requires a shared Bearer credential. The Qwen runtime is reachable only from its private container network, while the gateway is additionally bound to loopback for server-side diagnostics. TLS terminates at Cloudflare. Backups and monitoring remain deployment responsibilities.
