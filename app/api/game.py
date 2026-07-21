from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.services.game_service import GameService
from app.websocket.manager import manager
from app.schemas.game import (
    CreateRoomRequest,
    CreateRoomResponse,
    JoinRoomRequest,
    JoinRoomResponse,
    SubmitSecretRequest,
    SubmitSecretResponse,
    GuessRequest,
    GuessResponse,
    GameStateResponse,
    HistoryResponse,
    WinnerResponse,
)

router = APIRouter(tags=["Game"])


@router.post("/create-room", response_model=CreateRoomResponse)
async def create_room(request: CreateRoomRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.create_room(db, request.name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/join-room", response_model=JoinRoomResponse)
async def join_room(request: JoinRoomRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.join_room(db, request.room_code, request.name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/submit-secret", response_model=SubmitSecretResponse)
async def submit_secret(request: SubmitSecretRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.submit_secret(
            db, request.room_code, request.player_id, request.secret_number
        )
        # When both players have submitted, broadcast the game start via WebSocket
        if result.both_submitted and result.current_turn:
            try:
                await manager.broadcast(
                    request.room_code,
                    {
                        "type": "game_started",
                        "current_turn": result.current_turn,
                    },
                )
                await manager.broadcast(
                    request.room_code,
                    {
                        "type": "turn_changed",
                        "player_id": result.current_turn,
                    },
                )
            except Exception as broadcast_err:
                # Log but don't fail the request - DB commit is more important
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to broadcast game start: {broadcast_err}")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/guess", response_model=GuessResponse)
async def guess(request: GuessRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.make_guess(
            db, request.room_code, request.player_id, request.guess
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/game-state", response_model=GameStateResponse)
async def game_state(room_code: str, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.get_game_state(db, room_code)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/history", response_model=HistoryResponse)
async def history(room_code: str, player_id: str = None, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.get_history(db, room_code, player_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/winner", response_model=WinnerResponse)
async def winner(room_code: str, db: AsyncSession = Depends(get_db)):
    try:
        result = await GameService.get_winner(db, room_code)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
