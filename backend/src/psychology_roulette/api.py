from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from psychology_roulette.domain import GameError, Room, RoomPhase
from psychology_roulette.store import RoomStore, default_store


class CreateRoomRequest(BaseModel):
    host_name: str = Field(min_length=1, max_length=24)


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=24)


class PlayerActionRequest(BaseModel):
    player_id: str


class SubmitAnswerRequest(PlayerActionRequest):
    position: int
    confidence: int = Field(ge=0, le=100)


class PlayerView(BaseModel):
    name: str
    is_host: bool
    has_answered: bool


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


class RoomView(BaseModel):
    code: str
    phase: RoomPhase
    players: list[PlayerView]
    round_number: int | None
    round_count: int
    question: QuestionView | None
    revealed_answers: list[RevealedAnswerView] | None
    summary: dict[str, float | int] | None


class RoomSessionView(BaseModel):
    player_id: str
    room: RoomView


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

    return RoomView(
        code=room.code,
        phase=room.phase,
        players=[
            PlayerView(
                name=player.name,
                is_host=player.is_host,
                has_answered=bool(current_round and player.id in current_round.answers),
            )
            for player in room.players.values()
        ],
        round_number=current_round.number if current_round else None,
        round_count=len(room.rounds),
        question=question,
        revealed_answers=revealed_answers,
        summary=room.round_summary(),
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
    active_store = store or default_store

    @app.exception_handler(GameError)
    async def game_error_handler(_request: Request, exc: GameError):
        return __import__("fastapi").responses.JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/rooms", response_model=RoomSessionView, status_code=201)
    def create_room(request: CreateRoomRequest) -> RoomSessionView:
        room = active_store.create()
        host = room.add_player(request.host_name, is_host=True)
        return RoomSessionView(player_id=host.id, room=room_view(room))

    @app.get("/api/rooms/{code}", response_model=RoomView)
    def inspect_room(code: str) -> RoomView:
        room = active_store.get(code)
        return room_view(room)

    @app.post("/api/rooms/{code}/players", response_model=RoomSessionView, status_code=201)
    def join_room(code: str, request: JoinRoomRequest) -> RoomSessionView:
        room = active_store.get(code)
        player = room.add_player(request.name)
        return RoomSessionView(player_id=player.id, room=room_view(room))

    @app.post("/api/rooms/{code}/start", response_model=RoomView)
    def start_room(code: str, request: PlayerActionRequest) -> RoomView:
        room = active_store.get(code)
        room.start(host_id=request.player_id)
        return room_view(room)

    @app.post("/api/rooms/{code}/answers", response_model=RoomView)
    def submit_answer(code: str, request: SubmitAnswerRequest) -> RoomView:
        room = active_store.get(code)
        room.submit_answer(
            player_id=request.player_id,
            position=request.position,
            confidence=request.confidence,
        )
        return room_view(room)

    @app.post("/api/rooms/{code}/reveal", response_model=RoomView)
    def reveal_round(code: str, request: PlayerActionRequest) -> RoomView:
        room = active_store.get(code)
        room.reveal(host_id=request.player_id)
        return room_view(room)

    @app.post("/api/rooms/{code}/advance", response_model=RoomView)
    def advance_round(code: str, request: PlayerActionRequest) -> RoomView:
        room = active_store.get(code)
        room.advance(host_id=request.player_id)
        return room_view(room)

    @app.get("/")
    def root() -> dict[str, str]:
        raise HTTPException(status_code=404, detail="Run the web client on port 5173.")

    return app


app = create_app()
