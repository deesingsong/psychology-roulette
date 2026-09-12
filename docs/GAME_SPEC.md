# Game specification — draft 0.1

## Product principle

The questions create discussion. The software creates the game.

Psychology Roulette is not a personality test and does not infer diagnoses or fixed traits. It creates temporary, playful observations from the answers given during one session.

## Session

- 3–8 players is the intended social range; two players are allowed during development.
- A standard session contains six rounds and should last roughly 20–35 minutes.
- Players join using a four-letter room code and a display name.
- The host controls starting and advancing the session.
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
  -> ANSWERING (next round)
  -> COMPLETE
```

Answers are private while the room is in `ANSWERING`. The room snapshot exposes only whether each player has submitted. Positions become visible in `REVEAL`.

The selected modifier remains hidden while players choose their positions. A pre-reveal modifier becomes visible only after every position is locked. Modifier submissions remain private until the reveal.

## Modifier schedule

The opening round is always plain so players can learn the basic loop. Each later eligible round has a stable 65% chance of receiving a modifier. The schedule is derived from a server-secret per-room seed, so it remains stable during play without being predictable from the public room code. If every draw misses, the final eligible round receives one, ensuring a normal multi-round session demonstrates the feature. Setting the modifier chance to zero explicitly disables this fallback.

The engine selects only from modifier templates allowed by the current question. Target selection and scoring remain deterministic server responsibilities. Tests can inject a fixed seed for exact reproducibility, but production seeds are never serialized. Devil's Advocate targets are drawn from the least-targeted players and avoid an immediate repeat whenever another equally fair player is available.

AI may eventually generate contextual wording inside a selected template. It may not invent authoritative scoring rules, select ineligible players, or block the round. Invalid or late AI output falls back to curated copy.

Initial modifier families:

1. **Predict the Room** — every player predicts the group average before answers appear.
2. **Secret Principle** — every player privately selects the value that mattered most to their answer.
3. **Devil's Advocate** — one eligible player is challenged after the reveal to defend a meaningfully different position; a player who chose the middle may defend either clear side.
4. **Steelman** — planned.
5. **Change My Mind** — planned.

The first three are implemented with curated instructions. AI-authored contextual wording is a later enhancement and will use the same validated modifier structure.

## Scoring

Agreement with the room is never itself rewarded. Predict the Room awards up to 100 points:

```text
score = max(0, round(100 - abs(prediction - room_average) / 2))
```

This measures how accurately someone read the group, not whether their personal answer matched it. Future scoring can reward fair representation of another position or correctly identifying anonymous reasoning.

## Content

Questions are curated JSON records containing a category, intensity, relevant values, and allowed modifiers. Live AI-generated questions are outside the initial game loop.

## Deployment boundary

The current four-letter room code and unauthenticated room snapshot are intended for development and trusted local-network play. Before public internet deployment, the server must add unguessable participant or spectator read credentials and request rate limiting so room data cannot be enumerated.
