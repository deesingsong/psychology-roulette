import app as gateway
from fastapi.testclient import TestClient


def _request() -> dict:
    return {
        "schema_version": 1,
        "modifiers": [
            {
                "round_number": 2,
                "question_id": "q2",
                "question_prompt": "Should intent matter more than impact?",
                "category": "ethics",
                "values": ["intent", "impact"],
                "modifier_type": "devils_advocate",
                "modifier_title": "Devil's Advocate",
                "canonical_instructions": "Defend a meaningfully different view.",
            }
        ],
    }


def test_gateway_requires_credential(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")
    response = TestClient(gateway.app).post("/v1/modifier-contexts", json=_request())
    assert response.status_code == 401


def test_gateway_validates_and_returns_model_context(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-secret")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"contexts":[{"round_number":2,"question_id":"q2",'
                                '"modifier_type":"devils_advocate","context":'
                                '"  Ask whether intent changes what repair is owed.  "}]}'
                            )
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeAsyncClient)
    response = TestClient(gateway.app).post(
        "/v1/modifier-contexts",
        headers={"Authorization": "Bearer correct-secret"},
        json=_request(),
    )
    assert response.status_code == 200
    assert response.json()["contexts"][0]["context"] == (
        "Ask whether intent changes what repair is owed."
    )
