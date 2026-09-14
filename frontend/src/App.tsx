import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, api } from "./api";
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

const SESSION_STORAGE_VERSION = 1;
const SESSION_KEY = "psychology-roulette-session-v1";
const LEGACY_SESSION_KEY = "psychology-roulette-session";

interface StoredSession {
  version: typeof SESSION_STORAGE_VERSION;
  roomCode: string;
  accessToken: string;
}

interface PendingEntryAttempt {
  fingerprint: string;
  accessToken: string;
}

interface ActiveAction {
  accessToken: string;
  generation: number;
}

function createAccessToken() {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window
    .btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

function readStoredSession(): StoredSession | null {
  try {
    sessionStorage.removeItem(LEGACY_SESSION_KEY);
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "version" in parsed &&
      parsed.version === SESSION_STORAGE_VERSION &&
      "roomCode" in parsed &&
      typeof parsed.roomCode === "string" &&
      /^[A-Z]{4}$/.test(parsed.roomCode) &&
      "accessToken" in parsed &&
      typeof parsed.accessToken === "string" &&
      parsed.accessToken.length > 0
    ) {
      return {
        version: SESSION_STORAGE_VERSION,
        roomCode: parsed.roomCode,
        accessToken: parsed.accessToken,
      };
    }

    localStorage.removeItem(SESSION_KEY);
    return null;
  } catch {
    removeStoredSession();
    return null;
  }
}

function writeStoredSession(session: StoredSession) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return true;
  } catch {
    return false;
  }
}

function removeStoredSession() {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // The in-memory session can still be forgotten if browser storage is blocked.
  }
}

function isAbortError(reason: unknown) {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

async function copyText(value: string) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(value);
    return;
  }
  window.prompt("Copy this link:", value);
}

