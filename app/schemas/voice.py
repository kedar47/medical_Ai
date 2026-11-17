from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.enums import SessionStatus, VoiceIntent, VoiceSessionStatus
from app.schemas.order import OrderItemRead, OrderRead, OrderSessionCreate

VoiceLanguage = Literal["hi", "en"]


class VoiceSessionCreate(OrderSessionCreate):
    language: VoiceLanguage = "hi"


class VoiceTurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000)
    language: VoiceLanguage | None = None
    speak: bool = True

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class VoiceSessionRead(BaseModel):
    voice_session_id: str
    order_session_id: str
    status: VoiceSessionStatus
    language: VoiceLanguage
    turn_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    message: str


class VoiceTranscriptRead(BaseModel):
    transcript: str
    language: str
    language_probability: float | None = None
    duration_seconds: float | None = None


class VoiceTTSRead(BaseModel):
    audio_base64: str
    content_type: Literal["audio/wav"] = "audio/wav"
    sample_rate: int
    voice: str


class VoiceCandidateRead(BaseModel):
    ordinal: int
    medicine_id: str
    medicine_name: str
    description: str
    match_type: Literal["EXACT_NAME", "ALIAS", "FUZZY_SUGGESTION"]


class VoiceOrderSummaryRead(BaseModel):
    status: SessionStatus
    items: list[OrderItemRead]
    line_item_count: int
    can_confirm: bool
    message: str


class VoiceTurnResponse(BaseModel):
    voice_session_id: str
    order_session_id: str
    status: VoiceSessionStatus
    intent: VoiceIntent
    language: VoiceLanguage
    assistant_text: str
    transcript: str | None = None
    candidates: list[VoiceCandidateRead] = Field(default_factory=list)
    order_summary: VoiceOrderSummaryRead | None = None
    order: OrderRead | None = None
    tts: VoiceTTSRead | None = None
    tts_error: str | None = None


class VoiceComponentRead(BaseModel):
    provider: str
    ready: bool
    detail: str


class VoiceStatusRead(BaseModel):
    enabled: bool
    stt: VoiceComponentRead
    tts: VoiceComponentRead
    llm: VoiceComponentRead
