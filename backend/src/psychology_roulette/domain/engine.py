from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean
from uuid import uuid4

from .models import Answer, Player, Question, RoomPhase, Round

POSITION_VALUES = {-100, -67, -33, 0, 33, 67, 100}


class GameError(ValueError):
    """A rule violation that can safely be shown to a player."""


@dataclass(slots=True)
class Room:
    code: str
    questions: list[Question]
    max_players: int = 8
    phase: RoomPhase = RoomPhase.LOBBY
    players: dict[str, Player] = field(default_factory=dict)
    rounds: list[Round] = field(default_factory=list)
    current_round_index: int = -1

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
        return answer

    def reveal(self, *, host_id: str) -> None:
        self._require_host(host_id)
        if self.phase is not RoomPhase.ANSWERING:
            raise GameError("The room is not waiting for answers.")
        current_round = self._require_round()
        if set(current_round.answers) != set(self.players):
            raise GameError("Every player must answer before the reveal.")
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
