import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import relationship
from app.database.session import Base
import enum


class GameStatus(str, enum.Enum):
    WAITING = "waiting"
    PLAYING = "playing"
    FINISHED = "finished"


class Game(Base):
    __tablename__ = "games"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_code = Column(String(6), unique=True, nullable=False, index=True)
    status = Column(
        String(20),
        default=GameStatus.WAITING,
        nullable=False,
    )
    current_turn = Column(String, nullable=True)
    winner_id = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    players = relationship("Player", back_populates="game", cascade="all, delete-orphan")
    guesses = relationship("Guess", back_populates="game", cascade="all, delete-orphan")
