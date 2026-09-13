import secrets

from fastapi.testclient import TestClient

from psychology_roulette.ai import ModifierContext
from psychology_roulette.api import EntryRateLimits, create_app
from psychology_roulette.domain import ModifierType, Question
from psychology_roulette.store import RoomStore


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _new_access_token() -> str:
    return secrets.token_urlsafe(32)


def _create_two_player_room(client: TestClient) -> tuple[str, dict, dict]:
    host_token = _new_access_token()
    created_response = client.post(
        "/api/rooms",
        headers=_auth(host_token),
        json={"host_name": "Host"},
    )
    assert created_response.status_code == 201
    assert created_response.headers["cache-control"] == "no-store"
    created = created_response.json()
    assert created["access_token"] == host_token
    code = created["room"]["code"]

    guest_token = _new_access_token()
    joined_response = client.post(
        f"/api/rooms/{code}/players",
        headers=_auth(guest_token),
        json={"name": "Guest"},
    )
    assert joined_response.status_code == 201
    assert joined_response.headers["cache-control"] == "no-store"
    assert joined_response.json()["access_token"] == guest_token
    return code, created, joined_response.json()


def test_rooms_use_code_only_joining_and_do_not_expose_invites() -> None:
    client = TestClient(create_app(RoomStore()))
    created_response = client.post(
        "/api/rooms",
        headers=_auth(_new_access_token()),
        json={"host_name": "Host"},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    code = created["room"]["code"]
    assert "invite_token" not in created
    joined = client.post(
        f"/api/rooms/{code}/players",
        headers=_auth(_new_access_token()),
        json={"name": "Guest"},
    )
    assert joined.status_code == 201
    assert joined.json()["player_name"] == "Guest"


def test_create_and_join_entry_limits_return_retry_after() -> None:
    limits = EntryRateLimits(
        create_limit=1,
        create_window_seconds=600,
        join_limit=1,
        join_window_seconds=600,
    )
    store = RoomStore()
    client = TestClient(create_app(store, entry_rate_limits=limits))
    created = client.post(
        "/api/rooms",
        headers=_auth(_new_access_token()),
        json={"host_name": "Host"},
    )
    assert created.status_code == 201
    blocked_create = client.post(
        "/api/rooms",
        headers=_auth(_new_access_token()),
        json={"host_name": "Another Host"},
    )
    assert blocked_create.status_code == 429
    assert int(blocked_create.headers["Retry-After"]) >= 1

    code = created.json()["room"]["code"]
    first_join = client.post(
        f"/api/rooms/{code}/players",
        headers=_auth(_new_access_token()),
        json={"name": "Guest"},
    )
    assert first_join.status_code == 201
    blocked_join = client.post(
        f"/api/rooms/{code}/players",
        headers=_auth(_new_access_token()),
        json={"name": "Another Guest"},
    )
    assert blocked_join.status_code == 429
    assert int(blocked_join.headers["Retry-After"]) >= 1


def _assert_invalid_caller_token(response) -> None:
    assert response.status_code == 401
    assert response.json() == {"detail": "A valid room access token is required."}
    assert response.headers["www-authenticate"] == "Bearer"


def _assert_token_reuse_conflict(response) -> None:
    assert response.status_code == 409
    assert response.json() == {
        "detail": "That access token is already assigned to a different room or participant."
    }


def test_create_and_join_retries_return_the_original_session() -> None:
    store = RoomStore()
    client = TestClient(create_app(store))
    host_token = _new_access_token()
    create_request = {"host_name": "  Host   Person  "}

    created = client.post("/api/rooms", headers=_auth(host_token), json=create_request)
    retried_create = client.post(
        "/api/rooms",
        headers=_auth(host_token),
        json={"host_name": "Host Person"},
    )
    assert created.status_code == retried_create.status_code == 201
    assert retried_create.json() == created.json()
    assert created.json()["access_token"] == host_token
    code = created.json()["room"]["code"]

    guest_token = _new_access_token()
    joined = client.post(
        f"/api/rooms/{code}/players",
        headers=_auth(guest_token),
        json={"name": "  Guest   Person "},
    )
    retried_join = client.post(
        f"/api/rooms/{code.lower()}/players",
        headers=_auth(guest_token),
        json={"name": "Guest Person"},
    )
    assert joined.status_code == retried_join.status_code == 201
    assert retried_join.json() == joined.json()
    assert joined.json()["access_token"] == guest_token

    stored_room = store.get(code)
    assert [player.name for player in stored_room.players.values()] == [
        "Host Person",
        "Guest Person",
    ]


def test_create_and_join_require_canonical_strong_caller_tokens() -> None:
    client = TestClient(create_app(RoomStore()))

    _assert_invalid_caller_token(client.post("/api/rooms", json={"host_name": "Host"}))
    _assert_invalid_caller_token(
        client.post(
            "/api/rooms",
            headers=_auth("too-short"),
            json={"host_name": "Host"},
        )
    )

    host_token = _new_access_token()
    created = client.post(
        "/api/rooms",
        headers=_auth(host_token),
        json={"host_name": "Host"},
    )
    assert created.status_code == 201
    code = created.json()["room"]["code"]

    _assert_invalid_caller_token(client.post(f"/api/rooms/{code}/players", json={"name": "Guest"}))
    _assert_invalid_caller_token(
        client.post(
            f"/api/rooms/{code}/players",
            headers=_auth("still-too-short"),
            json={"name": "Guest"},
        )
    )


def test_caller_token_cannot_be_reused_for_another_identity() -> None:
    client = TestClient(create_app(RoomStore()))
    host_token = _new_access_token()
    first_room = client.post(
        "/api/rooms",
        headers=_auth(host_token),
        json={"host_name": "Host"},
    ).json()
    other_room = client.post(
        "/api/rooms",
        headers=_auth(_new_access_token()),
        json={"host_name": "Other host"},
    ).json()
    first_code = first_room["room"]["code"]
    other_code = other_room["room"]["code"]

    _assert_token_reuse_conflict(
        client.post(
            "/api/rooms",
            headers=_auth(host_token),
            json={"host_name": "Changed host"},
        )
    )
    _assert_token_reuse_conflict(
        client.post(
            f"/api/rooms/{first_code}/players",
            headers=_auth(host_token),
            json={"name": "Host"},
        )
    )

    guest_token = _new_access_token()
    joined = client.post(
        f"/api/rooms/{first_code}/players",
        headers=_auth(guest_token),
        json={"name": "Guest"},
    )
    assert joined.status_code == 201

    _assert_token_reuse_conflict(
        client.post(
            f"/api/rooms/{first_code}/players",
            headers=_auth(guest_token),
            json={"name": "Changed guest"},
        )
    )
    _assert_token_reuse_conflict(
        client.post(
            f"/api/rooms/{other_code}/players",
            headers=_auth(guest_token),
            json={"name": "Guest"},
        )
    )
    _assert_token_reuse_conflict(
        client.post(
            "/api/rooms",
            headers=_auth(guest_token),
            json={"host_name": "Guest"},
        )
    )


def test_room_api_hides_answers_until_reveal() -> None:
    client = TestClient(create_app(RoomStore()))
    code, host, guest = _create_two_player_room(client)
    assert "modifier_seed" not in host["room"]

    response = client.post(f"/api/rooms/{code}/start", headers=_auth(host["access_token"]))
    assert response.status_code == 200

    host_answer = client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host["access_token"]),
        json={"position": 67, "confidence": 80},
    )
    assert host_answer.status_code == 200
    assert host_answer.json()["revealed_answers"] is None

    guest_answer = client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest["access_token"]),
        json={"position": -33, "confidence": 40},
    )
    assert guest_answer.status_code == 200
    revealed = client.post(
        f"/api/rooms/{code}/reveal",
        headers=_auth(host["access_token"]),
    )

    assert revealed.status_code == 200
    assert len(revealed.json()["revealed_answers"]) == 2
    assert revealed.json()["summary"]["average_position"] == 17.0


