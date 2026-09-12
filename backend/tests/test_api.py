from fastapi.testclient import TestClient

from psychology_roulette.api import create_app
from psychology_roulette.store import RoomStore


def test_room_api_hides_answers_until_reveal() -> None:
    client = TestClient(create_app(RoomStore()))
    created = client.post("/api/rooms", json={"host_name": "Host"}).json()
    code = created["room"]["code"]
    host_id = created["player_id"]
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
