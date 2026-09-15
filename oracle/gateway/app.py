from __future__ import annotations

import hmac
import json
import os
import re
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


class GeneratedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: StrictStr = Field(min_length=20, max_length=220)


YES_NO_STARTERS = frozenset(
    {
        "are", "can", "could", "did", "do", "does", "has", "have", "is",
        "may", "might", "must", "ought", "should", "was", "were", "will", "would",
    }
)
OPEN_ENDED_STARTERS = frozenset(
    {
        "ask", "choose", "compare", "consider", "describe", "discuss", "explain",
        "how", "identify", "imagine", "list", "name", "rank", "share", "tell",
        "what", "when", "where", "which", "who", "whom", "whose", "why",
    }
)


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


def _single_prompt_prompt(focus: str) -> str:
    return (
        f"/no_think. Write one clear, provocative opinion about {focus}. Use one grammatical "
        "sentence of 7 to 18 words. Avoid questions, definitions, obvious facts, and answer "
        "labels. Return JSON only."
    )


def _single_prompt_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 20,
                "maxLength": 220,
                "description": "One complete, natural, debatable opinion sentence.",
            },
        },
        "required": ["prompt"],
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


def _prompt_supports_agreement_scale(prompt: str) -> bool:
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


def _prompt_has_obvious_quality_issue(prompt: str) -> bool:
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", prompt.casefold())
    if not 7 <= len(words) <= 18 or prompt[-1:] not in {".", "?"}:
        return True
    if re.match(r"^(?:strongly\s+)?(?:agree|disagree)\s*:", prompt, re.IGNORECASE):
        return True
    if re.search(
        r"\b(?:should|could|would|must|can|may|might|will)\s+"
        r"(?:builds|creates|encourages|has|is|makes|matters|prioritizes|promotes|requires|"
        r"supports|values)\b",
        prompt,
        flags=re.IGNORECASE,
    ):
        return True
    concept_families = (
        ("responsibility", "responsible", "accountability", "accountable"),
        ("community", "communities"),
        ("fairness", "fair"),
        ("technology", "technological"),
        ("friendship", "friends"),
    )
    return any(sum(words.count(term) for term in family) > 1 for family in concept_families)


async def _generate_game_content_with_retries(
    request: GameContentRequest,
) -> GameContentResponse:
    base_url = os.environ.get("LLAMA_BASE_URL", "http://model:8080").rstrip("/")
    model = os.environ.get("LLAMA_MODEL", "qwen3-0.6b")
    try:
        timeout = float(os.environ.get("LLAMA_TIMEOUT_SECONDS", "60"))
        attempts = int(os.environ.get("QWEN_GENERATION_ATTEMPTS", "3"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Model runtime is misconfigured.") from exc
    attempts = min(max(attempts, 1), 5)
    accepted: list[GeneratedQuestion] = []
    avoided = {" ".join(item.split()).casefold() for item in request.avoid_prompts}
    focuses = (
        ("everyday_life", "whether planning makes daily life better", 1, ["comfort", "fairness"]),
        ("relationships", "honesty between close friends", 2, ["loyalty", "honesty"]),
        ("technology", "privacy versus convenience in technology", 2, ["privacy", "convenience"]),
        ("fairness", "equal outcomes versus earned rewards", 2, ["equality", "merit"]),
        ("community", "individual freedom versus community rules", 2, ["freedom", "responsibility"]),
        (
            "personal_responsibility",
            "forgiving someone who caused unintended harm",
            3,
            ["accountability", "compassion"],
        ),
        ("culture", "culture", 2, ["tradition", "change"]),
        ("work", "work", 1, ["ambition", "balance"]),
        ("identity", "identity", 3, ["authenticity", "belonging"]),
        ("future", "the future", 2, ["progress", "stability"]),
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        for index in range(request.round_count):
            category, focus, intensity, values = focuses[index % len(focuses)]
            for attempt in range(1, attempts + 1):
                correction = "" if attempt == 1 else (
                    " Try a different, simpler sentence."
                )
                model_request = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Write concise debate statements. Follow the JSON schema.",
                        },
                        {
                            "role": "user",
                            "content": _single_prompt_prompt(focus) + correction,
                        },
                    ],
                    "temperature": 0.95,
                    "top_p": 0.85,
                    "top_k": 30,
                    "min_p": 0,
                    "presence_penalty": 1.25,
                    "max_tokens": 90,
                    "cache_prompt": True,
                    "response_format": {
                        "type": "json_object",
                        "schema": _single_prompt_schema(),
                    },
                }
                try:
                    response = await client.post(
                        f"{base_url}/v1/chat/completions",
                        json=model_request,
                    )
                    response.raise_for_status()
                    model_payload = response.json()
                    content = model_payload["choices"][0]["message"]["content"]
                    payload = _extract_json(content)
                except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="The Qwen runtime is unavailable.",
                    ) from exc
                except (ValueError, json.JSONDecodeError):
                    payload = None

                rejected = "structure"
                try:
                    generated = GeneratedPrompt.model_validate(payload)
                except ValidationError:
                    pass
                else:
                    prompt = " ".join(generated.prompt.split())
                    normalized = " ".join(prompt.split()).casefold()
                    seen = avoided | {
                        " ".join(item.prompt.split()).casefold() for item in accepted
                    }
                    if normalized in seen:
                        rejected = "duplicate"
                    elif not _prompt_supports_agreement_scale(prompt):
                        rejected = "scale"
                    elif _prompt_has_obvious_quality_issue(prompt):
                        rejected = "quality"
                    else:
                        accepted.append(
                            GeneratedQuestion(
                                prompt=prompt,
                                category=category,
                                intensity=intensity,
                                values=values,
                            )
                        )
                        break
                print(
                    json.dumps(
                        {
                            "message": "Qwen question rejected",
                            "question_number": index + 1,
                            "attempt": attempt,
                            "reason": rejected,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail="Qwen could not produce valid content after constrained retries.",
                )

    return GameContentResponse(questions=accepted)


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
        for attempt in range(1, attempts + 1):
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
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                print(
                    json.dumps(
                        {
                            "message": "Qwen output rejected by semantic validation",
                            "attempt": attempt,
                            "reason": str(exc),
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
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
    return await _generate_game_content_with_retries(request)


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
