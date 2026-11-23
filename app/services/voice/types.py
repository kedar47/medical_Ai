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
    # Unclear answers to this question so far; after three the agent stops
    # asking it and asks for the medicine name again (no endless loop).
    retries: int = Field(default=0, ge=0)
    # The caller picked this variant from a question ("PAHELA"). If they
    # name another medicine before saying how many, it is kept and asked
    # again afterwards instead of being dropped.
    chosen: bool = False
    # NAME_HEARD: an exact catalogue name was heard and only its variant is
    # open, so the caller may pick it by number, strength or flavour.
    match_status: Literal["CLARIFICATION_REQUIRED", "NOT_FOUND", "NAME_HEARD", "NAMES_HEARD", "STRENGTH_GUESS"] | None = None
    # NAMES_HEARD: two or more exact catalogue names were heard (often one
    # is Whisper echoing its hint); the caller picks one by saying its name.


class VoiceState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pending: VoicePendingState | None = None
    last_item_id: int | None = None
    guard: ConfirmationGuard = Field(default_factory=ConfirmationGuard)
    summary_digest: str | None = None
    language: Literal["hi", "en"] = "hi"
    # Language the session started in. Phone calls keep speech recognition in
    # this language: Hindi-mode Whisper handles English speech, but
    # English-mode Whisper turns Hindi speech into unrelated English words.
    initial_language: Literal["hi", "en"] | None = None
    # Chosen medicines still waiting for a quantity (see chosen above).
    deferred: list[VoicePendingState] = Field(default_factory=list)
    # The rest of "कॉम्बिफ्लेम दो पत्ते और डिस्प्रिन एक पत्ता": one medicine at a time.
    queued: list[str] = Field(default_factory=list)
    # Medicines asked for but not found or not heard clearly; named before the
    # read-back so nothing is left out silently.
    unresolved: list[str] = Field(default_factory=list)
    # Times the caller was asked for their name (asked in the greeting).
    name_asks: int = 0
    # The previous reply, to keep a repeated "not found" short.
    last_reply: str = ""


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
        allow_fallback: bool = True,
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
