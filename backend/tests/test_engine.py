import pytest

from psychology_roulette.domain import GameError, Question, Room, RoomPhase


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
