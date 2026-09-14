from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from psychology_roulette.domain import (
    ModifierType,
    Question,
    RecapHighlight,
    Room,
    SessionRecap,
)

MAX_RESPONSE_BYTES = 128 * 1024
MAX_QUESTION_LENGTH = 320
MAX_COPY_LENGTH = 240
SUPPORTED_MODIFIERS = tuple(item.value for item in ModifierType)
VALUE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
YES_NO_STARTERS = frozenset(
    {
        "are",
        "can",
        "could",
        "did",
        "do",
        "does",
        "has",
        "have",
        "is",
        "may",
        "might",
        "must",
        "ought",
        "should",
        "was",
        "were",
        "will",
        "would",
    }
)
OPEN_ENDED_STARTERS = frozenset(
    {
        "describe",
        "explain",
        "how",
        "identify",
        "list",
        "name",
        "rank",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
    }
)


class AIServiceUnavailable(RuntimeError):
    """The configured Qwen service could not be reached or run."""


class AIInvalidOutput(RuntimeError):
    """Qwen exhausted its constrained retries without valid content."""


@dataclass(frozen=True, slots=True)
class GameContent:
    questions: tuple[Question, ...]


@dataclass(frozen=True, slots=True)
class RecapFact:
    id: str
    title: str
    value: str
    detail: str


class AIProvider(Protocol):
    def generate_game_content(
        self,
        *,
        round_count: int,
        avoid_questions: tuple[Question, ...],
    ) -> GameContent: ...

    def generate_session_recap(self, room: Room) -> SessionRecap: ...


