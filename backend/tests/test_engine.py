from collections import Counter

import pytest

from psychology_roulette.domain import (
    GameError,
    ModifierTiming,
    ModifierType,
    Question,
    Room,
    RoomPhase,
)


@pytest.fixture
def questions() -> list[Question]:
    return [
        Question(
            id=f"q{number}",
            prompt=f"Question {number}",
            category="ethics",
            intensity=1,
            values=("fairness",),
            modifiers_allowed=("predict_room",),
        )
        for number in range(1, 4)
    ]


def test_complete_round_lifecycle(questions: list[Question]) -> None:
    room = Room(code="TEST", questions=questions)
    host = room.add_player("Host", is_host=True)
    guest = room.add_player("Guest")

    room.start(host_id=host.id, round_count=2)
    room.submit_answer(player_id=host.id, position=67, confidence=80)
    room.submit_answer(player_id=guest.id, position=-33, confidence=60)
    room.reveal(host_id=host.id)

    assert room.phase is RoomPhase.REVEAL
    assert room.round_summary() == {
        "average_position": 17.0,
        "average_confidence": 70.0,
        "range": 100,
        "answer_count": 2,
    }

    room.advance(host_id=host.id)
    assert room.phase is RoomPhase.ANSWERING
    assert room.current_round and room.current_round.number == 2


def test_cannot_reveal_until_everyone_answers(questions: list[Question]) -> None:
    room = Room(code="TEST", questions=questions)
    host = room.add_player("Host", is_host=True)
    room.add_player("Guest")
    room.start(host_id=host.id, round_count=1)
    room.submit_answer(player_id=host.id, position=0, confidence=50)

    with pytest.raises(GameError, match="Every player"):
        room.reveal(host_id=host.id)


def test_non_host_cannot_start_game(questions: list[Question]) -> None:
    room = Room(code="TEST", questions=questions)
    room.add_player("Host", is_host=True)
    guest = room.add_player("Guest")

    with pytest.raises(GameError, match="Only the host"):
        room.start(host_id=guest.id)


def test_duplicate_names_are_case_insensitive(questions: list[Question]) -> None:
    room = Room(code="TEST", questions=questions)
    room.add_player("Jonathan", is_host=True)

    with pytest.raises(GameError, match="already in use"):
        room.add_player("  jonathan ")


def _questions_for_modifier(modifier_type: ModifierType, count: int = 2) -> list[Question]:
    return [
        Question(
            id=f"modifier-q{number}",
            prompt=f"Modifier question {number}",
            category="ethics",
            intensity=1,
            values=("fairness", "autonomy", "care"),
            modifiers_allowed=() if number == 1 else (modifier_type.value,),
        )
        for number in range(1, count + 1)
    ]


def _room_at_modifier_round(
    modifier_type: ModifierType,
) -> tuple[Room, str, str]:
    room = Room(
        code="MODS",
        questions=_questions_for_modifier(modifier_type),
        modifier_chance=1,
        modifier_seed=modifier_type.value,
    )
    host = room.add_player("Host", is_host=True)
    guest = room.add_player("Guest")
    room.start(host_id=host.id, round_count=2)
    room.submit_answer(player_id=host.id, position=0, confidence=50)
    room.submit_answer(player_id=guest.id, position=33, confidence=60)
    room.reveal(host_id=host.id)
    room.advance(host_id=host.id)
    return room, host.id, guest.id


def test_predict_room_transitions_validates_and_scores() -> None:
    room, host_id, guest_id = _room_at_modifier_round(ModifierType.PREDICT_ROOM)
    current_round = room.current_round
    assert current_round and current_round.modifier
    assert current_round.modifier.timing is ModifierTiming.PRE_REVEAL
    assert current_round.modifier.options == (-100, -67, -33, 0, 33, 67, 100)

    room.submit_answer(player_id=host_id, position=100, confidence=90)
    room.submit_answer(player_id=guest_id, position=0, confidence=40)
    assert room.phase is RoomPhase.MODIFIER

    with pytest.raises(GameError, match="Every player must complete"):
        room.reveal(host_id=host_id)
    with pytest.raises(GameError, match="seven available"):
        room.submit_modifier(player_id=host_id, value="67")
    with pytest.raises(GameError, match="seven available"):
        room.submit_modifier(player_id=host_id, value=50)
    with pytest.raises(GameError, match="seven available"):
        room.submit_modifier(player_id=host_id, value=True)

    room.submit_modifier(player_id=host_id, value=67)
    room.submit_modifier(player_id=guest_id, value=-100)
    room.reveal(host_id=host_id)

    assert room.phase is RoomPhase.REVEAL
    assert current_round.modifier.results[host_id].score == 92
    assert current_round.modifier.results[guest_id].score == 25


