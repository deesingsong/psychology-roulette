import json

import pytest

from psychology_roulette import ai
from psychology_roulette.ai import AIInvalidOutput, HttpAIProvider
from psychology_roulette.domain import Question, Room


def _question(number: int) -> Question:
    return Question(
        id=f"curated-{number}",
        prompt=f"Existing curated prompt number {number} should be avoided.",
        category="ethics",
        intensity=1,
        values=("care", "fairness"),
        modifiers_allowed=("predict_room",),
    )


def _complete_room() -> Room:
    room = Room(code="SECR", questions=[_question(1)], modifier_chance=0)
    host = room.add_player("Alice", is_host=True)
    guest = room.add_player("Bob")
    room.start(host_id=host.id, round_count=1)
    room.submit_answer(player_id=host.id, position=67, confidence=80)
    room.submit_answer(player_id=guest.id, position=-33, confidence=60)
    room.reveal(host_id=host.id)
    room.advance(host_id=host.id)
    return room


def test_http_provider_generates_and_validates_a_fresh_question_pack(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "questions": [
                        {
                            "prompt": f"Original debatable statement number {number} for friends.",
                            "category": f"category_{number}",
                            "intensity": 2,
                            "values": ["autonomy", "fairness"],
                            "discussion_prompt": (
                                f"Which value changes how you read statement {number}?"
                            ),
                        }
                        for number in range(1, 7)
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
    provider = HttpAIProvider("https://ai.example.test", "shared-secret", 20)
    result = provider.generate_game_content(
        round_count=6,
        avoid_questions=tuple(_question(number) for number in range(1, 3)),
    )

    assert captured["url"] == "https://ai.example.test/v1/game-content"
    assert captured["timeout"] == 20
    assert captured["authorization"] == "Bearer shared-secret"
    assert len(result.questions) == 6
    assert len({question.id for question in result.questions}) == 6
    assert result.questions[0].discussion_prompt.startswith("Which value")
    assert result.questions[0].modifier_context == result.questions[0].discussion_prompt
    assert set(result.questions[0].modifiers_allowed) == {
        "predict_room",
        "secret_principle",
        "devils_advocate",
        "steelman",
        "change_my_mind",
    }


def test_http_provider_rejects_duplicate_generated_questions(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            question = {
                "prompt": "The same generated prompt is repeated in this pack.",
                "category": "ethics",
                "intensity": 1,
                "values": ["care", "fairness"],
                "discussion_prompt": "What principle is in tension here?",
            }
            return json.dumps({"questions": [question, question]}).encode()

    monkeypatch.setattr(ai, "urlopen", lambda *_args, **_kwargs: Response())
    provider = HttpAIProvider("https://ai.example.test", "secret")
    with pytest.raises(AIInvalidOutput, match="duplicate"):
        provider.generate_game_content(round_count=2, avoid_questions=())


def test_http_provider_maps_anonymous_verified_recap_facts(monkeypatch) -> None:
    room = _complete_room()
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "headline": "P1 and P2 split the room",
                    "summary": "P1 went high while P2 kept the table grounded.",
                    "highlights": [
                        {
                            "fact_id": fact_id,
                            "title": f"A fresh title for {fact_id}",
                            "commentary": "This verified result gave the table its shape.",
                        }
                        for fact_id in [
                            "most_divisive",
                            "strongest_consensus",
                            "most_niche",
                            "most_npc",
                            "highest_confidence",
                        ]
                    ],
                }
            ).encode()

    def fake_urlopen(request, **_kwargs):
        captured["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(ai, "urlopen", fake_urlopen)
    recap = HttpAIProvider("https://ai.example.test", "secret").generate_session_recap(room)

    encoded_request = json.dumps(captured["body"])
    assert "Alice" not in encoded_request
    assert "Bob" not in encoded_request
    assert recap.headline == "Alice and Bob split the room"
    assert recap.highlights[2].value in {"Alice", "Bob"}


@pytest.mark.parametrize(
    "url",
    ["http://ai.example.test", "ftp://ai.example.test", "missing-a-scheme"],
)
def test_http_provider_requires_https_except_for_local_development(url: str) -> None:
    with pytest.raises(ValueError):
        HttpAIProvider(url, "secret")


def test_http_provider_accepts_local_http() -> None:
    provider = HttpAIProvider("http://127.0.0.1:8787", "secret")
    assert provider.base_url == "http://127.0.0.1:8787"
