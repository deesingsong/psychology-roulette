import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import type { RoomView, Session } from "./types";

const POSITIONS = [
  { value: -100, short: "Strongly disagree", mark: "−−−" },
  { value: -67, short: "Disagree", mark: "−−" },
  { value: -33, short: "Lean disagree", mark: "−" },
  { value: 0, short: "In the middle", mark: "·" },
  { value: 33, short: "Lean agree", mark: "+" },
  { value: 67, short: "Agree", mark: "++" },
  { value: 100, short: "Strongly agree", mark: "+++" },
] as const;

const SESSION_KEY = "psychology-roulette-session";

function readSession(): Session | null {
  const raw = sessionStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

function App() {
  const [session, setSession] = useState<Session | null>(readSession);
  const [room, setRoom] = useState<RoomView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const saveSession = (next: Session | null) => {
    setSession(next);
    if (next) sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(SESSION_KEY);
  };

  const refresh = useCallback(async () => {
    if (!session) return;
    try {
      setRoom(await api.getRoom(session.roomCode));
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not reach the room.",
      );
    }
  }, [session]);

  useEffect(() => {
    if (!session) return;
    const initialRefresh = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), 1000);
    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
    };
  }, [refresh, session]);

  if (!session) {
    return (
      <Landing
        onSession={(nextSession, nextRoom) => {
          saveSession(nextSession);
          setRoom(nextRoom);
        }}
      />
    );
  }

  if (!room) {
    return (
      <main className="shell centered">
        <p className="eyebrow">ROOM {session.roomCode}</p>
        <h1>Finding your table…</h1>
        {error && <p className="error">{error}</p>}
        <button className="button secondary" onClick={() => saveSession(null)}>
          Leave room
        </button>
      </main>
    );
  }

  return (
    <Game
      session={session}
      room={room}
      error={error}
      onRoom={setRoom}
      onError={setError}
      onLeave={() => {
        saveSession(null);
        setRoom(null);
      }}
    />
  );
}

interface LandingProps {
  onSession: (session: Session, room: RoomView) => void;
}

