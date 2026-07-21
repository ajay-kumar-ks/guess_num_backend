from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.session import get_db
from app.services.game_service import GameService
from app.websocket.manager import manager
from app.models.game import Game
from app.models.guess import Guess
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
        # Broadcast guess result and turn change via WebSocket (for opponent and spectators)
        try:
            guess_message = {
                "type": "guess_result",
                "guess_id": result.guess_id,
                "guess": result.guess,
                "position_count": result.position_count,
                "number_count": result.number_count,
                "player_id": result.player_id,
            }
            await manager.broadcast(request.room_code, guess_message)

            turn_message = {
                "type": "turn_changed",
                "player_id": result.player_id if result.game_over else None,
            }
            # Get the actual current turn from DB
            game_state = await GameService.get_game_state(db, request.room_code)
            turn_message["player_id"] = game_state.current_turn
            await manager.broadcast(request.room_code, turn_message)

            if result.game_over:
                winner_name = await manager._get_player_name(
                    request.room_code, result.winner_id
                )
                await manager.broadcast(
                    request.room_code,
                    {
                        "type": "winner",
                        "winner_id": result.winner_id,
                        "winner_name": winner_name,
                    },
                )
        except Exception as broadcast_err:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to broadcast guess result: {broadcast_err}")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/spectate/game-state")
async def spectate_game_state(room_code: str, db: AsyncSession = Depends(get_db)):
    """
    Get spectator-friendly game state (no secret numbers exposed).
    Returns player info, game status, and guess history.
    """
    try:
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="Room not found")

        # Get guess history
        guess_query = (
            select(Guess)
            .where(Guess.game_id == game.id)
            .order_by(Guess.created_at.asc())
        )
        guess_result = await db.execute(guess_query)
        guesses = guess_result.scalars().all()

        from app.schemas.game import PlayerInfo, GuessHistoryItem

        players = [
            PlayerInfo(
                id=p.id,
                name=p.name,
                has_submitted_secret=p.secret_number is not None,
            )
            for p in game.players
        ]

        guess_items = [
            GuessHistoryItem(
                guess=g.guess,
                position_count=g.position_count,
                number_count=g.number_count,
                player_id=g.player_id,
                created_at=g.created_at,
            )
            for g in guesses
        ]

        return {
            "room_code": game.room_code,
            "status": game.status,
            "players": [p.model_dump() for p in players],
            "current_turn": game.current_turn,
            "winner_id": game.winner_id,
            "guesses": [g.model_dump(mode="json") for g in guess_items],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


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
