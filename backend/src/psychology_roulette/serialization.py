from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from psychology_roulette.domain import (
    Answer,
    Modifier,
    ModifierResult,
    ModifierSubmission,
    ModifierTiming,
    ModifierType,
    Player,
    Question,
    Room,
    RoomPhase,
    Round,
)

SNAPSHOT_FORMAT_VERSION = 1


class SnapshotError(ValueError):
    """Stored aggregate data cannot be safely reconstructed."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError(f"{field} must be an object.")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise SnapshotError(f"{field} must be an array.")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise SnapshotError(f"{field} must be a string.")
    return value


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise SnapshotError(f"{field} must be an integer.")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise SnapshotError(f"{field} must be a boolean.")
    return value


def _question_snapshot(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "prompt": question.prompt,
        "category": question.category,
        "intensity": question.intensity,
        "values": list(question.values),
        "modifiers_allowed": list(question.modifiers_allowed),
    }


def _question_from_snapshot(value: Any, field: str) -> Question:
    payload = _mapping(value, field)
    return Question(
        id=_string(payload.get("id"), f"{field}.id"),
        prompt=_string(payload.get("prompt"), f"{field}.prompt"),
        category=_string(payload.get("category"), f"{field}.category"),
        intensity=_integer(payload.get("intensity"), f"{field}.intensity"),
        values=tuple(
            _string(item, f"{field}.values[]")
            for item in _list(payload.get("values"), f"{field}.values")
        ),
        modifiers_allowed=tuple(
            _string(item, f"{field}.modifiers_allowed[]")
            for item in _list(
                payload.get("modifiers_allowed"),
                f"{field}.modifiers_allowed",
            )
        ),
    )


def _submission_value(value: Any, field: str) -> int | str:
    if type(value) is int or isinstance(value, str):
        return value
    raise SnapshotError(f"{field} must be an integer or string.")


def _modifier_snapshot(modifier: Modifier | None) -> dict[str, Any] | None:
    if modifier is None:
        return None
    return {
        "type": modifier.type.value,
        "timing": modifier.timing.value,
        "title": modifier.title,
        "instructions": modifier.instructions,
        "target_player_id": modifier.target_player_id,
        "options": list(modifier.options),
        "submissions": [
            {"player_id": submission.player_id, "value": submission.value}
            for submission in modifier.submissions.values()
        ],
        "results": [
            {
                "player_id": result.player_id,
                "value": result.value,
                "score": result.score,
            }
            for result in modifier.results.values()
        ],
    }


def _modifier_from_snapshot(value: Any, field: str) -> Modifier | None:
    if value is None:
        return None
    payload = _mapping(value, field)
    try:
        modifier_type = ModifierType(_string(payload.get("type"), f"{field}.type"))
        timing = ModifierTiming(_string(payload.get("timing"), f"{field}.timing"))
    except ValueError as exc:
        raise SnapshotError(f"{field} has an unknown enum value.") from exc

    target_value = payload.get("target_player_id")
    target_player_id = (
        None if target_value is None else _string(target_value, f"{field}.target_player_id")
    )
    options = tuple(
        _submission_value(item, f"{field}.options[]")
        for item in _list(payload.get("options"), f"{field}.options")
    )

    submissions: dict[str, ModifierSubmission] = {}
    for index, item in enumerate(_list(payload.get("submissions"), f"{field}.submissions")):
        submission_payload = _mapping(item, f"{field}.submissions[{index}]")
        player_id = _string(
            submission_payload.get("player_id"),
            f"{field}.submissions[{index}].player_id",
        )
        if player_id in submissions:
            raise SnapshotError(f"{field} contains duplicate modifier submissions.")
        submissions[player_id] = ModifierSubmission(
            player_id=player_id,
            value=_submission_value(
                submission_payload.get("value"),
                f"{field}.submissions[{index}].value",
            ),
        )

    results: dict[str, ModifierResult] = {}
    for index, item in enumerate(_list(payload.get("results"), f"{field}.results")):
        result_payload = _mapping(item, f"{field}.results[{index}]")
        player_id = _string(
            result_payload.get("player_id"),
            f"{field}.results[{index}].player_id",
        )
        if player_id in results:
            raise SnapshotError(f"{field} contains duplicate modifier results.")
        raw_score = result_payload.get("score")
        score = None if raw_score is None else _integer(raw_score, f"{field}.score")
        results[player_id] = ModifierResult(
            player_id=player_id,
            value=_submission_value(
                result_payload.get("value"),
                f"{field}.results[{index}].value",
            ),
            score=score,
        )

    return Modifier(
        type=modifier_type,
        timing=timing,
        title=_string(payload.get("title"), f"{field}.title"),
        instructions=_string(payload.get("instructions"), f"{field}.instructions"),
        target_player_id=target_player_id,
        options=options,
        submissions=submissions,
        results=results,
    )


def room_to_snapshot(room: Room) -> dict[str, Any]:
    """Convert the aggregate to an explicit, versioned JSON-compatible snapshot."""
    return {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "code": room.code,
        "max_players": room.max_players,
        "modifier_chance": room.modifier_chance,
        "modifier_seed": room.modifier_seed,
        "phase": room.phase.value,
        "current_round_index": room.current_round_index,
        "questions": [_question_snapshot(question) for question in room.questions],
        "players": [
            {"id": player.id, "name": player.name, "is_host": player.is_host}
            for player in room.players.values()
        ],
        "rounds": [
            {
                "number": game_round.number,
                "question": _question_snapshot(game_round.question),
                "answers": [
                    {
                        "player_id": answer.player_id,
                        "position": answer.position,
                        "confidence": answer.confidence,
                    }
                    for answer in game_round.answers.values()
                ],
                "modifier": _modifier_snapshot(game_round.modifier),
            }
            for game_round in room.rounds
        ],
    }


def room_to_json(room: Room) -> str:
    return json.dumps(
        room_to_snapshot(room),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def room_from_snapshot(value: Any) -> Room:
    """Reconstruct a Room without replaying rules or reselecting modifiers."""
    payload = _mapping(value, "room")
    version = _integer(payload.get("format_version"), "room.format_version")
    if version != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotError(f"Unsupported room snapshot format version: {version}.")

    questions = [
        _question_from_snapshot(item, f"room.questions[{index}]")
        for index, item in enumerate(_list(payload.get("questions"), "room.questions"))
    ]

    players: dict[str, Player] = {}
    for index, item in enumerate(_list(payload.get("players"), "room.players")):
        player_payload = _mapping(item, f"room.players[{index}]")
        player_id = _string(player_payload.get("id"), f"room.players[{index}].id")
        if player_id in players:
            raise SnapshotError("Room snapshot contains duplicate players.")
        players[player_id] = Player(
            id=player_id,
            name=_string(player_payload.get("name"), f"room.players[{index}].name"),
            is_host=_boolean(
                player_payload.get("is_host"),
                f"room.players[{index}].is_host",
            ),
        )

    rounds: list[Round] = []
    for index, item in enumerate(_list(payload.get("rounds"), "room.rounds")):
        round_payload = _mapping(item, f"room.rounds[{index}]")
        answers: dict[str, Answer] = {}
        for answer_index, answer_item in enumerate(
            _list(round_payload.get("answers"), f"room.rounds[{index}].answers")
        ):
            answer_payload = _mapping(
                answer_item,
                f"room.rounds[{index}].answers[{answer_index}]",
            )
            player_id = _string(
                answer_payload.get("player_id"),
                f"room.rounds[{index}].answers[{answer_index}].player_id",
            )
            if player_id in answers:
                raise SnapshotError("Room snapshot contains duplicate round answers.")
            answers[player_id] = Answer(
                player_id=player_id,
                position=_integer(
                    answer_payload.get("position"),
                    f"room.rounds[{index}].answers[{answer_index}].position",
                ),
                confidence=_integer(
                    answer_payload.get("confidence"),
                    f"room.rounds[{index}].answers[{answer_index}].confidence",
                ),
            )
        rounds.append(
            Round(
                number=_integer(round_payload.get("number"), f"room.rounds[{index}].number"),
                question=_question_from_snapshot(
                    round_payload.get("question"),
                    f"room.rounds[{index}].question",
                ),
                answers=answers,
                modifier=_modifier_from_snapshot(
                    round_payload.get("modifier"),
                    f"room.rounds[{index}].modifier",
                ),
            )
        )

    try:
        phase = RoomPhase(_string(payload.get("phase"), "room.phase"))
    except ValueError as exc:
        raise SnapshotError("Room snapshot has an unknown phase.") from exc

    raw_modifier_chance = payload.get("modifier_chance")
    if type(raw_modifier_chance) not in {int, float}:
        raise SnapshotError("room.modifier_chance must be a number.")
    current_round_index = _integer(
        payload.get("current_round_index"),
        "room.current_round_index",
    )
    if current_round_index < -1 or current_round_index >= len(rounds):
        raise SnapshotError("Room snapshot has an invalid current round index.")

    player_ids = set(players)
    for game_round in rounds:
        if not set(game_round.answers).issubset(player_ids):
            raise SnapshotError("A stored answer references an unknown player.")
        modifier = game_round.modifier
        if modifier is None:
            continue
        if modifier.target_player_id is not None and modifier.target_player_id not in players:
            raise SnapshotError("A stored modifier target references an unknown player.")
        if not set(modifier.submissions).issubset(player_ids):
            raise SnapshotError("A stored modifier submission references an unknown player.")
        if not set(modifier.results).issubset(player_ids):
            raise SnapshotError("A stored modifier result references an unknown player.")

    return Room(
        code=_string(payload.get("code"), "room.code"),
        questions=questions,
        max_players=_integer(payload.get("max_players"), "room.max_players"),
        modifier_chance=float(raw_modifier_chance),
        modifier_seed=_string(payload.get("modifier_seed"), "room.modifier_seed"),
        phase=phase,
        players=players,
        rounds=rounds,
        current_round_index=current_round_index,
    )


def room_from_json(value: str) -> Room:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SnapshotError("Stored room snapshot is not valid JSON.") from exc
    return room_from_snapshot(payload)
