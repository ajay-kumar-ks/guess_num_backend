import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer
from app.database.session import Base


class AccessLog(Base):
    __tablename__ = "access_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    ip_address = Column(String(45), nullable=True, index=True)
    method = Column(String(10), nullable=False, index=True)
    path = Column(String(255), nullable=False, index=True)
    user_agent = Column(String(500), nullable=True)
    referer = Column(String(500), nullable=True)
    status_code = Column(Integer, nullable=True, index=True)
    response_time_ms = Column(Integer, nullable=True)
    room_code = Column(String(20), nullable=True, index=True)
    game_name = Column(String(100), nullable=True, index=True)
    player_name = Column(String(100), nullable=True)
    query_params = Column(String(1000), nullable=True)
    request_details = Column(String(2000), nullable=True)
