from __future__ import annotations

import math
import secrets
import time
from collections.abc import Callable
from typing import TypeVar

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from psychology_roulette.content import load_questions
from psychology_roulette.domain import GameError, Player, Room
from psychology_roulette.serialization import (
    SNAPSHOT_FORMAT_VERSION,
    SnapshotError,
    room_from_json,
    room_to_json,
)
from psychology_roulette.store import (
    ROOM_ALPHABET,
    AccessTokenConflict,
    RoomAccessDenied,
    RoomStore,
    StoredRoomError,
    UnknownAccessToken,
)

T = TypeVar("T")


class PostgresRoomStore:
    """Postgres repository for horizontally scaled, serverless API instances."""

    def __init__(
        self,
        database_url: str,
        *,
        room_ttl_seconds: int | None,
    ) -> None:
        if not database_url:
            raise ValueError("database_url is required.")
        if room_ttl_seconds is not None and room_ttl_seconds < 1:
            raise ValueError("room_ttl_seconds must be positive or None.")
        self.room_ttl_seconds = room_ttl_seconds
        # Supabase's transaction pooler is intended for temporary/serverless clients.
        # One connection per warm function instance prevents a connection fan-out.
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=0,
            max_size=1,
            open=True,
            timeout=10,
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "row_factory": dict_row,
            },
        )

    @staticmethod
    def _advisory_key(token_hash: bytes) -> int:
        return int.from_bytes(token_hash[:8], byteorder="big", signed=True)

    @staticmethod
    def _load_room(connection, code: str, *, for_update: bool = False) -> Room:
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute(
            "SELECT state_json::text AS state_json, format_version "
            f"FROM rooms WHERE code = %s{suffix}",
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

    @staticmethod
    def _save_room(connection, room: Room) -> None:
        cursor = connection.execute(
            """
            UPDATE rooms
            SET state_json = %s::jsonb,
                format_version = %s,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE code = %s
            """,
            (room_to_json(room), SNAPSHOT_FORMAT_VERSION, room.code),
        )
        if cursor.rowcount != 1:
            raise StoredRoomError(f"Room {room.code} disappeared during an update.")

    @staticmethod
    def _insert_session(connection, room_code: str, player_id: str, token_hash: bytes) -> None:
        connection.execute(
            """
            INSERT INTO player_sessions (token_hash, room_code, player_id)
            VALUES (%s, %s, %s)
            """,
            (token_hash, room_code, player_id),
        )

    @staticmethod
    def _find_session(connection, token_hash: bytes) -> tuple[str, str] | None:
        row = connection.execute(
            "SELECT room_code, player_id FROM player_sessions WHERE token_hash = %s",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        return row["room_code"], row["player_id"]

    @classmethod
    def _resolve_session(cls, connection, token_hash: bytes) -> tuple[str, str]:
        session = cls._find_session(connection, token_hash)
        if session is None:
            raise UnknownAccessToken("A valid room access token is required.")
        return session

    @classmethod
    def _idempotent_session(
        cls,
        connection,
        token_hash: bytes,
        *,
        expected_room_code: str | None,
        expected_name: str,
        expected_host: bool,
    ) -> tuple[Room, Player] | None:
        session = cls._find_session(connection, token_hash)
        if session is None:
            return None
        room_code, player_id = session
        room = cls._load_room(connection, room_code)
        player = RoomStore._player_from_room(room, player_id)
        if (
            (expected_room_code is not None and room_code != expected_room_code)
            or player.name != expected_name
            or player.is_host is not expected_host
        ):
            raise AccessTokenConflict(
                "That access token is already assigned to a different room or participant."
            )
        return room, player

    def prune_expired_rooms(self) -> int:
        if self.room_ttl_seconds is None:
            return 0
        with self._pool.connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM rooms
                WHERE updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                """,
                (self.room_ttl_seconds,),
            )
            return cursor.rowcount

    def consume_rate_limit(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        if not bucket_key or len(bucket_key) > 200:
            raise ValueError("bucket_key must contain 1 to 200 characters.")
        if limit < 1 or window_seconds < 1:
            raise ValueError("Rate limit and window must be positive.")
        current_time = int(time.time() if now is None else now)
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "DELETE FROM entry_rate_limits WHERE window_started < %s",
                (current_time - max(86400, window_seconds * 2),),
            )
            connection.execute(
                """
                INSERT INTO entry_rate_limits (bucket_key, window_started, attempts)
                VALUES (%s, %s, 0)
                ON CONFLICT (bucket_key) DO NOTHING
                """,
                (bucket_key, current_time),
            )
            row = connection.execute(
                """
                SELECT window_started, attempts
                FROM entry_rate_limits
                WHERE bucket_key = %s
                FOR UPDATE
                """,
                (bucket_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Rate-limit bucket disappeared during an update.")
            if current_time - row["window_started"] >= window_seconds:
                connection.execute(
                    """
                    UPDATE entry_rate_limits
                    SET window_started = %s, attempts = 1
                    WHERE bucket_key = %s
                    """,
                    (current_time, bucket_key),
                )
                return None
            if row["attempts"] >= limit:
                return max(
                    1,
                    math.ceil(window_seconds - (current_time - row["window_started"])),
                )
            connection.execute(
                "UPDATE entry_rate_limits SET attempts = attempts + 1 WHERE bucket_key = %s",
                (bucket_key,),
            )
            return None

    def create_room(
        self,
        host_name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]:
        active_token = access_token if access_token is not None else RoomStore._new_access_token()
        token_hash = RoomStore._token_hash(active_token)
        normalized_name = RoomStore._normalized_name(host_name)
        self.prune_expired_rooms()

        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (self._advisory_key(token_hash),),
            )
            existing = self._idempotent_session(
                connection,
                token_hash,
                expected_room_code=None,
                expected_name=normalized_name,
                expected_host=True,
            )
            if existing is not None:
                room, host = existing
                return room, host, active_token

            for _ in range(50):
                code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
                room = Room(
                    code=code,
                    questions=load_questions(),
                    modifier_seed=secrets.token_hex(32),
                )
                host = room.add_player(host_name, is_host=True)
                inserted = connection.execute(
                    """
                    INSERT INTO rooms (code, state_json, format_version)
                    VALUES (%s, %s::jsonb, %s)
                    ON CONFLICT (code) DO NOTHING
                    RETURNING code
                    """,
                    (code, room_to_json(room), SNAPSHOT_FORMAT_VERSION),
                ).fetchone()
                if inserted is None:
                    continue
                self._insert_session(connection, code, host.id, token_hash)
                return room, host, active_token
            raise RuntimeError("Could not allocate a unique room code.")

    def join_room(
        self,
        code: str,
        name: str,
        access_token: str | None = None,
    ) -> tuple[Room, Player, str]:
        normalized = RoomStore._normalize_code(code)
        active_token = access_token if access_token is not None else RoomStore._new_access_token()
        token_hash = RoomStore._token_hash(active_token)
        normalized_name = RoomStore._normalized_name(name)
        self.prune_expired_rooms()

        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (self._advisory_key(token_hash),),
            )
            existing = self._idempotent_session(
                connection,
                token_hash,
                expected_room_code=normalized,
                expected_name=normalized_name,
                expected_host=False,
            )
            if existing is not None:
                room, player = existing
                return room, player, active_token

            room = self._load_room(connection, normalized, for_update=True)
            player = room.add_player(name)
            self._save_room(connection, room)
            self._insert_session(connection, room.code, player.id, token_hash)
            return room, player, active_token

    def end_room(self, code: str, access_token: str) -> None:
        normalized = RoomStore._normalize_code(code)
        token_hash = RoomStore._token_hash(access_token)
        self.prune_expired_rooms()
        with self._pool.connection() as connection, connection.transaction():
            room_code, player_id = self._resolve_session(connection, token_hash)
            if room_code != normalized:
                raise RoomAccessDenied("This access token does not belong to that room.")
            room = self._load_room(connection, normalized, for_update=True)
            player = RoomStore._player_from_room(room, player_id)
            if not player.is_host:
                raise GameError("Only the host can end the game.")
            cursor = connection.execute(
                "DELETE FROM rooms WHERE code = %s",
                (normalized,),
            )
            if cursor.rowcount != 1:
                raise StoredRoomError(f"Room {normalized} disappeared while ending.")

    def get(self, code: str) -> Room:
        normalized = RoomStore._normalize_code(code)
        self.prune_expired_rooms()
        with self._pool.connection() as connection:
            return self._load_room(connection, normalized)

    def resume(self, access_token: str) -> tuple[Room, Player]:
        token_hash = RoomStore._token_hash(access_token)
        self.prune_expired_rooms()
        with self._pool.connection() as connection:
            room_code, player_id = self._resolve_session(connection, token_hash)
            room = self._load_room(connection, room_code)
            return room, RoomStore._player_from_room(room, player_id)

    def read(self, code: str, access_token: str) -> tuple[Room, Player]:
        normalized = RoomStore._normalize_code(code)
        token_hash = RoomStore._token_hash(access_token)
        self.prune_expired_rooms()
        with self._pool.connection() as connection:
            room_code, player_id = self._resolve_session(connection, token_hash)
            if room_code != normalized:
                raise RoomAccessDenied("This access token does not belong to that room.")
            room = self._load_room(connection, normalized)
            return room, RoomStore._player_from_room(room, player_id)

    def mutate(
        self,
        code: str,
        access_token: str,
        operation: Callable[[Room, str], T],
    ) -> Room:
        normalized = RoomStore._normalize_code(code)
        token_hash = RoomStore._token_hash(access_token)
        self.prune_expired_rooms()
        with self._pool.connection() as connection, connection.transaction():
            room_code, player_id = self._resolve_session(connection, token_hash)
            if room_code != normalized:
                raise RoomAccessDenied("This access token does not belong to that room.")
            room = self._load_room(connection, normalized, for_update=True)
            RoomStore._player_from_room(room, player_id)
            operation(room, player_id)
            self._save_room(connection, room)
            return room

    def close(self) -> None:
        self._pool.close()
