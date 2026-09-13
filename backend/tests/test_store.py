import hashlib
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from psychology_roulette.api import create_app
from psychology_roulette.domain import Question
from psychology_roulette.store import RoomStore, UnknownAccessToken


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _configure_predict_room(room, _player_id: str) -> None:
    room.modifier_chance = 1
    room.modifier_seed = "restart-predict-room"
    room.questions = [
        Question(
            id=f"restart-q{number}",
            prompt=f"Restart question {number}",
            category="ethics",
            intensity=1,
            values=("fairness", "autonomy", "care"),
            modifiers_allowed=("predict_room",) if number == 2 else (),
        )
        for number in range(1, 7)
    ]


def test_room_and_private_modifier_state_survive_sqlite_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite3"
    first_store = RoomStore(database_path)
    try:
        first_client = TestClient(create_app(first_store))
        host_token = secrets.token_urlsafe(32)
        host = first_client.post(
            "/api/rooms",
            headers=_auth(host_token),
            json={"host_name": "Host"},
        ).json()
        code = host["room"]["code"]
        guest_token = secrets.token_urlsafe(32)
        guest = first_client.post(
            f"/api/rooms/{code}/players",
            headers=_auth(guest_token),
            json={"name": "Guest"},
        ).json()
        assert host["access_token"] == host_token
        assert guest["access_token"] == guest_token
        first_store.mutate(code, host_token, _configure_predict_room)

        assert (
            first_client.post(
                f"/api/rooms/{code}/start",
                headers=_auth(host_token),
            ).status_code
            == 200
        )
        for token, position in ((host_token, 0), (guest_token, 33)):
            assert (
                first_client.post(
                    f"/api/rooms/{code}/answers",
                    headers=_auth(token),
                    json={"position": position, "confidence": 60},
                ).status_code
                == 200
            )
        assert (
            first_client.post(
                f"/api/rooms/{code}/reveal",
                headers=_auth(host_token),
            ).status_code
            == 200
        )
        assert (
            first_client.post(
                f"/api/rooms/{code}/advance",
                headers=_auth(host_token),
            ).status_code
            == 200
        )

        for token, position in ((host_token, 100), (guest_token, 0)):
            second_round = first_client.post(
                f"/api/rooms/{code}/answers",
                headers=_auth(token),
                json={"position": position, "confidence": 80},
            )
            assert second_round.status_code == 200
        assert second_round.json()["phase"] == "modifier"
        host_prediction = first_client.post(
            f"/api/rooms/{code}/modifier-submissions",
            headers=_auth(host_token),
            json={"value": 67},
        )
        assert host_prediction.status_code == 200
        assert host_prediction.json()["modifier"]["submissions_count"] == 1
        assert host_prediction.json()["modifier"]["results"] is None
    finally:
        first_store.close()

    second_store = RoomStore(database_path)
    try:
        second_client = TestClient(create_app(second_store))
        host_session = second_client.get("/api/session", headers=_auth(host_token))
        guest_session = second_client.get("/api/session", headers=_auth(guest_token))
        assert host_session.status_code == guest_session.status_code == 200
        restored = guest_session.json()["room"]
        assert restored["phase"] == "modifier"
        assert restored["round_number"] == 2
        assert restored["modifier"]["type"] == "predict_room"
        assert restored["modifier"]["submissions_count"] == 1
        assert restored["modifier"]["results"] is None
        assert next(player for player in restored["players"] if player["name"] == "Host")[
            "has_modifier_submitted"
        ]

        assert (
            second_client.post(
                f"/api/rooms/{code}/modifier-submissions",
                headers=_auth(guest_token),
                json={"value": -100},
            ).status_code
            == 200
        )
        revealed = second_client.post(
            f"/api/rooms/{code}/reveal",
            headers=_auth(host_token),
        )
        assert revealed.status_code == 200
        assert revealed.json()["modifier"]["results"] == [
            {"player_name": "Host", "value": 67, "score": 92},
            {"player_name": "Guest", "value": -100, "score": 25},
        ]
    finally:
        second_store.close()

    third_store = RoomStore(database_path)
    try:
        room, player = third_store.resume(guest_token)
        assert player.name == "Guest"
        assert room.phase.value == "reveal"
        assert room.current_round is not None
        assert room.current_round.modifier is not None
        assert room.current_round.modifier.results
    finally:
        third_store.close()


