from __future__ import annotations

import hashlib
import logging
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, StrictInt, StrictStr

from psychology_roulette.ai import (
    AIInvalidOutput,
    AIProvider,
    AIServiceUnavailable,
    GameContent,
    configured_ai_provider,
)
from psychology_roulette.domain import (
    ContentStatus,
    GameError,
    ModifierTiming,
    ModifierType,
    Room,
    RoomPhase,
)
from psychology_roulette.store import (
    RoomAccessDenied,
    RoomRepository,
    UnknownAccessToken,
    default_store,
)

bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


def require_access_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    if (
        credentials is None
        or credentials.scheme.casefold() != "bearer"
        or not credentials.credentials
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid room access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


AccessToken = Annotated[str, Depends(require_access_token)]


class CreateRoomRequest(BaseModel):
    host_name: str = Field(min_length=1, max_length=24)


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=24)


class SubmitAnswerRequest(BaseModel):
    position: int
    confidence: int = Field(ge=0, le=100)


class SubmitModifierRequest(BaseModel):
    value: StrictInt | StrictStr


class PlayerView(BaseModel):
    name: str
    is_host: bool
    has_answered: bool
    has_modifier_submitted: bool


class QuestionView(BaseModel):
    id: str
    prompt: str
    category: str
    intensity: int
    values: list[str]


class RevealedAnswerView(BaseModel):
    player_name: str
    position: int
    confidence: int


class ModifierResultView(BaseModel):
    player_name: str
    value: int | str
    score: int | None = None
    movement: int | None = None


class ModifierView(BaseModel):
    type: ModifierType
    timing: ModifierTiming
    title: str
    instructions: str
    target_player_name: str | None
    source_player_name: str | None
    options: list[int | str]
    submissions_count: int
    required_submissions: int
    results: list[ModifierResultView] | None


class RoundAnalyticsView(BaseModel):
    number: int
    prompt: str
    average_position: float
    average_confidence: float
    position_range: int


class PlayerAnalyticsView(BaseModel):
    player_name: str
    title: str
    title_description: str
    rounds_answered: int
    average_position: float
    average_confidence: float
    average_room_distance: float
    position_span: int
    prediction_score: float | None
    movement_total: int


class SessionAnalyticsView(BaseModel):
    rounds_completed: int
    overall_average_position: float
    overall_average_confidence: float
    widest_round_number: int
    total_position_changes: int
    rounds: list[RoundAnalyticsView]
    players: list[PlayerAnalyticsView]


class RecapHighlightView(BaseModel):
    fact_id: str
    title: str
    value: str
    detail: str
    commentary: str


class SessionRecapView(BaseModel):
    headline: str
    summary: str
    highlights: list[RecapHighlightView]


class RoomView(BaseModel):
    code: str
    phase: RoomPhase
    content_status: ContentStatus
    content_generation_started_at: float | None
    players: list[PlayerView]
    round_number: int | None
    round_count: int
    question: QuestionView | None
    revealed_answers: list[RevealedAnswerView] | None
    summary: dict[str, float | int] | None
    modifier: ModifierView | None
    session_summary: SessionAnalyticsView | None
    session_recap: SessionRecapView | None


class ResumedRoomSessionView(BaseModel):
    player_id: str
    player_name: str
    is_host: bool
    room: RoomView


class RoomSessionView(ResumedRoomSessionView):
    access_token: str


@dataclass(frozen=True, slots=True)
class EntryRateLimits:
    create_limit: int = 10
    create_window_seconds: int = 600
    join_limit: int = 30
    join_window_seconds: int = 60

    def __post_init__(self) -> None:
        if (
            min(
                self.create_limit,
                self.create_window_seconds,
                self.join_limit,
                self.join_window_seconds,
            )
            < 1
        ):
            raise ValueError("Entry rate limits and windows must be positive.")

    @classmethod
    def from_environment(cls) -> EntryRateLimits:
        def value(name: str, default: int) -> int:
            raw_value = os.environ.get(name)
            if raw_value is None:
                return default
            try:
                parsed = int(raw_value)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be an integer.") from exc
            if parsed < 1:
                raise RuntimeError(f"{name} must be positive.")
            return parsed

        return cls(
            create_limit=value("PSYCHOLOGY_ROULETTE_CREATE_LIMIT", 10),
            create_window_seconds=value("PSYCHOLOGY_ROULETTE_CREATE_WINDOW_SECONDS", 600),
            join_limit=value("PSYCHOLOGY_ROULETTE_JOIN_LIMIT", 30),
            join_window_seconds=value("PSYCHOLOGY_ROULETTE_JOIN_WINDOW_SECONDS", 60),
        )


