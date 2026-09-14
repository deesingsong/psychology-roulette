from __future__ import annotations

import hashlib
import random
import secrets
from dataclasses import dataclass, field
from statistics import fmean
from uuid import uuid4

from .models import (
    Answer,
    ContentStatus,
    Modifier,
    ModifierResult,
    ModifierSubmission,
    ModifierTiming,
    ModifierType,
    Player,
    PlayerAnalytics,
    Question,
    RoomPhase,
    Round,
    RoundAnalytics,
    SessionAnalytics,
    SessionRecap,
)

POSITION_VALUES = {-100, -67, -33, 0, 33, 67, 100}
POSITION_OPTIONS = tuple(sorted(POSITION_VALUES))
DEFAULT_MODIFIER_CHANCE = 0.65
SUPPORTED_MODIFIERS = (
    ModifierType.PREDICT_ROOM,
    ModifierType.SECRET_PRINCIPLE,
    ModifierType.DEVILS_ADVOCATE,
    ModifierType.STEELMAN,
    ModifierType.CHANGE_MY_MIND,
)
FOLLOW_UP_MODIFIERS = {ModifierType.STEELMAN, ModifierType.CHANGE_MY_MIND}


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
    session_recap: SessionRecap | None = None
    content_status: ContentStatus = ContentStatus.PENDING
    content_generation_started_at: float | None = None

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
        self.session_recap = None
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
        if self.phase not in {RoomPhase.MODIFIER, RoomPhase.FOLLOW_UP}:
            raise GameError("Modifier submissions are not being accepted right now.")
        if player_id not in self.players:
            raise GameError("Player not found in this room.")

        current_round = self._require_round()
        modifier = current_round.modifier
        if not modifier:
            raise GameError("This round does not accept modifier submissions.")
        if self.phase is RoomPhase.MODIFIER and modifier.timing is not ModifierTiming.PRE_REVEAL:
            raise GameError("This round does not accept modifier submissions.")
        if self.phase is RoomPhase.FOLLOW_UP and modifier.type not in FOLLOW_UP_MODIFIERS:
            raise GameError("This round does not accept follow-up submissions.")

        if modifier.type is ModifierType.PREDICT_ROOM:
            if type(value) is not int or value not in POSITION_VALUES:
                raise GameError("Choose one of the seven available position predictions.")
        elif modifier.type is ModifierType.SECRET_PRINCIPLE:
            if type(value) is not str or value not in modifier.options:
                raise GameError("Choose one of the question's listed principles.")
        elif modifier.type is ModifierType.STEELMAN:
            if player_id != modifier.target_player_id:
                raise GameError("Only the selected player submits this steelman.")
            if type(value) is not str:
                raise GameError("Write a short steelman before submitting.")
            value = " ".join(value.split())
            if not value:
                raise GameError("Write a short steelman before submitting.")
            if len(value) > 280:
                raise GameError("Keep the steelman to 280 characters or fewer.")
        elif modifier.type is ModifierType.CHANGE_MY_MIND:
            if type(value) is not int or value not in POSITION_VALUES:
                raise GameError("Choose one of the seven available positions.")
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
        self._resolve_steelman_source(current_round)
        self.phase = RoomPhase.REVEAL

    def advance(self, *, host_id: str) -> None:
        self._require_host(host_id)
        current_round = self._require_round()
        modifier = current_round.modifier
        if self.phase is RoomPhase.REVEAL:
            if modifier and modifier.type in FOLLOW_UP_MODIFIERS:
                self.phase = RoomPhase.FOLLOW_UP
                return
            self._finish_round()
            return
        if self.phase is RoomPhase.FOLLOW_UP:
            required = self.modifier_required_player_ids()
            if not modifier or set(modifier.submissions) != required:
                raise GameError("Every required player must complete the follow-up first.")
            self._calculate_follow_up_results(current_round)
            self.phase = RoomPhase.FOLLOW_UP_REVEAL
            return
        if self.phase is RoomPhase.FOLLOW_UP_REVEAL:
            self._finish_round()
            return
        raise GameError("Reveal the current round before advancing.")

    def _finish_round(self) -> None:
        if self.current_round_index + 1 >= len(self.rounds):
            self.phase = RoomPhase.COMPLETE
            return
        self.current_round_index += 1
        self.phase = RoomPhase.ANSWERING

    def round_summary(self) -> dict[str, float | int] | None:
        current_round = self.current_round
        if (
            self.phase
            not in {
                RoomPhase.REVEAL,
                RoomPhase.FOLLOW_UP,
                RoomPhase.FOLLOW_UP_REVEAL,
                RoomPhase.COMPLETE,
            }
            or not current_round
        ):
            return None
        positions = [answer.position for answer in current_round.answers.values()]
        confidences = [answer.confidence for answer in current_round.answers.values()]
        return {
            "average_position": round(fmean(positions), 1),
            "average_confidence": round(fmean(confidences), 1),
            "range": max(positions) - min(positions),
            "answer_count": len(positions),
        }

    def modifier_required_player_ids(self) -> set[str]:
        current_round = self.current_round
        modifier = current_round.modifier if current_round else None
        if not modifier:
            return set()
        if modifier.timing is ModifierTiming.PRE_REVEAL:
            return set(self.players)
        if modifier.type is ModifierType.CHANGE_MY_MIND:
            return set(self.players)
        if modifier.type is ModifierType.STEELMAN and modifier.target_player_id:
            return {modifier.target_player_id}
        return set()

    def session_summary(self) -> SessionAnalytics | None:
        if self.phase is not RoomPhase.COMPLETE:
            return None
        completed_rounds = [
            game_round for game_round in self.rounds if set(game_round.answers) == set(self.players)
        ]
        if not completed_rounds:
            return None

        round_analytics = tuple(
            self._round_analytics(game_round) for game_round in completed_rounds
        )
        all_answers = [
            answer for game_round in completed_rounds for answer in game_round.answers.values()
        ]
        widest_round = max(round_analytics, key=lambda item: item.position_range)
        total_position_changes = sum(
            1
            for game_round in completed_rounds
            if game_round.modifier and game_round.modifier.type is ModifierType.CHANGE_MY_MIND
            for result in game_round.modifier.results.values()
            if result.movement
        )

        player_analytics: list[PlayerAnalytics] = []
        for player_id in self.players:
            answers = [game_round.answers[player_id] for game_round in completed_rounds]
            positions = [answer.position for answer in answers]
            confidences = [answer.confidence for answer in answers]
            room_distances = [
                abs(
                    game_round.answers[player_id].position
                    - fmean(answer.position for answer in game_round.answers.values())
                )
                for game_round in completed_rounds
            ]
            prediction_scores = [
                result.score
                for game_round in completed_rounds
                if game_round.modifier
                and game_round.modifier.type is ModifierType.PREDICT_ROOM
                and (result := game_round.modifier.results.get(player_id))
                and result.score is not None
            ]
            movement_total = sum(
                result.movement or 0
                for game_round in completed_rounds
                if game_round.modifier
                and game_round.modifier.type is ModifierType.CHANGE_MY_MIND
                and (result := game_round.modifier.results.get(player_id))
            )
            average_confidence = round(fmean(confidences), 1)
            average_room_distance = round(fmean(room_distances), 1)
            position_span = max(positions) - min(positions)
            prediction_score = round(fmean(prediction_scores), 1) if prediction_scores else None
            title, title_description = self._player_title(
                prediction_score=prediction_score,
                movement_total=movement_total,
                position_span=position_span,
                average_confidence=average_confidence,
                average_room_distance=average_room_distance,
            )
            player_analytics.append(
                PlayerAnalytics(
                    player_id=player_id,
                    title=title,
                    title_description=title_description,
                    rounds_answered=len(answers),
                    average_position=round(fmean(positions), 1),
                    average_confidence=average_confidence,
                    average_room_distance=average_room_distance,
                    position_span=position_span,
                    prediction_score=prediction_score,
                    movement_total=movement_total,
                )
            )

        return SessionAnalytics(
            rounds_completed=len(completed_rounds),
            overall_average_position=round(fmean(answer.position for answer in all_answers), 1),
            overall_average_confidence=round(fmean(answer.confidence for answer in all_answers), 1),
            widest_round_number=widest_round.number,
            total_position_changes=total_position_changes,
            rounds=round_analytics,
            players=tuple(player_analytics),
        )

    @staticmethod
    def _round_analytics(game_round: Round) -> RoundAnalytics:
        positions = [answer.position for answer in game_round.answers.values()]
        confidences = [answer.confidence for answer in game_round.answers.values()]
        return RoundAnalytics(
            number=game_round.number,
            prompt=game_round.question.prompt,
            average_position=round(fmean(positions), 1),
            average_confidence=round(fmean(confidences), 1),
            position_range=max(positions) - min(positions),
        )

    @staticmethod
    def _player_title(
        *,
        prediction_score: float | None,
        movement_total: int,
        position_span: int,
        average_confidence: float,
        average_room_distance: float,
    ) -> tuple[str, str]:
        if prediction_score is not None and prediction_score >= 80:
            return "Room Reader", "Your private predictions tracked the table closely."
        if movement_total:
            return "Open Door", "At least one conversation moved your follow-up answer."
        if position_span >= 134:
            return "Wide Lens", "You used far-apart positions across the session."
        if average_confidence >= 75:
            return "Firm Footing", "You usually answered with high confidence."
        if average_room_distance <= 25:
            return "Common Ground", "Your answers often landed near the room average."
        return "Thoughtful Constant", "You kept a measured rhythm across the table."

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
        if modifier_type is ModifierType.CHANGE_MY_MIND:
            return Modifier(
                type=modifier_type,
                timing=ModifierTiming.POST_REVEAL,
                title="Change My Mind",
                instructions=(
                    "After the discussion, privately choose your position again. "
                    "The room will reveal what moved, without treating movement as winning."
                ),
                options=POSITION_OPTIONS,
            )

        target_player_id = self._select_challenge_target(rng)
        if modifier_type is ModifierType.STEELMAN:
            return Modifier(
                type=modifier_type,
                timing=ModifierTiming.POST_REVEAL,
                title="Steelman",
                instructions=(
                    "Restate the assigned person's position as strongly and fairly as you can, "
                    "in language they could recognize."
                ),
                target_player_id=target_player_id,
            )
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

    def _select_challenge_target(self, rng: random.Random) -> str:
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
    def _resolve_steelman_source(game_round: Round) -> None:
        modifier = game_round.modifier
        if (
            not modifier
            or modifier.type is not ModifierType.STEELMAN
            or not modifier.target_player_id
        ):
            return
        target_answer = game_round.answers[modifier.target_player_id]
        candidates = [
            player_id for player_id in game_round.answers if player_id != modifier.target_player_id
        ]
        modifier.source_player_id = max(
            candidates,
            key=lambda player_id: abs(
                game_round.answers[player_id].position - target_answer.position
            ),
        )

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

    @staticmethod
    def _calculate_follow_up_results(game_round: Round) -> None:
        modifier = game_round.modifier
        if not modifier:
            return
        if modifier.type is ModifierType.STEELMAN:
            modifier.results = {
                player_id: ModifierResult(player_id=player_id, value=submission.value)
                for player_id, submission in modifier.submissions.items()
            }
        elif modifier.type is ModifierType.CHANGE_MY_MIND:
            modifier.results = {
                player_id: ModifierResult(
                    player_id=player_id,
                    value=submission.value,
                    movement=abs(submission.value - game_round.answers[player_id].position),
                )
                for player_id, submission in modifier.submissions.items()
                if type(submission.value) is int
            }
