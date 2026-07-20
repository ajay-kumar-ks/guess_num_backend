from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class CreateRoomRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=20)


class CreateRoomResponse(BaseModel):
    room_code: str
    player_id: str
    message: str


class JoinRoomRequest(BaseModel):
    room_code: str = Field(..., min_length=1, max_length=6)
    name: str = Field(..., min_length=1, max_length=20)


class JoinRoomResponse(BaseModel):
    room_code: str
    player_id: str
    opponent_name: str
    message: str


class SubmitSecretRequest(BaseModel):
    room_code: str
    player_id: str
    secret_number: str

    @field_validator("secret_number", mode="before")
    @classmethod
    def validate_secret(cls, v) -> str:
        v = str(v)
        if not v.isdigit():
            raise ValueError("Secret number must contain only digits")
        if not all("1" <= c <= "9" for c in v):
            raise ValueError("Digits must be between 1 and 9")
        if len(set(v)) != 3:
            raise ValueError("All digits must be unique (no repeats)")
        return v


class SubmitSecretResponse(BaseModel):
    message: str
    both_submitted: bool = False


class GuessRequest(BaseModel):
    room_code: str
    player_id: str
    guess: str

    @field_validator("guess", mode="before")
    @classmethod
    def validate_guess(cls, v) -> str:
        v = str(v)
        if not v.isdigit():
            raise ValueError("Guess must contain only digits")
        if not all("1" <= c <= "9" for c in v):
            raise ValueError("Digits must be between 1 and 9")
        if len(set(v)) != 3:
            raise ValueError("All digits must be unique (no repeats)")
        return v


class GuessResponse(BaseModel):
    guess_id: str
    guess: str
    position_count: int
    number_count: int
    player_id: str
    game_over: bool = False
    winner_id: Optional[str] = None


class GuessHistoryItem(BaseModel):
    guess: str
    position_count: int
    number_count: int
    player_id: str
    created_at: datetime


class PlayerInfo(BaseModel):
    id: str
    name: str
    has_submitted_secret: bool


class GameStateResponse(BaseModel):
    room_code: str
    status: str
    players: List[PlayerInfo]
    current_turn: Optional[str] = None
    winner_id: Optional[str] = None


class HistoryResponse(BaseModel):
    guesses: List[GuessHistoryItem]


class WinnerResponse(BaseModel):
    winner_id: Optional[str] = None
    winner_name: Optional[str] = None
    game_over: bool = False