def room_view(room: Room) -> RoomView:
    current_round = room.current_round
    answers_visible = room.phase in {
        RoomPhase.REVEAL,
        RoomPhase.FOLLOW_UP,
        RoomPhase.FOLLOW_UP_REVEAL,
        RoomPhase.COMPLETE,
    }

    revealed_answers = None
    if answers_visible and current_round:
        revealed_answers = [
            RevealedAnswerView(
                player_name=room.players[answer.player_id].name,
                position=answer.position,
                confidence=answer.confidence,
            )
            for answer in current_round.answers.values()
        ]

    question = None
    if current_round:
        question = QuestionView(
            id=current_round.question.id,
            prompt=current_round.question.prompt,
            category=current_round.question.category,
            intensity=current_round.question.intensity,
            values=list(current_round.question.values),
        )

    modifier_view = None
    if (
        current_round
        and current_round.modifier
        and room.phase
        in {
            RoomPhase.MODIFIER,
            RoomPhase.REVEAL,
            RoomPhase.FOLLOW_UP,
            RoomPhase.FOLLOW_UP_REVEAL,
            RoomPhase.COMPLETE,
        }
    ):
        modifier = current_round.modifier
        results = None
        results_visible = answers_visible and not (
            modifier.type in {ModifierType.STEELMAN, ModifierType.CHANGE_MY_MIND}
            and room.phase not in {RoomPhase.FOLLOW_UP_REVEAL, RoomPhase.COMPLETE}
        )
        if results_visible:
            results = [
                ModifierResultView(
                    player_name=room.players[player_id].name,
                    value=modifier.results[player_id].value,
                    score=modifier.results[player_id].score,
                    movement=modifier.results[player_id].movement,
                )
                for player_id in room.players
                if player_id in modifier.results
            ]
        modifier_view = ModifierView(
            type=modifier.type,
            timing=modifier.timing,
            title=modifier.title,
            instructions=modifier.instructions,
            target_player_name=(
                room.players[modifier.target_player_id].name
                if modifier.target_player_id is not None
                else None
            ),
            source_player_name=(
                room.players[modifier.source_player_id].name
                if modifier.source_player_id is not None
                else None
            ),
            options=list(modifier.options),
            submissions_count=len(modifier.submissions),
            required_submissions=len(room.modifier_required_player_ids()),
            results=results,
        )

    session_summary = None
    if analytics := room.session_summary():
        session_summary = SessionAnalyticsView(
            rounds_completed=analytics.rounds_completed,
            overall_average_position=analytics.overall_average_position,
            overall_average_confidence=analytics.overall_average_confidence,
            widest_round_number=analytics.widest_round_number,
            total_position_changes=analytics.total_position_changes,
            rounds=[
                RoundAnalyticsView(
                    number=item.number,
                    prompt=item.prompt,
                    average_position=item.average_position,
                    average_confidence=item.average_confidence,
                    position_range=item.position_range,
                )
                for item in analytics.rounds
            ],
            players=[
                PlayerAnalyticsView(
                    player_name=room.players[item.player_id].name,
                    title=item.title,
                    title_description=item.title_description,
                    rounds_answered=item.rounds_answered,
                    average_position=item.average_position,
                    average_confidence=item.average_confidence,
                    average_room_distance=item.average_room_distance,
                    position_span=item.position_span,
                    prediction_score=item.prediction_score,
                    movement_total=item.movement_total,
                )
                for item in analytics.players
            ],
        )

    session_recap = None
    if room.session_recap is not None:
        session_recap = SessionRecapView(
            headline=room.session_recap.headline,
            summary=room.session_recap.summary,
            highlights=[
                RecapHighlightView(
                    fact_id=item.fact_id,
                    title=item.title,
                    value=item.value,
                    detail=item.detail,
                    commentary=item.commentary,
                )
                for item in room.session_recap.highlights
            ],
        )

    return RoomView(
        code=room.code,
        phase=room.phase,
        content_status=room.content_status,
        content_generation_started_at=room.content_generation_started_at,
        players=[
            PlayerView(
                name=player.name,
                is_host=player.is_host,
                has_answered=bool(current_round and player.id in current_round.answers),
                has_modifier_submitted=bool(
                    current_round
                    and current_round.modifier
                    and player.id in current_round.modifier.submissions
                ),
            )
            for player in room.players.values()
        ],
        round_number=current_round.number if current_round else None,
        round_count=len(room.rounds),
        question=question,
        revealed_answers=revealed_answers,
        summary=room.round_summary(),
        modifier=modifier_view,
        session_summary=session_summary,
        session_recap=session_recap,
    )


