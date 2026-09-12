from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RoomPhase(StrEnum):
    LOBBY = "lobby"
    ANSWERING = "answering"
    REVEAL = "reveal"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    prompt: str
    category: str
    intensity: int
    values: tuple[str, ...]
    modifiers_allowed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Player:
    id: str
    name: str
    is_host: bool = False


@dataclass(frozen=True, slots=True)
class Answer:
    player_id: str
    position: int
    confidence: int


@dataclass(slots=True)
class Round:
    number: int
    question: Question
    answers: dict[str, Answer] = field(default_factory=dict)