function App() {
  const [storedSession, setStoredSession] = useState<StoredSession | null>(
    readStoredSession,
  );
  const [session, setSession] = useState<Session | null>(null);
  const [room, setRoom] = useState<RoomView | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [landingNotice, setLandingNotice] = useState<string | null>(null);
  const activeTokenRef = useRef(storedSession?.accessToken ?? null);
  const activeActionRef = useRef<ActiveAction | null>(null);
  const actionPendingRef = useRef(false);
  const pollControllerRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);

  const clearSession = useCallback((notice: string | null = null) => {
    requestGenerationRef.current += 1;
    activeTokenRef.current = null;
    activeActionRef.current = null;
    actionPendingRef.current = false;
    pollControllerRef.current?.abort();
    removeStoredSession();
    setStoredSession(null);
    setSession(null);
    setRoom(null);
    setActionError(null);
    setConnectionError(null);
    setLandingNotice(notice);
  }, []);

  const activateSession = useCallback(
    (nextSession: Session, nextRoom: RoomView) => {
      requestGenerationRef.current += 1;
      activeTokenRef.current = nextSession.accessToken;
      activeActionRef.current = null;
      actionPendingRef.current = false;
      pollControllerRef.current?.abort();
      const stored = {
        version: SESSION_STORAGE_VERSION,
        roomCode: nextSession.roomCode,
        accessToken: nextSession.accessToken,
      } satisfies StoredSession;
      const wasSaved = writeStoredSession(stored);
      setStoredSession(stored);
      setSession(nextSession);
      setRoom(nextRoom);
      setLandingNotice(null);
      setConnectionError(null);
      setActionError(
        wasSaved
          ? null
          : "This browser could not save your reconnect key. Keep this tab open.",
      );
    },
    [],
  );

  useEffect(() => {
    if (!storedSession) return;

    let cancelled = false;
    let inFlight = false;
    let wakeAfterFlight = false;
    let failures = 0;
    let timer: number | undefined;

    const schedule = (delay: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void poll(), delay);
    };

    const poll = async () => {
      if (cancelled) return;
      if (inFlight) {
        wakeAfterFlight = true;
        return;
      }
      if (actionPendingRef.current) {
        schedule(250);
        return;
      }

      inFlight = true;
      const controller = new AbortController();
      pollControllerRef.current = controller;
      const generation = ++requestGenerationRef.current;

      try {
        const restored = await api.resume(
          storedSession.accessToken,
          controller.signal,
        );
        if (
          cancelled ||
          controller.signal.aborted ||
          generation !== requestGenerationRef.current
        ) {
          return;
        }

        const nextSession: Session = {
          roomCode: restored.room.code,
          playerId: restored.player_id,
          playerName: restored.player_name,
          isHost: restored.is_host,
          accessToken: storedSession.accessToken,
        };
        setSession(nextSession);
        setRoom(restored.room);
        setConnectionError(null);
        failures = 0;

        if (storedSession.roomCode !== restored.room.code) {
          const corrected = {
            ...storedSession,
            roomCode: restored.room.code,
          };
          writeStoredSession(corrected);
          setStoredSession(corrected);
        }
      } catch (reason) {
        if (cancelled || isAbortError(reason)) return;
        if (
          reason instanceof ApiError &&
          (reason.status === 401 || reason.status === 410)
        ) {
          cancelled = true;
          clearSession(
            "That game has ended or your saved seat is no longer available.",
          );
          return;
        }

        failures += 1;
        setConnectionError(
          navigator.onLine
            ? "Connection lost. Retrying automatically…"
            : "You appear to be offline. Your seat is saved on this device.",
        );
      } finally {
        inFlight = false;
        if (pollControllerRef.current === controller) {
          pollControllerRef.current = null;
        }
        if (!cancelled) {
          const retryDelay =
            failures === 0
              ? 1000
              : Math.min(1000 * 2 ** Math.min(failures, 4), 10000);
          const delay =
            document.visibilityState === "hidden"
              ? Math.max(retryDelay, 5000)
              : retryDelay;
          schedule(wakeAfterFlight ? 0 : delay);
          wakeAfterFlight = false;
        }
      }
    };

    const wake = () => {
      if (cancelled || document.visibilityState === "hidden") return;
      if (inFlight) {
        wakeAfterFlight = true;
        return;
      }
      schedule(0);
    };

    schedule(0);
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      cancelled = true;
      requestGenerationRef.current += 1;
      window.clearTimeout(timer);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
      pollControllerRef.current?.abort();
    };
  }, [clearSession, storedSession]);

  useEffect(() => {
    if (room?.phase === "complete") {
      // Keep the final table on screen, but a reload now starts from home.
      removeStoredSession();
    }
  }, [room?.phase]);

  const beginAction = useCallback((accessToken: string) => {
    if (activeTokenRef.current !== accessToken) return null;
    const generation = ++requestGenerationRef.current;
    activeActionRef.current = { accessToken, generation };
    actionPendingRef.current = true;
    pollControllerRef.current?.abort();
    return generation;
  }, []);

  const finishAction = useCallback(
    (accessToken: string, generation: number) => {
      const activeAction = activeActionRef.current;
      if (
        activeAction?.accessToken !== accessToken ||
        activeAction.generation !== generation
      ) {
        return;
      }
      activeActionRef.current = null;
      actionPendingRef.current = false;
    },
    [],
  );

  const acceptActionRoom = useCallback(
    (nextRoom: RoomView, accessToken: string, generation: number) => {
      const activeAction = activeActionRef.current;
      if (
        activeTokenRef.current !== accessToken ||
        activeAction?.accessToken !== accessToken ||
        activeAction.generation !== generation
      ) {
        return;
      }
      setRoom(nextRoom);
      setConnectionError(null);
    },
    [],
  );

  const handleActionError = useCallback(
    (reason: unknown, accessToken: string, generation: number) => {
      const activeAction = activeActionRef.current;
      if (
        activeTokenRef.current !== accessToken ||
        activeAction?.accessToken !== accessToken ||
        activeAction.generation !== generation
      ) {
        return;
      }

      if (
        reason instanceof ApiError &&
        (reason.status === 401 || reason.status === 410)
      ) {
        clearSession(
          "Your reconnect key is no longer valid. Join the room again.",
        );
        return;
      }

      if (!(reason instanceof ApiError) || reason.code === "request_timeout") {
        setConnectionError(
          reason instanceof ApiError
            ? "The server is taking too long. Retrying automatically…"
            : "Connection lost. Retrying automatically…",
        );
        setActionError(
          "We could not confirm that action. The latest room state will appear when you reconnect.",
        );
        return;
      }

      setActionError(errorMessage(reason, "That action did not work."));
    },
    [clearSession],
  );

  const leaveSession = useCallback(() => {
    if (
      window.confirm(
        "Leave this room? You will not be able to reclaim this seat.",
      )
    ) {
      clearSession();
    }
  }, [clearSession]);

  if (!storedSession) {
    return (
      <Landing
        notice={landingNotice}
        onSession={(nextSession, nextRoom) => {
          activateSession(nextSession, nextRoom);
        }}
      />
    );
  }

  if (!session || !room) {
    return (
      <main className="shell centered">
        <p className="eyebrow">ROOM {storedSession.roomCode}</p>
        <h1>Finding your table…</h1>
        {connectionError && (
          <p className="connection-notice" role="status">
            {connectionError}
          </p>
        )}
      </main>
    );
  }

  return (
    <Game
      session={session}
      room={room}
      actionError={actionError}
      connectionError={connectionError}
      onActionRoom={acceptActionRoom}
      onActionError={handleActionError}
      onClearActionError={() => setActionError(null)}
      onActionStart={beginAction}
      onActionEnd={finishAction}
      onReturnHome={(notice) => clearSession(notice ?? null)}
      onLeave={leaveSession}
    />
  );
}