CONTENT_GENERATION_STALE_SECONDS = 90


def _begin_content_generation(room: Room, player_id: str, started_at: float) -> None:
    player = room.players.get(player_id)
    if not player or not player.is_host:
        raise GameError("Only the host can do that.")
    if room.phase is not RoomPhase.LOBBY:
        raise GameError("This game has already started.")
    if room.content_status in {ContentStatus.READY, ContentStatus.FALLBACK}:
        return
    if (
        room.content_status is ContentStatus.GENERATING
        and room.content_generation_started_at is not None
        and started_at - room.content_generation_started_at
        < CONTENT_GENERATION_STALE_SECONDS
    ):
        return
    room.content_status = ContentStatus.GENERATING
    room.content_generation_started_at = started_at


def _finish_content_generation(
    room: Room,
    player_id: str,
    started_at: float,
    status: ContentStatus,
    content: GameContent | None = None,
) -> None:
    player = room.players.get(player_id)
    if not player or not player.is_host:
        raise GameError("Only the host can do that.")
    if (
        room.phase is not RoomPhase.LOBBY
        or room.content_status is not ContentStatus.GENERATING
        or room.content_generation_started_at != started_at
    ):
        return
    if content is not None:
        room.questions = list(content.questions)
    room.content_status = status
    room.content_generation_started_at = None


def _start_prepared_room(room: Room, player_id: str, *, ai_enabled: bool) -> None:
    if room.content_status is ContentStatus.PENDING and not ai_enabled:
        room.content_status = ContentStatus.FALLBACK
    if room.content_status not in {ContentStatus.READY, ContentStatus.FALLBACK}:
        if room.content_status is ContentStatus.ERROR:
            raise GameError("Question preparation failed. Retry it before starting.")
        raise GameError("The questions are still being prepared.")
    room.start(host_id=player_id)


def _advance_with_recap(room: Room, player_id: str, recap) -> None:
    room.advance(host_id=player_id)
    if room.phase is RoomPhase.COMPLETE:
        room.session_recap = recap


