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
  -> REVEAL
  -> ANSWERING (next round)
  -> COMPLETE
```

Answers are private while the room is in `ANSWERING`. The room snapshot exposes only whether each player has submitted. Positions become visible in `REVEAL`.

## Modifier schedule

Modifiers are occasional: not every round must contain one. The engine selects from allowed modifier templates using seeded weighted randomness. Target selection and scoring remain deterministic server responsibilities.

AI may eventually generate contextual wording inside a selected template. It may not invent authoritative scoring rules, select ineligible players, or block the round. Invalid or late AI output falls back to curated copy.

Initial modifier families:

1. Predict the Room
2. Devil's Advocate
3. Secret Principle
4. Steelman
5. Change My Mind

## Scoring

Agreement with the room is never itself rewarded. Future scoring can reward prediction accuracy, fair representation of another position, or correctly identifying anonymous reasoning.

## Content

Questions are curated JSON records containing a category, intensity, relevant values, and allowed modifiers. Live AI-generated questions are outside the initial game loop.