function Landing({ onSession }: LandingProps) {
  const [mode, setMode] = useState<"home" | "create" | "join">("home");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const normalizedName = name.trim().replace(/\s+/g, " ");
    try {
      const result =
        mode === "create"
          ? await api.createRoom(normalizedName)
          : await api.joinRoom(code.trim().toUpperCase(), normalizedName);
      onSession(
        {
          roomCode: result.room.code,
          playerId: result.player_id,
          playerName: normalizedName,
          isHost: mode === "create",
        },
        result.room,
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not enter the room.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="landing shell">
      <div className="brand-row">
        <span className="brand-mark">PR</span>
        <span>PSYCHOLOGY ROULETTE</span>
      </div>
      <section className="hero">
        <p className="eyebrow">A SOCIAL REASONING GAME</p>
        <h1>How well do you understand the room?</h1>
        <p className="lede">
          Take a position. Read your friends. Defend the argument you disagree
          with. See what changes when people actually talk.
        </p>
      </section>

      {mode === "home" ? (
        <section className="action-grid">
          <button
            className="choice-card coral"
            onClick={() => setMode("create")}
          >
            <span className="choice-number">01</span>
            <strong>Create a room</strong>
            <span>Host a new table and invite your friends.</span>
          </button>
          <button
            className="choice-card violet"
            onClick={() => setMode("join")}
          >
            <span className="choice-number">02</span>
            <strong>Join a room</strong>
            <span>Enter the four-letter code on the host's screen.</span>
          </button>
        </section>
      ) : (
        <form className="entry-card" onSubmit={submit}>
          <button
            className="text-button"
            type="button"
            onClick={() => setMode("home")}
          >
            ← Back
          </button>
          <p className="eyebrow">
            {mode === "create" ? "CREATE A TABLE" : "JOIN A TABLE"}
          </p>
          <h2>
            {mode === "create" ? "What should we call you?" : "Enter the room"}
          </h2>
          {mode === "join" && (
            <label>
              Room code
              <input
                className="code-input"
                value={code}
                onChange={(event) =>
                  setCode(event.target.value.slice(0, 4).toUpperCase())
                }
                placeholder="KJDM"
                minLength={4}
                maxLength={4}
                required
              />
            </label>
          )}
          <label>
            Display name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Your name"
              maxLength={24}
              autoFocus
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="button primary" disabled={busy}>
            {busy
              ? "One moment…"
              : mode === "create"
                ? "Create room"
                : "Take my seat"}
          </button>
        </form>
      )}

      <footer>Understanding earns more than agreement.</footer>
    </main>
  );
}

interface GameProps {
  session: Session;
  room: RoomView;
  error: string | null;
  onRoom: (room: RoomView) => void;
  onError: (error: string | null) => void;
  onLeave: () => void;
}

function Game({ session, room, error, onRoom, onError, onLeave }: GameProps) {
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);

  const act = async (request: () => Promise<RoomView>) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    onError(null);
    try {
      onRoom(await request());
    } catch (reason) {
      onError(
        reason instanceof Error ? reason.message : "That action did not work.",
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return (
    <main className="game-shell shell">
      <header className="game-header">
        <div className="brand-row compact">
          <span className="brand-mark">PR</span>
          <span>PSYCHOLOGY ROULETTE</span>
        </div>
        <button
          className="room-chip"
          onClick={() => navigator.clipboard.writeText(room.code)}
        >
          ROOM <strong>{room.code}</strong>
        </button>
      </header>

      {error && <p className="error floating-error">{error}</p>}

      {room.phase === "lobby" && (
        <Lobby
          room={room}
          isHost={session.isHost}
          pending={pending}
          onStart={() => act(() => api.startRoom(room.code, session.playerId))}
        />
      )}
      {room.phase === "answering" && (
        <Answering
          key={room.round_number}
          session={session}
          room={room}
          pending={pending}
          onSubmit={(position, confidence) =>
            act(() =>
              api.submitAnswer(
                room.code,
                session.playerId,
                position,
                confidence,
              ),
            )
          }
          onReveal={() => act(() => api.reveal(room.code, session.playerId))}
        />
      )}
      {room.phase === "modifier" && (
        <ModifierStage
          key={room.round_number}
          session={session}
          room={room}
          pending={pending}
          onSubmit={(value) =>
            act(() => api.submitModifier(room.code, session.playerId, value))
          }
          onReveal={() => act(() => api.reveal(room.code, session.playerId))}
        />
      )}
      {room.phase === "reveal" && (
        <Reveal
          room={room}
          isHost={session.isHost}
          pending={pending}
          onAdvance={() => act(() => api.advance(room.code, session.playerId))}
        />
      )}
      {room.phase === "complete" && <Complete room={room} />}

      <button className="text-button leave" onClick={onLeave}>
        Leave room
      </button>
    </main>
  );
}

function Lobby({
  room,
  isHost,
  pending,
  onStart,
}: {
  room: RoomView;
  isHost: boolean;
  pending: boolean;
  onStart: () => void;
}) {
  return (
    <section className="stage lobby-stage">
      <div>
        <p className="eyebrow">THE TABLE IS OPEN</p>
        <h1>Waiting for minds to arrive.</h1>
        <p className="lede">
          Share the room code. Two players are enough for development; the final
          game is designed for three to eight.
        </p>
      </div>
      <div className="player-grid">
        {room.players.map((player, index) => (
          <article className="player-card" key={player.name}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{player.name}</strong>
            <small>{player.is_host ? "HOST" : "READY"}</small>
          </article>
        ))}
        {Array.from({ length: Math.max(0, 3 - room.players.length) }).map(
          (_, index) => (
            <article className="player-card empty" key={`empty-${index}`}>
              <span>··</span>
              <strong>Open seat</strong>
            </article>
          ),
        )}
      </div>
      {isHost ? (
        <button
          className="button primary wide"
          disabled={room.players.length < 2 || pending}
          onClick={onStart}
        >
          Begin the game
        </button>
      ) : (
        <p className="waiting-note">
          The host will begin when everyone is ready.
        </p>
      )}
    </section>
  );
}

function Answering({
  session,
  room,
  pending,
  onSubmit,
  onReveal,
}: {
  session: Session;
  room: RoomView;
  pending: boolean;
  onSubmit: (position: number, confidence: number) => void;
  onReveal: () => void;
}) {
  const [position, setPosition] = useState<number | null>(null);
  const [confidence, setConfidence] = useState(50);
  const me = room.players.find((player) => player.name === session.playerName);
  const everyoneAnswered = room.players.every((player) => player.has_answered);

  return (
    <section className="stage question-stage">
      <div className="round-meta">
        <span>
          ROUND {room.round_number} / {room.round_count}
        </span>
        <span>{room.question?.category.replaceAll("_", " ")}</span>
        <span>INTENSITY {room.question?.intensity}</span>
      </div>
      <h1 className="question">{room.question?.prompt}</h1>

      {me?.has_answered ? (
        <div className="submitted-panel">
          <span className="pulse" />
          <h2>Position locked.</h2>
          <p>
            {room.players.filter((player) => player.has_answered).length} of{" "}
            {room.players.length} minds are in.
          </p>
          {session.isHost && everyoneAnswered && (
            <button
              className="button primary"
              disabled={pending}
              onClick={onReveal}
            >
              Reveal the room
            </button>
          )}
        </div>
      ) : (
        <div className="answer-panel">
          <div className="scale-labels">
            <span>DISAGREE</span>
            <span>AGREE</span>
          </div>
          <div className="position-grid">
            {POSITIONS.map((option) => (
              <button
                className={
                  position === option.value ? "position active" : "position"
                }
                key={option.value}
                onClick={() => setPosition(option.value)}
                title={option.short}
                aria-label={option.short}
                aria-pressed={position === option.value}
                disabled={pending}
              >
                <strong>{option.mark}</strong>
                <span>{option.short}</span>
              </button>
            ))}
          </div>
          <label className="confidence">
            <span>How certain are you?</span>
            <strong>{confidence}%</strong>
            <input
              type="range"
              min="0"
              max="100"
              step="10"
              value={confidence}
              onChange={(event) => setConfidence(Number(event.target.value))}
              disabled={pending}
            />
          </label>
          <button
            className="button primary wide"
            disabled={position === null || pending}
            onClick={() => position !== null && onSubmit(position, confidence)}
          >
            Lock my position
          </button>
        </div>
      )}
      <SubmissionRail room={room} />
    </section>
  );
}

function SubmissionRail({ room }: { room: RoomView }) {
  return (
    <div className="submission-rail">
      {room.players.map((player) => (
        <span
          className={player.has_answered ? "answered" : ""}
          key={player.name}
        >
          {player.name}
        </span>
      ))}
    </div>
  );
}

function ModifierStage({
  session,
  room,
  pending,
  onSubmit,
  onReveal,
}: {
  session: Session;
  room: RoomView;
  pending: boolean;
  onSubmit: (value: number | string) => void;
  onReveal: () => void;
}) {
  const [value, setValue] = useState<number | string | null>(null);
  const modifier = room.modifier;
  const me = room.players.find((player) => player.name === session.playerName);
  const everyoneSubmitted =
    modifier !== null &&
    modifier.submissions_count >= modifier.required_submissions;

  if (!modifier) {
    return (
      <section className="stage modifier-stage">
        <p className="eyebrow">THE WHEEL IS TURNING</p>
        <h1>Preparing the challenge…</h1>
      </section>
    );
  }

  const predictionOptions = modifier.options
    .filter((option): option is number => typeof option === "number")
    .map(
      (option) =>
        POSITIONS.find((position) => position.value === option) ?? {
          value: option,
          short: formatPosition(option),
          mark: formatPosition(option),
        },
    );
  const principleOptions = modifier.options.filter(
    (option): option is string => typeof option === "string",
  );

  return (
    <section className="stage modifier-stage">
      <div className="modifier-heading">
        <span className="modifier-badge">OCCASIONAL MODIFIER</span>
        <p className="eyebrow">ROUND {room.round_number}</p>
        <h1>{modifier.title}</h1>
        <p className="lede">{modifier.instructions}</p>
      </div>
      <div className="modifier-question">
        <span>THE QUESTION</span>
        <p>{room.question?.prompt}</p>
      </div>

      {me?.has_modifier_submitted ? (
        <div className="submitted-panel modifier-wait">
          <span className="pulse violet-pulse" />
          <h2>Choice locked.</h2>
          <p>
            {modifier.submissions_count} of {modifier.required_submissions}{" "}
            minds are in.
          </p>
          {session.isHost && everyoneSubmitted && (
            <button
              className="button primary"
              disabled={pending}
              onClick={onReveal}
            >
              Reveal the room
            </button>
          )}
        </div>
      ) : (
        <div className="answer-panel modifier-input">
          {modifier.type === "predict_room" && (
            <>
              <div className="scale-labels">
                <span>ROOM DISAGREES</span>
                <span>ROOM AGREES</span>
              </div>
              {predictionOptions.length === 0 && (
                <p className="error">
                  No valid prediction options are available.
                </p>
              )}
              <div className="position-grid">
                {predictionOptions.map((option) => (
                  <button
                    className={
                      value === option.value ? "position active" : "position"
                    }
                    key={option.value}
                    onClick={() => setValue(option.value)}
                    title={option.short}
                    aria-label={option.short}
                    aria-pressed={value === option.value}
                    disabled={pending}
                  >
                    <strong>{option.mark}</strong>
                    <span>{option.short}</span>
                  </button>
                ))}
              </div>
            </>
          )}

          {modifier.type === "secret_principle" && (
            <div className="principle-grid">
              {principleOptions.length === 0 && (
                <p className="error">No valid principles are available.</p>
              )}
              {principleOptions.map((option) => (
                <button
                  className={
                    value === option ? "principle active" : "principle"
                  }
                  key={option}
                  onClick={() => setValue(option)}
                  aria-pressed={value === option}
                  disabled={pending}
                >
                  {option.replaceAll("_", " ")}
                </button>
              ))}
            </div>
          )}

          <button
            className="button primary wide"
            disabled={value === null || pending}
            onClick={() => value !== null && onSubmit(value)}
          >
            Lock my choice
          </button>
        </div>
      )}

      <div className="submission-rail">
        {room.players.map((player) => (
          <span
            className={player.has_modifier_submitted ? "answered" : ""}
            key={player.name}
          >
            {player.name}
          </span>
        ))}
      </div>
    </section>
  );
}

function Reveal({
  room,
  isHost,
  pending,
  onAdvance,
}: {
  room: RoomView;
  isHost: boolean;
  pending: boolean;
  onAdvance: () => void;
}) {
  const sortedAnswers = useMemo(
    () =>
      [...(room.revealed_answers ?? [])].sort(
        (a, b) => a.position - b.position,
      ),
    [room.revealed_answers],
  );
  return (
    <section className="stage reveal-stage">
      <p className="eyebrow">THE ROOM HAS SPOKEN</p>
      <h1>{room.question?.prompt}</h1>
      <RevealModifierCard room={room} />
      <div className="reveal-axis">
        <div className="axis-line" />
        <span className="axis-left">DISAGREE</span>
        <span className="axis-right">AGREE</span>
        {sortedAnswers.map((answer, index) => (
          <div
            className="answer-pin"
            key={answer.player_name}
            style={{
              left: `${(answer.position + 100) / 2}%`,
              top: `${42 + (index % 2) * 58}px`,
            }}
          >
            <span>{answer.player_name.slice(0, 1).toUpperCase()}</span>
            <strong>{answer.player_name}</strong>
            <small>{answer.confidence}% sure</small>
          </div>
        ))}
      </div>
      <div className="stat-grid">
        <article>
          <span>ROOM AVERAGE</span>
          <strong>{formatPosition(room.summary?.average_position)}</strong>
        </article>
        <article>
          <span>OPINION RANGE</span>
          <strong>{room.summary?.range ?? 0}</strong>
        </article>
        <article>
          <span>AVG. CONFIDENCE</span>
          <strong>{room.summary?.average_confidence ?? 0}%</strong>
        </article>
      </div>
      <div className="discussion-card">
        <span>DISCUSSION PROMPT</span>
        <p>
          Who is furthest from the room—and what value is their answer
          protecting?
        </p>
      </div>
      {isHost ? (
        <button
          className="button primary wide"
          disabled={pending}
          onClick={onAdvance}
        >
          {room.round_number === room.round_count
            ? "See the final table"
            : "Next round"}
        </button>
      ) : (
        <p className="waiting-note">
          Discuss it. The host advances when the room is ready.
        </p>
      )}
    </section>
  );
}

function RevealModifierCard({ room }: { room: RoomView }) {
  const modifier = room.modifier;
  if (!modifier) return null;

  if (modifier.type === "devils_advocate") {
    return (
      <article className="reveal-modifier devil-card">
        <span className="modifier-badge">DEVIL'S ADVOCATE</span>
        <h2>{modifier.target_player_name}, the wheel chose you.</h2>
        <p>{modifier.instructions}</p>
      </article>
    );
  }

  return (
    <article className="reveal-modifier">
      <span className="modifier-badge">{modifier.title}</span>
      <h2>
        {modifier.type === "predict_room"
          ? "Who read the room?"
          : "What mattered underneath?"}
      </h2>
      <div className="modifier-result-grid">
        {(modifier.results ?? []).map((result) => (
          <div key={result.player_name}>
            <strong>{result.player_name}</strong>
            <span>
              {typeof result.value === "number"
                ? formatPosition(result.value)
                : result.value.replaceAll("_", " ")}
            </span>
            {result.score !== null && <small>{result.score} pts</small>}
          </div>
        ))}
      </div>
    </article>
  );
}

function Complete({ room }: { room: RoomView }) {
  return (
    <section className="stage complete-stage">
      <p className="eyebrow">SESSION COMPLETE</p>
      <h1>Tonight's table</h1>
      <p className="lede">
        You finished {room.round_count} rounds. Session titles and cross-round
        movement analytics are the next feature entering the engine.
      </p>
      <div className="player-grid">
        {room.players.map((player) => (
          <article className="player-card" key={player.name}>
            <span>◆</span>
            <strong>{player.name}</strong>
            <small>PLAYED</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function formatPosition(value: number | undefined) {
  if (value === undefined) return "—";
  if (value === 0) return "0";
  return value > 0 ? `+${value}` : String(value);
}

export default App;