def test_secret_principle_accepts_only_question_values_and_reveals_them() -> None:
    room, host_id, guest_id = _room_at_modifier_round(ModifierType.SECRET_PRINCIPLE)
    current_round = room.current_round
    assert current_round and current_round.modifier

    room.submit_answer(player_id=host_id, position=-33, confidence=70)
    room.submit_answer(player_id=guest_id, position=67, confidence=80)

    with pytest.raises(GameError, match="listed principles"):
        room.submit_modifier(player_id=host_id, value="Fairness")
    with pytest.raises(GameError, match="listed principles"):
        room.submit_modifier(player_id=host_id, value=0)

    room.submit_modifier(player_id=host_id, value="fairness")
    room.submit_modifier(player_id=guest_id, value="care")
    room.reveal(host_id=host_id)

    assert current_round.modifier.results[host_id].value == "fairness"
    assert current_round.modifier.results[host_id].score is None
    assert current_round.modifier.results[guest_id].value == "care"


def test_devils_advocate_is_a_post_reveal_targeted_challenge() -> None:
    room, host_id, guest_id = _room_at_modifier_round(ModifierType.DEVILS_ADVOCATE)
    current_round = room.current_round
    assert current_round and current_round.modifier
    assert current_round.modifier.timing is ModifierTiming.POST_REVEAL
    assert current_round.modifier.target_player_id in room.players

    target_id = current_round.modifier.target_player_id
    room.submit_answer(
        player_id=host_id,
        position=0 if host_id == target_id else -67,
        confidence=55,
    )
    room.submit_answer(
        player_id=guest_id,
        position=0 if guest_id == target_id else 67,
        confidence=75,
    )
    assert room.phase is RoomPhase.ANSWERING

    with pytest.raises(GameError, match="not being accepted"):
        room.submit_modifier(player_id=host_id, value="anything")

    room.reveal(host_id=host_id)
    assert room.phase is RoomPhase.REVEAL
    assert current_round.modifier.results == {}
    assert "middle" in current_round.modifier.instructions.lower()


def test_steelman_selects_a_counterpart_and_keeps_the_draft_private() -> None:
    room, host_id, guest_id = _room_at_modifier_round(ModifierType.STEELMAN)
    current_round = room.current_round
    assert current_round and current_round.modifier
    modifier = current_round.modifier
    assert modifier.timing is ModifierTiming.POST_REVEAL
    assert modifier.target_player_id in {host_id, guest_id}

    room.submit_answer(player_id=host_id, position=-100, confidence=75)
    room.submit_answer(player_id=guest_id, position=100, confidence=65)
    room.reveal(host_id=host_id)

    assert room.phase is RoomPhase.REVEAL
    assert modifier.source_player_id in {host_id, guest_id}
    assert modifier.source_player_id != modifier.target_player_id
    assert modifier.results == {}

    room.advance(host_id=host_id)
    assert room.phase is RoomPhase.FOLLOW_UP
    selected_id = modifier.target_player_id
    other_id = guest_id if selected_id == host_id else host_id
    assert room.modifier_required_player_ids() == {selected_id}

    with pytest.raises(GameError, match="selected player"):
        room.submit_modifier(player_id=other_id, value="A fair account")
    with pytest.raises(GameError, match="short steelman"):
        room.submit_modifier(player_id=selected_id, value="   ")
    with pytest.raises(GameError, match="280"):
        room.submit_modifier(player_id=selected_id, value="x" * 281)
    with pytest.raises(GameError, match="required player"):
        room.advance(host_id=host_id)

    room.submit_modifier(
        player_id=selected_id,
        value="  Their view protects a concern the room should take seriously.  ",
    )
    assert modifier.results == {}
    room.advance(host_id=host_id)

    assert room.phase is RoomPhase.FOLLOW_UP_REVEAL
    assert modifier.results[selected_id].value == (
        "Their view protects a concern the room should take seriously."
    )
    room.advance(host_id=host_id)
    assert room.phase is RoomPhase.COMPLETE


