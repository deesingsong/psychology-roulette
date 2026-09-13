from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Protocol, TypeVar

from psychology_roulette.content import load_questions
from psychology_roulette.domain import GameError, Player, Room
from psychology_roulette.serialization import (
    SNAPSHOT_FORMAT_VERSION,
    SnapshotError,
    room_from_json,
    room_to_json,
)

ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "psychology-roulette.sqlite3"
DEFAULT_ROOM_TTL_SECONDS = 7 * 24 * 60 * 60
T = TypeVar("T")


class UnknownAccessToken(ValueError):
    """The presented bearer token does not identify a participant."""


class RoomAccessDenied(ValueError):
    """A valid participant token was used for a different room."""


class AccessTokenConflict(GameError):
    """A create/join idempotency token was reused for different semantics."""


class StoredRoomError(RuntimeError):
    """Persisted room state is corrupt or uses an unsupported format."""


def configured_database_path() -> str | Path:
    configured = os.environ.get("PSYCHOLOGY_ROULETTE_DB_PATH")
    if configured:
        return configured if configured == ":memory:" else Path(configured).expanduser()
    return DEFAULT_DATABASE_PATH


def configured_room_ttl_seconds() -> int:
    raw_value = os.environ.get("PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS")
    if raw_value is None:
        return DEFAULT_ROOM_TTL_SECONDS
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS must be an integer.") from exc
    if value < 1:
        raise RuntimeError("PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS must be positive.")
    return value


