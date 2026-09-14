from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

bearer = HTTPBearer(auto_error=False)
app = FastAPI(
    title="Are You Niche or NPC? AI Gateway",
    description="Two narrow, authenticated Qwen capabilities for the party game.",
    version="0.2.0",
)

ModifierName = Literal[
    "predict_room",
    "secret_principle",
    "devils_advocate",
    "steelman",
    "change_my_mind",
]


class GameContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    round_count: int = Field(ge=1, le=10)
    avoid_prompts: list[str] = Field(default_factory=list, max_length=30)


class GeneratedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: StrictStr = Field(min_length=20, max_length=320)
    category: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    intensity: Literal[1, 2, 3]
    values: list[StrictStr] = Field(min_length=2, max_length=4)
    discussion_prompt: StrictStr = Field(min_length=12, max_length=240)


class GameContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[GeneratedQuestion]


class RecapFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,59}$")
    title: str = Field(min_length=3, max_length=80)
    value: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=500)


class SessionRecapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    facts: list[RecapFactInput] = Field(min_length=3, max_length=16)


class GeneratedHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: StrictStr
    title: StrictStr = Field(min_length=3, max_length=80)
    commentary: StrictStr = Field(min_length=8, max_length=180)


class SessionRecapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: StrictStr = Field(min_length=3, max_length=100)
    summary: StrictStr = Field(min_length=12, max_length=240)
    highlights: list[GeneratedHighlight]


def require_gateway_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
) -> None:
    expected = os.environ.get("GATEWAY_TOKEN", "")
    supplied = credentials.credentials if credentials else ""
    if (
        not expected
        or credentials is None
        or credentials.scheme.casefold() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid gateway credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _game_content_prompt(request: GameContentRequest) -> str:
    return (
        "/no_think. Create a fresh party-game pack with exactly "
        f"{request.round_count} original statements. Players rate agreement from -100 "
        "to +100. Every prompt must be debatable, understandable without specialist "
        "knowledge, non-diagnostic, and meaningfully different from the others. Use at "
        "several categories across the pack. Avoid trivia, personal-data requests, "
        "graphic harm, targeted politics, and a plainly correct answer. A discussion_prompt "
        "is one concise question that opens the value tension and can also serve as a "
        "surprise-round angle; it must not invent rules, targets, or scores. Values are 2-4 "
        "lowercase snake_case principles. Return only the required JSON object. Do not "
        "repeat these "
        "existing prompts: "
        + json.dumps(request.avoid_prompts, ensure_ascii=False, separators=(",", ":"))
    )


def _game_content_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "minLength": 20, "maxLength": 320},
                        "category": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]{1,39}$",
                        },
                        "intensity": {"type": "integer", "enum": [1, 2, 3]},
                        "values": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "uniqueItems": True,
                            "items": {
                                "type": "string",
                                "pattern": "^[a-z][a-z0-9_]{1,39}$",
                            },
                        },
                        "discussion_prompt": {
                            "type": "string",
                            "minLength": 12,
                            "maxLength": 240,
                        },
                    },
                    "required": [
                        "prompt",
                        "category",
                        "intensity",
                        "values",
                        "discussion_prompt",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }


