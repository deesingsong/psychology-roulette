from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from psychology_roulette.domain import ModifierType, Room

MAX_RESPONSE_BYTES = 64 * 1024
MAX_CONTEXT_LENGTH = 280


class ModifierContextError(RuntimeError):
    """The optional contextual-copy service returned an unusable response."""


@dataclass(frozen=True, slots=True)
class ModifierContext:
    round_number: int
    question_id: str
    modifier_type: ModifierType
    text: str


class ModifierContextProvider(Protocol):
    def generate(self, room: Room) -> tuple[ModifierContext, ...]: ...


@dataclass(frozen=True, slots=True)
class HttpModifierContextProvider:
    base_url: str
    token: str
    timeout_seconds: float = 12.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
            raise ValueError("The AI service URL must use HTTPS (HTTP is allowed locally).")
        if not parsed.netloc:
            raise ValueError("The AI service URL must include a host.")
        if not self.token:
            raise ValueError("The AI service token cannot be empty.")
        if not 0 < self.timeout_seconds <= 20:
            raise ValueError("The AI timeout must be greater than zero and at most 20 seconds.")

    def generate(self, room: Room) -> tuple[ModifierContext, ...]:
        requested: set[tuple[int, str, ModifierType]] = set()
        items: list[dict[str, Any]] = []
        for game_round in room.rounds:
            modifier = game_round.modifier
            if modifier is None:
                continue
            key = (game_round.number, game_round.question.id, modifier.type)
            requested.add(key)
            items.append(
                {
                    "round_number": game_round.number,
                    "question_id": game_round.question.id,
                    "question_prompt": game_round.question.prompt,
                    "category": game_round.question.category,
                    "values": list(game_round.question.values),
                    "modifier_type": modifier.type.value,
                    "modifier_title": modifier.title,
                    "canonical_instructions": modifier.instructions,
                }
            )

        if not items:
            return ()

        body = json.dumps(
            {"schema_version": 1, "modifiers": items},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}/v1/modifier-contexts",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "psychology-roulette/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ModifierContextError("The AI service is unavailable.") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModifierContextError("The AI service response is too large.")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModifierContextError("The AI service returned invalid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("contexts"), list):
            raise ModifierContextError("The AI service response has an invalid shape.")

        contexts: list[ModifierContext] = []
        seen: set[tuple[int, str, ModifierType]] = set()
        for raw_context in payload["contexts"]:
            if not isinstance(raw_context, dict):
                raise ModifierContextError("The AI service returned an invalid context.")
            try:
                round_number = raw_context["round_number"]
                question_id = raw_context["question_id"]
                modifier_type = ModifierType(raw_context["modifier_type"])
                raw_text = raw_context["context"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ModifierContextError("The AI service returned an invalid context.") from exc
            if type(round_number) is not int or not isinstance(question_id, str):
                raise ModifierContextError("The AI service returned invalid context identifiers.")
            if not isinstance(raw_text, str):
                raise ModifierContextError("The AI service returned non-text context.")
            context = " ".join(raw_text.split())
            if not context or len(context) > MAX_CONTEXT_LENGTH:
                raise ModifierContextError("The AI service returned context of an invalid length.")
            key = (round_number, question_id, modifier_type)
            if key not in requested or key in seen:
                raise ModifierContextError("The AI service returned an unexpected context.")
            seen.add(key)
            contexts.append(
                ModifierContext(
                    round_number=round_number,
                    question_id=question_id,
                    modifier_type=modifier_type,
                    text=context,
                )
            )
        return tuple(contexts)


def configured_modifier_context_provider() -> ModifierContextProvider | None:
    base_url = os.environ.get("PSYCHOLOGY_ROULETTE_AI_BASE_URL")
    if not base_url:
        return None
    token = os.environ.get("PSYCHOLOGY_ROULETTE_AI_TOKEN")
    if not token:
        raise RuntimeError(
            "PSYCHOLOGY_ROULETTE_AI_TOKEN is required when the AI service is enabled."
        )
    raw_timeout = os.environ.get("PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS", "12")
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS must be a number.") from exc
    try:
        return HttpModifierContextProvider(base_url, token, timeout)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def apply_modifier_contexts(room: Room, contexts: tuple[ModifierContext, ...]) -> None:
    """Attach copy only when it still matches the engine's deterministic plan."""
    by_key = {
        (context.round_number, context.question_id, context.modifier_type): context.text
        for context in contexts
    }
    for game_round in room.rounds:
        modifier = game_round.modifier
        if modifier is None:
            continue
        key = (game_round.number, game_round.question.id, modifier.type)
        if text := by_key.get(key):
            modifier.context = text
