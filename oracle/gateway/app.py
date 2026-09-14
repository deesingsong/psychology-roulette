from __future__ import annotations

import hmac
import json
import os
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictStr

bearer = HTTPBearer(auto_error=False)
app = FastAPI(
    title="Psychology Roulette AI Gateway",
    description="A narrow authenticated boundary around the local Qwen runtime.",
    version="0.1.0",
)


class ModifierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_number: int = Field(ge=1, le=20)
    question_id: str = Field(min_length=1, max_length=80)
    question_prompt: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=80)
    values: list[str] = Field(max_length=8)
    modifier_type: Literal[
        "predict_room",
        "secret_principle",
        "devils_advocate",
        "steelman",
        "change_my_mind",
    ]
    modifier_title: str = Field(min_length=1, max_length=80)
    canonical_instructions: str = Field(min_length=1, max_length=500)


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    modifiers: list[ModifierInput] = Field(min_length=1, max_length=10)


class ContextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_number: int
    question_id: str
    modifier_type: str
    context: str = Field(min_length=1, max_length=280)


class ContextResponse(BaseModel):
    contexts: list[ContextOutput]


class GeneratedContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contexts: list[StrictStr] = Field(min_length=1, max_length=10)


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


def _prompt(request: ContextRequest) -> str:
    source = [
        {
            "question_prompt": item.question_prompt,
            "category": item.category,
            "values": item.values,
            "modifier_title": item.modifier_title,
            "canonical_instructions": item.canonical_instructions,
        }
        for item in request.modifiers
    ]
    return (
        "/no_think. "
        "Return JSON with one concise discussion angle for every input item, in "
        "the exact same order. The contexts array must contain exactly "
        f"{len(source)} strings. "
        "Each angle must be one sentence and at most 220 characters. It may pose a "
        "question or name a tension in the topic. It must not invent or change rules, "
        "select players, assign targets, mention scoring, or answer the question. "
        "Return only an object with a top-level contexts array of strings. INPUT: "
        + json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    )


def _context_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "contexts": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 220},
                "minItems": count,
                "maxItems": count,
            }
        },
        "required": ["contexts"],
        "additionalProperties": False,
    }


def _extract_json(text: str) -> Any:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model response.")
    return json.loads(text[start : end + 1])


def _validate_model_output(payload: Any, request: ContextRequest) -> ContextResponse:
    generated = GeneratedContextResponse.model_validate(payload)
    if len(generated.contexts) != len(request.modifiers):
        raise ValueError("Model response context count did not match the request.")
    contexts = [
        ContextOutput(
            round_number=item.round_number,
            question_id=item.question_id,
            modifier_type=item.modifier_type,
            context=" ".join(context.split()),
        )
        for item, context in zip(request.modifiers, generated.contexts, strict=True)
    ]
    return ContextResponse(contexts=contexts)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/modifier-contexts", response_model=ContextResponse)
async def modifier_contexts(
    request: ContextRequest,
    _authorized: Annotated[None, Depends(require_gateway_token)],
) -> ContextResponse:
    base_url = os.environ.get("LLAMA_BASE_URL", "http://model:8080").rstrip("/")
    model = os.environ.get("LLAMA_MODEL", "qwen3-0.6b")
    try:
        timeout = float(os.environ.get("LLAMA_TIMEOUT_SECONDS", "45"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Model runtime is misconfigured.") from exc

    model_request = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write neutral, thought-provoking discussion angles for a "
                    "social party game. Follow the requested JSON format exactly."
                ),
            },
            {"role": "user", "content": _prompt(request)},
        ],
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0,
        "presence_penalty": 1.5,
        "max_tokens": 400,
        "cache_prompt": True,
        "response_format": {
            "type": "json_object",
            "schema": _context_schema(len(request.modifiers)),
        },
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json=model_request,
            )
            response.raise_for_status()
            model_payload = response.json()
        content = model_payload["choices"][0]["message"]["content"]
        return _validate_model_output(_extract_json(content), request)
    except (
        httpx.HTTPError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="Model output was unavailable or invalid.",
        ) from exc