interface LandingProps {
  notice: string | null;
  onSession: (session: Session, room: RoomView) => void;
}

function Landing({ notice, onSession }: LandingProps) {
  const [mode, setMode] = useState<"home" | "create" | "join">("home");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pendingAttemptRef = useRef<PendingEntryAttempt | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const normalizedName = name.trim().replace(/\s+/g, " ");
    const normalizedCode = code.trim().toUpperCase();
    const fingerprint = JSON.stringify([
      mode,
      mode === "join" ? normalizedCode : null,
      normalizedName,
    ]);
    const pendingAttempt =
      pendingAttemptRef.current?.fingerprint === fingerprint
        ? pendingAttemptRef.current
        : {
            fingerprint,
            accessToken: createAccessToken(),
          };
    pendingAttemptRef.current = pendingAttempt;

    try {
      const result =
        mode === "create"
          ? await api.createRoom(normalizedName, pendingAttempt.accessToken)
          : await api.joinRoom(
              normalizedCode,
              normalizedName,
              pendingAttempt.accessToken,
            );
      if (result.access_token !== pendingAttempt.accessToken) {
        throw new ApiError(
          "The server returned a different reconnect key.",
          502,
          "access_token_mismatch",
        );
      }
      pendingAttemptRef.current = null;
      window.history.replaceState(null, "", window.location.pathname);
      onSession(
        {
          roomCode: result.room.code,
          playerId: result.player_id,
          playerName: result.player_name,
          isHost: result.is_host,
          accessToken: result.access_token,
        },
        result.room,
      );
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.code === "request_timeout"
          ? "The server took too long to respond. Try again—the same reconnect key will be reused."
          : errorMessage(reason, "Could not enter the room."),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="landing shell">
      <div className="brand-row">
        <span className="brand-mark">PR</span>
        <span>ARE YOU NICHE OR NPC?</span>
      </div>
      <section className="hero">
        <p className="eyebrow">A SOCIAL PARTY GAME</p>
        <h1>How well do you understand the room?</h1>
        <p className="lede">
          Take a position. Read your friends. Defend the argument you disagree
          with. See what changes when people actually talk.
        </p>
      </section>

      {notice && (
        <p className="connection-notice landing-notice" role="status">
          {notice}
        </p>
      )}

      {mode === "home" ? (
        <section className="action-grid">
          <button
            className="choice-card coral"
            onClick={() => setMode("create")}
          >
            <span className="choice-number">01</span>
            <strong>Create a room</strong>
            <span>Host a new table and share its room code.</span>
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
  actionError: string | null;
  connectionError: string | null;
  onActionRoom: (
    room: RoomView,
    accessToken: string,
    generation: number,
  ) => void;
  onActionError: (
    reason: unknown,
    accessToken: string,
    generation: number,
  ) => void;
  onClearActionError: () => void;
  onActionStart: (accessToken: string) => number | null;
  onActionEnd: (accessToken: string, generation: number) => void;
  onReturnHome: (notice?: string) => void;
  onLeave: () => void;
}

function Game({
  session,
  room,
  actionError,
  connectionError,
  onActionRoom,
  onActionError,
  onClearActionError,
  onActionStart,
  onActionEnd,
  onReturnHome,
  onLeave,
}: GameProps) {
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);

  const act = async (request: () => Promise<RoomView>) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    onClearActionError();
    const accessToken = session.accessToken;
    const generation = onActionStart(accessToken);
    if (generation === null) {
      pendingRef.current = false;
      setPending(false);
      return;
    }
    try {
      onActionRoom(await request(), accessToken, generation);
    } catch (reason) {
      onActionError(reason, accessToken, generation);
    } finally {
      onActionEnd(accessToken, generation);
      pendingRef.current = false;
      setPending(false);
    }
  };

  const endGame = async () => {
    if (
      pendingRef.current ||
      !window.confirm("End this game for everyone and return to the home screen?")
    ) {
      return;
    }
    pendingRef.current = true;
    setPending(true);
    onClearActionError();
    const accessToken = session.accessToken;
    const generation = onActionStart(accessToken);
    if (generation === null) {
      pendingRef.current = false;
      setPending(false);
      return;
    }
    let ended = false;
    try {
      await api.endRoom(room.code, accessToken);
      ended = true;
    } catch (reason) {
      onActionError(reason, accessToken, generation);
    } finally {
      onActionEnd(accessToken, generation);
      pendingRef.current = false;
      setPending(false);
    }
    if (ended) {
      onReturnHome("The game ended.");
    }
  };

  return (
    <main className="game-shell shell">
      <header className="game-header">
        <div className="brand-row compact">
          <span className="brand-mark">PR</span>
          <span>ARE YOU NICHE OR NPC?</span>
        </div>
        <button className="room-chip" onClick={() => void copyText(room.code)}>
          ROOM <strong>{room.code}</strong>
        </button>
        {session.isHost && room.phase !== "complete" && (
          <button
            className="room-chip end-game-chip"
            disabled={pending}
            onClick={() => void endGame()}
          >
            END GAME
          </button>
        )}
      </header>

      {(actionError || connectionError) && (
        <div className="floating-messages">
          {actionError && (
            <p className="error" role="alert">
              {actionError}
            </p>
          )}
          {connectionError && (
            <p className="connection-notice" role="status">
              {connectionError}
            </p>
          )}
        </div>
      )}

      {room.phase === "lobby" && (
        <Lobby
          room={room}
          isHost={session.isHost}
          pending={pending}
          onStart={() =>
            act(() => api.startRoom(room.code, session.accessToken))
          }
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
                session.accessToken,
                position,
                confidence,
              ),
            )
          }
          onReveal={() => act(() => api.reveal(room.code, session.accessToken))}
        />
      )}
      {room.phase === "modifier" && (
        <ModifierStage
          key={room.round_number}
          session={session}
          room={room}
          pending={pending}
          onSubmit={(value) =>
            act(() => api.submitModifier(room.code, session.accessToken, value))
          }
          onReveal={() => act(() => api.reveal(room.code, session.accessToken))}
        />
      )}
      {room.phase === "reveal" && (
        <Reveal
          room={room}
          isHost={session.isHost}
          pending={pending}
          onAdvance={() =>
            act(() => api.advance(room.code, session.accessToken))
          }
        />
      )}
      {room.phase === "follow_up" && (
        <FollowUpStage
          key={room.round_number}
          session={session}
          room={room}
          pending={pending}
          onSubmit={(value) =>
            act(() => api.submitModifier(room.code, session.accessToken, value))
          }
          onReveal={() =>
            act(() => api.advance(room.code, session.accessToken))
          }
        />
      )}
      {room.phase === "follow_up_reveal" && (
        <Reveal
          room={room}
          isHost={session.isHost}
          pending={pending}
          onAdvance={() =>
            act(() => api.advance(room.code, session.accessToken))
          }
        />
      )}
      {room.phase === "complete" && (
        <Complete room={room} onReturnHome={() => onReturnHome()} />
      )}

      {room.phase === "lobby" && (
        <button
          className="text-button leave"
          disabled={pending}
          onClick={onLeave}
        >
          Leave room
        </button>
      )}
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
        <span className="modifier-badge">SURPRISE MODIFIER</span>
        <p className="eyebrow">ROUND {room.round_number}</p>
        <h1>{modifier.title}</h1>
        <p className="lede">{modifier.instructions}</p>
        <ModifierContext context={modifier.context} />
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

