import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship
from app.database.session import Base


class Player(Base):
    __tablename__ = "players"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    game_id = Column(
        String,
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(20), nullable=False)
    secret_number = Column(String(3), nullable=True)
    joined_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    game = relationship("Game", back_populates="players")
    guesses = relationship("Guess", back_populates="player", cascade="all, delete-orphan")
