from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path
from typing import List, Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Guess The Number API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/guess_the_number"

    # Security
    SECRET_KEY: str = "change-me-to-a-secure-random-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    ALLOWED_ORIGIN_REGEX: Optional[str] = r"https://.*\.vercel\.app|http://localhost(:\d+)?"

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def cors_origin_regex(self) -> Optional[str]:
        cleaned = (self.ALLOWED_ORIGIN_REGEX or "").strip()
        return cleaned or None

    class Config:
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