def create_app(
    store: RoomRepository | None = None,
    *,
    entry_rate_limits: EntryRateLimits | None = None,
    ai_provider: AIProvider | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Are You Niche or NPC? API",
        description="Authoritative multiplayer game server.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    active_store = store if store is not None else default_store
    active_rate_limits = entry_rate_limits or EntryRateLimits.from_environment()
    active_ai_provider = ai_provider if ai_provider is not None else configured_ai_provider()

    def enforce_entry_limit(request: Request, action: str) -> None:
        client_host = request.client.host if request.client else "unknown"
        client_fingerprint = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
        if action == "create":
            limit = active_rate_limits.create_limit
            window_seconds = active_rate_limits.create_window_seconds
        else:
            limit = active_rate_limits.join_limit
            window_seconds = active_rate_limits.join_window_seconds
        retry_after = active_store.consume_rate_limit(
            f"{action}:{client_fingerprint}",
            limit=limit,
            window_seconds=window_seconds,
        )
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many room entry attempts. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    @app.middleware("http")
    async def prevent_session_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/session" or request.url.path.startswith("/api/rooms"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(GameError)
    async def game_error_handler(_request: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UnknownAccessToken)
    async def unknown_access_token_handler(
        _request: Request,
        _exc: UnknownAccessToken,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "A valid room access token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(RoomAccessDenied)
    async def room_access_denied_handler(
        _request: Request,
        _exc: RoomAccessDenied,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": "This access token does not belong to that room."},
        )

    @app.exception_handler(AIInvalidOutput)
    async def invalid_ai_output_handler(
        _request: Request,
        _exc: AIInvalidOutput,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Qwen could not create valid game content after several attempts. "
                    "Please try again."
                )
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/rooms", response_model=RoomSessionView, status_code=201)
    def create_room(
        request: CreateRoomRequest,
        http_request: Request,
        access_token: AccessToken,
    ) -> RoomSessionView:
        enforce_entry_limit(http_request, "create")
        room, host, access_token = active_store.create_room(
            request.host_name,
            access_token,
        )
        return RoomSessionView(
            access_token=access_token,
            player_id=host.id,
            player_name=host.name,
            is_host=host.is_host,
            room=room_view(room),
        )

    @app.get("/api/session", response_model=ResumedRoomSessionView)
    def resume_session(access_token: AccessToken) -> ResumedRoomSessionView:
        room, player = active_store.resume(access_token)
        return ResumedRoomSessionView(
            player_id=player.id,
            player_name=player.name,
            is_host=player.is_host,
            room=room_view(room),
        )

    @app.get("/api/rooms/{code}", response_model=RoomView)
    def inspect_room(code: str, access_token: AccessToken) -> RoomView:
        room, _player = active_store.read(code, access_token)
        return room_view(room)

    @app.post("/api/rooms/{code}/players", response_model=RoomSessionView, status_code=201)
    def join_room(
        code: str,
        request: JoinRoomRequest,
        http_request: Request,
        access_token: AccessToken,
    ) -> RoomSessionView:
        enforce_entry_limit(http_request, "join")
        room, player, access_token = active_store.join_room(
            code,
            request.name,
            access_token,
        )
        return RoomSessionView(
            access_token=access_token,
            player_id=player.id,
            player_name=player.name,
            is_host=player.is_host,
            room=room_view(room),
        )

    @app.post("/api/rooms/{code}/start", response_model=RoomView)
    def start_room(code: str, access_token: AccessToken) -> RoomView:
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: _start_prepared_room(
                active_room,
                player_id,
                ai_enabled=active_ai_provider is not None,
            ),
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/prepare", response_model=RoomView)
    def prepare_room(code: str, access_token: AccessToken) -> RoomView:
        started_at = time.time()
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: _begin_content_generation(
                active_room, player_id, started_at
            ),
        )
        if (
            room.content_status is not ContentStatus.GENERATING
            or room.content_generation_started_at != started_at
        ):
            return room_view(room)

        if active_ai_provider is None:
            room = active_store.mutate(
                code,
                access_token,
                lambda active_room, player_id: _finish_content_generation(
                    active_room,
                    player_id,
                    started_at,
                    ContentStatus.FALLBACK,
                ),
            )
            return room_view(room)

        try:
            content = active_ai_provider.generate_game_content(
                round_count=6,
                avoid_questions=tuple(room.questions),
            )
        except AIServiceUnavailable:
            logger.exception("Qwen is unavailable; keeping the curated question pack.")
            room = active_store.mutate(
                code,
                access_token,
                lambda active_room, player_id: _finish_content_generation(
                    active_room,
                    player_id,
                    started_at,
                    ContentStatus.FALLBACK,
                ),
            )
            return room_view(room)
        except AIInvalidOutput:
            active_store.mutate(
                code,
                access_token,
                lambda active_room, player_id: _finish_content_generation(
                    active_room,
                    player_id,
                    started_at,
                    ContentStatus.ERROR,
                ),
            )
            raise

        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: _finish_content_generation(
                active_room,
                player_id,
                started_at,
                ContentStatus.READY,
                content,
            ),
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/answers", response_model=RoomView)
    def submit_answer(
        code: str,
        request: SubmitAnswerRequest,
        access_token: AccessToken,
    ) -> RoomView:
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: active_room.submit_answer(
                player_id=player_id,
                position=request.position,
                confidence=request.confidence,
            ),
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/modifier-submissions", response_model=RoomView)
    def submit_modifier(
        code: str,
        request: SubmitModifierRequest,
        access_token: AccessToken,
    ) -> RoomView:
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: active_room.submit_modifier(
                player_id=player_id,
                value=request.value,
            ),
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/reveal", response_model=RoomView)
    def reveal_round(code: str, access_token: AccessToken) -> RoomView:
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: active_room.reveal(host_id=player_id),
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/advance", response_model=RoomView)
    def advance_round(code: str, access_token: AccessToken) -> RoomView:
        if active_ai_provider is not None:
            preview, player = active_store.read(code, access_token)
            candidate = deepcopy(preview)
            candidate.advance(host_id=player.id)
            if candidate.phase is RoomPhase.COMPLETE:
                try:
                    recap = active_ai_provider.generate_session_recap(candidate)
                except AIServiceUnavailable:
                    logger.exception(
                        "Qwen is unavailable; completing with deterministic statistics."
                    )
                else:
                    room = active_store.mutate(
                        code,
                        access_token,
                        lambda active_room, player_id: _advance_with_recap(
                            active_room, player_id, recap
                        ),
                    )
                    return room_view(room)
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: active_room.advance(host_id=player_id),
        )
        return room_view(room)

    @app.delete("/api/rooms/{code}", status_code=status.HTTP_204_NO_CONTENT)
    def end_room(code: str, access_token: AccessToken) -> Response:
        active_store.end_room(code, access_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/")
    def root() -> dict[str, str]:
        raise HTTPException(status_code=404, detail="Run the web client on port 5173.")

    return app


app = create_app()
