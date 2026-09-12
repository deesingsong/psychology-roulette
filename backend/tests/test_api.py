from fastapi.testclient import TestClient

from psychology_roulette.api import create_app
from psychology_roulette.domain import Question
from psychology_roulette.store import RoomStore


def test_room_api_hides_answers_until_reveal() -> None:
    client = TestClient(create_app(RoomStore()))
    created = client.post("/api/rooms", json={"host_name": "Host"}).json()
    code = created["room"]["code"]
    host_id = created["player_id"]
    assert "modifier_seed" not in created["room"]
    guest = client.post(f"/api/rooms/{code}/players", json={"name": "Guest"}).json()

    response = client.post(f"/api/rooms/{code}/start", json={"player_id": host_id})
    assert response.status_code == 200

    host_answer = client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": host_id, "position": 67, "confidence": 80},
    )
    assert host_answer.json()["revealed_answers"] is None

    client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": guest["player_id"], "position": -33, "confidence": 40},
    )
    revealed = client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})

    assert revealed.status_code == 200
    assert len(revealed.json()["revealed_answers"]) == 2
    assert revealed.json()["summary"]["average_position"] == 17.0


def test_unknown_room_returns_conflict() -> None:
    client = TestClient(create_app(RoomStore()))
    response = client.get("/api/rooms/NOPE")

    assert response.status_code == 409
    assert response.json() == {"detail": "Room not found."}


def _client_at_second_round(modifier_type: str) -> tuple[TestClient, str, str, str]:
    store = RoomStore()
    client = TestClient(create_app(store))
    created = client.post("/api/rooms", json={"host_name": "Host"}).json()
    code = created["room"]["code"]
    host_id = created["player_id"]
    guest = client.post(f"/api/rooms/{code}/players", json={"name": "Guest"}).json()
    guest_id = guest["player_id"]

    room = store.get(code)
    room.modifier_chance = 1
    room.modifier_seed = f"api-{modifier_type}"
    room.questions = [
        Question(
            id=f"api-q{number}",
            prompt=f"API question {number}",
            category="ethics",
            intensity=1,
            values=("fairness", "autonomy", "care"),
            modifiers_allowed=(modifier_type,) if number == 2 else (),
        )
        for number in range(1, 7)
    ]

    assert client.post(f"/api/rooms/{code}/start", json={"player_id": host_id}).status_code == 200
    client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": host_id, "position": 0, "confidence": 50},
    )
    client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": guest_id, "position": 33, "confidence": 60},
    )
    client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})
    advanced = client.post(f"/api/rooms/{code}/advance", json={"player_id": host_id})
    assert advanced.status_code == 200
    assert advanced.json()["round_number"] == 2
    assert advanced.json()["modifier"] is None
    return client, code, host_id, guest_id


def test_predict_room_api_keeps_predictions_private_until_reveal() -> None:
    client, code, host_id, guest_id = _client_at_second_round("predict_room")
    client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": host_id, "position": 100, "confidence": 90},
    )
    modifier_phase = client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": guest_id, "position": 0, "confidence": 40},
    )

    payload = modifier_phase.json()
    assert payload["phase"] == "modifier"
    assert payload["modifier"] == {
        "type": "predict_room",
        "timing": "pre_reveal",
        "title": "Predict the Room",
        "instructions": (
            "Before the answers are revealed, predict where the room's average position will land."
        ),
        "target_player_name": None,
        "options": [-100, -67, -33, 0, 33, 67, 100],
        "submissions_count": 0,
        "required_submissions": 2,
        "results": None,
    }
    assert all(not player["has_modifier_submitted"] for player in payload["players"])

    invalid_number = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": host_id, "value": 12.5},
    )
    assert invalid_number.status_code == 422

    host_prediction = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": host_id, "value": 67},
    ).json()
    assert host_prediction["modifier"]["submissions_count"] == 1
    assert host_prediction["modifier"]["results"] is None
    assert next(player for player in host_prediction["players"] if player["name"] == "Host")[
        "has_modifier_submitted"
    ]

    early_reveal = client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})
    assert early_reveal.status_code == 409
    assert "Every player must complete" in early_reveal.json()["detail"]

    client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": guest_id, "value": -100},
    )
    revealed = client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})
    assert revealed.status_code == 200
    assert revealed.json()["modifier"]["results"] == [
        {"player_name": "Host", "value": 67, "score": 92},
        {"player_name": "Guest", "value": -100, "score": 25},
    ]