def test_change_my_mind_repolls_privately_and_records_movement() -> None:
    room, host_id, guest_id = _room_at_modifier_round(ModifierType.CHANGE_MY_MIND)
    current_round = room.current_round
    assert current_round and current_round.modifier
    modifier = current_round.modifier
    assert modifier.options == (-100, -67, -33, 0, 33, 67, 100)

    room.submit_answer(player_id=host_id, position=-67, confidence=80)
    room.submit_answer(player_id=guest_id, position=67, confidence=70)
    room.reveal(host_id=host_id)
    room.advance(host_id=host_id)
    assert room.phase is RoomPhase.FOLLOW_UP
    assert room.modifier_required_player_ids() == {host_id, guest_id}

    with pytest.raises(GameError, match="seven available"):
        room.submit_modifier(player_id=host_id, value="0")
    room.submit_modifier(player_id=host_id, value=0)
    assert modifier.results == {}
    with pytest.raises(GameError, match="required player"):
        room.advance(host_id=host_id)
    room.submit_modifier(player_id=guest_id, value=67)
    room.advance(host_id=host_id)

    assert room.phase is RoomPhase.FOLLOW_UP_REVEAL
    assert modifier.results[host_id].movement == 67
    assert modifier.results[guest_id].movement == 0
    room.advance(host_id=host_id)
    assert room.phase is RoomPhase.COMPLETE

    summary = room.session_summary()
    assert summary is not None
    assert summary.rounds_completed == 2
    assert summary.overall_average_position == 8.2
    assert summary.overall_average_confidence == 65.0
    assert summary.widest_round_number == 2
    assert summary.total_position_changes == 1
    host_summary = next(item for item in summary.players if item.player_id == host_id)
    assert host_summary.movement_total == 67
    assert host_summary.title == "Open Door"


def test_default_modifier_seed_is_private_random_material(
    questions: list[Question],
) -> None:
    first = Room(code="SAME", questions=questions)
    second = Room(code="SAME", questions=questions)

    assert first.modifier_seed != second.modifier_seed
    assert first.modifier_seed != first.code
    assert len(first.modifier_seed) == 64


def test_modifier_plan_is_seeded_allowed_and_reproducible() -> None:
    questions = [
        Question(
            id=f"seeded-q{number}",
            prompt=f"Seeded question {number}",
            category="ethics",
            intensity=1,
            values=("fairness", "care"),
            modifiers_allowed=(
                "change_my_mind",
                "predict_room",
                "secret_principle",
                "devils_advocate",
            ),
        )
        for number in range(1, 9)
    ]

    def plan() -> list[tuple[ModifierType | None, str | None]]:
        room = Room(code="SAME", questions=questions, modifier_seed="stable-seed")
        host = room.add_player("Ada", is_host=True)
        room.add_player("Grace")
        room.add_player("Linus")
        room.start(host_id=host.id, round_count=len(questions))
        return [
            (
                game_round.modifier.type if game_round.modifier else None,
                (
                    room.players[game_round.modifier.target_player_id].name
                    if game_round.modifier and game_round.modifier.target_player_id
                    else None
                ),
            )
            for game_round in room.rounds
        ]

    first_plan = plan()
    second_plan = plan()
    assert first_plan == second_plan
    assert first_plan[0] == (None, None)
    assert any(modifier_type is not None for modifier_type, _target in first_plan[1:])
    assert all(
        modifier_type is None or modifier_type.value in questions[index].modifiers_allowed
        for index, (modifier_type, _target) in enumerate(first_plan)
    )


def test_devils_advocate_targets_are_balanced_and_not_back_to_back() -> None:
    room = Room(
        code="FAIR",
        questions=_questions_for_modifier(ModifierType.DEVILS_ADVOCATE, count=7),
        modifier_chance=1,
    )
    host = room.add_player("Host", is_host=True)
    room.add_player("Guest one")
    room.add_player("Guest two")
    room.start(host_id=host.id, round_count=7)

    targets = [
        game_round.modifier.target_player_id
        for game_round in room.rounds[1:]
        if game_round.modifier
    ]
    assert len(targets) == 6
    assert set(Counter(targets).values()) == {2}
    assert all(first != second for first, second in zip(targets, targets[1:], strict=False))


def test_zero_modifier_chance_disables_modifiers() -> None:
    room = Room(
        code="NONE",
        questions=_questions_for_modifier(ModifierType.PREDICT_ROOM, count=3),
        modifier_chance=0,
    )
    host = room.add_player("Host", is_host=True)
    room.add_player("Guest")
    room.start(host_id=host.id, round_count=3)

    assert all(game_round.modifier is None for game_round in room.rounds)


def test_session_forces_one_late_modifier_when_seeded_draws_all_miss() -> None:
    room = Room(
        code="FALL",
        questions=_questions_for_modifier(ModifierType.SECRET_PRINCIPLE, count=4),
        modifier_chance=1e-12,
    )
    host = room.add_player("Host", is_host=True)
    room.add_player("Guest")
    room.start(host_id=host.id, round_count=4)

    assert [game_round.modifier is not None for game_round in room.rounds] == [
        False,
        False,
        False,
        True,
    ]