@dataclass(frozen=True, slots=True)
class HttpAIProvider:
    base_url: str
    token: str
    timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
            raise ValueError("The AI service URL must use HTTPS (HTTP is allowed locally).")
        if not parsed.netloc:
            raise ValueError("The AI service URL must include a host.")
        if not self.token:
            raise ValueError("The AI service token cannot be empty.")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("The AI timeout must be greater than zero and at most 60 seconds.")

    def generate_game_content(
        self,
        *,
        round_count: int,
        avoid_questions: tuple[Question, ...],
    ) -> GameContent:
        payload = self._post(
            "/v1/game-content",
            {
                "schema_version": 1,
                "round_count": round_count,
                "avoid_prompts": [question.prompt for question in avoid_questions],
            },
        )
        raw_questions = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(raw_questions, list) or len(raw_questions) != round_count:
            raise AIInvalidOutput("The AI service returned the wrong number of questions.")

        avoided = {question.prompt.casefold().strip() for question in avoid_questions}
        prompts: set[str] = set()
        questions: list[Question] = []
        pack_id = uuid4().hex[:12]
        for index, raw in enumerate(raw_questions, start=1):
            if not isinstance(raw, dict):
                raise AIInvalidOutput("The AI service returned an invalid question.")
            prompt = _clean_text(raw.get("prompt"), MAX_QUESTION_LENGTH, minimum=20)
            if not prompt_supports_agreement_scale(prompt):
                raise AIInvalidOutput(
                    "The AI service returned a prompt that is not a statement or "
                    "yes/no question."
                )
            folded_prompt = prompt.casefold()
            if folded_prompt in prompts or folded_prompt in avoided:
                raise AIInvalidOutput("The AI service returned a duplicate question.")
            prompts.add(folded_prompt)

            category = _slug(raw.get("category"), "category")
            intensity = raw.get("intensity")
            if type(intensity) is not int or intensity not in {1, 2, 3}:
                raise AIInvalidOutput("The AI service returned an invalid intensity.")
            values = _unique_slugs(raw.get("values"), "values", minimum=2, maximum=4)
            questions.append(
                Question(
                    id=f"ai_{pack_id}_{index}",
                    prompt=prompt,
                    category=category,
                    intensity=intensity,
                    values=values,
                    modifiers_allowed=SUPPORTED_MODIFIERS,
                )
            )
        return GameContent(questions=tuple(questions))

    def generate_session_recap(self, room: Room) -> SessionRecap:
        facts, aliases = session_recap_facts(room)
        if len(facts) < 3:
            raise AIInvalidOutput("There are not enough verified facts for a recap.")
        payload = self._post(
            "/v1/session-recap",
            {
                "schema_version": 1,
                "facts": [
                    {
                        "id": fact.id,
                        "title": fact.title,
                        "value": fact.value,
                        "detail": fact.detail,
                    }
                    for fact in facts
                ],
            },
        )
        if not isinstance(payload, dict):
            raise AIInvalidOutput("The AI service returned an invalid recap.")
        headline = _replace_aliases(
            _clean_text(payload.get("headline"), 100, minimum=3), aliases
        )
        summary = _replace_aliases(
            _clean_text(payload.get("summary"), MAX_COPY_LENGTH, minimum=12), aliases
        )
        raw_highlights = payload.get("highlights")
        if not isinstance(raw_highlights, list) or not 3 <= len(raw_highlights) <= 6:
            raise AIInvalidOutput("The AI service returned an invalid highlight count.")

        facts_by_id = {fact.id: fact for fact in facts}
        seen: set[str] = set()
        highlights: list[RecapHighlight] = []
        for raw in raw_highlights:
            if not isinstance(raw, dict) or not isinstance(raw.get("fact_id"), str):
                raise AIInvalidOutput("The AI service returned an invalid highlight.")
            fact_id = raw["fact_id"]
            if fact_id in seen or fact_id not in facts_by_id:
                raise AIInvalidOutput("The AI service referenced an invalid recap fact.")
            seen.add(fact_id)
            fact = facts_by_id[fact_id]
            highlights.append(
                RecapHighlight(
                    fact_id=fact.id,
                    title=_replace_aliases(
                        _clean_text(raw.get("title"), 80, minimum=3), aliases
                    ),
                    value=_replace_aliases(fact.value, aliases),
                    detail=_replace_aliases(fact.detail, aliases),
                    commentary=_replace_aliases(
                        _clean_text(raw.get("commentary"), 180, minimum=8), aliases
                    ),
                )
            )
        return SessionRecap(
            headline=headline,
            summary=summary,
            highlights=tuple(highlights),
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "psychology-roulette/0.2",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            if exc.code == 422:
                raise AIInvalidOutput("Qwen could not produce valid content.") from exc
            raise AIServiceUnavailable("The AI service is unavailable.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AIServiceUnavailable("The AI service is unavailable.") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AIInvalidOutput("The AI service response is too large.")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIInvalidOutput("The AI service returned invalid JSON.") from exc
        if not isinstance(decoded, dict):
            raise AIInvalidOutput("The AI service response must be an object.")
        return decoded


def configured_ai_provider() -> AIProvider | None:
    base_url = os.environ.get("PSYCHOLOGY_ROULETTE_AI_BASE_URL")
    if not base_url:
        return None
    token = os.environ.get("PSYCHOLOGY_ROULETTE_AI_TOKEN")
    if not token:
        raise RuntimeError(
            "PSYCHOLOGY_ROULETTE_AI_TOKEN is required when the AI service is enabled."
        )
    raw_timeout = os.environ.get("PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS", "45")
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("PSYCHOLOGY_ROULETTE_AI_TIMEOUT_SECONDS must be a number.") from exc
    try:
        return HttpAIProvider(base_url, token, timeout)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def session_recap_facts(room: Room) -> tuple[tuple[RecapFact, ...], dict[str, str]]:
    analytics = room.session_summary()
    if analytics is None:
        raise AIInvalidOutput("A completed session is required for a recap.")
    aliases = {
        f"P{index}": room.players[item.player_id].name
        for index, item in enumerate(analytics.players, start=1)
    }
    player_alias = {
        item.player_id: f"P{index}" for index, item in enumerate(analytics.players, start=1)
    }
    widest = max(analytics.rounds, key=lambda item: item.position_range)
    consensus = min(analytics.rounds, key=lambda item: item.position_range)
    confident_round = max(analytics.rounds, key=lambda item: item.average_confidence)
    niche = max(analytics.players, key=lambda item: item.average_room_distance)
    npc = min(analytics.players, key=lambda item: item.average_room_distance)
    confident_player = max(analytics.players, key=lambda item: item.average_confidence)
    wide_lens = max(analytics.players, key=lambda item: item.position_span)

    facts = [
        RecapFact(
            "most_divisive",
            "Most divisive subject",
            f"Round {widest.number}",
            f'Range {widest.position_range}: "{widest.prompt}"',
        ),
        RecapFact(
            "strongest_consensus",
            "Strongest consensus",
            f"Round {consensus.number}",
            f'Range {consensus.position_range}: "{consensus.prompt}"',
        ),
        RecapFact(
            "highest_round_confidence",
            "Most certain round",
            f"Round {confident_round.number}",
            f"Average confidence {confident_round.average_confidence}%",
        ),
        RecapFact(
            "most_niche",
            "Furthest from the room",
            player_alias[niche.player_id],
            f"Average room distance {niche.average_room_distance}",
        ),
        RecapFact(
            "most_npc",
            "Closest to the room",
            player_alias[npc.player_id],
            f"Average room distance {npc.average_room_distance}",
        ),
        RecapFact(
            "highest_confidence",
            "Highest confidence",
            player_alias[confident_player.player_id],
            f"Average confidence {confident_player.average_confidence}%",
        ),
        RecapFact(
            "widest_personal_range",
            "Widest personal range",
            player_alias[wide_lens.player_id],
            f"Position span {wide_lens.position_span}",
        ),
    ]
    predictors = [item for item in analytics.players if item.prediction_score is not None]
    if predictors:
        best = max(predictors, key=lambda item: item.prediction_score or 0)
        worst = min(predictors, key=lambda item: item.prediction_score or 0)
        facts.extend(
            [
                RecapFact(
                    "best_predictor",
                    "Best predictor of the room",
                    player_alias[best.player_id],
                    f"Prediction score {best.prediction_score}",
                ),
                RecapFact(
                    "worst_predictor",
                    "Worst predictor of the room",
                    player_alias[worst.player_id],
                    f"Prediction score {worst.prediction_score}",
                ),
            ]
        )
    movers = [item for item in analytics.players if item.movement_total > 0]
    if movers:
        mover = max(movers, key=lambda item: item.movement_total)
        facts.append(
            RecapFact(
                "largest_mover",
                "Largest opinion movement",
                player_alias[mover.player_id],
                f"Total movement {mover.movement_total}",
            )
        )
    return tuple(facts), aliases


def _clean_text(value: Any, maximum: int, *, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise AIInvalidOutput("The AI service returned non-text content.")
    cleaned = " ".join(value.split())
    if not minimum <= len(cleaned) <= maximum:
        raise AIInvalidOutput("The AI service returned text of an invalid length.")
    return cleaned


def prompt_supports_agreement_scale(prompt: str) -> bool:
    """Accept declarative claims or single yes/no questions, never open prompts."""
    match = re.match(r"[A-Za-z]+", prompt)
    if match is None:
        return False
    first_word = match.group(0).casefold()
    if first_word in OPEN_ENDED_STARTERS:
        return False
    question_marks = prompt.count("?")
    if question_marks:
        return (
            question_marks == 1
            and prompt.endswith("?")
            and first_word in YES_NO_STARTERS
        )
    return True


def _slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or not VALUE_PATTERN.fullmatch(value):
        raise AIInvalidOutput(f"The AI service returned an invalid {field}.")
    return value


def _unique_slugs(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise AIInvalidOutput(f"The AI service returned an invalid {field} list.")
    items = tuple(_slug(item, field) for item in value)
    if len(set(items)) != len(items):
        raise AIInvalidOutput(f"The AI service returned duplicate {field} values.")
    return items


def _replace_aliases(value: str, aliases: dict[str, str]) -> str:
    for alias, player_name in sorted(aliases.items(), reverse=True):
        value = re.sub(rf"\b{re.escape(alias)}\b", player_name, value)
    return value
