import json

import pytest

from psychology_roulette import ai
from psychology_roulette.ai import HttpModifierContextProvider, apply_modifier_contexts
from psychology_roulette.domain import Question, Room


def _started_room() -> Room:
    room = Room(
        code="SECR",
        questions=[
            Question(
                id="q1",
                prompt="Opening question",
                category="ethics",
                intensity=1,
                values=("care",),
                modifiers_allowed=(),
            ),
            Question(
                id="q2",
                prompt="Should intent matter more than impact?",
                category="ethics",
                intensity=2,
                values=("intent", "impact"),
                modifiers_allowed=("devils_advocate",),
            ),
        ],
        modifier_chance=1,
        modifier_seed="ai-test",
    )
    host = room.add_player("Alice", is_host=True)
    room.add_player("Bob")
    room.start(host_id=host.id, round_count=2)
    assert room.rounds[1].modifier is not None
    return room


def test_http_provider_sends_only_curated_round_context(monkeypatch) -> None:
    room = _started_room()
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "contexts": [
                        {
                            "round_number": 2,
                            "question_id": "q2",
                            "modifier_type": "devils_advocate",
                            "context": "Consider whether good intentions reduce responsibility.",
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data)
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(ai, "urlopen", fake_urlopen)
    provider = HttpModifierContextProvider("https://ai.example.test", "shared-secret", 3)
    contexts = provider.generate(room)

    assert captured["url"] == "https://ai.example.test/v1/modifier-contexts"
    assert captured["timeout"] == 3
    assert captured["authorization"] == "Bearer shared-secret"
    encoded_body = json.dumps(captured["body"])
    assert "SECR" not in encoded_body
    assert "Alice" not in encoded_body
    assert "Bob" not in encoded_body
    assert len(contexts) == 1
    apply_modifier_contexts(room, contexts)
    assert room.rounds[1].modifier.context == (
        "Consider whether good intentions reduce responsibility."
    )


@pytest.mark.parametrize(
    "url",
    ["http://ai.example.test", "ftp://ai.example.test", "missing-a-scheme"],
)
def test_http_provider_requires_https_except_for_local_development(url: str) -> None:
    with pytest.raises(ValueError):
        HttpModifierContextProvider(url, "secret")


def test_http_provider_accepts_local_http() -> None:
    provider = HttpModifierContextProvider("http://127.0.0.1:8787", "secret")
    assert provider.base_url == "http://127.0.0.1:8787"
