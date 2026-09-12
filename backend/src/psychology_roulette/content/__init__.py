from __future__ import annotations

import json
from importlib.resources import files

from psychology_roulette.domain.models import Question


def load_questions() -> list[Question]:
    payload = json.loads(files(__package__).joinpath("questions.json").read_text(encoding="utf-8"))
    return [
        Question(
            id=item["id"],
            prompt=item["prompt"],
            category=item["category"],
            intensity=item["intensity"],
            values=tuple(item["values"]),
            modifiers_allowed=tuple(item["modifiers_allowed"]),
        )
        for item in payload
    ]


__all__ = ["load_questions"]