def _recap_prompt(request: SessionRecapRequest, highlight_count: int) -> str:
    return (
        "/no_think. Build a lively end-of-game board from verified facts. Select exactly "
        f"{highlight_count} distinct, genuinely interesting facts. Refer to a fact only by "
        "its exact id. You may interpret and connect facts, but never alter a number, claim "
        "a diagnosis, or invent a result. P1, P2, and similar tokens are anonymous player "
        "aliases and may be used naturally. Titles should be playful and concise; commentary "
        "must be kind enough for friends to laugh about. Return only the required JSON object. "
        "FACTS: "
        + json.dumps(
            [item.model_dump() for item in request.facts],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _recap_schema(fact_ids: list[str], count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "minLength": 3, "maxLength": 100},
            "summary": {"type": "string", "minLength": 12, "maxLength": 240},
            "highlights": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_id": {"type": "string", "enum": fact_ids},
                        "title": {"type": "string", "minLength": 3, "maxLength": 80},
                        "commentary": {
                            "type": "string",
                            "minLength": 8,
                            "maxLength": 180,
                        },
                    },
                    "required": ["fact_id", "title", "commentary"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["headline", "summary", "highlights"],
        "additionalProperties": False,
    }


def _extract_json(text: str) -> Any:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model response.")
    return json.loads(text[start : end + 1])


def _validate_game_content(payload: Any, request: GameContentRequest) -> GameContentResponse:
    generated = GameContentResponse.model_validate(payload)
    if len(generated.questions) != request.round_count:
        raise ValueError("Question count did not match the request.")
    prompts = {" ".join(item.prompt.split()).casefold() for item in generated.questions}
    avoided = {" ".join(item.split()).casefold() for item in request.avoid_prompts}
    if len(prompts) != request.round_count or prompts & avoided:
        raise ValueError("Question pack contained a duplicate prompt.")
    discussions = {
        " ".join(item.discussion_prompt.split()).casefold() for item in generated.questions
    }
    if len(discussions) != request.round_count:
        raise ValueError("Question pack contained duplicate discussion prompts.")
    for item in generated.questions:
        if len(set(item.values)) != len(item.values):
            raise ValueError("Question values must be unique.")
    return generated


def _validate_recap(
    payload: Any,
    request: SessionRecapRequest,
    highlight_count: int,
) -> SessionRecapResponse:
    generated = SessionRecapResponse.model_validate(payload)
    if len(generated.highlights) != highlight_count:
        raise ValueError("Highlight count did not match the request.")
    allowed = {fact.id for fact in request.facts}
    selected = [item.fact_id for item in generated.highlights]
    if len(set(selected)) != len(selected) or not set(selected).issubset(allowed):
        raise ValueError("Recap selected duplicate or unknown facts.")
    return generated


T = TypeVar("T", bound=BaseModel)


async def _generate_with_retries(
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    validator: Callable[[Any], T],
    max_tokens: int,
    temperature: float,
) -> T:
    base_url = os.environ.get("LLAMA_BASE_URL", "http://model:8080").rstrip("/")
    model = os.environ.get("LLAMA_MODEL", "qwen3-0.6b")
    try:
        timeout = float(os.environ.get("LLAMA_TIMEOUT_SECONDS", "60"))
        attempts = int(os.environ.get("QWEN_GENERATION_ATTEMPTS", "3"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Model runtime is misconfigured.") from exc
    attempts = min(max(attempts, 1), 5)
    correction = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _attempt in range(attempts):
            model_request = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt + correction},
                ],
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 30,
                "min_p": 0,
                "presence_penalty": 1.25,
                "max_tokens": max_tokens,
                "cache_prompt": True,
                "response_format": {"type": "json_object", "schema": schema},
            }
            try:
                response = await client.post(
                    f"{base_url}/v1/chat/completions",
                    json=model_request,
                )
                response.raise_for_status()
                model_payload = response.json()
                content = model_payload["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="The Qwen runtime is unavailable.",
                ) from exc
            try:
                return validator(_extract_json(content))
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
                correction = (
                    " Your prior attempt failed semantic validation. Produce a completely "
                    "new result and obey every count, uniqueness, and allowed-value rule."
                )
    raise HTTPException(
        status_code=422,
        detail="Qwen could not produce valid content after constrained retries.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/game-content", response_model=GameContentResponse)
async def game_content(
    request: GameContentRequest,
    _authorized: Annotated[None, Depends(require_gateway_token)],
) -> GameContentResponse:
    return await _generate_with_retries(
        system=(
            "You design fresh, safe, high-replay-value discussion content for the party "
            "game Are You Niche or NPC?. Follow the JSON schema exactly."
        ),
        prompt=_game_content_prompt(request),
        schema=_game_content_schema(request.round_count),
        validator=lambda payload: _validate_game_content(payload, request),
        max_tokens=750,
        temperature=0.95,
    )


@app.post("/v1/session-recap", response_model=SessionRecapResponse)
async def session_recap(
    request: SessionRecapRequest,
    _authorized: Annotated[None, Depends(require_gateway_token)],
) -> SessionRecapResponse:
    highlight_count = min(5, len(request.facts))
    return await _generate_with_retries(
        system=(
            "You curate an accurate, playful end board for Are You Niche or NPC?. "
            "Use only verified facts and follow the JSON schema exactly."
        ),
        prompt=_recap_prompt(request, highlight_count),
        schema=_recap_schema([fact.id for fact in request.facts], highlight_count),
        validator=lambda payload: _validate_recap(payload, request, highlight_count),
        max_tokens=650,
        temperature=0.75,
    )
