from __future__ import annotations

import hmac
import json
import os
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

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
    source = [item.model_dump() for item in request.modifiers]
    return (
        "/no_think. "
        "Return JSON with one concise discussion angle for every input item. "
        "Each angle must be one sentence and at most 220 characters. It may pose a "
        "question or name a tension in the topic. It must not invent or change rules, "
        "select players, assign targets, mention scoring, or give an answer to the "
        "question. Preserve round_number, question_id, and modifier_type exactly. "
        "Return only an object with a top-level contexts array. Every array item "
        "must contain round_number, question_id, modifier_type, and context. INPUT: "
        + json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    )


def _extract_json(text: str) -> Any:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model response.")
    return json.loads(text[start : end + 1])


def _validate_model_output(payload: Any, request: ContextRequest) -> ContextResponse:
    response = ContextResponse.model_validate(payload)
    expected = {
        (item.round_number, item.question_id, item.modifier_type)
        for item in request.modifiers
    }
    received = {
        (item.round_number, item.question_id, item.modifier_type)
        for item in response.contexts
    }
    if received != expected or len(response.contexts) != len(expected):
        raise ValueError("Model response identifiers did not match the request.")
    cleaned = [
        ContextOutput(
            round_number=item.round_number,
            question_id=item.question_id,
            modifier_type=item.modifier_type,
            context=" ".join(item.context.split()),
        )
        for item in response.contexts
    ]
    return ContextResponse(contexts=cleaned)


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
        "response_format": {"type": "json_object"},
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