def test_access_tokens_are_unique_hashed_and_never_embedded_in_room_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tokens.sqlite3"
    store = RoomStore(database_path)
    try:
        room, host, host_token = store.create_room("Host")
        room, guest, guest_token = store.join_room(room.code, "Guest")
        code = room.code
        assert host_token != guest_token
        assert host_token not in {host.id, guest.id}
        assert guest_token not in {host.id, guest.id}

        with pytest.raises(UnknownAccessToken):
            store.resume(host.id)
    finally:
        store.close()

    with sqlite3.connect(database_path) as connection:
        stored_state = connection.execute(
            "SELECT state_json FROM rooms WHERE code = ?",
            (code,),
        ).fetchone()[0]
        token_hashes = {
            row[0]
            for row in connection.execute(
                "SELECT token_hash FROM player_sessions WHERE room_code = ?",
                (code,),
            )
        }

    assert host_token not in stored_state
    assert guest_token not in stored_state
    assert token_hashes == {
        hashlib.sha256(host_token.encode()).digest(),
        hashlib.sha256(guest_token.encode()).digest(),
    }
    for database_file in tmp_path.glob("tokens.sqlite3*"):
        contents = database_file.read_bytes()
        assert host_token.encode() not in contents
        assert guest_token.encode() not in contents


def test_failed_mutation_rolls_back_room_snapshot_and_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "rollback.sqlite3"
    store = RoomStore(database_path)
    try:
        room, host, access_token = store.create_room("Host")
        code = room.code

        def mutate_then_fail(room, _player_id: str) -> None:
            room.add_player("Uncommitted player")
            raise RuntimeError("deliberate failure")

        with pytest.raises(RuntimeError, match="deliberate failure"):
            store.mutate(code, access_token, mutate_then_fail)

        after_failure = store.get(code)
        assert list(after_failure.players) == [host.id]
        with sqlite3.connect(database_path) as connection:
            revision = connection.execute(
                "SELECT revision FROM rooms WHERE code = ?",
                (code,),
            ).fetchone()[0]
        assert revision == 0
    finally:
        store.close()

    reopened = RoomStore(database_path)
    try:
        restored, restored_host = reopened.resume(access_token)
        assert restored_host.id == host.id
        assert list(restored.players) == [host.id]
        assert restored.phase.value == "lobby"
    finally:
        reopened.close()


def test_commit_failure_rolls_back_and_connection_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "commit-failure.sqlite3"
    store = RoomStore(database_path)
    try:
        room, _host, access_token = store.create_room("Host")
        original_commit = store._commit

        def fail_commit() -> None:
            raise sqlite3.OperationalError("deliberate commit failure")

        def lower_player_limit(active_room, _player_id: str) -> None:
            active_room.max_players = 7

        monkeypatch.setattr(store, "_commit", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="deliberate commit failure"):
            store.mutate(room.code, access_token, lower_player_limit)

        monkeypatch.setattr(store, "_commit", original_commit)
        assert store.get(room.code).max_players == 8
        store.mutate(room.code, access_token, lower_player_limit)
        assert store.get(room.code).max_players == 7
    finally:
        store.close()


def test_concurrent_store_instances_preserve_joins_and_answers(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    stores = [RoomStore(database_path) for _ in range(3)]
    try:
        room, _host, host_token = stores[0].create_room("Host")
        code = room.code
        join_barrier = Barrier(2)

        def join(store: RoomStore, name: str) -> str:
            join_barrier.wait()
            _room, _player, access_token = store.join_room(code, name)
            return access_token

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_join = executor.submit(join, stores[0], "Guest one")
            second_join = executor.submit(join, stores[1], "Guest two")
            guest_tokens = [first_join.result(), second_join.result()]

        joined_room = stores[2].get(code)
        assert {player.name for player in joined_room.players.values()} == {
            "Host",
            "Guest one",
            "Guest two",
        }

        stores[0].mutate(
            code,
            host_token,
            lambda active_room, player_id: active_room.start(host_id=player_id),
        )
        answer_barrier = Barrier(3)

        def answer(store: RoomStore, token: str, position: int) -> None:
            answer_barrier.wait()
            store.mutate(
                code,
                token,
                lambda active_room, player_id: active_room.submit_answer(
                    player_id=player_id,
                    position=position,
                    confidence=60,
                ),
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            answers = [
                executor.submit(answer, stores[0], host_token, -67),
                executor.submit(answer, stores[1], guest_tokens[0], 0),
                executor.submit(answer, stores[2], guest_tokens[1], 67),
            ]
            for submitted in answers:
                submitted.result()

        answered_room = stores[0].get(code)
        assert answered_room.current_round is not None
        assert len(answered_room.current_round.answers) == 3
        assert {answer.position for answer in answered_room.current_round.answers.values()} == {
            -67,
            0,
            67,
        }
    finally:
        for store in stores:
            store.close()
