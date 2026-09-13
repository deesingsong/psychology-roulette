from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, StrictInt, StrictStr

from psychology_roulette.domain import (
    GameError,
    ModifierTiming,
    ModifierType,
    Room,
    RoomPhase,
)
from psychology_roulette.store import (
    RoomAccessDenied,
    RoomStore,
    UnknownAccessToken,
    default_store,
)

bearer_scheme = HTTPBearer(auto_error=False)


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


class ModifierView(BaseModel):
    type: ModifierType
    timing: ModifierTiming
    title: str
    instructions: str
    target_player_name: str | None
    options: list[int | str]
    submissions_count: int
    required_submissions: int
    results: list[ModifierResultView] | None


class RoomView(BaseModel):
    code: str
    phase: RoomPhase
    players: list[PlayerView]
    round_number: int | None
    round_count: int
    question: QuestionView | None
    revealed_answers: list[RevealedAnswerView] | None
    summary: dict[str, float | int] | None
    modifier: ModifierView | None


class ResumedRoomSessionView(BaseModel):
    player_id: str
    player_name: str
    is_host: bool
    room: RoomView


class RoomSessionView(ResumedRoomSessionView):
    access_token: str


def room_view(room: Room) -> RoomView:
    current_round = room.current_round
    answers_visible = room.phase in {RoomPhase.REVEAL, RoomPhase.COMPLETE}

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
            RoomPhase.COMPLETE,
        }
    ):
        modifier = current_round.modifier
        results = None
        if answers_visible:
            results = [
                ModifierResultView(
                    player_name=room.players[player_id].name,
                    value=modifier.results[player_id].value,
                    score=modifier.results[player_id].score,
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
            options=list(modifier.options),
            submissions_count=len(modifier.submissions),
            required_submissions=(
                len(room.players) if modifier.timing is ModifierTiming.PRE_REVEAL else 0
            ),
            results=results,
        )

    return RoomView(
        code=room.code,
        phase=room.phase,
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
    )


def create_app(store: RoomStore | None = None) -> FastAPI:
    app = FastAPI(
        title="Psychology Roulette API",
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

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/rooms", response_model=RoomSessionView, status_code=201)
    def create_room(
        request: CreateRoomRequest,
        access_token: AccessToken,
    ) -> RoomSessionView:
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
        access_token: AccessToken,
    ) -> RoomSessionView:
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
            lambda active_room, player_id: active_room.start(host_id=player_id),
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
        room = active_store.mutate(
            code,
            access_token,
            lambda active_room, player_id: active_room.advance(host_id=player_id),
        )
        return room_view(room)

    @app.get("/")
    def root() -> dict[str, str]:
        raise HTTPException(status_code=404, detail="Run the web client on port 5173.")

    return app


app = create_app()
