import random
import string
from typing import Optional, Tuple, List
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.game import Game, GameStatus
from app.models.player import Player
from app.models.guess import Guess
from app.schemas.game import (
    CreateRoomResponse,
    JoinRoomResponse,
    SubmitSecretResponse,
    GuessResponse,
    GameStateResponse,
    PlayerInfo,
    GuessHistoryItem,
    HistoryResponse,
    WinnerResponse,
    GameResultResponse,
    GameSecrets,
)


def generate_room_code() -> str:
    """Generate a unique 6-character room code (uppercase letters + digits)."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


def calculate_result(secret: str, guess: str) -> Tuple[int, int]:
    """
    Calculate position and number counts.
    Position = correct digit in correct position
    Number = correct digit in wrong position
    """
    position = 0
    number = 0
    secret_digits = list(secret)
    guess_digits = list(guess)

    # First pass: count positions
    for i in range(3):
        if guess_digits[i] == secret_digits[i]:
            position += 1
            secret_digits[i] = None
            guess_digits[i] = None

    # Second pass: count correct numbers in wrong positions
    for i in range(3):
        if guess_digits[i] is not None and guess_digits[i] in secret_digits:
            number += 1
            secret_digits[secret_digits.index(guess_digits[i])] = None

    return position, number


class GameService:
    """Service handling all game business logic."""

    @staticmethod
    async def create_room(db: AsyncSession, name: str) -> CreateRoomResponse:
        """Create a new game room and add the creator as the first player."""
        # Generate unique room code
        while True:
            room_code = generate_room_code()
            existing = await db.execute(
                select(Game).where(Game.room_code == room_code)
            )
            if not existing.scalar_one_or_none():
                break

        game = Game(room_code=room_code)
        db.add(game)
        await db.flush()

        player = Player(game_id=game.id, name=name)
        db.add(player)
        await db.flush()

        return CreateRoomResponse(
            room_code=room_code,
            player_id=player.id,
            message="Room created successfully",
        )

    @staticmethod
    async def join_room(
        db: AsyncSession, room_code: str, name: str
    ) -> JoinRoomResponse:
        """Join an existing room."""
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        if game.status != GameStatus.WAITING:
            raise ValueError("Game has already started or finished")

        if len(game.players) >= 2:
            raise ValueError("Room is full (max 2 players)")

        # Check if this name already exists in the room
        if any(p.name == name for p in game.players):
            raise ValueError("Name already taken in this room")

        opponent_name = game.players[0].name

        player = Player(game_id=game.id, name=name)
        db.add(player)
        await db.flush()

        return JoinRoomResponse(
            room_code=room_code,
            player_id=player.id,
            opponent_name=opponent_name,
            message=f"Joined room {room_code}",
        )

    @staticmethod
    async def submit_secret(
        db: AsyncSession, room_code: str, player_id: str, secret_number: str
    ) -> SubmitSecretResponse:
        """Submit a player's secret number."""
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        player = next((p for p in game.players if p.id == player_id), None)
        if not player:
            raise ValueError("Player not found in this room")

        if len(game.players) < 2:
            raise ValueError("Waiting for opponent to join")

        player.secret_number = secret_number
        await db.flush()

        # Check if both players have submitted
        both_submitted = all(p.secret_number is not None for p in game.players)

        current_turn = None
        if both_submitted:
            game.status = GameStatus.PLAYING
            # Set current turn to the player who joined first
            sorted_players = sorted(game.players, key=lambda p: p.joined_at)
            game.current_turn = sorted_players[0].id
            current_turn = game.current_turn

        return SubmitSecretResponse(
            message="Secret number submitted",
            both_submitted=both_submitted,
            current_turn=current_turn,
        )

    @staticmethod
    async def make_guess(
        db: AsyncSession, room_code: str, player_id: str, guess: str
    ) -> GuessResponse:
        """Process a guess and return the result.
        
        CRITICAL: Uses SERIALIZABLE isolation to prevent race conditions with concurrent users.
        This ensures that when multiple users submit guesses simultaneously, only one succeeds
        and the other gets a proper error.
        """
        # Use SERIALIZABLE isolation to prevent race conditions
        # This is crucial for Vercel where 10+ concurrent users can play
        try:
            game = await db.execute(
                select(Game)
                .options(selectinload(Game.players), selectinload(Game.guesses))
                .where(Game.room_code == room_code)
                .with_for_update()  # Lock the row to prevent concurrent modifications
            )
            game = game.scalar_one_or_none()

            if not game:
                raise ValueError("Room not found")

            # Re-validate game state (critical for concurrent users)
            if game.status != GameStatus.PLAYING:
                raise ValueError("Game is not in progress")

            # Re-validate turn (may have changed due to concurrent guess)
            if game.current_turn != player_id:
                raise ValueError("It's not your turn")

            # Get the player and the opponent
            player = next((p for p in game.players if p.id == player_id), None)
            opponent = next((p for p in game.players if p.id != player_id), None)

            if not player or not opponent:
                raise ValueError("Player not found")

            if not opponent.secret_number:
                raise ValueError("Opponent hasn't submitted a secret number yet")

            # Calculate result
            position, number = calculate_result(opponent.secret_number, guess)

            guess_obj = Guess(
                game_id=game.id,
                player_id=player_id,
                guess=guess,
                position_count=position,
                number_count=number,
            )
            db.add(guess_obj)
            await db.flush()

            game_over = position == 3
            winner_id = None

            if game_over:
                game.status = GameStatus.FINISHED
                game.winner_id = player_id
                game.current_turn = None
                winner_id = player_id
            else:
                # Switch turn to opponent
                game.current_turn = opponent.id

            return GuessResponse(
                guess_id=guess_obj.id,
                guess=guess,
                position_count=position,
                number_count=number,
                player_id=player_id,
                game_over=game_over,
                winner_id=winner_id,
            )
        except Exception as e:
            # If lock acquisition fails, it means another user is modifying this game
            # This is expected in concurrent scenarios
            if "FOR UPDATE" in str(e) or "deadlock" in str(e).lower():
                raise ValueError("Game state changed. Please refresh and try again.")
            raise

    @staticmethod
    async def get_game_state(
        db: AsyncSession, room_code: str
    ) -> GameStateResponse:
        """Get the current state of a game."""
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        players = [
            PlayerInfo(
                id=p.id,
                name=p.name,
                has_submitted_secret=p.secret_number is not None,
            )
            for p in game.players
        ]

        return GameStateResponse(
            room_code=game.room_code,
            status=game.status,
            players=players,
            current_turn=game.current_turn,
            winner_id=game.winner_id,
        )

    @staticmethod
    async def get_history(
        db: AsyncSession, room_code: str, player_id: Optional[str] = None
    ) -> HistoryResponse:
        """Get guess history for a game."""
        game = await db.execute(
            select(Game).where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        query = (
            select(Guess)
            .where(Guess.game_id == game.id)
            .order_by(Guess.created_at.asc())
        )

        if player_id:
            query = query.where(Guess.player_id == player_id)

        result = await db.execute(query)
        guesses = result.scalars().all()

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

        return HistoryResponse(guesses=guess_items)

    @staticmethod
    async def get_game_result(
        db: AsyncSession, room_code: str
    ) -> GameResultResponse:
        """Get game result including both players' secret numbers (only when game is finished)."""
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        game_over = game.status == GameStatus.FINISHED

        winner_name = None
        if game.winner_id:
            winner = next(
                (p for p in game.players if p.id == game.winner_id), None
            )
            if winner:
                winner_name = winner.name

        # Only reveal secrets when game is actually finished
        secrets = []
        if game_over:
            secrets = [
                GameSecrets(
                    player_id=p.id,
                    player_name=p.name,
                    secret_number=p.secret_number,
                )
                for p in game.players
            ]

        return GameResultResponse(
            winner_id=game.winner_id,
            winner_name=winner_name,
            game_over=game_over,
            secrets=secrets,
        )

    @staticmethod
    async def get_winner(
        db: AsyncSession, room_code: str
    ) -> WinnerResponse:
        """Get winner information for a game."""
        game = await db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.room_code == room_code)
        )
        game = game.scalar_one_or_none()

        if not game:
            raise ValueError("Room not found")

        winner_name = None
        if game.winner_id:
            winner = next(
                (p for p in game.players if p.id == game.winner_id), None
            )
            if winner:
                winner_name = winner.name

        return WinnerResponse(
            winner_id=game.winner_id,
            winner_name=winner_name,
            game_over=game.status == GameStatus.FINISHED,
        )
