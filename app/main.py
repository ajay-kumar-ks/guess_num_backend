import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.health import router as health_router
from app.api.game import router as game_router
from app.database.session import Base, async_session, engine
from app.models.access_log import AccessLog
from app.websocket.manager import manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(game_router)


@app.middleware("http")
async def log_access(request: Request, call_next):
    start_time = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        try:
            async with async_session() as session:
                # Body may be consumed by route handler or client may disconnect - handle gracefully
                raw_body = ""
                try:
                    body = await request.body()
                    raw_body = body.decode("utf-8", errors="ignore") if body else ""
                except Exception:
                    # Stream consumed by another handler, or client disconnected mid-request
                    raw_body = ""
                room_code = None
                game_name = None
                player_name = None

                if request.url.path.startswith("/create-room"):
                    game_name = "room-create"
                elif request.url.path.startswith("/join-room"):
                    game_name = "room-join"
                elif request.url.path.startswith("/submit-secret"):
                    game_name = "submit-secret"
                elif request.url.path.startswith("/guess"):
                    game_name = "guess"
                elif request.url.path.startswith("/game-state") or request.url.path.startswith("/history"):
                    game_name = "game-state"

                if room_code is None:
                    try:
                        if "room_code" in request.query_params:
                            room_code = request.query_params.get("room_code")
                    except Exception:
                        room_code = None

                if game_name is None and room_code:
                    game_name = f"room:{room_code}"

                if raw_body:
                    try:
                        import json
                        payload = json.loads(raw_body)
                        if isinstance(payload, dict):
                            if not room_code and isinstance(payload.get("room_code"), str):
                                room_code = payload.get("room_code")
                            if isinstance(payload.get("name"), str):
                                player_name = payload.get("name")
                            if isinstance(payload.get("player_name"), str):
                                player_name = payload.get("player_name")
                            if isinstance(payload.get("game_name"), str):
                                game_name = payload.get("game_name")
                    except Exception:
                        pass

                log_entry = AccessLog(
                    id=str(uuid.uuid4()),
                    created_at=datetime.now(timezone.utc),
                    ip_address=request.client.host if request.client else None,
                    method=request.method,
                    path=request.url.path,
                    user_agent=request.headers.get("user-agent"),
                    referer=request.headers.get("referer"),
                    status_code=response.status_code if response else None,
                    response_time_ms=duration_ms,
                    room_code=room_code,
                    game_name=game_name,
                    player_name=player_name,
                    query_params=str(dict(request.query_params)),
                    request_details=raw_body[:2000],
                )
                session.add(log_entry)
                await session.commit()
        except Exception as log_error:
            logger.exception(f"Failed to persist access log: {log_error}")


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