def test_api_requires_tokens_and_isolates_rooms() -> None:
    client = TestClient(create_app(RoomStore()))
    first_code, first_host, first_guest = _create_two_player_room(client)
    second = client.post(
        "/api/rooms",
        headers=_auth(_new_access_token()),
        json={"host_name": "Other host"},
    ).json()
    second_code = second["room"]["code"]

    missing_token = client.get(f"/api/rooms/{first_code}")
    assert missing_token.status_code == 401
    assert missing_token.headers["www-authenticate"] == "Bearer"
    assert (
        client.get(
            f"/api/rooms/{first_code}",
            headers=_auth("not-a-valid-token"),
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/rooms/{first_code}",
            headers=_auth(first_host["player_id"]),
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/rooms/{second_code}",
            headers=_auth(first_host["access_token"]),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/rooms/{second_code}/start",
            headers=_auth(first_host["access_token"]),
        ).status_code
        == 403
    )

    resumed = client.get("/api/session", headers=_auth(first_guest["access_token"]))
    assert resumed.status_code == 200
    assert resumed.headers["cache-control"] == "no-store"
    assert resumed.json()["player_id"] == first_guest["player_id"]
    assert resumed.json()["player_name"] == "Guest"
    assert resumed.json()["is_host"] is False
    assert resumed.json()["room"]["code"] == first_code
    assert "access_token" not in resumed.json()


