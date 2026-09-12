from __future__ import annotations

import hashlib
import random
import secrets
from dataclasses import dataclass, field
from statistics import fmean
from uuid import uuid4

from .models import (
    Answer,
    Modifier,
    ModifierResult,
    ModifierSubmission,
    ModifierTiming,
    ModifierType,
    Player,
    Question,
    RoomPhase,
    Round,
)

POSITION_VALUES = {-100, -67, -33, 0, 33, 67, 100}
POSITION_OPTIONS = tuple(sorted(POSITION_VALUES))
DEFAULT_MODIFIER_CHANCE = 0.65
SUPPORTED_MODIFIERS = (
    ModifierType.PREDICT_ROOM,
    ModifierType.SECRET_PRINCIPLE,
    ModifierType.DEVILS_ADVOCATE,
)


class GameError(ValueError):
    """A rule violation that can safely be shown to a player."""


@dataclass(slots=True)
class Room:
    code: str
    questions: list[Question]
    max_players: int = 8
    modifier_chance: float = DEFAULT_MODIFIER_CHANCE
    modifier_seed: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    phase: RoomPhase = RoomPhase.LOBBY
    players: dict[str, Player] = field(default_factory=dict)
    rounds: list[Round] = field(default_factory=list)
    current_round_index: int = -1

    def __post_init__(self) -> None:
        if not 0 <= self.modifier_chance <= 1:
            raise ValueError("modifier_chance must be between 0 and 1.")

    @property
    def current_round(self) -> Round | None:
        if self.current_round_index < 0 or self.current_round_index >= len(self.rounds):
            return None
        return self.rounds[self.current_round_index]

    def add_player(self, name: str, *, is_host: bool = False) -> Player:
        clean_name = " ".join(name.split())
        if self.phase is not RoomPhase.LOBBY:
            raise GameError("This game has already started.")
        if not clean_name:
            raise GameError("Enter a display name.")
        if len(clean_name) > 24:
            raise GameError("Display names may contain at most 24 characters.")
        if len(self.players) >= self.max_players:
            raise GameError("This room is full.")
        if any(player.name.casefold() == clean_name.casefold() for player in self.players.values()):
            raise GameError("That display name is already in use.")
        if is_host and any(player.is_host for player in self.players.values()):
            raise GameError("This room already has a host.")

        player = Player(id=uuid4().hex, name=clean_name, is_host=is_host)
        self.players[player.id] = player
        return player

    def start(self, *, host_id: str, round_count: int = 6) -> None:
        self._require_host(host_id)
        if self.phase is not RoomPhase.LOBBY:
            raise GameError("This game has already started.")
        if len(self.players) < 2:
            raise GameError("At least two players are required during development.")
        if round_count < 1:
            raise GameError("A game needs at least one round.")
        if len(self.questions) < round_count:
            raise GameError("The selected question pack is too small.")

        self.rounds = [
            Round(number=index + 1, question=question)
            for index, question in enumerate(self.questions[:round_count])
        ]
        self._assign_modifiers()
        self.current_round_index = 0
        self.phase = RoomPhase.ANSWERING

    def submit_answer(self, *, player_id: str, position: int, confidence: int) -> Answer:
        if self.phase is not RoomPhase.ANSWERING:
            raise GameError("Answers are not being accepted right now.")
        if player_id not in self.players:
            raise GameError("Player not found in this room.")
        if position not in POSITION_VALUES:
            raise GameError("Choose one of the seven available positions.")
        if not 0 <= confidence <= 100:
            raise GameError("Confidence must be between 0 and 100.")

        current_round = self._require_round()
        answer = Answer(player_id=player_id, position=position, confidence=confidence)
        current_round.answers[player_id] = answer
        if (
            set(current_round.answers) == set(self.players)
            and current_round.modifier
            and current_round.modifier.timing is ModifierTiming.PRE_REVEAL
        ):
            self.phase = RoomPhase.MODIFIER
        return answer

    def submit_modifier(self, *, player_id: str, value: int | str) -> ModifierSubmission:
        if self.phase is not RoomPhase.MODIFIER:
            raise GameError("Modifier submissions are not being accepted right now.")
        if player_id not in self.players:
            raise GameError("Player not found in this room.")

        current_round = self._require_round()
        modifier = current_round.modifier
        if not modifier or modifier.timing is not ModifierTiming.PRE_REVEAL:
            raise GameError("This round does not accept modifier submissions.")

        if modifier.type is ModifierType.PREDICT_ROOM:
            if type(value) is not int or value not in POSITION_VALUES:
                raise GameError("Choose one of the seven available position predictions.")
        elif modifier.type is ModifierType.SECRET_PRINCIPLE:
            if type(value) is not str or value not in modifier.options:
                raise GameError("Choose one of the question's listed principles.")
        else:
            raise GameError("This modifier does not accept submissions.")

        submission = ModifierSubmission(player_id=player_id, value=value)
        modifier.submissions[player_id] = submission
        return submission

    def reveal(self, *, host_id: str) -> None:
        self._require_host(host_id)
        if self.phase not in {RoomPhase.ANSWERING, RoomPhase.MODIFIER}:
            raise GameError("The room is not waiting for answers.")
        current_round = self._require_round()
        if set(current_round.answers) != set(self.players):
            raise GameError("Every player must answer before the reveal.")
        modifier = current_round.modifier
        if self.phase is RoomPhase.MODIFIER:
            if not modifier or set(modifier.submissions) != set(self.players):
                raise GameError("Every player must complete the modifier before the reveal.")
        elif modifier and modifier.timing is ModifierTiming.PRE_REVEAL:
            raise GameError("Every player must complete the modifier before the reveal.")

        self._calculate_modifier_results(current_round)
        self.phase = RoomPhase.REVEAL

    def advance(self, *, host_id: str) -> None:
        self._require_host(host_id)
        if self.phase is not RoomPhase.REVEAL:
            raise GameError("Reveal the current round before advancing.")
        if self.current_round_index + 1 >= len(self.rounds):
            self.phase = RoomPhase.COMPLETE
            return
        self.current_round_index += 1
        self.phase = RoomPhase.ANSWERING

    def round_summary(self) -> dict[str, float | int] | None:
        current_round = self.current_round
        if self.phase not in {RoomPhase.REVEAL, RoomPhase.COMPLETE} or not current_round:
            return None
        positions = [answer.position for answer in current_round.answers.values()]
        confidences = [answer.confidence for answer in current_round.answers.values()]
        return {
            "average_position": round(fmean(positions), 1),
            "average_confidence": round(fmean(confidences), 1),
            "range": max(positions) - min(positions),
            "answer_count": len(positions),
        }

    def _require_host(self, player_id: str) -> Player:
        player = self.players.get(player_id)
        if not player or not player.is_host:
            raise GameError("Only the host can do that.")
        return player

    def _require_round(self) -> Round:
        current_round = self.current_round
        if current_round is None:
            raise GameError("There is no active round.")
        return current_round

    def _assign_modifiers(self) -> None:
        """Plan the session once, using stable random seeds rather than process hash state."""
        for game_round in self.rounds[1:]:
            game_round.modifier = self._select_modifier(game_round, force=False)

        if self.modifier_chance > 0 and not any(game_round.modifier for game_round in self.rounds):
            # Keep the opening round simple, but guarantee one modifier later when possible.
            for game_round in reversed(self.rounds[1:]):
                modifier = self._select_modifier(game_round, force=True)
                if modifier:
                    game_round.modifier = modifier
                    break

    def _select_modifier(self, game_round: Round, *, force: bool) -> Modifier | None:
        allowed = tuple(
            modifier_type
            for modifier_type in SUPPORTED_MODIFIERS
            if modifier_type.value in game_round.question.modifiers_allowed
        )
        if not allowed:
            return None

        rng = self._round_rng(game_round)
        if not force and rng.random() >= self.modifier_chance:
            return None

        modifier_type = rng.choice(allowed)
        if modifier_type is ModifierType.PREDICT_ROOM:
            return Modifier(
                type=modifier_type,
                timing=ModifierTiming.PRE_REVEAL,
                title="Predict the Room",
                instructions=(
                    "Before the answers are revealed, predict where the room's average "
                    "position will land."
                ),
                options=POSITION_OPTIONS,
            )
        if modifier_type is ModifierType.SECRET_PRINCIPLE:
            return Modifier(
                type=modifier_type,
                timing=ModifierTiming.PRE_REVEAL,
                title="Secret Principle",
                instructions=(
                    "Privately choose the principle that mattered most to your answer. "
                    "Everyone's choices appear at the reveal."
                ),
                options=game_round.question.values,
            )

        target_player_id = self._select_devil_target(rng)
        return Modifier(
            type=ModifierType.DEVILS_ADVOCATE,
            timing=ModifierTiming.POST_REVEAL,
            title="Devil's Advocate",
            instructions=(
                "Make the strongest good-faith case for a meaningfully different position "
                "than your own. If you chose the middle, defend either clear side."
            ),
            target_player_id=target_player_id,
        )

    def _select_devil_target(self, rng: random.Random) -> str:
        target_counts = dict.fromkeys(self.players, 0)
        for game_round in self.rounds:
            modifier = game_round.modifier
            if modifier and modifier.target_player_id in target_counts:
                target_counts[modifier.target_player_id] += 1

        lowest_count = min(target_counts.values())
        eligible = [
            player_id for player_id, count in target_counts.items() if count == lowest_count
        ]
        previous_target_id = next(
            (
                modifier.target_player_id
                for game_round in reversed(self.rounds)
                if (modifier := game_round.modifier) and modifier.target_player_id is not None
            ),
            None,
        )
        if len(eligible) > 1 and previous_target_id in eligible:
            eligible.remove(previous_target_id)
        return rng.choice(eligible)

    def _round_rng(self, game_round: Round) -> random.Random:
        seed_material = (
            "psychology-roulette:v1:"
            f"{self.modifier_seed}:{game_round.number}:{game_round.question.id}"
        )
        digest = hashlib.sha256(seed_material.encode()).digest()
        return random.Random(int.from_bytes(digest))

    @staticmethod
    def _calculate_modifier_results(game_round: Round) -> None:
        modifier = game_round.modifier
        if not modifier:
            return

        if modifier.type is ModifierType.PREDICT_ROOM:
            actual_average = fmean(answer.position for answer in game_round.answers.values())
            modifier.results = {
                player_id: ModifierResult(
                    player_id=player_id,
                    value=submission.value,
                    score=max(0, round(100 - abs(submission.value - actual_average) / 2)),
                )
                for player_id, submission in modifier.submissions.items()
                if type(submission.value) is int
            }
        elif modifier.type is ModifierType.SECRET_PRINCIPLE:
            modifier.results = {
                player_id: ModifierResult(
                    player_id=player_id,
                    value=submission.value,
                )
                for player_id, submission in modifier.submissions.items()
            }