class RoomRepository(Protocol):
    """Storage contract shared by local SQLite and hosted Postgres backends."""

    def create_room(
        self,
        host_name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]: ...

    def join_room(
        self,
        code: str,
        name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]: ...

    def get(self, code: str) -> Room: ...

    def resume(self, access_token: str) -> tuple[Room, Player]: ...

    def read(self, code: str, access_token: str) -> tuple[Room, Player]: ...

    def mutate(
        self,
        code: str,
        access_token: str,
        operation: Callable[[Room, str], T],
    ) -> Room: ...

    def consume_rate_limit(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None: ...

    def prune_expired_rooms(self) -> int: ...

    def end_room(self, code: str, access_token: str) -> None: ...

    def close(self) -> None: ...


class RoomStore:
    """SQLite-backed transactional repository for complete Room aggregates."""

    def __init__(
        self,
        database_path: str | Path = ":memory:",
        *,
        room_ttl_seconds: int | None = DEFAULT_ROOM_TTL_SECONDS,
    ) -> None:
        if room_ttl_seconds is not None and room_ttl_seconds < 1:
            raise ValueError("room_ttl_seconds must be positive or None.")
        self.database_path = str(database_path)
        self.room_ttl_seconds = room_ttl_seconds
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if self.database_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
            self._initialize_schema()
            self._prune_expired_rooms_locked()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY COLLATE NOCASE,
                state_json TEXT NOT NULL,
                format_version INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            );

            CREATE TABLE IF NOT EXISTS player_sessions (
                token_hash BLOB PRIMARY KEY,
                room_code TEXT NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
                player_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                UNIQUE (room_code, player_id)
            );

            CREATE INDEX IF NOT EXISTS player_sessions_room_code_idx
                ON player_sessions (room_code);

            CREATE TABLE IF NOT EXISTS entry_rate_limits (
                bucket_key TEXT PRIMARY KEY,
                window_started INTEGER NOT NULL,
                attempts INTEGER NOT NULL
            );
            """
        )
    @staticmethod
    def _normalize_code(code: str) -> str:
        return code.strip().upper()

    @staticmethod
    def _validate_access_token(access_token: str) -> None:
        if not isinstance(access_token, str) or len(access_token) != 43:
            raise UnknownAccessToken("A valid room access token is required.")
        try:
            decoded = base64.b64decode(
                access_token + "=",
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise UnknownAccessToken("A valid room access token is required.") from exc
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        if len(decoded) != 32 or canonical != access_token:
            raise UnknownAccessToken("A valid room access token is required.")

    @classmethod
    def _token_hash(cls, access_token: str) -> bytes:
        cls._validate_access_token(access_token)
        return hashlib.sha256(access_token.encode("utf-8")).digest()

    @staticmethod
    def _new_access_token() -> str:
        # token_urlsafe receives bytes of entropy; 32 bytes is 256 bits.
        return secrets.token_urlsafe(32)

    def _begin_write(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _rollback(self) -> None:
        self._connection.rollback()

    def _commit(self) -> None:
        self._connection.commit()

    def _load_room(self, code: str) -> Room:
        row = self._connection.execute(
            "SELECT state_json, format_version FROM rooms WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            raise GameError("Room not found.")
        if row["format_version"] != SNAPSHOT_FORMAT_VERSION:
            raise StoredRoomError(
                f"Room {code} uses unsupported snapshot format {row['format_version']}."
            )
        try:
            room = room_from_json(row["state_json"])
        except SnapshotError as exc:
            raise StoredRoomError(f"Room {code} has invalid stored state.") from exc
        if room.code != code:
            raise StoredRoomError(f"Room {code} has a mismatched stored code.")
        return room

    def _prune_expired_rooms_locked(self) -> int:
        if self.room_ttl_seconds is None:
            return 0
        cursor = self._connection.execute(
            """
            DELETE FROM rooms
            WHERE (julianday('now') - julianday(updated_at)) * 86400 > ?
            """,
            (self.room_ttl_seconds,),
        )
        return cursor.rowcount

    def prune_expired_rooms(self) -> int:
        """Delete inactive rooms and their participant credentials."""
        with self._lock:
            return self._prune_expired_rooms_locked()

    def consume_rate_limit(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        """Consume one persistent fixed-window attempt, or return Retry-After seconds."""
        if not bucket_key or len(bucket_key) > 200:
            raise ValueError("bucket_key must contain 1 to 200 characters.")
        if limit < 1 or window_seconds < 1:
            raise ValueError("Rate limit and window must be positive.")
        current_time = int(time.time() if now is None else now)
        with self._lock:
            try:
                self._begin_write()
                self._connection.execute(
                    "DELETE FROM entry_rate_limits WHERE window_started < ?",
                    (current_time - max(86400, window_seconds * 2),),
                )
                row = self._connection.execute(
                    """
                    SELECT window_started, attempts
                    FROM entry_rate_limits
                    WHERE bucket_key = ?
                    """,
                    (bucket_key,),
                ).fetchone()
                if row is None or current_time - row["window_started"] >= window_seconds:
                    self._connection.execute(
                        """
                        INSERT INTO entry_rate_limits (bucket_key, window_started, attempts)
                        VALUES (?, ?, 1)
                        ON CONFLICT(bucket_key) DO UPDATE SET
                            window_started = excluded.window_started,
                            attempts = 1
                        """,
                        (bucket_key, current_time),
                    )
                    self._commit()
                    return None
                if row["attempts"] >= limit:
                    retry_after = max(
                        1,
                        math.ceil(window_seconds - (current_time - row["window_started"])),
                    )
                    self._commit()
                    return retry_after
                self._connection.execute(
                    "UPDATE entry_rate_limits SET attempts = attempts + 1 WHERE bucket_key = ?",
                    (bucket_key,),
                )
                self._commit()
                return None
            except Exception:
                self._rollback()
                raise

    def _save_room(self, room: Room) -> None:
        cursor = self._connection.execute(
            """
            UPDATE rooms
            SET state_json = ?,
                format_version = ?,
                revision = revision + 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE code = ?
            """,
            (room_to_json(room), SNAPSHOT_FORMAT_VERSION, room.code),
        )
        if cursor.rowcount != 1:
            raise StoredRoomError(f"Room {room.code} disappeared during an update.")

    def _insert_session(self, room_code: str, player_id: str, access_token: str) -> None:
        self._connection.execute(
            """
            INSERT INTO player_sessions (token_hash, room_code, player_id)
            VALUES (?, ?, ?)
            """,
            (self._token_hash(access_token), room_code, player_id),
        )

    def _find_session(self, access_token: str) -> tuple[str, str] | None:
        row = self._connection.execute(
            "SELECT room_code, player_id FROM player_sessions WHERE token_hash = ?",
            (self._token_hash(access_token),),
        ).fetchone()
        if row is None:
            return None
        return row["room_code"], row["player_id"]

    def _resolve_session(self, access_token: str) -> tuple[str, str]:
        session = self._find_session(access_token)
        if session is None:
            raise UnknownAccessToken("A valid room access token is required.")
        return session

    @staticmethod
    def _player_from_room(room: Room, player_id: str) -> Player:
        player = room.players.get(player_id)
        if player is None:
            raise StoredRoomError(
                f"A participant session for room {room.code} references a missing player."
            )
        return player

    @staticmethod
    def _normalized_name(name: str) -> str:
        return " ".join(name.split())

    def _idempotent_session(
        self,
        access_token: str,
        *,
        expected_room_code: str | None,
        expected_name: str,
        expected_host: bool,
    ) -> tuple[Room, Player] | None:
        session = self._find_session(access_token)
        if session is None:
            return None
        room_code, player_id = session
        room = self._load_room(room_code)
        player = self._player_from_room(room, player_id)
        if (
            (expected_room_code is not None and room_code != expected_room_code)
            or player.name != expected_name
            or player.is_host is not expected_host
        ):
            raise AccessTokenConflict(
                "That access token is already assigned to a different room or participant."
            )
        return room, player

    def create_room(
        self,
        host_name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]:
        """Create a room, its host, and the host credential as one durable unit."""
        active_token = access_token if access_token is not None else self._new_access_token()
        self._validate_access_token(active_token)
        normalized_name = self._normalized_name(host_name)

        with self._lock:
            self._prune_expired_rooms_locked()
            try:
                self._begin_write()
                existing = self._idempotent_session(
                    active_token,
                    expected_room_code=None,
                    expected_name=normalized_name,
                    expected_host=True,
                )
                if existing is not None:
                    room, host = existing
                    self._commit()
                    return room, host, active_token

                for _ in range(50):
                    code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
                    collision = self._connection.execute(
                        "SELECT 1 FROM rooms WHERE code = ?",
                        (code,),
                    ).fetchone()
                    if collision is not None:
                        continue
                    room = Room(
                        code=code,
                        questions=load_questions(),
                        modifier_seed=secrets.token_hex(32),
                    )
                    host = room.add_player(host_name, is_host=True)
                    self._connection.execute(
                        """
                        INSERT INTO rooms (code, state_json, format_version)
                        VALUES (?, ?, ?)
                        """,
                        (
                            room.code,
                            room_to_json(room),
                            SNAPSHOT_FORMAT_VERSION,
                        ),
                    )
                    self._insert_session(room.code, host.id, active_token)
                    self._commit()
                    return room, host, active_token
                raise RuntimeError("Could not allocate a unique room code.")
            except Exception:
                self._rollback()
                raise

    def join_room(
        self,
        code: str,
        name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]:
        """Join and issue a credential in the same transaction as the room update."""
        normalized = self._normalize_code(code)
        active_token = access_token if access_token is not None else self._new_access_token()
        self._validate_access_token(active_token)
        normalized_name = self._normalized_name(name)
        with self._lock:
            self._prune_expired_rooms_locked()
            try:
                self._begin_write()
                existing = self._idempotent_session(
                    active_token,
                    expected_room_code=normalized,
                    expected_name=normalized_name,
                    expected_host=False,
                )
                if existing is not None:
                    room, player = existing
                    self._commit()
                    return room, player, active_token

                room = self._load_room(normalized)
                player = room.add_player(name)
                self._save_room(room)
                self._insert_session(room.code, player.id, active_token)
                self._commit()
                return room, player, active_token
            except Exception:
                self._rollback()
                raise

    def end_room(self, code: str, access_token: str) -> None:
        """Delete a room and every participant credential after host authorization."""
        normalized = self._normalize_code(code)
        with self._lock:
            self._prune_expired_rooms_locked()
            try:
                self._begin_write()
                room_code, player_id = self._resolve_session(access_token)
                if room_code != normalized:
                    raise RoomAccessDenied("This access token does not belong to that room.")
                room = self._load_room(normalized)
                player = self._player_from_room(room, player_id)
                if not player.is_host:
                    raise GameError("Only the host can end the game.")
                cursor = self._connection.execute(
                    "DELETE FROM rooms WHERE code = ?",
                    (normalized,),
                )
                if cursor.rowcount != 1:
                    raise StoredRoomError(f"Room {normalized} disappeared while ending.")
                self._commit()
            except Exception:
                self._rollback()
                raise

    def get(self, code: str) -> Room:
        """Return a detached read-only aggregate snapshot for diagnostics and tests."""
        normalized = self._normalize_code(code)
        with self._lock:
            self._prune_expired_rooms_locked()
            return self._load_room(normalized)

    def resume(self, access_token: str) -> tuple[Room, Player]:
        with self._lock:
            self._prune_expired_rooms_locked()
            room_code, player_id = self._resolve_session(access_token)
            room = self._load_room(room_code)
            return room, self._player_from_room(room, player_id)

    def read(self, code: str, access_token: str) -> tuple[Room, Player]:
        normalized = self._normalize_code(code)
        with self._lock:
            self._prune_expired_rooms_locked()
            room_code, player_id = self._resolve_session(access_token)
            if room_code != normalized:
                raise RoomAccessDenied("This access token does not belong to that room.")
            room = self._load_room(normalized)
            return room, self._player_from_room(room, player_id)

    def mutate(
        self,
        code: str,
        access_token: str,
        operation: Callable[[Room, str], T],
    ) -> Room:
        """Authenticate, mutate, and save an aggregate under one write transaction."""
        normalized = self._normalize_code(code)
        with self._lock:
            self._prune_expired_rooms_locked()
            try:
                self._begin_write()
                room_code, player_id = self._resolve_session(access_token)
                if room_code != normalized:
                    raise RoomAccessDenied("This access token does not belong to that room.")
                room = self._load_room(normalized)
                self._player_from_room(room, player_id)
                operation(room, player_id)
                self._save_room(room)
                self._commit()
                return room
            except Exception:
                self._rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def configured_store() -> RoomRepository:
    database_url = os.environ.get("PSYCHOLOGY_ROULETTE_DATABASE_URL")
    settings = {
        "room_ttl_seconds": configured_room_ttl_seconds(),
    }
    if database_url:
        # Import lazily so local SQLite development does not require Postgres at startup.
        from psychology_roulette.postgres_store import PostgresRoomStore

        return PostgresRoomStore(database_url, **settings)
    return RoomStore(configured_database_path(), **settings)


default_store: RoomRepository = configured_store()
