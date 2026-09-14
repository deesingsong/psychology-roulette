import json

import app as gateway
from fastapi.testclient import TestClient


def _content_request(count: int = 6) -> dict:
    return {
        "schema_version": 1,
        "round_count": count,
        "avoid_prompts": ["An existing curated statement should not repeat."],
    }


def _question(number: int) -> dict:
    return {
        "prompt": f"Fresh debatable statement number {number} for this game.",
        "category": f"category_{number}",
        "intensity": 2,
        "values": ["autonomy", "fairness"],
        "discussion_prompt": f"Which principle shapes statement {number} most?",
    }


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
        [{"questions": [_question(number) for number in range(1, 7)]}],
        captured,
    )
    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 200
    assert len(response.json()["questions"]) == 6
    schema = captured[0]["response_format"]["schema"]
    assert schema["properties"]["questions"]["minItems"] == 6
    assert schema["properties"]["questions"]["maxItems"] == 6


def test_gateway_retries_semantically_invalid_game_content(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    captured: list[dict] = []
    duplicate = _question(1)
    valid = {"questions": [_question(number) for number in range(1, 7)]}
    _install_model(monkeypatch, [{"questions": [duplicate] * 6}, valid], captured)

    response = TestClient(gateway.app).post(
        "/v1/game-content",
        headers={"Authorization": "Bearer correct-secret"},
        json=_content_request(),
    )

    assert response.status_code == 200
    assert len(captured) == 2
    assert "prior attempt failed" in captured[1]["messages"][1]["content"]


def test_gateway_returns_422_after_invalid_retry_exhaustion(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    monkeypatch.setenv("QWEN_GENERATION_ATTEMPTS", "2")
    captured: list[dict] = []
    duplicate = {"questions": [_question(1)] * 6}
    _install_model(monkeypatch, [duplicate, duplicate], captured)

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
