import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.health import router as health_router
from app.api.game import router as game_router
from app.websocket.manager import manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(game_router)


@app.get("/")
async def root():
    return {
        "message": "Guess The Number API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: str):
    await manager.connect(websocket, room_code, player_id)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await manager.send_personal(
                    websocket, {"type": "error", "message": "Invalid JSON"}
                )
                continue

            msg_type = message.get("type")

            if msg_type == "guess":
                guess = message.get("guess", "")
                await manager.handle_guess(room_code, player_id, guess)

            elif msg_type == "player_ready":
                await manager.handle_ready(room_code, player_id)

            elif msg_type == "heartbeat":
                # Respond with a simple acknowledgment
                await manager.send_personal(
                    websocket, {"type": "heartbeat_ack"}
                )

            elif msg_type == "leave_room":
                break

            else:
                await manager.send_personal(
                    websocket,
                    {"type": "error", "message": f"Unknown message type: {msg_type}"},
                )

    except WebSocketDisconnect:
        logger.info(f"Player {player_id} WebSocket disconnected from room {room_code}")
    except Exception as e:
        logger.error(f"WebSocket error for player {player_id}: {e}")
    finally:
        await manager.disconnect(websocket, room_code, player_id)


@app.websocket("/ws/spectate/{room_code}")
async def websocket_spectate(websocket: WebSocket, room_code: str):
    """
    WebSocket endpoint for spectators.
    Spectators can watch the game in real-time but cannot make guesses.
    """
    await manager.connect_spectator(websocket, room_code)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await manager.send_personal(
                    websocket, {"type": "error", "message": "Invalid JSON"}
                )
                continue

            msg_type = message.get("type")

            if msg_type == "heartbeat":
                await manager.send_personal(
                    websocket, {"type": "heartbeat_ack"}
                )
            # Spectators only get heartbeats - no game interactions

    except WebSocketDisconnect:
        logger.info(f"Spectator WebSocket disconnected from room {room_code}")
    except Exception as e:
        logger.error(f"Spectator WebSocket error for room {room_code}: {e}")
    finally:
        manager._disconnect_spectator(websocket, room_code)