def test_guest_token_cannot_perform_host_actions() -> None:
    client = TestClient(create_app(RoomStore()))
    code, host, guest = _create_two_player_room(client)

    rejected = client.post(f"/api/rooms/{code}/start", headers=_auth(guest["access_token"]))
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "Only the host can do that."}

    room = client.get(f"/api/rooms/{code}", headers=_auth(host["access_token"]))
    assert room.json()["phase"] == "lobby"
    assert (
        client.post(
            f"/api/rooms/{code}/start",
            headers=_auth(host["access_token"]),
        ).status_code
        == 200
    )


def test_only_host_can_end_room_and_every_session_is_revoked() -> None:
    client = TestClient(create_app(RoomStore()))
    code, host, guest = _create_two_player_room(client)

    rejected = client.delete(
        f"/api/rooms/{code}",
        headers=_auth(guest["access_token"]),
    )
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "Only the host can end the game."}

    ended = client.delete(
        f"/api/rooms/{code}",
        headers=_auth(host["access_token"]),
    )
    assert ended.status_code == 204
    assert ended.content == b""
    for participant in (host, guest):
        expired = client.get(
            "/api/session",
            headers=_auth(participant["access_token"]),
        )
        assert expired.status_code == 401


def _client_at_second_round(modifier_type: str) -> tuple[TestClient, str, str, str]:
    store = RoomStore()
    client = TestClient(create_app(store))
    code, host, guest = _create_two_player_room(client)
    host_token = host["access_token"]
    guest_token = guest["access_token"]

    def configure_modifier_room(room, _player_id: str) -> None:
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

    store.mutate(code, host_token, configure_modifier_room)

    assert client.post(f"/api/rooms/{code}/start", headers=_auth(host_token)).status_code == 200
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host_token),
        json={"position": 0, "confidence": 50},
    )
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest_token),
        json={"position": 33, "confidence": 60},
    )
    client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
    advanced = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token))
    assert advanced.status_code == 200
    assert advanced.json()["round_number"] == 2
    assert advanced.json()["modifier"] is None
    return client, code, host_token, guest_token


def test_start_attaches_optional_ai_context_without_changing_modifier_rules() -> None:
    store = RoomStore()
    original_instructions = []

    class ContextProvider:
        def generate(self, room):
            modifier = room.rounds[1].modifier
            assert modifier is not None
            assert modifier.target_player_id is not None
            original_instructions.append(modifier.instructions)
            return (
                ModifierContext(
                    round_number=2,
                    question_id="api-q2",
                    modifier_type=ModifierType.DEVILS_ADVOCATE,
                    text="Ask which hidden tradeoff the opposing view protects.",
                ),
            )

    client = TestClient(create_app(store, modifier_context_provider=ContextProvider()))
    code, host, _guest = _create_two_player_room(client)
    host_token = host["access_token"]

    def configure(room, _player_id: str) -> None:
        room.modifier_chance = 1
        room.questions = [
            Question(
                id=f"api-q{number}",
                prompt=f"API question {number}",
                category="ethics",
                intensity=1,
                values=("fairness", "care"),
                modifiers_allowed=("devils_advocate",) if number == 2 else (),
            )
            for number in range(1, 7)
        ]

    store.mutate(code, host_token, configure)
    response = client.post(f"/api/rooms/{code}/start", headers=_auth(host_token))
    assert response.status_code == 200
    saved_modifier = store.get(code).rounds[1].modifier
    assert saved_modifier is not None
    assert saved_modifier.instructions == original_instructions[0]
    assert saved_modifier.context == (
        "Ask which hidden tradeoff the opposing view protects."
    )


