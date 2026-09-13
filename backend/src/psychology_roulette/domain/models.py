from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RoomPhase(StrEnum):
    LOBBY = "lobby"
    ANSWERING = "answering"
    MODIFIER = "modifier"
    REVEAL = "reveal"
    FOLLOW_UP = "follow_up"
    FOLLOW_UP_REVEAL = "follow_up_reveal"
    COMPLETE = "complete"


class ModifierType(StrEnum):
    PREDICT_ROOM = "predict_room"
    SECRET_PRINCIPLE = "secret_principle"
    DEVILS_ADVOCATE = "devils_advocate"
    STEELMAN = "steelman"
    CHANGE_MY_MIND = "change_my_mind"


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
    movement: int | None = None


@dataclass(slots=True)
class Modifier:
    type: ModifierType
    timing: ModifierTiming
    title: str
    instructions: str
    target_player_id: str | None = None
    source_player_id: str | None = None
    options: tuple[int | str, ...] = ()
    submissions: dict[str, ModifierSubmission] = field(default_factory=dict)
    results: dict[str, ModifierResult] = field(default_factory=dict)


@dataclass(slots=True)
class Round:
    number: int
    question: Question
    answers: dict[str, Answer] = field(default_factory=dict)
    modifier: Modifier | None = None


@dataclass(frozen=True, slots=True)
class RoundAnalytics:
    number: int
    prompt: str
    average_position: float
    average_confidence: float
    position_range: int


@dataclass(frozen=True, slots=True)
class PlayerAnalytics:
    player_id: str
    title: str
    title_description: str
    rounds_answered: int
    average_position: float
    average_confidence: float
    average_room_distance: float
    position_span: int
    prediction_score: float | None
    movement_total: int


@dataclass(frozen=True, slots=True)
class SessionAnalytics:
    rounds_completed: int
    overall_average_position: float
    overall_average_confidence: float
    widest_round_number: int
    total_position_changes: int
    rounds: tuple[RoundAnalytics, ...]
    players: tuple[PlayerAnalytics, ...]
