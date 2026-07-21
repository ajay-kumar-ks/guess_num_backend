import json
import logging
from typing import Dict, List, Set, Optional
from fastapi import WebSocket

from app.services.game_service import GameService
from app.database.session import async_session

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections per room.
    Each room has a dict of player_id -> WebSocket.
    """

    def __init__(self):
        # rooms: room_code -> {player_id: websocket}
        self.rooms: Dict[str, Dict[str, WebSocket]] = {}
        # spectators: room_code -> [websocket, ...]
        self.spectators: Dict[str, List[WebSocket]] = {}
        # Cache player names to reduce DB queries on Vercel (in-memory is fine per-instance)
        self._player_name_cache: Dict[str, str] = {}

    async def connect(self, websocket: WebSocket, room_code: str, player_id: str):
        await websocket.accept()
        if room_code not in self.rooms:
            self.rooms[room_code] = {}
        self.rooms[room_code][player_id] = websocket
        logger.info(f"Player {player_id} connected to room {room_code}")

        # Look up the connecting player's name (with caching)
        player_name = await self._get_player_name(room_code, player_id)
        if player_name:
            self._player_name_cache[player_id] = player_name

        # Look up existing opponent in the room (if any)
        opponent_name = await self._get_opponent_name(room_code, player_id)

        # Look up current game state to sync reconnecting players
        game_status = None
        current_turn = None
        try:
            async with async_session() as db:
                game_state = await GameService.get_game_state(db, room_code)
                game_status = game_state.status
                current_turn = game_state.current_turn
        except Exception as e:
            logger.warning(f"Could not get game state for {room_code}: {e}")

        # Send room_joined event with full game state (critical for reconnection)
        await self.send_personal(
            websocket,
            {
                "type": "room_joined",
                "room_code": room_code,
                "player_id": player_id,
                "opponent_name": opponent_name,
                "game_status": game_status,
                "current_turn": current_turn,
            },
        )

        # Notify existing players about the new player
        await self.broadcast(
            room_code,
            {
                "type": "opponent_joined",
                "player_id": player_id,
                "name": player_name or "Unknown",
            },
            exclude=player_id,
        )

    async def disconnect(self, websocket: WebSocket, room_code: str, player_id: str):
        self._disconnect_spectator(websocket, room_code)
        if room_code in self.rooms:
            if player_id in self.rooms[room_code]:
                del self.rooms[room_code][player_id]
                logger.info(f"Player {player_id} disconnected from room {room_code}")

                # Notify remaining players and spectators
                await self.broadcast(
                    room_code,
                    {
                        "type": "opponent_disconnected",
                        "player_id": player_id,
                    },
                )

                # Clean up empty rooms
                if not self.rooms[room_code]:
                    del self.rooms[room_code]

    async def broadcast(self, room_code: str, message: dict, exclude: Optional[str] = None):
        """Send a message to all players AND spectators in a room, optionally excluding one player."""
        # Send to players
        if room_code in self.rooms:
            disconnected = []
            for pid, ws in self.rooms[room_code].items():
                if pid == exclude:
                    continue
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.error(f"Failed to send to player {pid}: {e}")
                    disconnected.append(pid)

            # Clean up disconnected players
            for pid in disconnected:
                del self.rooms[room_code][pid]

        # Also send to spectators (they get all events)
        await self._broadcast_to_spectators(room_code, message)

    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send a message to a specific player."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send personal message: {e}")

    async def handle_guess(
        self, room_code: str, player_id: str, guess: str
    ):
        """Process a guess via WebSocket and broadcast result."""
        async with async_session() as db:
            try:
                result = await GameService.make_guess(db, room_code, player_id, guess)
                await db.commit()

                # Broadcast the guess result
                guess_message = {
                    "type": "guess_result",
                    "guess_id": result.guess_id,
                    "guess": result.guess,
                    "position_count": result.position_count,
                    "number_count": result.number_count,
                    "player_id": result.player_id,
                }
                await self.broadcast(room_code, guess_message)

                # Broadcast turn change with new current turn
                game_state = await GameService.get_game_state(db, room_code)
                turn_message = {
                    "type": "turn_changed",
                    "player_id": game_state.current_turn,
                }
                await self.broadcast(room_code, turn_message)

                # If game over, broadcast winner with secret numbers
                if result.game_over:
                    # Fetch both players' secrets
                    secrets = []
                    try:
                        async with async_session() as db2:
                            game_result = await GameService.get_game_result(db2, room_code)
                            secrets = [
                                {"player_id": s.player_id, "player_name": s.player_name, "secret_number": s.secret_number}
                                for s in game_result.secrets
                            ]
                    except Exception as e:
                        logger.error(f"Failed to fetch game secrets: {e}")

                    winner_message = {
                        "type": "winner",
                        "winner_id": result.winner_id,
                        "winner_name": await self._get_player_name(
                            room_code, result.winner_id
                        ),
                        "secrets": secrets,
                    }
                    await self.broadcast(room_code, winner_message)

            except ValueError as e:
                # Send error message back to the player who guessed
                error_message = {
                    "type": "error",
                    "message": str(e),
                }
                if player_id in self.rooms.get(room_code, {}):
                    await self.send_personal(
                        self.rooms[room_code][player_id], error_message
                    )

    async def _get_player_name(self, room_code: str, player_id: str) -> Optional[str]:
        """Get a player's name from the database (with in-memory cache)."""
        # Check cache first to avoid DB query on every heartbeat/event
        if player_id in self._player_name_cache:
            return self._player_name_cache[player_id]

        from app.models.player import Player
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Player.name).where(Player.id == player_id)
            )
            row = result.scalar_one_or_none()
            name = row if row else None
            if name:
                self._player_name_cache[player_id] = name
            return name

    async def _get_opponent_name(self, room_code: str, my_player_id: str) -> Optional[str]:
        """Get the name of the other player in the room (if any)."""
        from app.models.player import Player
        from app.models.game import Game
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async with async_session() as db:
            result = await db.execute(
                select(Game).options(selectinload(Game.players)).where(Game.room_code == room_code)
            )
            game = result.scalar_one_or_none()
            if game:
                for p in game.players:
                    if p.id != my_player_id:
                        return p.name
            return None

    async def handle_ready(self, room_code: str, player_id: str):
        """Handle player_ready event - check if game should start."""
        async with async_session() as db:
            try:
                game_state = await GameService.get_game_state(db, room_code)

                # Both players joined and at least one has submitted
                players = game_state.players
                if len(players) == 2:
                    # Notify both players that the game is starting
                    await self.broadcast(
                        room_code,
                        {
                            "type": "game_started",
                            "current_turn": game_state.current_turn,
                        },
                    )
            except Exception as e:
                logger.error(f"Error handling ready: {e}")

    async def connect_spectator(self, websocket: WebSocket, room_code: str):
        """Connect a spectator to a room. They receive all broadcasts but can't interact."""
        await websocket.accept()
        if room_code not in self.spectators:
            self.spectators[room_code] = []
        self.spectators[room_code].append(websocket)
        logger.info(f"Spectator connected to room {room_code}")

        # Send initial game state to the spectator
        try:
            async with async_session() as db:
                game_state = await GameService.get_game_state(db, room_code)
                await self.send_personal(
                    websocket,
                    {
                        "type": "spectate_joined",
                        "room_code": room_code,
                        "game_status": game_state.status,
                        "players": [
                            {"id": p.id, "name": p.name, "has_submitted_secret": p.has_submitted_secret}
                            for p in game_state.players
                        ],
                        "current_turn": game_state.current_turn,
                        "winner_id": game_state.winner_id,
                    },
                )
        except Exception as e:
            logger.warning(f"Could not get initial game state for spectator: {e}")
            await self.send_personal(
                websocket,
                {
                    "type": "spectate_joined",
                    "room_code": room_code,
                    "game_status": None,
                    "players": [],
                    "current_turn": None,
                    "winner_id": None,
                },
            )

    def _disconnect_spectator(self, websocket: WebSocket, room_code: str):
        """Remove a spectator from the room."""
        if room_code in self.spectators:
            if websocket in self.spectators[room_code]:
                self.spectators[room_code].remove(websocket)
                logger.info(f"Spectator disconnected from room {room_code}")
            if not self.spectators[room_code]:
                del self.spectators[room_code]

    async def _broadcast_to_spectators(self, room_code: str, message: dict):
        """Send a message to all spectators in a room."""
        if room_code not in self.spectators:
            return

        disconnected = []
        for ws in self.spectators[room_code]:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send to spectator: {e}")
                disconnected.append(ws)

        # Clean up disconnected spectators
        for ws in disconnected:
            self.spectators[room_code].remove(ws)
        if room_code in self.spectators and not self.spectators[room_code]:
            del self.spectators[room_code]


# Singleton instance
manager = ConnectionManager()