def test_start_falls_back_to_curated_copy_when_ai_fails() -> None:
    class FailingProvider:
        def generate(self, _room):
            raise TimeoutError("offline")

    client = TestClient(
        create_app(RoomStore(), modifier_context_provider=FailingProvider())
    )
    code, host, _guest = _create_two_player_room(client)
    response = client.post(
        f"/api/rooms/{code}/start",
        headers=_auth(host["access_token"]),
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "answering"


def test_predict_room_api_keeps_predictions_private_until_reveal() -> None:
    client, code, host_token, guest_token = _client_at_second_round("predict_room")
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host_token),
        json={"position": 100, "confidence": 90},
    )
    modifier_phase = client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest_token),
        json={"position": 0, "confidence": 40},
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
        "context": None,
        "target_player_name": None,
        "source_player_name": None,
        "options": [-100, -67, -33, 0, 33, 67, 100],
        "submissions_count": 0,
        "required_submissions": 2,
        "results": None,
    }
    assert all(not player["has_modifier_submitted"] for player in payload["players"])

    invalid_number = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(host_token),
        json={"value": 12.5},
    )
    assert invalid_number.status_code == 422

    host_prediction = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(host_token),
        json={"value": 67},
    ).json()
    assert host_prediction["modifier"]["submissions_count"] == 1
    assert host_prediction["modifier"]["results"] is None
    assert next(player for player in host_prediction["players"] if player["name"] == "Host")[
        "has_modifier_submitted"
    ]

    early_reveal = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
    assert early_reveal.status_code == 409
    assert "Every player must complete" in early_reveal.json()["detail"]

    client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(guest_token),
        json={"value": -100},
    )
    revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
    assert revealed.status_code == 200
    assert revealed.json()["modifier"]["results"] == [
        {"player_name": "Host", "value": 67, "score": 92, "movement": None},
        {"player_name": "Guest", "value": -100, "score": 25, "movement": None},
    ]


def test_secret_principle_api_reveals_selections_only_at_reveal() -> None:
    client, code, host_token, guest_token = _client_at_second_round("secret_principle")
    for token, position in ((host_token, -33), (guest_token, 67)):
        response = client.post(
            f"/api/rooms/{code}/answers",
            headers=_auth(token),
            json={"position": position, "confidence": 70},
        )
    assert response.json()["phase"] == "modifier"
    assert response.json()["modifier"]["options"] == ["fairness", "autonomy", "care"]

    invalid = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(host_token),
        json={"value": "Fairness"},
    )
    assert invalid.status_code == 409

    first_submission = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(host_token),
        json={"value": "fairness"},
    )
    assert first_submission.json()["modifier"]["results"] is None
    client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(guest_token),
        json={"value": "care"},
    )

    revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
    assert revealed.json()["modifier"]["results"] == [
        {"player_name": "Host", "value": "fairness", "score": None, "movement": None},
        {"player_name": "Guest", "value": "care", "score": None, "movement": None},
    ]


def test_devils_advocate_api_is_hidden_until_the_reveal() -> None:
    client, code, host_token, guest_token = _client_at_second_round("devils_advocate")
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host_token),
        json={"position": -67, "confidence": 55},
    )
    answered = client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest_token),
        json={"position": 67, "confidence": 75},
    )
    assert answered.json()["phase"] == "answering"
    assert answered.json()["modifier"] is None

    revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
    modifier = revealed.json()["modifier"]
    assert modifier["type"] == "devils_advocate"
    assert modifier["timing"] == "post_reveal"
    assert modifier["target_player_name"] in {"Host", "Guest"}
    assert modifier["required_submissions"] == 0
    assert modifier["submissions_count"] == 0
    assert modifier["options"] == []
    assert modifier["results"] == []


def test_steelman_api_reveals_only_the_selected_players_finished_text() -> None:
    client, code, host_token, guest_token = _client_at_second_round("steelman")
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host_token),
        json={"position": -100, "confidence": 75},
    )
    answered = client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest_token),
        json={"position": 100, "confidence": 65},
    )
    assert answered.json()["modifier"] is None

    revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token)).json()
    modifier = revealed["modifier"]
    assert modifier["type"] == "steelman"
    assert modifier["target_player_name"] in {"Host", "Guest"}
    assert modifier["source_player_name"] in {"Host", "Guest"}
    assert modifier["target_player_name"] != modifier["source_player_name"]
    assert modifier["results"] is None

    follow_up = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
    assert follow_up["phase"] == "follow_up"
    assert follow_up["modifier"]["required_submissions"] == 1
    selected_token = host_token if modifier["target_player_name"] == "Host" else guest_token
    other_token = guest_token if selected_token == host_token else host_token
    rejected = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(other_token),
        json={"value": "I should not be able to submit this."},
    )
    assert rejected.status_code == 409

    submitted = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(selected_token),
        json={"value": "Their view protects a real cost that deserves attention."},
    ).json()
    assert submitted["modifier"]["results"] is None
    public_follow_up = client.get(f"/api/rooms/{code}", headers=_auth(other_token)).json()
    assert public_follow_up["modifier"]["results"] is None

    result = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
    assert result["phase"] == "follow_up_reveal"
    assert result["modifier"]["results"] == [
        {
            "player_name": modifier["target_player_name"],
            "value": "Their view protects a real cost that deserves attention.",
            "score": None,
            "movement": None,
        }
    ]


