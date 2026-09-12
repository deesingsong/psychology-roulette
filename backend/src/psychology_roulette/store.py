from __future__ import annotations

import secrets
from threading import RLock

from psychology_roulette.content import load_questions
from psychology_roulette.domain import GameError, Room

ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"


class RoomStore:
    """Process-local storage for the first playable vertical slice."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = RLock()

    def create(self) -> Room:
        with self._lock:
            for _ in range(50):
                code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
                if code not in self._rooms:
                    room = Room(code=code, questions=load_questions())
                    self._rooms[code] = room
                    return room
        raise RuntimeError("Could not allocate a unique room code.")

    def get(self, code: str) -> Room:
        normalized = code.strip().upper()
        with self._lock:
            room = self._rooms.get(normalized)
            if room is None:
                raise GameError("Room not found.")
            return room


default_store = RoomStore()
