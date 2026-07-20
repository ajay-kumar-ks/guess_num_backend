import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship
from app.database.session import Base


class Guess(Base):
    __tablename__ = "guesses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    game_id = Column(
        String,
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )
    player_id = Column(
        String,
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    guess = Column(String(3), nullable=False)
    position_count = Column(Integer, default=0, nullable=False)
    number_count = Column(Integer, default=0, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    game = relationship("Game", back_populates="guesses")
    player = relationship("Player", back_populates="guesses")