function FollowUpStage({
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
  const [position, setPosition] = useState<number | null>(null);
  const [steelman, setSteelman] = useState("");
  const modifier = room.modifier;
  const me = room.players.find((player) => player.name === session.playerName);
  const everyoneSubmitted =
    modifier !== null &&
    modifier.submissions_count >= modifier.required_submissions;

  if (!modifier) {
    return (
      <section className="stage modifier-stage">
        <p className="eyebrow">AFTER THE DISCUSSION</p>
        <h1>Preparing the follow-up...</h1>
      </section>
    );
  }

  const isSteelmanWriter =
    modifier.type === "steelman" &&
    modifier.target_player_name === session.playerName;
  const shouldSubmit = modifier.type === "change_my_mind" || isSteelmanWriter;

  return (
    <section className="stage modifier-stage follow-up-stage">
      <div className="modifier-heading">
        <span className="modifier-badge">AFTER THE DISCUSSION</span>
        <p className="eyebrow">ROUND {room.round_number}</p>
        <h1>{modifier.title}</h1>
        <p className="lede">{modifier.instructions}</p>
        <ModifierContext context={modifier.context} />
      </div>

      {modifier.type === "steelman" && (
        <div className="modifier-question">
          <span>THE HANDOFF</span>
          <p>
            <strong>{modifier.target_player_name}</strong> will steelman{" "}
            <strong>{modifier.source_player_name}</strong>'s position.
          </p>
        </div>
      )}

      {!shouldSubmit ? (
        <div className="submitted-panel modifier-wait">
          <span className="pulse violet-pulse" />
          <h2>Listen for a fair restatement.</h2>
          <p>{modifier.target_player_name} is writing the steelman.</p>
        </div>
      ) : me?.has_modifier_submitted ? (
        <div className="submitted-panel modifier-wait">
          <span className="pulse violet-pulse" />
          <h2>Follow-up locked.</h2>
          <p>
            {modifier.submissions_count} of {modifier.required_submissions}{" "}
            responses are in.
          </p>
        </div>
      ) : modifier.type === "steelman" ? (
        <div className="answer-panel modifier-input">
          <label className="steelman-field">
            <span>YOUR STRONGEST FAIR RESTATEMENT</span>
            <textarea
              value={steelman}
              maxLength={280}
              rows={5}
              disabled={pending}
              onChange={(event) => setSteelman(event.target.value)}
              placeholder="State their reasoning in terms they could endorse..."
            />
            <small>{steelman.trim().length}/280</small>
          </label>
          <button
            className="button primary wide"
            disabled={!steelman.trim() || pending}
            onClick={() => onSubmit(steelman)}
          >
            Lock the steelman
          </button>
        </div>
      ) : (
        <div className="answer-panel modifier-input">
          <div className="scale-labels">
            <span>STRONGLY DISAGREE</span>
            <span>STRONGLY AGREE</span>
          </div>
          <div className="position-grid">
            {POSITIONS.map((option) => (
              <button
                className={
                  position === option.value ? "position active" : "position"
                }
                key={option.value}
                onClick={() => setPosition(option.value)}
                aria-label={option.short}
                aria-pressed={position === option.value}
                disabled={pending}
              >
                <strong>{option.mark}</strong>
                <span>{option.short}</span>
              </button>
            ))}
          </div>
          <button
            className="button primary wide"
            disabled={position === null || pending}
            onClick={() => position !== null && onSubmit(position)}
          >
            Lock my new position
          </button>
        </div>
      )}

      {session.isHost && everyoneSubmitted ? (
        <button
          className="button primary wide"
          disabled={pending}
          onClick={onReveal}
        >
          {modifier.type === "steelman"
            ? "Reveal the steelman"
            : "Reveal what moved"}
        </button>
      ) : (
        <p className="waiting-note">
          {everyoneSubmitted
            ? "The host will reveal the follow-up."
            : "Responses stay private until everyone required has locked in."}
        </p>
      )}
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
  const isFollowUpReveal = room.phase === "follow_up_reveal";
  const advanceLabel = isFollowUpReveal
    ? room.round_number === room.round_count
      ? "See the final table"
      : "Next round"
    : room.modifier?.type === "steelman"
      ? "Begin the steelman"
      : room.modifier?.type === "change_my_mind"
        ? "Poll the room again"
        : room.round_number === room.round_count
          ? "See the final table"
          : "Next round";
  return (
    <section className="stage reveal-stage">
      <p className="eyebrow">
        {isFollowUpReveal ? "THE FOLLOW-UP IS IN" : "THE ROOM HAS SPOKEN"}
      </p>
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
      {!isFollowUpReveal && (
        <div className="discussion-card">
          <span>DISCUSSION PROMPT</span>
          <p>
            {room.question?.discussion_prompt ??
              "Who is furthest from the room—and what value is their answer protecting?"}
          </p>
        </div>
      )}
      {isHost ? (
        <button
          className="button primary wide"
          disabled={pending}
          onClick={onAdvance}
        >
          {advanceLabel}
        </button>
      ) : (
        <p className="waiting-note">
          Discuss it. The host advances when the room is ready.
        </p>
      )}
    </section>
  );
}

function ModifierContext({ context }: { context: string | null }) {
  if (!context) return null;
  return (
    <aside className="modifier-context">
      <span>AI DISCUSSION ANGLE</span>
      <p>{context}</p>
    </aside>
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
        <ModifierContext context={modifier.context} />
      </article>
    );
  }

  if (modifier.type === "steelman") {
    const result = modifier.results?.[0];
    return (
      <article className="reveal-modifier steelman-card">
        <span className="modifier-badge">STEELMAN</span>
        {room.phase === "follow_up_reveal" && result ? (
          <>
            <h2>
              {modifier.target_player_name} on {modifier.source_player_name}'s
              view
            </h2>
            <blockquote>{String(result.value)}</blockquote>
          </>
        ) : (
          <>
            <h2>
              {modifier.target_player_name}, listen closely to{" "}
              {modifier.source_player_name}.
            </h2>
            <p>
              Discuss the question first. Then the selected player will write
              the strongest fair version of that position.
            </p>
          </>
        )}
        <ModifierContext context={modifier.context} />
      </article>
    );
  }

  if (modifier.type === "change_my_mind") {
    if (room.phase !== "follow_up_reveal") {
      return (
        <article className="reveal-modifier">
          <span className="modifier-badge">CHANGE MY MIND</span>
          <h2>Talk first. Then answer once more.</h2>
          <p>
            Everyone will privately choose again after the discussion. Movement
            is information, not a score.
          </p>
          <ModifierContext context={modifier.context} />
        </article>
      );
    }
    return (
      <article className="reveal-modifier">
        <span className="modifier-badge">CHANGE MY MIND</span>
        <h2>What moved?</h2>
        <div className="modifier-result-grid movement-grid">
          {(modifier.results ?? []).map((result) => {
            const original = room.revealed_answers?.find(
              (answer) => answer.player_name === result.player_name,
            )?.position;
            return (
              <div key={result.player_name}>
                <strong>{result.player_name}</strong>
                <span>
                  {formatPosition(original)} to{" "}
                  {formatPosition(Number(result.value))}
                </span>
                <small>
                  {result.movement === 0
                    ? "held position"
                    : `${result.movement} points moved`}
                </small>
              </div>
            );
          })}
        </div>
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
      <ModifierContext context={modifier.context} />
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

function Complete({
  room,
  onReturnHome,
}: {
  room: RoomView;
  onReturnHome: () => void;
}) {
  const summary = room.session_summary;
  if (summary) {
    const recap = room.session_recap;
    return (
      <section className="stage complete-stage">
        <p className="eyebrow">SESSION COMPLETE</p>
        <h1>{recap?.headline ?? "Tonight's table"}</h1>
        <p className="lede">
          {recap?.summary ??
            `${summary.rounds_completed} rounds, mapped without diagnosing anyone.`}
        </p>
        {recap && (
          <div className="recap-grid">
            {recap.highlights.map((item) => (
              <article key={item.fact_id}>
                <span>{item.title}</span>
                <strong>{item.value}</strong>
                <small>{item.detail}</small>
                <p>{item.commentary}</p>
              </article>
            ))}
          </div>
        )}
        <div className="stat-grid final-stats">
          <article>
            <span>TABLE AVERAGE</span>
            <strong>{formatPosition(summary.overall_average_position)}</strong>
          </article>
          <article>
            <span>WIDEST ROUND</span>
            <strong>#{summary.widest_round_number}</strong>
          </article>
          <article>
            <span>CHANGED ANSWERS</span>
            <strong>{summary.total_position_changes}</strong>
          </article>
        </div>
        <div className="round-history">
          <h2>The table across rounds</h2>
          {summary.rounds.map((item) => (
            <article key={item.number}>
              <span>R{item.number}</span>
              <div>
                <strong>{item.prompt}</strong>
                <small>
                  average {formatPosition(item.average_position)} / range{" "}
                  {item.position_range}
                </small>
              </div>
              <i style={{ left: `${(item.average_position + 100) / 2}%` }} />
            </article>
          ))}
        </div>
        <div className="player-summary-grid">
          {summary.players.map((player) => (
            <article className="player-summary-card" key={player.player_name}>
              <span>{player.title}</span>
              <h2>{player.player_name}</h2>
              <p>{player.title_description}</p>
              <dl>
                <div>
                  <dt>Average</dt>
                  <dd>{formatPosition(player.average_position)}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>{player.average_confidence}%</dd>
                </div>
                <div>
                  <dt>Room distance</dt>
                  <dd>{player.average_room_distance}</dd>
                </div>
                <div>
                  <dt>Position span</dt>
                  <dd>{player.position_span}</dd>
                </div>
              </dl>
              {player.prediction_score !== null && (
                <small>Room-reading score: {player.prediction_score}</small>
              )}
              {player.movement_total > 0 && (
                <small>Total follow-up movement: {player.movement_total}</small>
              )}
            </article>
          ))}
        </div>
        <button className="button primary completion-home" onClick={onReturnHome}>
          Return home
        </button>
      </section>
    );
  }
  return (
    <section className="stage complete-stage">
      <p className="eyebrow">SESSION COMPLETE</p>
      <h1>Tonight's table</h1>
      <p className="lede">
        You finished {room.round_count} rounds. The final table is being
        prepared.
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
      <button className="button primary completion-home" onClick={onReturnHome}>
        Return home
      </button>
    </section>
  );
}

function formatPosition(value: number | undefined) {
  if (value === undefined) return "—";
  if (value === 0) return "0";
  return value > 0 ? `+${value}` : String(value);
}

export default App;
