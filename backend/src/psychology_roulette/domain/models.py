from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RoomPhase(StrEnum):
    LOBBY = "lobby"
    ANSWERING = "answering"
    MODIFIER = "modifier"
    REVEAL = "reveal"
    COMPLETE = "complete"


class ModifierType(StrEnum):
    PREDICT_ROOM = "predict_room"
    SECRET_PRINCIPLE = "secret_principle"
    DEVILS_ADVOCATE = "devils_advocate"


class ModifierTiming(StrEnum):
    PRE_REVEAL = "pre_reveal"
    POST_REVEAL = "post_reveal"


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


@dataclass(frozen=True, slots=True)
class ModifierSubmission:
    player_id: str
    value: int | str


@dataclass(frozen=True, slots=True)
class ModifierResult:
    player_id: str
    value: int | str
    score: int | None = None


@dataclass(slots=True)
class Modifier:
    type: ModifierType
    timing: ModifierTiming
    title: str
    instructions: str
    target_player_id: str | None = None
    options: tuple[int | str, ...] = ()
    submissions: dict[str, ModifierSubmission] = field(default_factory=dict)
    results: dict[str, ModifierResult] = field(default_factory=dict)


@dataclass(slots=True)
class Round:
    number: int
    question: Question
    answers: dict[str, Answer] = field(default_factory=dict)
    modifier: Modifier | None = None
