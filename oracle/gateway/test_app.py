import json

from fastapi.testclient import TestClient

import app as gateway


def _content_request(count: int = 6) -> dict:
    return {
        "schema_version": 1,
        "round_count": count,
        "avoid_prompts": ["An existing curated statement should not repeat."],
    }


def _question(number: int) -> dict:
    prompts = (
        "Daily routines should leave room for unplanned choices.",
        "Friendship matters more than avoiding every disagreement.",
        "Technology should prioritize privacy over convenience.",
        "Fairness sometimes requires treating people differently.",
        "Community needs should sometimes outweigh personal convenience.",
        "Responsibility matters even when intentions were good.",
    )
    return {
        "prompt": prompts[number - 1],
        "category": f"category_{number}",
        "intensity": 2,
        "values": ["autonomy", "fairness"],
    }


def _prompt_payloads(count: int = 6) -> list[dict]:
    completions = (
        "leave room for unplanned choices",
        "matter more than avoiding every disagreement",
        "prioritize privacy over convenience",
        "sometimes require treating people differently",
        "put shared needs above personal convenience",
        "matter even when intentions were good",
    )
    return [{"completion": completions[number - 1]} for number in range(1, count + 1)]


def _recap_request() -> dict:
    return {
        "schema_version": 1,
        "facts": [
            {
                "id": f"fact_{number}",
                "title": f"Verified fact {number}",
                "value": f"P{number}",
                "detail": f"Verified value {number}",
            }
            for number in range(1, 7)
        ],
    }


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": json.dumps(self.payload)}}]
        }


def _install_model(monkeypatch, payloads: list[dict], captured: list[dict]) -> None:
    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **kwargs):
            captured.append(kwargs["json"])
            return FakeResponse(payloads.pop(0))

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeAsyncClient)


def test_gateway_requires_credential(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    response = TestClient(gateway.app).post("/v1/game-content", json=_content_request())
    assert response.status_code == 401


def test_gateway_returns_schema_constrained_game_content(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    captured: list[dict] = []
    _install_model(
        monkeypatch,
        _prompt_payloads(),
        captured,
    )
    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 200
    assert len(response.json()["questions"]) == 6
    assert len(captured) == 6
    schema = captured[0]["response_format"]["schema"]
    assert schema["required"] == ["completion"]
    assert set(schema["properties"]) == {"completion"}
    assert response.json()["questions"][0]["category"] == "everyday_life"


def test_gateway_retries_a_semantically_invalid_question(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    captured: list[dict] = []
    invalid = {"prompt": "How should friends decide?"}
    _install_model(monkeypatch, [invalid, *_prompt_payloads()], captured)

    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 200
    assert len(captured) == 7
    assert "prior attempt failed" in captured[1]["messages"][1]["content"]
    assert len({item["messages"][1]["content"] for item in captured[1:]}) == 6


def test_gateway_retries_open_ended_questions(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    captured: list[dict] = []
    open_ended = {"completion": "ask what friends should value most?"}
    _install_model(monkeypatch, [open_ended, *_prompt_payloads()], captured)

    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 200
    assert len(captured) == 7


def test_gateway_returns_422_after_invalid_retry_exhaustion(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    monkeypatch.setenv("QWEN_GENERATION_ATTEMPTS", "2")
    captured: list[dict] = []
    invalid = {"completion": "ask why this matters?"}
    _install_model(monkeypatch, [invalid, invalid], captured)

    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 422
    assert len(captured) == 2


def test_gateway_curates_recap_only_from_verified_fact_ids(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    captured: list[dict] = []
    generated = {
        "headline": "The room found its fault line",
        "summary": "A playful summary grounded entirely in verified results.",
        "highlights": [
            {
                "fact_id": f"fact_{number}",
                "title": f"Dynamic title {number}",
                "commentary": f"Verified fact {number} shaped this table.",
            }
            for number in range(1, 6)
        ],
    }
    _install_model(monkeypatch, [generated], captured)

    response = TestClient(gateway.app).post(
        "/v1/session-recap",
        headers={"Authorization": "Bearer correct-secret"},
        json=_recap_request(),
    )

    assert response.status_code == 200
    assert len(response.json()["highlights"]) == 5
    fact_id_schema = captured[0]["response_format"]["schema"]["properties"][
        "highlights"
    ]["items"]["properties"]["fact_id"]
    assert fact_id_schema["enum"] == [f"fact_{number}" for number in range(1, 7)]