def test_secret_principle_api_reveals_selections_only_at_reveal() -> None:
    client, code, host_id, guest_id = _client_at_second_round("secret_principle")
    for player_id, position in ((host_id, -33), (guest_id, 67)):
        response = client.post(
            f"/api/rooms/{code}/answers",
            json={"player_id": player_id, "position": position, "confidence": 70},
        )
    assert response.json()["phase"] == "modifier"
    assert response.json()["modifier"]["options"] == ["fairness", "autonomy", "care"]

    invalid = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": host_id, "value": "Fairness"},
    )
    assert invalid.status_code == 409

    first_submission = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": host_id, "value": "fairness"},
    )
    assert first_submission.json()["modifier"]["results"] is None
    client.post(
        f"/api/rooms/{code}/modifier-submissions",
        json={"player_id": guest_id, "value": "care"},
    )

    revealed = client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})
    assert revealed.json()["modifier"]["results"] == [
        {"player_name": "Host", "value": "fairness", "score": None},
        {"player_name": "Guest", "value": "care", "score": None},
    ]


def test_devils_advocate_api_is_hidden_until_the_reveal() -> None:
    client, code, host_id, guest_id = _client_at_second_round("devils_advocate")
    client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": host_id, "position": -67, "confidence": 55},
    )
    answered = client.post(
        f"/api/rooms/{code}/answers",
        json={"player_id": guest_id, "position": 67, "confidence": 75},
    )
    assert answered.json()["phase"] == "answering"
    assert answered.json()["modifier"] is None

    revealed = client.post(f"/api/rooms/{code}/reveal", json={"player_id": host_id})
    modifier = revealed.json()["modifier"]
    assert modifier["type"] == "devils_advocate"
    assert modifier["timing"] == "post_reveal"
    assert modifier["target_player_name"] in {"Host", "Guest"}
    assert modifier["required_submissions"] == 0
    assert modifier["submissions_count"] == 0
    assert modifier["options"] == []
    assert modifier["results"] == []


def test_default_six_round_api_session_reaches_completion() -> None:
    client = TestClient(create_app(RoomStore()))
    created = client.post("/api/rooms", json={"host_name": "Host"}).json()
    code = created["room"]["code"]
    host_id = created["player_id"]
    guest_id = client.post(
        f"/api/rooms/{code}/players",
        json={"name": "Guest"},
    ).json()["player_id"]

    room = client.post(
        f"/api/rooms/{code}/start",
        json={"player_id": host_id},
    ).json()
    modifier_rounds = 0

    for round_number in range(1, 7):
        assert room["phase"] == "answering"
        assert room["round_number"] == round_number

        client.post(
            f"/api/rooms/{code}/answers",
            json={"player_id": host_id, "position": 67, "confidence": 80},
        )
        room = client.post(
            f"/api/rooms/{code}/answers",
            json={"player_id": guest_id, "position": -33, "confidence": 60},
        ).json()

        if room["phase"] == "modifier":
            modifier_rounds += 1
            modifier = room["modifier"]
            submission_value = modifier["options"][0]
            client.post(
                f"/api/rooms/{code}/modifier-submissions",
                json={"player_id": host_id, "value": submission_value},
            )
            room = client.post(
                f"/api/rooms/{code}/modifier-submissions",
                json={"player_id": guest_id, "value": submission_value},
            ).json()

        revealed = client.post(
            f"/api/rooms/{code}/reveal",
            json={"player_id": host_id},
        )
        assert revealed.status_code == 200
        room = revealed.json()
        assert room["phase"] == "reveal"
        if room["modifier"] is not None:
            modifier_rounds += int(room["modifier"]["timing"] == "post_reveal")

        room = client.post(
            f"/api/rooms/{code}/advance",
            json={"player_id": host_id},
        ).json()

    assert room["phase"] == "complete"
    assert modifier_rounds >= 1