def test_change_my_mind_api_keeps_repoll_private_and_reveals_movement() -> None:
    client, code, host_token, guest_token = _client_at_second_round("change_my_mind")
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(host_token),
        json={"position": -67, "confidence": 80},
    )
    client.post(
        f"/api/rooms/{code}/answers",
        headers=_auth(guest_token),
        json={"position": 67, "confidence": 70},
    )
    revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token)).json()
    assert revealed["modifier"]["type"] == "change_my_mind"
    assert revealed["modifier"]["results"] is None

    follow_up = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
    assert follow_up["phase"] == "follow_up"
    assert follow_up["modifier"]["required_submissions"] == 2
    first = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(host_token),
        json={"value": 0},
    ).json()
    assert first["modifier"]["results"] is None
    assert first["modifier"]["submissions_count"] == 1
    early_reveal = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token))
    assert early_reveal.status_code == 409

    second = client.post(
        f"/api/rooms/{code}/modifier-submissions",
        headers=_auth(guest_token),
        json={"value": 67},
    ).json()
    assert second["modifier"]["results"] is None
    result = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
    assert result["phase"] == "follow_up_reveal"
    assert result["modifier"]["results"] == [
        {"player_name": "Host", "value": 0, "score": None, "movement": 67},
        {"player_name": "Guest", "value": 67, "score": None, "movement": 0},
    ]


def test_default_six_round_api_session_reaches_completion() -> None:
    client = TestClient(create_app(RoomStore()))
    code, host, guest = _create_two_player_room(client)
    host_token = host["access_token"]
    guest_token = guest["access_token"]

    room = client.post(f"/api/rooms/{code}/start", headers=_auth(host_token)).json()
    modifier_rounds = 0

    for round_number in range(1, 7):
        assert room["phase"] == "answering"
        assert room["round_number"] == round_number

        client.post(
            f"/api/rooms/{code}/answers",
            headers=_auth(host_token),
            json={"position": 67, "confidence": 80},
        )
        room = client.post(
            f"/api/rooms/{code}/answers",
            headers=_auth(guest_token),
            json={"position": -33, "confidence": 60},
        ).json()

        if room["phase"] == "modifier":
            modifier_rounds += 1
            submission_value = room["modifier"]["options"][0]
            client.post(
                f"/api/rooms/{code}/modifier-submissions",
                headers=_auth(host_token),
                json={"value": submission_value},
            )
            room = client.post(
                f"/api/rooms/{code}/modifier-submissions",
                headers=_auth(guest_token),
                json={"value": submission_value},
            ).json()

        revealed = client.post(f"/api/rooms/{code}/reveal", headers=_auth(host_token))
        assert revealed.status_code == 200
        room = revealed.json()
        assert room["phase"] == "reveal"
        if room["modifier"] is not None:
            modifier_rounds += int(room["modifier"]["timing"] == "post_reveal")

        room = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
        if room["phase"] == "follow_up":
            modifier = room["modifier"]
            if modifier["type"] == "steelman":
                selected_token = (
                    host_token if modifier["target_player_name"] == "Host" else guest_token
                )
                room = client.post(
                    f"/api/rooms/{code}/modifier-submissions",
                    headers=_auth(selected_token),
                    json={"value": "The other position protects a legitimate concern."},
                ).json()
            else:
                client.post(
                    f"/api/rooms/{code}/modifier-submissions",
                    headers=_auth(host_token),
                    json={"value": 33},
                )
                room = client.post(
                    f"/api/rooms/{code}/modifier-submissions",
                    headers=_auth(guest_token),
                    json={"value": -33},
                ).json()
            assert room["modifier"]["results"] is None
            room = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()
            assert room["phase"] == "follow_up_reveal"
            room = client.post(f"/api/rooms/{code}/advance", headers=_auth(host_token)).json()

    assert room["phase"] == "complete"
    assert modifier_rounds >= 1
    assert room["session_summary"]["rounds_completed"] == 6
    assert len(room["session_summary"]["players"]) == 2
