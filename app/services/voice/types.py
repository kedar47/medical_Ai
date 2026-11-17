from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import OrderUnit, VoiceIntent
from app.schemas.matching import MedicineMatchCandidate
from app.schemas.order import OrderRead
from app.services.voice.confirmation_guard import ConfirmationGuard
from app.schemas.voice import VoiceOrderSummaryRead


class VoicePendingState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["quantity", "candidate"]
    medicine_id: str | None = None
    medicine_name: str | None = None
    description: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit: OrderUnit | None = None
    candidates: list[MedicineMatchCandidate] = Field(default_factory=list)
    requested_text: str | None = None
    match_status: Literal["CLARIFICATION_REQUIRED", "NOT_FOUND"] | None = None


class VoiceState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pending: VoicePendingState | None = None
    last_item_id: int | None = None
    guard: ConfirmationGuard = Field(default_factory=ConfirmationGuard)
    summary_digest: str | None = None
    language: Literal["hi", "en"] = "hi"


class ParsedIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: VoiceIntent = VoiceIntent.UNKNOWN
    language: Literal["hi", "en"] = "hi"
    medicine_query: str | None = None
    medicine_id: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit: OrderUnit | None = None
    item_reference: str | None = None
    candidate_reference: str | None = None
    source: str = "deterministic"


@dataclass(frozen=True)
class TranscriptionResult:
    transcript: str
    language: str
    language_probability: float | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    sample_rate: int
    voice: str


@dataclass
class TurnDraft:
    intent: VoiceIntent
    assistant_text: str
    language: Literal["hi", "en"]
    candidates: list[MedicineMatchCandidate] = field(default_factory=list)
    order_summary: VoiceOrderSummaryRead | None = None
    order: OrderRead | None = None


class SpeechToText(Protocol):
    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio: ...


class IntentParser(Protocol):
    def parse(
        self,
        text: str,
        *,
        language: str,
        pending: VoicePendingState | None = None,
    ) -> ParsedIntent: ...
