from dataclasses import replace
import base64
import importlib.util
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import (
    OrderUnit,
    SessionStatus,
    VoiceIntent,
    VoiceSessionStatus,
)
from app.core.exceptions import OrderError, VoiceError
from app.database.models import OrderItem, OrderSession, VoiceSession, utc_now
from app.repositories.medicine_repository import MedicineRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.voice_repository import VoiceRepository
from app.schemas.matching import MedicineMatchCandidate
from app.schemas.order import (
    OrderConfirmationRequest,
    OrderItemAdd,
    OrderItemUpdate,
    OrderSessionCreate,
    OrderSessionRead,
    OrderSummaryRead,
)
from app.schemas.voice import (
    VoiceCandidateRead,
    VoiceComponentRead,
    VoiceOrderSummaryRead,
    VoiceSessionCreate,
    VoiceSessionRead,
    VoiceStatusRead,
    VoiceTranscriptRead,
    VoiceTTSRead,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from app.services.matching_service import MedicineMatchingService, normalize_text, phonetic_key
from app.services.medicine_attributes import (
    MedicineAttributes,
    attributes_from_candidate,
    attributes_from_text,
    not_found_text,
    pack_size_tokens,
    select_strict_candidate,
    spoken_name,
    strictly_matches,
)
from app.services.order_service import OrderService
from app.services.voice import build_intent_parser, build_stt, build_tts
from app.services.voice.intent import (
    DO_AS_ENGLISH_WORDS,
    STRENGTH_ONLY_WORDS,
    DeterministicIntentParser,
    first_quantity_word,
    fold_latin,
    clean_medicine_query,
    is_plain_affirmative,
    is_plain_negative,
    is_nothing_more,
    extract_caller_name,
    is_only_greeting,
    is_only_fillers,
    sounds_like_confirm,
    normalize_spoken_strengths,
    split_spoken_hundreds,
    spoken_hundreds,
    contains_medicine_id,
    description_strength_tokens,
    detect_language,
    extract_ordinal,
    extract_quantity,
    extract_quantity_unit,
    extract_strength_tokens,
    has_ambiguous_quantity,
    has_ambiguous_quantity_answer,
    mentions_last_item,
    quantity_values,
)
from app.services.voice.types import (
    IntentParser,
    ParsedIntent,
    SpeechToText,
    SynthesizedAudio,
    TextToSpeech,
    TurnDraft,
    VoicePendingState,
    VoiceState,
)

from app.services.voice.quantity_correction import detect_correction, is_correction_request, is_strong_correction, parse_quantity
from pydantic import ValidationError

logger = logging.getLogger(__name__)

ACTIVE = VoiceSessionStatus.ACTIVE.value
CONFIRMED = VoiceSessionStatus.CONFIRMED.value
CANCELLED = VoiceSessionStatus.CANCELLED.value
ENDED = VoiceSessionStatus.ENDED.value
EXPIRED = VoiceSessionStatus.EXPIRED.value
ORDER_CONFIRMATION_SOURCE = "VOICE_DEMO"
CONFIRMATION_RESPONSE = {
    "hi": "आपका ऑर्डर कन्फर्म हो गया है। धन्यवाद।",
    "en": "Your order is confirmed. Thank you.",
}

UNIT_LABELS_HI = {
    OrderUnit.TABLET: "गोली",
    OrderUnit.STRIP: "स्ट्रिप",
    OrderUnit.BOTTLE: "बोतल",
    OrderUnit.BOX: "डिब्बा",
    OrderUnit.UNIT: "यूनिट",
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)



_PACK_OF_TABLETS = re.compile(r"(?<!\w)\d+\s*['*]?\s*s(?!\w)", re.IGNORECASE)
_LIQUID = re.compile(r"(?<!\w)(?:syp|syrup|susp|suspension)(?!\w)", re.IGNORECASE)


_BOX_PACK = re.compile(r"(?<![A-Z])BOX(?:ES)?(?![A-Z])", re.IGNORECASE)


def default_unit_for(description: str | None) -> OrderUnit | None:
    """Natural unit when the caller gives only a number: strips for a tablet
    pack ("15'S", "1*S"), bottles for a syrup or suspension, else ask."""
    description = description or ""
    if _PACK_OF_TABLETS.search(description):
        return OrderUnit.STRIP
    if _LIQUID.search(description):
        return OrderUnit.BOTTLE
    return None

def _text(hi: str, en: str, language: str) -> str:
    return hi if language == "hi" else en


# English number words as Sarvam writes them in Devanagari: "वेलेंटा फिफ्टी",
# "सिक्स हंड्रेड फिफ्टी". वन / एट are left alone (also Hindi words).
_DEVANAGARI_ENGLISH_UNITS = {
    "टू": 2, "थ्री": 3, "फोर": 4, "फ़ोर": 4, "फाइव": 5, "फ़ाइव": 5, "सिक्स": 6,
    "सेवन": 7, "नाइन": 9,
}
_DEVANAGARI_ENGLISH_TENS = {
    "टेन": 10, "ट्वेंटी": 20, "थर्टी": 30, "फोर्टी": 40, "फ़ोर्टी": 40, "फिफ्टी": 50,
    "फ़िफ़्टी": 50, "सिक्सटी": 60, "सेवेंटी": 70, "एटी": 80, "नाइंटी": 90,
    "ट्वेल्व": 12, "फिफ्टीन": 15, "ट्वेंटीफाइव": 25,
}
_DEVANAGARI_HUNDRED = {"हंड्रेड", "हन्ड्रेड", "हंड्रड"}


def english_number_words_to_digits(text: str) -> str:
    tokens = text.split()
    out: list[str] = []
    index = 0
    while index < len(tokens):
        word = tokens[index].strip(",.।")
        tail = tokens[index][len(word):] if tokens[index].startswith(word) else ""
        if word not in _DEVANAGARI_ENGLISH_UNITS and word not in _DEVANAGARI_ENGLISH_TENS and word not in _DEVANAGARI_HUNDRED:
            out.append(tokens[index])
            index += 1
            continue
        value, current, used = 0, 0, 0
        while index + used < len(tokens):
            w = tokens[index + used].strip(",.।")
            if w in _DEVANAGARI_ENGLISH_UNITS:
                current += _DEVANAGARI_ENGLISH_UNITS[w]
            elif w in _DEVANAGARI_ENGLISH_TENS:
                current += _DEVANAGARI_ENGLISH_TENS[w]
            elif w in _DEVANAGARI_HUNDRED:
                current = (current or 1) * 100
            else:
                break
            tail = tokens[index + used][len(w):] if tokens[index + used].startswith(w) else ""
            used += 1
        value = current
        out.append(f"{value}{tail}")
        index += used
    return " ".join(out)


def initial_greeting_text(language: str) -> str:
    return _text(
        "नमस्ते! मैं आपका मेडिसिन ऑर्डर असिस्टेंट हूँ। कृपया अपना नाम बताइए। या सीधे दवा बोलिए, जैसे डोलो 650 दो पत्ते।",
        "Hello, welcome to Medicure. Please tell me your name, or say the medicine, for example two strips of Dolo 650.",
        language,
    )


_PLURAL_EN = {"STRIP": "strips", "BOTTLE": "bottles", "BOX": "boxes",
              "TABLET": "tablets", "UNIT": "units"}


def _unit_label(unit: OrderUnit | str, language: str) -> str:
    value = OrderUnit(unit)
    if language == "hi":
        return UNIT_LABELS_HI[value]
    return value.value.title()


class VoiceService:
    def __init__(
        self,
        db: Session,
        *,
        stt: SpeechToText | None = None,
        tts: TextToSpeech | None = None,
        intent_parser: IntentParser | None = None,
    ):
        self.db = db
        self.settings = get_settings()
        self.orders = OrderService(db)
        self.voice_repository = VoiceRepository(db)
        self.order_repository = OrderRepository(db)
        self.medicine_repository = MedicineRepository(db)
        self.matching = MedicineMatchingService(db)
        self._stt = stt
        self._tts = tts
        self._intent_parser = intent_parser

    def _spoken_attributes(self, text: str) -> MedicineAttributes:
        return attributes_from_text(text, strengths=extract_strength_tokens(text))

    def _selection_strengths(
        self,
        text: str,
        candidates: list[MedicineMatchCandidate],
    ) -> set[str]:
        strengths = extract_strength_tokens(text) | spoken_hundreds(text)
        if candidates:
            shared = set.intersection(
                *(pack_size_tokens(candidate.description or "") for candidate in candidates)
            )
            strengths -= shared
        return strengths

    def _selection_attributes(
        self,
        text: str,
        candidates: list[MedicineMatchCandidate],
    ) -> MedicineAttributes:
        return attributes_from_text(
            text, strengths=self._selection_strengths(text, candidates)
        )

    def _has_ambiguous_strength_only(self, text: str) -> bool:
        strengths = extract_strength_tokens(text)
        if len(strengths) < 2:
            return False
        cleaned = text
        for strength in sorted(strengths, key=len, reverse=True):
            cleaned = re.sub(rf"(?<!\d){re.escape(strength)}(?!\d)", " ", cleaned)
        return len(quantity_values(cleaned)) <= 1

    def _not_found_draft(self, state: VoiceState, language: str) -> TurnDraft:
        state.pending = None
        state.summary_digest = None
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            assistant_text=not_found_text(language),
        )

    def _explicit_add_request(self, text: str) -> bool:
        normalized = normalize_text(text)
        return re.search(
            r"(?:^|\s)(?:add|jo[dr]|जोड़\w*|डाल\w*)(?:\s|$)",
            normalized,
            re.IGNORECASE,
        ) is not None

    def _log_matching(
        self,
        stage: str,
        text: str,
        candidates: list[MedicineMatchCandidate],
        selected: MedicineMatchCandidate | None,
        status: str,
    ) -> None:
        logger.info(
            "voice.medicine.match stage=%s status=%s text_len=%d strengths=%s candidate_ids=%s selected_id=%s",
            stage,
            status,
            len(text),
            ",".join(sorted(self._spoken_attributes(text).strengths)) or "-",
            ",".join(sorted(candidate.medicine_id for candidate in candidates)) or "-",
            selected.medicine_id if selected is not None else "-",
        )

    def create_session(
        self, payload: VoiceSessionCreate
    ) -> VoiceSessionRead:
        self._ensure_enabled()
        order_session = self.orders.create_session(
            OrderSessionCreate(
                customer_phone=payload.customer_phone,
                customer_name=payload.customer_name,
            )
        )
        order_db = self.order_repository.get_session(order_session.session_id)
        if order_db is None:
            raise VoiceError(500, "Order session could not be initialized.")

        now = utc_now()
        voice_expiry = now + timedelta(
            minutes=self.settings.voice_session_ttl_minutes
        )
        voice_session = VoiceSession(
            voice_session_id=f"voice_{uuid.uuid4().hex}",
            order_session=order_db,
            status=ACTIVE,
            language=payload.language,
            state_json=VoiceState(
                language=payload.language, initial_language=payload.language
            ).model_dump_json(),
            expires_at=min(_as_utc(order_session.expires_at), voice_expiry),
        )
        self.voice_repository.add_session(voice_session)
        self.db.commit()
        return self._serialize_session(voice_session)

    def get_session(self, public_voice_session_id: str) -> VoiceSessionRead:
        self._ensure_enabled()
        voice = self._get_voice(public_voice_session_id)
        if self._sync_status(voice):
            self.db.commit()
        return self._serialize_session(voice)

    def speech_language(self, public_voice_session_id: str) -> str:
        """Language for phone speech recognition: the one the call started in."""
        voice = self._get_voice(public_voice_session_id)
        state = self._load_state(voice)
        return state.initial_language or state.language

    def awaiting_confirmation(self, public_voice_session_id: str) -> bool:
        voice = self._get_voice(public_voice_session_id)
        state = self._load_state(voice)
        return (
            state.pending is None
            and not state.guard.pending_correction
            and state.summary_digest is not None
            and state.guard.can_confirm(state.summary_digest)
        )

    def process_text(
        self,
        public_voice_session_id: str,
        payload: VoiceTurnRequest,
    ) -> VoiceTurnResponse:
        self._ensure_enabled()
        voice = self._get_voice(
            public_voice_session_id, for_update=True
        )
        if self._sync_status(voice):
            self.db.commit()
        if voice.turn_count >= self.settings.voice_max_turns:
            raise VoiceError(
                429,
                "Voice session turn limit reached. Start a new session.",
            )

        state = self._load_state(voice)
        language = payload.language or detect_language(
            payload.text, state.language
        )
        state.language = language
        voice.language = language
        parsed = None
        if voice.status != ACTIVE:
            if voice.status != CONFIRMED:
                raise VoiceError(
                    409,
                    f"Voice session is not active ({voice.status}).",
                )
            parsed = self._parse_intent(
                payload.text, language, state.pending
            )
            if parsed.intent not in {
                VoiceIntent.CONFIRM_ORDER,
                VoiceIntent.SHOW_SUMMARY,
                VoiceIntent.END_CALL,
                VoiceIntent.HELP,
            }:
                raise VoiceError(
                    409,
                    "Voice session is not active (CONFIRMED).",
                )
        draft = self._handle_utterance(
            voice, state, payload.text, language, parsed=parsed
        )
        self._track_unresolved(state, payload.text, draft, language)
        draft = self._shorten_repeat(state, draft, language)
        voice.turn_count += 1
        voice.last_intent = draft.intent.value
        voice.state_json = state.model_dump_json()
        self.db.commit()

        tts_audio, tts_error = self._maybe_synthesize(
            draft.assistant_text, draft.language, payload.speak
        )
        return self._turn_response(
            voice, draft, tts_audio=tts_audio, tts_error=tts_error
        )

    def process_audio(
        self,
        public_voice_session_id: str,
        audio_bytes: bytes,
        *,
        filename: str | None,
        content_type: str | None,
        language: str | None = None,
        speak: bool = True,
    ) -> VoiceTurnResponse:
        self._ensure_enabled()
        self._validate_audio(audio_bytes, content_type)
        voice = self._get_voice(public_voice_session_id)
        if self._sync_status(voice):
            self.db.commit()
        self._require_active(voice)

        # Prefer the session language over auto-detection: short Hindi clips are
        # often misdetected as English, which can trigger slow repetition loops.
        transcription_language = (
            language
            if language in {"hi", "en"}
            else (voice.language if voice.language in {"hi", "en"} else None)
        )
        transcription = self._get_stt().transcribe(
            audio_bytes,
            filename=filename,
            language=transcription_language,
        )
        if len(transcription.transcript) > self.settings.voice_max_transcript_chars:
            raise VoiceError(
                400,
                "The spoken request is too long. Please keep it under "
                f"{self.settings.voice_max_transcript_chars} characters.",
            )
        turn = self.process_text(
            public_voice_session_id,
            VoiceTurnRequest(
                text=transcription.transcript,
                language=(
                    transcription.language
                    if transcription.language in {"hi", "en"}
                    else None
                ),
                speak=speak,
            ),
        )
        return turn.model_copy(
            update={"transcript": transcription.transcript}
        )

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None,
        content_type: str | None,
        language: str | None = None,
        allow_fallback: bool = True,
    ) -> VoiceTranscriptRead:
        self._ensure_enabled()
        self._validate_audio(audio_bytes, content_type)
        extra = {} if allow_fallback else {"allow_fallback": False}
        result = self._get_stt().transcribe(
            audio_bytes,
            filename=filename,
            language=language if language in {"hi", "en"} else None,
            **extra,
        )
        if len(result.transcript) > self.settings.voice_max_transcript_chars:
            raise VoiceError(
                400,
                "The transcription is too long to process.",
            )
        return VoiceTranscriptRead(
            transcript=result.transcript,
            language=result.language,
            language_probability=result.language_probability,
            duration_seconds=result.duration_seconds,
        )

    def end_session(self, public_voice_session_id: str) -> VoiceSessionRead:
        self._ensure_enabled()
        voice = self._get_voice(
            public_voice_session_id, for_update=True
        )
        if self._sync_status(voice):
            self.db.commit()
        state = self._load_state(voice)
        state.pending = None
        state.summary_digest = None
        voice.ended_at = voice.ended_at or utc_now()

        if voice.status == ACTIVE:
            try:
                self.orders.cancel_session(
                    voice.order_session.session_id
                )
                voice.status = ENDED
            except OrderError:
                refreshed = self.orders.get_session(
                    voice.order_session.session_id
                )
                if refreshed.status == SessionStatus.CONFIRMED:
                    voice.status = CONFIRMED
                elif refreshed.status == SessionStatus.CANCELLED:
                    voice.status = CANCELLED
                elif refreshed.status == SessionStatus.EXPIRED:
                    voice.status = EXPIRED
                else:
                    voice.status = ENDED

        voice.state_json = state.model_dump_json()
        self.db.commit()
        return self._serialize_session(voice)

    def status(self) -> VoiceStatusRead:
        stt_ready = (
            importlib.util.find_spec("faster_whisper") is not None
        )
        tts_ready = (
            self.settings.voice_tts_enabled
            and importlib.util.find_spec("kokoro_onnx") is not None
            and self._tts_files_available()
        )
        llm_ready = (
            self.settings.voice_llm_enabled
            and importlib.util.find_spec("httpx") is not None
        )
        return VoiceStatusRead(
            enabled=self.settings.voice_enabled,
            stt=VoiceComponentRead(
                provider=(
                    "sarvam"
                    if self.settings.voice_stt_provider == "sarvam" and self.settings.sarvam_api_key
                    else "faster-whisper"
                ),
                ready=stt_ready,
                detail=(
                    f"Model {self.settings.voice_stt_model} on "
                    f"{self.settings.voice_stt_device}."
                ),
            ),
            tts=VoiceComponentRead(
                provider="kokoro-onnx",
                ready=tts_ready,
                detail=(
                    "Local Kokoro-82M speech synthesis is configured."
                    if tts_ready
                    else "Kokoro model files or package are unavailable."
                ),
            ),
            llm=VoiceComponentRead(
                provider="ollama",
                ready=llm_ready,
                detail=(
                    f"Intent extraction uses {self.settings.ollama_model}."
                    if self.settings.voice_llm_enabled
                    else "Optional Ollama intent extraction is disabled."
                ),
            ),
        )

    _LIST_SEPARATOR = re.compile(
        r"\s*,\s*|(?<!\S)(?:and|aur|or|और|तथा|फिर|phir|साथ में|saath mein)(?!\S)",
        re.IGNORECASE,
    )

    def _split_medicines(self, text: str) -> list[str] | None:
        """ "कॉम्बिफ्लेम दो पत्ते और डिस्प्रिन एक पत्ता" -> one part per medicine.

        Only when every part names a different catalogue medicine; "BRUFEN
        400 और दो स्ट्रिप" stays one request.
        """
        counted = list(self._COUNT_THEN_UNIT.finditer(text))
        if self._LIST_SEPARATOR.search(text):
            counted = []
        if len(counted) >= 2:
            # Live Exotel call 13:38: "सीरम 500 के 5 पत्ते टोलो 650 के 4 पत्ते
            # ब्रोफिन 200 के 5 पत्ते ऐड कर दो" had no commas.
            text = self._COUNT_THEN_UNIT.sub(lambda m: m.group(0) + ",", text)
        if not self._LIST_SEPARATOR.search(text):
            return None
        parts = [p.strip(" ,.।") for p in self._LIST_SEPARATOR.split(text)]
        parts = [self._mark_strengths(p) for p in parts if p]
        if len(counted) >= 2 and len(parts) > 2 and extract_quantity_unit(parts[-1])[0] is None                 and not self.matching.suggestions_from_utterance(parts[-1]):
            parts.pop()  # "... के 5 पत्ते, ऐड कर दो" / ", चाहिए"
        if len(parts) > 2 and is_nothing_more(parts[-1]):
            parts.pop()
        if len(parts) > 2 and self._NAME_MARKER.search(parts[0]) and extract_quantity_unit(parts[0])[0] is None:
            parts.pop(0)  # "मेरा नाम हर्ष है, मुझे ... चाहिए": the name is taken separately
        if len(parts) < 2:
            return None
        named = [
            {normalize_text(c.medicine_name) for c in self.matching.suggestions_from_utterance(part)}
            for part in parts
        ]
        counted = all(extract_quantity_unit(part)[0] is not None for part in parts)
        if counted:
            # The client's sample call: "वेलेंटास 100 के 20 पत्ते, वेलेंटास
            # 200 के 20 पत्ते, नेबी स्टार 5 के 50 पत्ते, ...". Each counted
            # item is taken in turn; one not in the list is said so.
            return parts
        if not all(named) or len(set().union(*named)) < sum(len(n) for n in named):
            return None
        # Only a comma between names ("brufin, dolo650") is how Whisper echoed
        # its hint list on live calls: ask which one instead.
        joined_by_word = bool(re.search(r"(?<!\S)(?:and|aur|or|और|तथा|फिर|phir|साथ में|saath mein)(?!\S)", text, re.IGNORECASE))
        return parts if joined_by_word else None

    _COUNT_THEN_UNIT = re.compile(
        r"(?<!\S)(?:\d+|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ|दस|ek|do|teen|char|paanch|one|two|three|four|five)\s+"
        r"(?:पत्ते|पत्ता|पत्ती|पट्टे|स्ट्रिप|strips?|बोतल|बोतलें|बॉटल|bottles?|डिब्बे|डिब्बा|गोली|गोलियां|tablets?)(?=\s+\S)",
        re.IGNORECASE,
    )
    _GLUED_DO_PATTE = re.compile(r"(?<!\S)(?:दुपत्ते|दुपत्ता|दोपत्ते|dupatte|dupatta|dopatte)(?!\S)", re.IGNORECASE)
    _UNIT_AFTER = r"\s*(?:के\s+|ke\s+)?(?:पत्ते|पत्ता|पत्ती|स्ट्रिप|strips?|patte|patta|बोतल|bottles?|गोली|goli|tablets?|डिब्बे|डिब्बा|box)(?!\w)"

    def _normalize_heard(self, text: str) -> str:
        """Sarvam's ways of writing a phone order, put the way the parser expects.

        - "कॉम्बी फ्लेम" / "Livo Set": a name split in two is joined back when
          only the joined form is a catalogue name or alias.
        - "दुपत्ते": दो पत्ते said fast.
        - "ब्रूफेन 201 पत्ता": "200 एक पत्ता" written as one number.
        - "कैलपोल सिरप 250 एक बोतल": 250 is the listed strength, not a count.
        """
        text = self._GLUED_DO_PATTE.sub("दो पत्ते", text)
        text = english_number_words_to_digits(text)
        tokens = text.split()
        if len(tokens) >= 2:
            lexicon = set(self.matching._aliases()) | {
                normalize_text(m.medicine_name) for m in self.matching.repository.list_all_active()
            }
            joined_tokens = []
            index = 0
            while index < len(tokens):
                if index + 1 < len(tokens):
                    left = normalize_text(tokens[index])
                    right = normalize_text(tokens[index + 1])
                    glued = normalize_text(tokens[index] + tokens[index + 1])
                    if glued in lexicon and left not in lexicon and right not in lexicon:
                        joined_tokens.append(tokens[index] + tokens[index + 1])
                        index += 2
                        continue
                joined_tokens.append(tokens[index])
                index += 1
            text = " ".join(joined_tokens)
        strengths = self._catalogue_strengths()
        if not strengths:
            return text

        def split_number(match):
            number = match.group(1)
            if number in strengths:
                return match.group(0)
            value = int(number)
            for strength in sorted(strengths, key=len, reverse=True):
                rest = None
                if int(strength) >= 100 and 0 < value - int(strength) <= 9:
                    rest = str(value - int(strength))  # 201 = 200 + 1
                elif (
                    number.startswith(strength)
                    and len(number) - len(strength) == 1
                    and number[len(strength):] != "0"
                ):
                    rest = number[len(strength):]  # 6502 = 650 + 2
                if rest is not None:
                    return f"{strength} {rest}"
            return match.group(0)

        text = re.sub(rf"(?<!\d)(\d{{3,4}})(?={self._UNIT_AFTER})", split_number, text)
        return self._mark_strengths(text, strengths)

    def _mark_strengths(self, text: str, strengths: set[str] | None = None) -> str:
        """"कैलपोल सिरप 250 एक बोतल" / "वेलेंटास 100 के 20 पत्ते": two numbers
        in one request; the listed strength gets "mg" so it is not a count.
        The number right before पत्ते/बोतल is always the count. Lists are
        marked one item at a time."""
        if self._LIST_SEPARATOR.search(text) or not has_ambiguous_quantity(text):
            return text
        strengths = self._catalogue_strengths() if strengths is None else strengths
        for strength in sorted(strengths, key=len, reverse=True):
            if int(strength) >= 20:
                text = re.sub(
                    rf"(?<![\d.]){strength}(?!\d|\s*(?:mg|ml|एमजी|एमएल)(?!\w)|{self._UNIT_AFTER})",
                    f"{strength}mg", text, count=1, flags=re.IGNORECASE,
                )
        return text

    def _catalogue_strengths(self) -> set[str]:
        values = set()
        for medicine in self.matching.repository.list_all_active():
            candidate = MedicineMatchCandidate(
                medicine_id=medicine.medicine_id,
                medicine_name=medicine.medicine_name,
                description=medicine.description,
                match_type="EXACT_NAME",
            )
            values |= {v for v in attributes_from_candidate(candidate).strengths if v.isdigit()}
        return values

    _NAME_MARKER = re.compile(r"(?<!\S)(?:नाम|naam|name)(?!\S)", re.IGNORECASE)

    def _explicit_name(self, voice, state, text: str, language: str) -> TurnDraft | None:
        """"मेरा नाम नितिन प्रजापति है" at any point: save (or correct) the name."""
        if voice.status != ACTIVE or not self._NAME_MARKER.search(text):
            return None
        if self.matching.suggestions_from_utterance(text):
            return None
        name = extract_caller_name(text)
        if name is None:
            return None
        voice.order_session.customer_name = name[:255]
        state.name_asks = 2
        logger.info("voice.customer_name.saved chars=%s explicit=1", len(name))
        if state.pending is not None:
            follow = self._pending_question(state.pending, language)
        elif voice.order_session.items:
            follow = _text("और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?", "Anything else, or shall I confirm the order?", language)
        else:
            follow = _text("कौन सी दवा चाहिए, और कितनी? जैसे डोलो 650 दो पत्ते।", "Which medicine do you want, and how many?", language)
        return TurnDraft(
            intent=VoiceIntent.UNKNOWN,
            language=language,
            assistant_text=_text(f"धन्यवाद {name} जी। ", f"Thank you, {name}. ", language) + follow,
        )

    @staticmethod
    def _unclear_name_draft(language: str) -> TurnDraft:
        return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language, assistant_text=_text(
            "माफ़ कीजिए, दवा का नाम साफ़ सुनाई नहीं दिया। कृपया दवा का नाम दोबारा बताइए।",
            "Sorry, I did not catch the medicine name. Please say it again.", language))

    _CONTINUE_ORDER = re.compile(
        r"(?:और|aur)\s+(?:भी\s+|और\s+|कोई\s+|koi\s+)*(?:दवा|दवाई|दवाइयां|दवाइयाँ|दवाएं|मेडिसिन|medicine|medicines|dawa|dawai)"
        r"|(?:और|aur)\s+(?:कुछ\s+|kuch\s+|भी\s+)*(?:चाहिए|chahiye)|more medicines?|something else",
        re.IGNORECASE,
    )
    _TENTATIVE_CONFIRM = re.compile(
        r"(?:कन्फर्म|कंफर्म|confirm)\S*\s+(?:\S+\s+)?(?:कर\s+)?(?:दूं|दूँ|दू|दूंगा|दूँगा|दूंगी|करूं|करूँ|करू|करूंगा|करूँगा|करेंगे|karu|karun|dun|doon|dunga)(?![^\s।.,!?])"
        r"|(?:कन्फर्म|कंफर्म|confirm)[^?]*\?",
        re.IGNORECASE,
    )

    # "कन्फर्म कर दो" / "confirm karo": a clear go-ahead, even beside a "करूँ".
    _CLEAR_CONFIRM = re.compile(
        r"(?:कन्फर्म|कंफर्म|confirm)\S*\s+(?:कर\s+)?(?:दो|दीजिए|दीजिये|करो|करें|करिए|karo|kar\s+do|kardo)(?![^\s।.,!?])",
        re.IGNORECASE,
    )

    def _pending_question(self, pending, language: str) -> str:
        if pending.kind == "candidate":
            return self._candidate_retry_prompt(pending, language)
        candidate = MedicineMatchCandidate(
            medicine_id=pending.medicine_id,
            medicine_name=pending.medicine_name or "",
            description=pending.description or "",
            match_type="EXACT_NAME",
        )
        return self._quantity_prompt(candidate, pending.quantity, pending.unit, language)

    _NAMED_NOT_FOUND = re.compile(r"([^।.?]+?) (?:हमारी लिस्ट में नहीं मिली|is not in our list)")

    def _track_unresolved(self, state, heard: str, draft: TurnDraft, language: str) -> None:
        text = draft.assistant_text
        names = [m.group(1).strip().removeprefix("ठीक है, ") for m in self._NAMED_NOT_FOUND.finditer(text)]
        names = [n for n in names if not n.startswith(("यह", "This", "That"))]
        if not names and (text.startswith(not_found_text(language)) or "साफ़ सुनाई नहीं दिया" in text
                          or "did not catch the medicine" in text):
            query = " ".join(w for w in (self._parse_intent(heard, language, None).medicine_query or "").split()
                             if extract_quantity(w) is None)
            names = [query] if query else []
        for name in names:
            if name and name not in state.unresolved:
                state.unresolved = (state.unresolved + [name])[-5:]

    def _shorten_repeat(self, state, draft: TurnDraft, language: str) -> TurnDraft:
        """The same "not found" / "unclear quantity" twice in a row: say it short.

        Live call (the client, 2026-11-22): the full "यह दवा हमारी लिस्ट में
        नहीं मिली। कृपया दवा का नाम दोबारा बताइए, जैसे डोलो 650 या ब्रूफेन
        400।" was said three times in a row.
        """
        text = draft.assistant_text
        short = None
        if text == state.last_reply:
            if text == not_found_text(language):
                short = _text("यह भी लिस्ट में नहीं मिली। दवा का नाम धीरे-धीरे बोलिए या उसकी स्पेलिंग बताइए। "
                              "नहीं चाहिए तो 'छोड़ दो' बोलें।",
                              "That is not in our list either. Say the name slowly or spell it, or say 'skip' to leave it.", language)
            elif (state.pending is not None and state.pending.kind == "candidate" and state.pending.candidates
                  and state.pending.match_status != "STRENGTH_GUESS"
                  and len({normalize_text(c.medicine_name) for c in state.pending.candidates}) == 1):
                name = state.pending.candidates[0].medicine_name
                short = _text(
                    f"{name} अभी तय नहीं हुई। {self._variant_question(state.pending.candidates, language)} "
                    "या यह दवा छोड़ने के लिए 'नहीं' बोलें।",
                    f"{name} is still open. {self._variant_question(state.pending.candidates, language)} "
                    "Or say 'no' to skip it.", language)
            elif (state.pending is not None and state.pending.kind == "candidate" and state.pending.candidates
                  and state.pending.match_status != "STRENGTH_GUESS"):
                names = list(dict.fromkeys(c.medicine_name for c in state.pending.candidates))
                short = self._which_medicine_prompt(names, language) + _text(
                    " या यह दवा छोड़ने के लिए 'नहीं' बोलें।", " Or say 'no' to skip it.", language)
            elif text == self._repeat_quantity_text(language):
                short = _text("कृपया सिर्फ़ एक मात्रा बताइए, जैसे दो पत्ते।",
                              "Please say just one quantity, for example two strips.", language)
        state.last_reply = text
        return replace(draft, assistant_text=short) if short else draft

    def _maybe_take_name(self, voice, state, text: str, language: str) -> TurnDraft | None:
        """The greeting asks for the caller's name; it is saved with the order.

        Optional: a caller who names a medicine straight away is served at
        once, and after two unclear answers the name is not asked again.
        """
        order_session = voice.order_session
        if (
            voice.status != ACTIVE
            or order_session.customer_name
            or order_session.items
            or state.pending is not None
            or state.name_asks >= 2
            or (voice.turn_count > 0 and state.name_asks == 0)
        ):
            return None
        if self.matching.suggestions_from_utterance(text):
            return None
        parsed = self._parse_intent(text, language, None)
        if parsed.intent not in (VoiceIntent.UNKNOWN, VoiceIntent.ADD_ITEM) and not is_only_fillers(text):
            return None
        name = extract_caller_name(text)
        if name is not None and parsed.medicine_query:
            result = self.matching.search(parsed.medicine_query)
            if result.status != "NOT_FOUND" or result.candidates:
                return None  # sounds like a medicine
        if name is None:
            # Only "हेलो" / "हाँ जी" (nothing but greeting words) asks again;
            # anything else is handled as an order request.
            if not is_only_fillers(text):
                return None
            state.name_asks += 1
            if state.name_asks >= 2:
                return TurnDraft(
                    intent=VoiceIntent.UNKNOWN,
                    language=language,
                    assistant_text=_text(
                        "कोई बात नहीं। कौन सी दवा चाहिए? दवा का नाम बताइए, जैसे डोलो 650 या ब्रूफेन 400।",
                        "No problem. Which medicine do you want? For example Dolo 650 or Brufen 400.",
                        language,
                    ),
                )
            return TurnDraft(
                intent=VoiceIntent.UNKNOWN,
                language=language,
                assistant_text=_text(
                    "जी, कृपया अपना नाम बताइए। या सीधे दवा का नाम बोलिए।",
                    "Sorry, please tell me your name, or say the medicine name.",
                    language,
                ),
            )
        order_session.customer_name = name[:255]
        state.name_asks = 2
        logger.info("voice.customer_name.saved chars=%s", len(name))
        return TurnDraft(
            intent=VoiceIntent.UNKNOWN,
            language=language,
            assistant_text=_text(
                f"धन्यवाद {name} जी। कौन सी दवा चाहिए, और कितनी? जैसे डोलो 650 दो पत्ते।",
                f"Thank you, {name}. Which medicine do you want, and how many? For example two strips of Dolo 650.",
                language,
            ),
        )

    @staticmethod
    def _brief(draft: TurnDraft) -> str:
        text = draft.assistant_text
        for marker in ("ऑर्डर में अभी", "The draft now has", "कौन सी दवा चाहिए", "Which medicine"):
            if marker in text:
                return text.split(marker, 1)[0].strip()
        return text

    def _handle_utterance(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
        *,
        parsed: ParsedIntent | None = None,
    ) -> TurnDraft:
        if parsed is None:
            text = self._normalize_heard(text)
            named = self._maybe_take_name(voice, state, text, language) or self._explicit_name(
                voice, state, text, language
            )
            if named is not None:
                return named
            letters = [ch for ch in text if ch.isalpha()]
            if letters and sum(1 for ch in letters if ch.isascii() or "\u0900" <= ch <= "\u097f") < len(letters) / 2:
                follow = (
                    self._pending_question(state.pending, language) if state.pending is not None
                    else _text("कृपया दवा का नाम दोबारा बताइए, जैसे डोलो 650 दो पत्ते।", "Please say the medicine again, for example two strips of Dolo 650.", language)
                )
                return TurnDraft(
                    intent=VoiceIntent.UNKNOWN,
                    language=language,
                    assistant_text=_text("माफ़ कीजिए, साफ़ सुनाई नहीं दिया। ", "Sorry, I could not hear that clearly. ", language) + follow,
                )
            if state.pending is not None and is_only_greeting(text):
                # Live call: "Hello" while a quantity was asked got the help
                # text. Repeat the question instead.
                return TurnDraft(
                    intent=VoiceIntent.SELECT_CANDIDATE,
                    language=language,
                    assistant_text=self._pending_question(state.pending, language),
                )
        if parsed is None and voice.status == ACTIVE:
            parts = self._split_medicines(text)
            if parts and not voice.order_session.customer_name and self._NAME_MARKER.search(text):
                name = extract_caller_name(re.split(r"[।.]", self._LIST_SEPARATOR.split(text)[0])[0])
                if name:
                    voice.order_session.customer_name = name[:255]
                    state.name_asks = 2
            if parts:
                # Live call (the client): a whole list said while the AI was
                # asking about MAXTRA P was refused as "unclear quantity".
                state.pending = None
                state.queued = parts[1:]
                text = parts[0]
        in_list = bool(state.queued)
        before = len(voice.order_session.items)
        draft = self._fraction_strength_draft(state, text, language) or self._handle_text(
            voice, state, text, language, parsed=parsed
        )
        said: list[str] = []
        while state.queued and voice.status == ACTIVE:
            if draft.intent not in (VoiceIntent.ADD_ITEM, VoiceIntent.SELECT_CANDIDATE):
                state.queued = []  # confirm, cancel, a correction...: start over
                break
            if state.pending is not None:
                break  # a variant or quantity question first; the rest waits
            brief = self._list_brief(draft, text, before, voice, language)
            if said:
                for opener in ("ठीक है, ", "Okay, "):
                    brief = brief.removeprefix(opener)
            said.append(brief)
            text = state.queued.pop(0)
            before = len(voice.order_session.items)
            draft = self._fraction_strength_draft(state, text, language) or self._handle_text(
                voice, state, text, language
            )
        if (said or in_list) and draft.intent in (VoiceIntent.ADD_ITEM, VoiceIntent.SELECT_CANDIDATE):
            if state.pending is None and not state.queued and len(voice.order_session.items) == before:
                # The last item of a list was not found: say so briefly.
                draft = replace(draft, order_summary=self._safe_summary(self.orders.get_summary(voice.order_session.session_id), language) if voice.order_session.items else None, assistant_text=self._list_brief(draft, text, before, voice, language) + " " + _text(
                    "और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?",
                    "Anything else, or shall I confirm the order?",
                    language,
                ))
            rest = draft.assistant_text
            if said:
                for opener in ("ठीक है, ", "Okay, "):
                    rest = rest.removeprefix(opener)
            draft = replace(draft, assistant_text=" ".join(said + [rest]))
        return draft

    _FRACTION_STRENGTH = re.compile(r"(?<!\S)(ढाई|साढ़े\s+\S+|डेढ़|\d+\.\d+)(?!\S)")

    def _fraction_strength_draft(self, state, text: str, language: str) -> TurnDraft | None:
        """No listed medicine has a 2.5 or 1.5 strength; "ढाई" must not be
        dropped and the 10MG pack added in its place."""
        if state.pending is not None:
            return None
        match = self._FRACTION_STRENGTH.search(text)
        if match is None:
            return None
        mentioned = self.matching.suggestions_from_utterance(text)
        names = list(dict.fromkeys(c.medicine_name for c in mentioned))
        if len(names) != 1:
            return None
        labels = " / ".join(spoken_name(c, alternatives=mentioned) for c in mentioned)
        said = {"ढाई": "2.5", "डेढ़": "1.5"}.get(match.group(1), match.group(1))
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            assistant_text=_text(
                f"{names[0]} {said} हमारी लिस्ट में नहीं है। लिस्ट में {labels} है।",
                f"{names[0]} {said} is not in our list. We have {labels}.",
                language,
            ),
        )

    def _list_brief(self, draft, part: str, before: int, voice, language: str) -> str:
        if len(voice.order_session.items) > before or draft.order_summary is not None and (
            "जोड़ दी" in draft.assistant_text or "Added" in draft.assistant_text
        ):
            return self._brief(draft)
        if "लिस्ट में नहीं है" in draft.assistant_text or "is not in our list" in draft.assistant_text:
            return draft.assistant_text
        if "पहले से ऑर्डर में है" in draft.assistant_text or "already in the order" in draft.assistant_text:
            return draft.assistant_text
        for skipped in ("ठीक है, वह दवा नहीं जोड़ी।", "Okay, I did not add it."):
            if draft.assistant_text.startswith(skipped):
                return skipped
        heard = (self._parse_intent(part, language, None).medicine_query or part).strip()
        heard = heard.upper() if heard.isascii() else heard
        return _text(f"{heard} हमारी लिस्ट में नहीं मिली।", f"{heard} is not in our list.", language)

    def _handle_text(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
        *,
        parsed: ParsedIntent | None = None,
    ) -> TurnDraft:
        if parsed is None:
            parsed = self._parse_intent(text, language, state.pending)

        rest = ""
        if self._CONTINUE_ORDER.search(text):
            rest = re.sub(r"^[\s,.।]*(?:भी\s+)?(?:चाहिए|चाहिये|chahiye)?[\s,.।]*", "",
                          self._CONTINUE_ORDER.split(text, 1)[-1])
        if rest and (self.matching.suggestions_from_utterance(rest) or extract_quantity_unit(rest)[0] is not None):
            # Live Exotel call 12:57: "हाँ और दवाई भी चाहिए। जीरोम 500 के चार
            # पत्ते ऐड कर दो" read the order back. The new item is handled.
            state.summary_digest = None
            first_word = (normalize_text(text).split() or [""])[0]
            said = ""
            if (state.pending is not None and state.pending.match_status == "STRENGTH_GUESS"
                    and is_plain_affirmative(first_word)):
                guess, state.pending = state.pending, None
                said = self._brief(self._add_candidate(voice, state, guess.candidates[0], guess.quantity, guess.unit, language)) + " "
            draft = self._handle_utterance(voice, state, rest, language)
            return replace(draft, assistant_text=said + draft.assistant_text) if said else draft
        if (self._CONTINUE_ORDER.search(text) and not self.matching.suggestions_from_utterance(text)
                and extract_quantity_unit(text)[0] is None):
            # Live call: "हाँ, और दवाई भी चाहिए" read the order back as final.
            state.summary_digest = None
            first_word = (normalize_text(text).split() or [""])[0]
            if (state.pending is not None and state.pending.match_status == "STRENGTH_GUESS"
                    and is_plain_affirmative(first_word)):
                # "हाँ, और दवाई भी चाहिए" to "क्या आपको BRUFEN 200MG चाहिए?": yes, and more.
                guess, state.pending = state.pending, None
                added = self._add_candidate(voice, state, guess.candidates[0], guess.quantity, guess.unit, language)
                if state.pending is None:
                    return replace(added, assistant_text=self._brief(added) + _text(
                        " और कौन सी दवा चाहिए?", " Which other medicine?", language))
                return added
            follow = (self._pending_question(state.pending, language) if state.pending is not None
                      else _text("और कौन सी दवा चाहिए? नाम और मात्रा बताइए।",
                                 "Which other medicine do you want? Say its name and quantity.", language))
            return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language,
                             assistant_text=_text("जी, बताइए। ", "Sure. ", language) + follow)

        if (self._TENTATIVE_CONFIRM.search(text) and not self._CLEAR_CONFIRM.search(text)
                and voice.order_session.items):
            # Live call: "कन्फर्म कर दूं।" saved the order. A question or "shall
            # I" is not a go-ahead: read the order and ask for a clear yes.
            state.pending = None
            state.queued = []
            draft = self._show_summary(voice, state, language)
            return replace(draft, assistant_text=draft.assistant_text + _text(
                " पक्का करने के लिए साफ़ बोलें: हाँ, कन्फर्म करो।",
                " To place it, say clearly: yes, confirm.", language))

        if (
            state.pending is not None
            and voice.order_session.items
            and is_nothing_more(text)
            and not is_plain_negative(text)
        ):
            # Live call: "बस" while MAXTRA P's variant was asked repeated the
            # question. The caller is done: drop that item, read the order.
            state.pending = None
            state.deferred = []
            state.queued = []
            draft = self._show_summary(voice, state, language)
            return replace(draft, assistant_text=_text(
                "ठीक है, वह दवा नहीं जोड़ी। ", "Okay, I did not add it. ", language,
            ) + draft.assistant_text)

        if state.pending is not None and is_plain_negative(text):
            # "नहीं" to "BRUFEN 200MG चाहिए या 400MG?" or to a quantity
            # question drops that item only; the order stays as it is.
            state.pending = None
            resumed = self._resume_deferred(state)
            if resumed is not None:
                return TurnDraft(
                    intent=VoiceIntent.ADD_ITEM,
                    language=language,
                    assistant_text=_text(
                        "ठीक है, वह दवा नहीं जोड़ी। अब " + self._deferred_question(resumed, "hi"),
                        "Okay, I did not add it. Next: " + self._deferred_question(resumed, "en"),
                        language,
                    ),
                )
            if state.queued:
                return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language,
                                 assistant_text=_text("ठीक है, वह दवा नहीं जोड़ी।", "Okay, I did not add it.", language))
            if voice.order_session.items:
                return TurnDraft(
                    intent=VoiceIntent.ADD_ITEM,
                    language=language,
                    assistant_text=_text(
                        "ठीक है, वह दवा नहीं जोड़ी। और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?",
                        "Okay, I did not add it. Anything else, or shall I confirm the order?",
                        language,
                    ),
                )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    "ठीक है, वह दवा नहीं जोड़ी। कौन सी दवा चाहिए?",
                    "Okay, I did not add it. Which medicine do you want?",
                    language,
                ),
            )

        if state.pending is None and not voice.order_session.items and is_nothing_more(text):
            return TurnDraft(
                intent=VoiceIntent.UNKNOWN,
                language=language,
                assistant_text=_text(
                    "ठीक है। कौन सी दवा चाहिए? दवा का नाम बताइए।",
                    "Okay. Which medicine do you want? Please say its name.",
                    language,
                ),
            )
        if state.pending is None and voice.order_session.items and is_nothing_more(text):
            if state.summary_digest is None:
                # "नहीं" to "और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?" means
                # "nothing more": read the order back for confirmation. It used
                # to start a correction, and every "कन्फर्म" after was refused.
                return self._show_summary(voice, state, language)
            # "नहीं" to the read-back: something is wrong. Ask what, without
            # blocking a later confirm.
            state.summary_digest = None
            return TurnDraft(
                intent=VoiceIntent.UPDATE_ITEM,
                language=language,
                assistant_text=_text(
                    "ठीक है। क्या बदलना है? दवा का नाम और नई मात्रा बताइए, या कोई और दवा बताइए।",
                    "Okay. What should I change? Say the medicine and the new quantity, or another medicine.",
                    language,
                ),
            )

        if state.pending is not None and is_plain_affirmative(text) and (
            (state.pending.kind != "candidate" and state.pending.quantity is None)
            or (state.pending.kind == "candidate" and state.pending.match_status != "STRENGTH_GUESS"
                and len(state.pending.candidates) > 1)
        ):
            return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language,
                             assistant_text=self._pending_question(state.pending, language))

        if (
            state.pending is not None
            and state.pending.match_status == "STRENGTH_GUESS"
            and is_plain_affirmative(text)
        ):
            guess = state.pending
            state.pending = None
            self._log_matching("strength_guess", text, guess.candidates, guess.candidates[0], "CONFIRMED")
            return self._add_candidate(
                voice, state, guess.candidates[0], guess.quantity, guess.unit, language
            )

        if (
            state.pending is None
            and state.summary_digest is not None
            and state.guard.can_confirm(state.summary_digest)
            and sounds_like_confirm(text)
        ):
            # Live calls: after "सही हो तो कन्फर्म कहें" Whisper wrote कन्फर्म as
            # "GUNFORM" / "PANFAM", the agent said "not found" and the order was
            # lost at the last step. Only right after the read-back.
            return self._handle_confirm(voice, state, language)

        # Correction markers take precedence over optional LLM/add classification,
        # but never over an explicit control phrase. Hindi/English confirmation
        # phrases such as "confirm kar do" contain correction words ("kar do").
        control_intents = {
            VoiceIntent.CONFIRM_ORDER,
            VoiceIntent.CANCEL_ORDER,
            VoiceIntent.SHOW_SUMMARY,
            VoiceIntent.END_CALL,
            VoiceIntent.HELP,
        }
        if (
            is_correction_request(text)
            and voice.status == ACTIVE
            # Live Exotel call 12:57: "हाँ हाँ दोनों 650 की चार की जगह सात तक"
            # right after the read-back confirmed the order unchanged.
            and (parsed.intent not in control_intents or is_strong_correction(text))
        ):
            state.guard.correction_started()
            state.summary_digest = None
            state.pending = None
            try:
                return self._handle_update(voice, state, ParsedIntent(intent=VoiceIntent.UPDATE_ITEM), text, language)
            except (OrderError, ValidationError):
                return TurnDraft(intent=VoiceIntent.UPDATE_ITEM, language=language,
                    assistant_text=_text("बदलाव पूरा नहीं हुआ। दवा, नई मात्रा और इकाई बताएं।", "The correction was not applied. Specify the medicine, new quantity and unit.", language))

        if parsed.intent == VoiceIntent.UPDATE_ITEM:
            state.guard.correction_started()
            state.summary_digest = None

        if parsed.intent == VoiceIntent.CONFIRM_ORDER:
            return self._handle_confirm(voice, state, language)
        if parsed.intent == VoiceIntent.CANCEL_ORDER:
            return self._handle_cancel(voice, state, language)
        if parsed.intent == VoiceIntent.SHOW_SUMMARY:
            return self._show_summary(voice, state, language)
        if parsed.intent == VoiceIntent.END_CALL:
            return self._handle_end(voice, state, language)
        if parsed.intent == VoiceIntent.HELP:
            return self._handle_help(state, language)
        if (
            parsed.intent == VoiceIntent.ADD_ITEM
            and has_ambiguous_quantity(text)
            and not self._has_ambiguous_strength_only(text)
        ):
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=self._repeat_quantity_text(language),
            )

        if state.pending is not None:
            if state.pending.kind == "candidate":
                return self._handle_candidate_pending(
                    voice, state, text, language
                )
            return self._handle_quantity_pending(
                voice, state, text, language
            )

        strength_value = self._strength_only_value(text)
        if strength_value is not None:
            return self._offer_by_strength(voice, state, strength_value, language)
        guess = self._strength_with_count(text)
        if guess is not None:
            value, guess_quantity, guess_unit = guess
            return self._offer_by_strength(
                voice, state, value, language, guess_quantity, guess_unit, ask_first=True
            )
        if parsed.intent == VoiceIntent.SELECT_CANDIDATE:
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                assistant_text=_text(
                    "कौन सी दवा चाहिए? कृपया दवा का नाम बताएं, जैसे ब्रूफेन 400।",
                    "Which medicine do you want? Please say its name, for example BRUFEN 400.",
                    language,
                ),
            )
        if parsed.intent == VoiceIntent.UPDATE_ITEM:
            return self._handle_update(
                voice, state, parsed, text, language
            )
        if parsed.intent == VoiceIntent.REMOVE_ITEM:
            return self._handle_remove(
                voice, state, text, language
            )
        if parsed.intent == VoiceIntent.ADD_ITEM:
            return self._handle_add(
                voice, state, parsed, text, language
            )
        return TurnDraft(
            intent=VoiceIntent.UNKNOWN,
            language=language,
            assistant_text=_text(
                "मैं समझ नहीं पाया। उदाहरण: ब्रूफेन दो स्ट्रिप जोड़ो, सारांश दिखाओ, या ऑर्डर कन्फर्म करो।",
                "I did not understand. For example: add two strips of BRUFEN, show summary, or confirm the order.",
                language,
            ),
        )

    def _parse_intent(
        self,
        text: str,
        language: str,
        pending: VoicePendingState | None,
    ) -> ParsedIntent:
        parser = self._intent_parser
        if parser is None:
            parser = build_intent_parser(self.settings)
            self._intent_parser = parser
        try:
            return parser.parse(
                text, language=language, pending=pending
            )
        except Exception as exc:
            logger.warning("Intent extraction failed: %s", exc)
            return DeterministicIntentParser().parse(
                text, language=language, pending=pending
            )

    def _handle_add(
        self,
        voice: VoiceSession,
        state: VoiceState,
        parsed: ParsedIntent,
        text: str,
        language: str,
    ) -> TurnDraft:
        quantity = parsed.quantity
        unit = parsed.unit
        if parsed.medicine_id:
            medicine = self.medicine_repository.get_by_medicine_id(
                parsed.medicine_id
            )
            if medicine is None or not medicine.is_active:
                return TurnDraft(
                    intent=VoiceIntent.ADD_ITEM,
                    language=language,
                    assistant_text=_text(
                        "यह medicine ID स्वीकृत कैटलॉग में नहीं मिली। कृपया दवा का नाम बताएं।",
                        "That medicine ID is not in the approved catalogue. Please say the medicine name.",
                        language,
                    ),
                )
            candidate = MedicineMatchCandidate(
                medicine_id=medicine.medicine_id,
                medicine_name=medicine.medicine_name,
                description=medicine.description,
                match_type="EXACT_NAME",
            )
            if not strictly_matches(
                candidate, self._selection_attributes(text, [candidate])
            ):
                self._log_matching("add_id", text, [candidate], None, "ATTRIBUTE_CONFLICT")
                return self._not_found_draft(state, language)
            self._log_matching("add_id", text, [candidate], candidate, "STRICT_MATCH")
            return self._add_candidate(
                voice, state, candidate, quantity, unit, language
            )

        query = (parsed.medicine_query or "").strip()
        if not query:
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    "कृपया दवा का नाम, मात्रा और इकाई बताएं, जैसे ब्रूफेन दो स्ट्रिप।",
                    "Please say the medicine name, quantity, and unit, for example two strips of BRUFEN.",
                    language,
                ),
            )

        mentioned = self.matching.suggestions_from_utterance(text)
        result = self.matching.search(query)
        if result.status == "NOT_FOUND" and not result.candidates and not mentioned:
            # "टेरॉम 500 की चार पत्री": the name alone is too far off, but the
            # spoken strength can confirm a numbered catalogue name (CEROM 500).
            limit = self.settings.max_order_item_quantity
            said = [w for w in normalize_text(normalize_spoken_strengths(text)).split() if w.isdigit() and int(w) > limit]
            if said:
                retry = self.matching.search(f"{query} {' '.join(said)}")
                if retry.candidates:
                    result = retry
        heard_names = {normalize_text(c.medicine_name) for c in mentioned}
        if (
            len(heard_names) == 1
            and result.candidates
            and {normalize_text(c.medicine_name) for c in result.candidates} != heard_names
        ):
            # "नेबीस्टार 5 के 50 पत्ते": the query lost the "5" and found
            # NEBISTAR; the caller named NEBISTAR-5.
            result = result.model_copy(update={
                "status": "EXACT_MATCH" if len(mentioned) == 1 else "CLARIFICATION_REQUIRED",
                "candidates": mentioned,
            })
        if result.status == "NOT_FOUND":
            if mentioned:
                selected = self._exact_name_with_strength(mentioned, query, text)
                if selected is not None:
                    self._log_matching("add_alias", text, mentioned, selected, "STRICT_MATCH")
                    return self._add_candidate(
                        voice, state, selected, quantity, unit, language
                    )
                result = result.model_copy(update={"candidates": mentioned})
        if result.status == "EXACT_MATCH":
            selected = select_strict_candidate(
                result.candidates,
                self._selection_attributes(text, result.candidates),
            )
            if selected is None:
                self._log_matching("add_exact", text, result.candidates, None, "ATTRIBUTE_CONFLICT")
                offer = self._strength_not_listed(
                    state, result.candidates, text, quantity, unit, language
                ) or self._listed_strengths_draft(state, result.candidates, text, quantity, unit, language)
                return offer or self._not_found_draft(state, language)
            self._log_matching("add_exact", text, result.candidates, selected, "STRICT_MATCH")
            return self._add_candidate(
                voice,
                state,
                selected,
                quantity,
                unit,
                language,
            )
        if result.status == "CLARIFICATION_REQUIRED":
            selected = self._select_candidate_from_strength(
                result.candidates, text
            ) or self._select_variant_by_words(result.candidates, text, unit)
            if selected is not None:
                self._log_matching("add_variant", text, result.candidates, selected, "STRICT_MATCH")
                return self._add_candidate(
                    voice, state, selected, quantity, unit, language
                )
            spoken_form = self._selection_attributes(text, result.candidates)
            if spoken_form.strengths and not any(strictly_matches(c, spoken_form) for c in result.candidates):
                listed = self._listed_strengths_draft(state, result.candidates, text, quantity, unit, language)
                if listed is not None:
                    self._log_matching("add_variant", text, result.candidates, None, "ATTRIBUTE_CONFLICT")
                    return listed
            self._log_matching("add_variant", text, result.candidates, None, "CLARIFICATION_REQUIRED")
            if spoken_form.formulations and not spoken_form.strengths:
                fitting = [c for c in result.candidates if strictly_matches(c, spoken_form)]
                if 1 < len(fitting) < len(result.candidates):
                    result = result.model_copy(update={"candidates": fitting})
            if not spoken_form.strengths:
                result = result.model_copy(update={"candidates": self._narrow_by_pack(result.candidates, unit)})
            state.pending = VoicePendingState(
                kind="candidate",
                candidates=result.candidates,
                quantity=quantity,
                unit=unit,
                requested_text=query,
                match_status="CLARIFICATION_REQUIRED",
            )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                candidates=result.candidates,
                assistant_text=self._candidate_prompt(
                    result.candidates, language, fuzzy=False
                ),
            )

        if result.candidates:
            spoken = self._selection_attributes(text, result.candidates)
            if spoken.strengths and not mentioned:
                # Live call: "इरोम ₹500 के पांच पत्ते" was offered CREMA ANO 20GM.
                # A near name whose own strength contradicts the spoken one is
                # another medicine.
                kept = [c for c in result.candidates
                        if strictly_matches(c, spoken) or not description_strength_tokens(
                            f"{c.medicine_name} {c.description or ''}")]
                if not kept:
                    return self._unclear_name_draft(language)
                result = result.model_copy(update={"candidates": kept})
            strict_candidates = [
                candidate for candidate in result.candidates
                if strictly_matches(candidate, spoken)
            ]
            name_heard = (
                bool(mentioned)
                and bool(strict_candidates)
                and len(
                    {normalize_text(c.medicine_name) for c in result.candidates}
                ) == 1
            )
            if name_heard and (spoken.strengths or spoken.formulations) and len(strict_candidates) == 1:
                # "brufin car so chahiye" (चार सौ): the name and a strength that
                # fits exactly one variant.
                selected = strict_candidates[0]
                self._log_matching("add_fuzzy", text, result.candidates, selected, "STRICT_MATCH")
                return self._add_candidate(
                    voice, state, selected, quantity, unit, language
                )
            if name_heard and spoken.formulations and not spoken.strengths and 1 < len(strict_candidates) < len(result.candidates):
                result = result.model_copy(update={"candidates": strict_candidates})
            if name_heard and not spoken.strengths and len(result.candidates) > 1:
                narrowed_packs = self._narrow_by_pack(result.candidates, unit)
                if len(narrowed_packs) == 1:
                    self._log_matching("add_fuzzy", text, result.candidates, narrowed_packs[0], "STRICT_MATCH")
                    return self._add_candidate(voice, state, narrowed_packs[0], quantity, unit, language)
                result = result.model_copy(update={"candidates": narrowed_packs})
                strict_candidates = [c for c in strict_candidates if c in narrowed_packs] or strict_candidates
            if name_heard and len(result.candidates) > 1:
                one = self._select_variant_by_words(strict_candidates, text, unit)
                if one is not None:
                    # "Limsi Zinc के दो पत्ते" / "Livo Set सिरप एक बोतल".
                    self._log_matching("add_fuzzy", text, result.candidates, one, "STRICT_MATCH")
                    return self._add_candidate(voice, state, one, quantity, unit, language)
            if name_heard and len(result.candidates) == 1:
                selected = strict_candidates[0]
                self._log_matching("add_fuzzy", text, result.candidates, selected, "STRICT_MATCH")
                return self._add_candidate(
                    voice, state, selected, quantity, unit, language
                )
            heard_names = list(dict.fromkeys(c.medicine_name for c in result.candidates))
            separate = self._strength_apart_from_names(text, result.candidates) if mentioned else None
            if mentioned and len(heard_names) > 1 and separate is not None:
                # Live call: "brufan 200 MG CEROM" (CEROM from Whisper's hint);
                # 200 MG fits only BRUFEN 200MG, so there is nothing to ask.
                selected = separate
                self._log_matching("add_fuzzy", text, result.candidates, selected, "STRICT_MATCH")
                return self._add_candidate(
                    voice, state, selected, quantity, unit, language
                )
            if mentioned and len(heard_names) > 1:
                # Live call: "brufan, dolo650, cainu650". Whisper often adds a
                # name from its hint, so ask which one by name instead of
                # listing every variant of both and asking for the full name.
                self._log_matching("add_fuzzy", text, result.candidates, None, "NAMES_HEARD")
                state.pending = VoicePendingState(
                    kind="candidate",
                    candidates=result.candidates,
                    quantity=quantity,
                    unit=unit,
                    requested_text=query,
                    match_status="NAMES_HEARD",
                )
                return TurnDraft(
                    intent=VoiceIntent.ADD_ITEM,
                    language=language,
                    candidates=result.candidates,
                    assistant_text=self._which_medicine_prompt(heard_names, language),
                )
            if not name_heard and len(heard_names) == 1:
                # "Pantosif के दो पत्ते" / "मोटेयर 10 एमजी": the name was only
                # nearly heard. Say the listed name and ask, like a pharmacist:
                # "क्या आपको MONTAIR 10MG की 2 स्ट्रिप चाहिए?" Nothing is added
                # without the caller's yes.
                narrowed = (
                    [c for c in result.candidates if strictly_matches(c, spoken)]
                    if spoken.strengths else list(result.candidates)
                )
                if len(narrowed) > 1:
                    one = self._select_variant_by_words(narrowed, text, unit)
                    narrowed = [one] if one is not None else narrowed
                if len(narrowed) > 1 and unit in (OrderUnit.STRIP, OrderUnit.TABLET, OrderUnit.BOTTLE):
                    wanted = OrderUnit.BOTTLE if unit == OrderUnit.BOTTLE else OrderUnit.STRIP
                    packs = [c for c in narrowed if default_unit_for(c.description) == wanted]
                    narrowed = packs or narrowed
                if len(narrowed) == 1:
                    self._log_matching("add_fuzzy", text, result.candidates, None, "STRENGTH_GUESS")
                    state.pending = VoicePendingState(
                        kind="candidate",
                        candidates=narrowed,
                        quantity=quantity,
                        unit=unit,
                        requested_text=query,
                        match_status="STRENGTH_GUESS",
                    )
                    return TurnDraft(
                        intent=VoiceIntent.ADD_ITEM,
                        language=language,
                        candidates=narrowed,
                        assistant_text=self._strength_guess_question(state.pending, language),
                    )
            self._log_matching("add_fuzzy", text, result.candidates, None, "CLARIFICATION_REQUIRED")
            state.pending = VoicePendingState(
                kind="candidate",
                candidates=result.candidates,
                quantity=quantity,
                unit=unit,
                requested_text=query,
                match_status="NAME_HEARD" if name_heard else "NOT_FOUND",
            )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                candidates=result.candidates,
                assistant_text=self._candidate_prompt(
                    result.candidates, language, fuzzy=not name_heard
                ),
            )
        self._log_matching("add", text, [], None, "NOT_FOUND")
        offer = self._strength_not_listed(
            state, mentioned, text, quantity, unit, language
        ) or self._listed_strengths_draft(state, mentioned, text, quantity, unit, language)
        if offer is None and not mentioned:
            # Live Exotel call 13:38: "दोनों 650 के 4 पत्ते" (DOLO 650 misheard).
            # A strength only one listed medicine has is offered as a yes/no.
            limit = self.settings.max_order_item_quantity
            values = {w for w in normalize_text(normalize_spoken_strengths(text)).split() if w.isdigit() and int(w) > limit}
            if len(values) == 1:
                value = next(iter(values))
                owners = {normalize_text(c.medicine_name) for c in self._strength_candidates(value)}
                if len(owners) == 1:
                    return self._offer_by_strength(voice, state, value, language, quantity, unit, ask_first=True)
        return offer or self._not_found_draft(state, language)

    def _add_candidate(
        self,
        voice: VoiceSession,
        state: VoiceState,
        candidate: MedicineMatchCandidate,
        quantity: int | None,
        unit: OrderUnit | None,
        language: str,
    ) -> TurnDraft:
        if unit in (OrderUnit.STRIP, OrderUnit.TABLET) and default_unit_for(
            candidate.description
        ) == OrderUnit.BOTTLE:
            # "दो पत्ते" for a syrup: Hindi callers say पत्ते for any pack. A
            # syrup comes in bottles; the read-back says "2 बोतल".
            unit = OrderUnit.BOTTLE
        if unit in (OrderUnit.STRIP, OrderUnit.TABLET, OrderUnit.BOTTLE) and _BOX_PACK.search(
            candidate.description or ""
        ) and default_unit_for(candidate.description) is None:
            # Live call: "ARACHITOL NANO SUGAR FREE की 2 बोतल" for a BOX pack.
            # The catalogue cannot tell how many bottles a box is: ask in boxes.
            state.pending = VoicePendingState(
                kind="quantity", medicine_id=candidate.medicine_id,
                medicine_name=candidate.medicine_name, description=candidate.description,
                quantity=None, unit=OrderUnit.BOX,
            )
            name = spoken_name(candidate)
            return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language, assistant_text=_text(
                f"{name} डिब्बे में आती है, {UNIT_LABELS_HI.get(unit, 'पत्ते')} में नहीं। कितने डिब्बे चाहिए?",
                f"{name} comes in boxes, not {unit.value.lower()}s. How many boxes?", language))
        if quantity is not None and unit is None:
            # "दो" for a 15'S tablet pack means two strips. The reply reads the
            # unit back and the order still needs an explicit confirmation.
            unit = default_unit_for(candidate.description)
        if quantity is None or unit is None:
            state.pending = VoicePendingState(
                kind="quantity",
                medicine_id=candidate.medicine_id,
                medicine_name=candidate.medicine_name,
                description=candidate.description,
                quantity=quantity,
                unit=unit,
            )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=self._quantity_prompt(
                    candidate, quantity, unit, language
                ),
            )

        same = next((i for i in voice.order_session.items if i.medicine_id == candidate.medicine_id
                     and i.quantity == quantity and i.unit == unit.value), None)
        if same is not None:
            # Live Exotel call 13:38: the caller said DOLO 650 x4 again and the
            # order got 8. The same item and count again is a repeat.
            state.pending = None
            label = spoken_name(candidate)
            unit_word = UNIT_LABELS_HI.get(unit, "") if language == "hi" else unit.value.lower()
            return TurnDraft(intent=VoiceIntent.ADD_ITEM, language=language, assistant_text=_text(
                f"{label} की {quantity} {unit_word} पहले से ऑर्डर में है, दोबारा नहीं जोड़ी।",
                f"{label}, {quantity} {unit_word}, is already in the order; not added again.", language))
        try:
            order_session = self.orders.add_item(
                voice.order_session.session_id,
                OrderItemAdd(
                    medicine_id=candidate.medicine_id,
                    quantity=quantity,
                    unit=unit,
                ),
            )
        except OrderError:
            state.pending = VoicePendingState(
                kind="quantity",
                medicine_id=candidate.medicine_id,
                medicine_name=candidate.medicine_name,
                description=candidate.description,
                quantity=None,
                unit=None,
            )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    "मात्रा 1 से 100 के बीच होनी चाहिए। कृपया मात्रा दोबारा बताएं।",
                    "Quantity must be between 1 and 100. Please say the quantity again.",
                    language,
                ),
            )
        state.pending = None
        state.summary_digest = None
        state.last_item_id = self._find_added_item_id(
            order_session, candidate.medicine_id, unit
        )
        summary = self._safe_summary_from_session(
            order_session, language
        )
        label = spoken_name(candidate)
        # Live call: BRUFEN 200 was chosen, then "DOLO 650" was said before
        # BRUFEN's quantity. Once DOLO is added, ask about BRUFEN again.
        state.deferred = [d for d in state.deferred if d.medicine_id != candidate.medicine_id]
        resumed = self._resume_deferred(state)
        next_hi = "और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?"
        next_en = "Anything else, or shall I confirm the order?"
        if resumed is not None:
            next_hi = "अब " + self._deferred_question(resumed, "hi")
            next_en = "Next: " + self._deferred_question(resumed, "en")
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            assistant_text=_text(
                f"ठीक है, {label} की "
                f"{quantity} {_unit_label(unit, language)} जोड़ दी। "
                f"ऑर्डर में अभी {summary.line_item_count} आइटम हैं। "
                + next_hi,
                f"Added {quantity} {_unit_label(unit, language)} of "
                f"{label}. "
                f"The draft now has {summary.line_item_count} line item(s). "
                + next_en,
                language,
            ),
            order_summary=summary,
        )

    def _mark_chosen(self, state, options: int) -> None:
        if options > 1 and state.pending is not None and state.pending.kind == "quantity":
            state.pending = state.pending.model_copy(update={"chosen": True})

    @staticmethod
    def _note_dropped(pending, draft: TurnDraft, language: str) -> TurnDraft:
        """A new request while a choice was open: the open item is said to be
        left out, never dropped silently (live call: CEROM vanished)."""
        names = list(dict.fromkeys(c.medicine_name for c in pending.candidates))
        if len(names) != 1 or names[0] in draft.assistant_text:
            return draft
        return replace(draft, assistant_text=_text(
            f"{names[0]} तय नहीं हुई थी, उसे छोड़ दिया। ", f"{names[0]} was not decided, so I left it out. ", language,
        ) + draft.assistant_text)

    def _defer(self, state, pending) -> None:
        if pending.medicine_id is None or any(
            d.medicine_id == pending.medicine_id for d in state.deferred
        ):
            return
        state.deferred = (state.deferred + [pending.model_copy(update={"retries": 0})])[-2:]

    def _resume_deferred(self, state):
        if state.pending is not None or not state.deferred:
            return None
        item, state.deferred = state.deferred[0], state.deferred[1:]
        state.pending = item.model_copy(update={"retries": 0, "chosen": True})
        return state.pending

    def _deferred_question(self, pending, language: str) -> str:
        candidate = MedicineMatchCandidate(
            medicine_id=pending.medicine_id,
            medicine_name=pending.medicine_name or "",
            description=pending.description or "",
            match_type="EXACT_NAME",
        )
        return self._quantity_prompt(candidate, pending.quantity, pending.unit, language)

    def _handle_candidate_pending(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
    ) -> TurnDraft:
        pending = state.pending
        if pending is None or not pending.candidates:
            state.pending = None
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                assistant_text=_text(
                    "विकल्प की जानकारी उपलब्ध नहीं है। कृपया दवा का नाम दोबारा बताएं।",
                    "The choice list is no longer available. Please say the medicine name again.",
                    language,
                ),
            )

        switched = self._names_another_medicine(
            text, {c.medicine_name for c in pending.candidates}
        )
        if switched is not None:
            return self._note_dropped(pending, switched(voice, state, language), language)

        parsed_new = self._parse_intent(text, language, None)
        if (
            self._explicit_add_request(text)
            and parsed_new.medicine_query
            and not any(
                self._mentions_candidate_name(text, candidate)
                for candidate in pending.candidates
            )
        ):
            state.pending = None
            return self._note_dropped(pending, self._handle_add(voice, state, parsed_new, text, language), language)

        by_words =self._select_variant_by_words(pending.candidates, text, extract_quantity_unit(text)[1])
        if by_words is not None:
            quantity, unit, _ = extract_quantity_unit(self._without_medicine_name(text, by_words))
            self._log_matching("candidate_pending", text, pending.candidates, by_words, "STRICT_MATCH")
            draft = self._add_candidate(voice, state, by_words, quantity or pending.quantity,
                                        (unit if quantity else None) or pending.unit, language)
            self._mark_chosen(state, len(pending.candidates))
            return draft
        if pending.match_status == "NAMES_HEARD":
            return self._narrow_to_named_medicine(voice, state, pending, text, language)
        if pending.match_status == "STRENGTH_GUESS":
            heard_quantity, heard_unit, _ = extract_quantity_unit(text)
            if heard_quantity is None and not extract_strength_tokens(text):
                heard_quantity = extract_quantity(text)
            if (
                pending.quantity is None
                and heard_quantity is not None
                and not self.matching.suggestions_from_utterance(text)
            ):
                # "क्या आपको PANTOCID TAB चाहिए?" answered with "दो": a count
                # for the offered medicine is a yes.
                state.pending = None
                self._log_matching("strength_guess", text, pending.candidates, pending.candidates[0], "CONFIRMED")
                return self._add_candidate(
                    voice, state, pending.candidates[0], heard_quantity, heard_unit, language
                )
            # Not a plain yes/no: a medicine or strength is a new request;
            # anything else repeats the question (at most three times).
            if (
                self.matching.suggestions_from_utterance(text)
                or extract_strength_tokens(text)
                or spoken_hundreds(text)
            ):
                state.pending = None
                return self._note_dropped(pending, self._handle_text(voice, state, text, language), language)
            gave_up = self._count_retry(state, pending, language)
            if gave_up is not None:
                return gave_up
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                candidates=pending.candidates,
                assistant_text=self._candidate_retry_prompt(pending, language),
            )

        # Live call: "650." while ARACHITOL's variant was being chosen. A
        # strength that only other medicines have means the caller moved on.
        strength_value = self._strength_only_value(text)
        if strength_value is not None:
            pending_ids = {c.medicine_id for c in pending.candidates}
            matches = self._strength_candidates(strength_value)
            if matches and not ({c.medicine_id for c in matches} & pending_ids):
                state.pending = None
                return self._offer_by_strength(voice, state, strength_value, language)

        spoken = self._selection_attributes(text, pending.candidates)
        selected = self._select_candidate(pending, text)
        if selected is None:
            explicit_name = any(
                self._mentions_candidate_name(text, candidate)
                for candidate in pending.candidates
            )
            has_attributes = bool(
                spoken.strengths or spoken.strength_units or spoken.formulations
            )
            if (explicit_name and has_attributes) or (
                spoken.strengths and len(pending.candidates) == 1
            ):
                self._log_matching(
                    "candidate_pending", text, pending.candidates, None,
                    "ATTRIBUTE_CONFLICT",
                )
                return self._not_found_draft(state, language)
            # Only the name again ("brufen") or an unclear answer: ask
            # again, and after three tries ask for the medicine afresh. A
            # count said meanwhile ("5 STRIP ...") is kept for the choice.
            heard_quantity, heard_unit, _ = extract_quantity_unit(text)
            updates = {}
            if heard_quantity is not None:
                updates["quantity"] = heard_quantity
            if heard_unit is not None:
                updates["unit"] = heard_unit
            gave_up = self._count_retry(state, pending, language, updates)
            if gave_up is not None:
                return gave_up
            self._log_matching(
                "candidate_pending", text, pending.candidates, None,
                "CLARIFICATION_REQUIRED",
            )
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                candidates=pending.candidates,
                assistant_text=self._candidate_retry_prompt(
                    pending, language
                ),
            )

        self._log_matching(
            "candidate_pending", text, pending.candidates, selected,
            "STRICT_MATCH",
        )
        quantity, unit, _ = extract_quantity_unit(text)
        if quantity is None:
            # "गोली वाली" picks the tablet variant; without a number it is not
            # the unit (CORFLAM TAB 15'S is still counted in strips).
            unit = None
        quantity = quantity or pending.quantity
        unit = unit or pending.unit
        draft = self._add_candidate(
            voice, state, selected, quantity, unit, language
        )
        self._mark_chosen(state, len(pending.candidates))
        return draft

    def _narrow_to_named_medicine(self, voice, state, pending, text, language):
        """Answer to "BRUFEN या DOLO 650?": keep the one medicine the caller
        named. One variant is added (quantity asked if missing); several
        variants are picked by strength or offered as पहला/दूसरा."""
        chosen = [c for c in pending.candidates if self._mentions_candidate_name(text, c)]
        names = list(dict.fromkeys(c.medicine_name for c in chosen))
        if not names:
            # "PAHEL DALO" (पहला डालो): pick by position in the question.
            asked = list(dict.fromkeys(c.medicine_name for c in pending.candidates))
            position = extract_ordinal(text)
            if position is not None and 1 <= position <= len(asked):
                wanted = normalize_text(asked[position - 1])
                chosen = [c for c in pending.candidates if normalize_text(c.medicine_name) == wanted]
                names = [asked[position - 1]]
        if not names:
            # Live call: the question was built from Whisper's hint echo
            # ("CREMAFFIN ... DOLO 650") and the caller named neither, five
            # times. Ask once more for an unclear answer; a clear new request
            # (a strength such as "650.") or a second miss starts afresh.
            if pending.retries == 0 and self._strength_only_value(text) is None:
                state.pending = pending.model_copy(update={"retries": 1})
                all_names = list(dict.fromkeys(c.medicine_name for c in pending.candidates))
                return TurnDraft(
                    intent=VoiceIntent.SELECT_CANDIDATE,
                    language=language,
                    candidates=pending.candidates,
                    assistant_text=self._which_medicine_prompt(all_names, language),
                )
            state.pending = None
            return self._handle_text(voice, state, text, language)
        if len(names) > 1:
            gave_up = self._count_retry(state, pending, language)
            if gave_up is not None:
                return gave_up
            all_names = list(dict.fromkeys(c.medicine_name for c in pending.candidates))
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                candidates=pending.candidates,
                assistant_text=self._which_medicine_prompt(all_names, language),
            )
        count_text = self._without_medicine_name(text, chosen[0])
        quantity, unit, _ = extract_quantity_unit(count_text)
        quantity = quantity or pending.quantity
        unit = unit or pending.unit
        if len(chosen) == 1:
            selected = chosen[0]
        else:
            selected = self._select_candidate_from_strength(chosen, text)
        if selected is not None:
            self._log_matching("candidate_pending", text, pending.candidates, selected, "STRICT_MATCH")
            draft = self._add_candidate(voice, state, selected, quantity, unit, language)
            self._mark_chosen(state, len(pending.candidates))
            return draft
        state.pending = pending.model_copy(update={
            "candidates": chosen, "match_status": "NAME_HEARD",
            "quantity": quantity, "unit": unit,
        })
        return TurnDraft(
            intent=VoiceIntent.SELECT_CANDIDATE,
            language=language,
            candidates=chosen,
            assistant_text=self._candidate_prompt(chosen, language, fuzzy=False),
        )

    def _count_retry(self, state, pending, language, updates=None):
        retries = pending.retries + 1
        if retries >= 3:
            state.pending = None
            return TurnDraft(
                intent=VoiceIntent.SELECT_CANDIDATE,
                language=language,
                assistant_text=_text(
                    "माफ़ कीजिए, मैं समझ नहीं पाया। कौन सी दवा चाहिए? दवा का नाम दोबारा बताएं।",
                    "Sorry, I did not catch that. Which medicine do you want? Please say its name again.",
                    language,
                ),
            )
        state.pending = pending.model_copy(update={"retries": retries, **(updates or {})})
        return None

    def _variant_question(self, candidates, language: str) -> str:
        """"BRUFEN 200MG चाहिए, 400MG या 600MG?" - the part that differs
        between variants, in catalogue order (so पहला/दूसरा still work)."""
        labels = [spoken_name(c, alternatives=candidates) for c in candidates]
        parts = [label.split() for label in labels]
        prefix: list[str] = []
        for column in zip(*parts):
            if all(word == column[0] for word in column):
                prefix.append(column[0])
            else:
                break
        rest = [p[len(prefix):] for p in parts]
        suffix: list[str] = []
        for column in zip(*(r[::-1] for r in rest)):
            if all(word == column[0] for word in column) and all(len(r) > len(suffix) + 1 for r in rest):
                suffix.insert(0, column[0])
            else:
                break
        options = [" ".join(r[: len(r) - len(suffix)]) for r in rest]
        if 2 <= len(options) <= 4 and prefix and all(options) and len(set(options)) == len(options):
            base = " ".join(prefix)
            middle_hi = "".join(f", {o}" for o in options[1:-1])
            middle_en = "".join(f", {o}" for o in options[1:-1])
            return _text(
                f"{base} {options[0]} चाहिए{middle_hi} या {options[-1]}?",
                f"Do you want {base} {options[0]}{middle_en} or {options[-1]}?",
                language,
            )
        if 2 <= len(labels) <= 4:
            return _text(
                ", ".join(labels[:-1]) + f" या {labels[-1]} में से कौन सी दवा चाहिए?",
                "Which one do you want: " + ", ".join(labels[:-1]) + f" or {labels[-1]}?",
                language,
            )
        return _text(
            "पहला, दूसरा, या strength बताएं: ",
            "Please say first, second, or strength: ",
            language,
        ) + self._format_candidates(candidates)

    _FORM_FAMILIES = {
        "tablet": {"tab", "tabs", "tablet", "tablets", "goli", "goliyan", "गोली",
                   "गोलियां", "गोलियाँ", "टैबलेट", "टेबलेट", "cap", "caps",
                   "capsule", "capsules", "कैप्सूल"},
        "liquid": {"syrup", "syp", "susp", "suspension", "सिरप", "सस्पेंशन",
                   "liquid", "sirap", "sirup"},
        "injection": {"inj", "injection", "injections", "इंजेक्शन", "injekshan"},
    }

    def _families(self, text: str) -> set[str]:
        tokens = {fold_latin(t) for t in normalize_text(text).split()}
        return {family for family, words in self._FORM_FAMILIES.items() if tokens & words}

    def _select_variant_by_words(self, candidates, text: str, unit) -> MedicineMatchCandidate | None:
        """"Limsi Zinc" / "Corflam के दो पत्ते" / "Calpol syrup": one variant of
        one medicine fits a word only its description has, its form (syrup,
        tablet), or the pack the caller counted in (पत्ते are tablets)."""
        if len({normalize_text(c.medicine_name) for c in candidates}) != 1 or len(candidates) < 2:
            return None
        if self._selection_strengths(text, candidates):
            return None  # a strength was said: only the strength decides
        selected = self._select_candidate_from_words(candidates, text) or self._select_candidate_by_form(candidates, text)
        if selected is not None:
            return selected
        if unit in (OrderUnit.STRIP, OrderUnit.TABLET, OrderUnit.BOTTLE):
            wanted = OrderUnit.BOTTLE if unit == OrderUnit.BOTTLE else OrderUnit.STRIP
            packs = [c for c in candidates if default_unit_for(c.description) == wanted]
            if len(packs) == 1:
                return packs[0]
        return None

    @staticmethod
    def _narrow_by_pack(candidates, unit):
        """"दो बॉटल" asks only between the liquids, "दो पत्ते" between tablets."""
        if unit not in (OrderUnit.STRIP, OrderUnit.TABLET, OrderUnit.BOTTLE) or len(candidates) < 2:
            return candidates
        wanted = OrderUnit.BOTTLE if unit == OrderUnit.BOTTLE else OrderUnit.STRIP
        packs = [c for c in candidates if default_unit_for(c.description) == wanted]
        return packs or candidates

    def _select_candidate_by_form(self, candidates, text):
        """"गोली वाली" / "syrup" / "injection" between a tablet and a liquid."""
        spoken = self._families(text)
        if len(spoken) != 1 or len(candidates) < 2:
            return None
        matches = [
            c for c in candidates
            if spoken & self._families(f"{c.medicine_name} {c.description or ''}")
        ]
        return matches[0] if len(matches) == 1 else None

    def _strength_only_value(self, text: str) -> str | None:
        """"650." / "600 mg" / "छह सौ": Whisper kept only the strength of
        "DOLO 650". A small number alone is a count, not a strength."""
        values = extract_strength_tokens(text) | spoken_hundreds(text)
        if len(values) != 1:
            return None
        value = next(iter(values))
        normalized = normalize_spoken_strengths(text)
        for token in normalize_text(normalized).split():
            if token.isdigit() or re.fullmatch(r"\d+(?:mg|mcg|ml|gm|g|iu)", token):
                continue
            if fold_latin(token) not in STRENGTH_ONLY_WORDS:
                return None
        has_unit = bool(re.search(r"\d\s*(?:mg|mcg|ml|gm|g|iu)(?!\w)", normalized, re.IGNORECASE))
        if not value.isdigit():
            return None
        if int(value) <= self.settings.max_order_item_quantity and not has_unit:
            return None
        return value

    def _strength_apart_from_names(self, text, candidates):
        """The one candidate fitting a strength said apart from the names:
        "brufan 200 MG CEROM" -> BRUFEN 200MG. The 650 in "DOLO 650" is part
        of a name, and digits stuck to junk ("cainu650") are not counted."""
        rest = text
        for candidate in {c.medicine_name: c for c in candidates}.values():
            rest = self._without_medicine_name(rest, candidate)
        rest = normalize_spoken_strengths(rest)
        said = {
            str(int(match.group(1)))
            for match in re.finditer(r"(?<![^\W\d_])(\d{2,4})(?!\d)", rest)
        } | spoken_hundreds(rest)
        for candidate in candidates:
            said -= pack_size_tokens(candidate.description or "")
        if not said:
            return None
        spoken = attributes_from_text(rest, strengths=said)
        fitting = [c for c in candidates if strictly_matches(c, spoken)]
        return fitting[0] if len(fitting) == 1 else None

    def _strength_with_count(self, text: str):
        """"200 के 2 पत्ते चाहिए" with the name lost: (strength, count, unit),
        when nothing but a strength, a count and filler words was heard."""
        quantity, unit, remaining = extract_quantity_unit(text)
        if quantity is None:
            return None
        values = extract_strength_tokens(text) | spoken_hundreds(remaining)
        if len(values) != 1:
            return None
        value = next(iter(values))
        has_unit = bool(re.search(r"\d\s*(?:mg|mcg|ml|gm|g|iu)(?!\w)", normalize_spoken_strengths(remaining), re.IGNORECASE))
        if not value.isdigit() or (int(value) <= self.settings.max_order_item_quantity and not has_unit):
            return None
        leftover = [
            token for token in clean_medicine_query(split_spoken_hundreds(remaining)[1]).split()
            if len(token) > 1 and fold_latin(token) not in STRENGTH_ONLY_WORDS
        ]
        if leftover:
            return None
        return value, quantity, unit

    def _strength_guess_question(self, pending, language: str) -> str:
        candidate = pending.candidates[0]
        label = spoken_name(candidate)
        if pending.quantity is None:
            return _text(
                f"क्या आपको {label} चाहिए? हाँ या नहीं बोलें।",
                f"Do you want {label}? Please say yes or no.",
                language,
            )
        unit = pending.unit or default_unit_for(candidate.description) or OrderUnit.STRIP
        return _text(
            f"क्या आपको {label} की {pending.quantity} {_unit_label(unit, 'hi')} चाहिए? हाँ या नहीं बोलें।",
            f"Do you want {pending.quantity} {_PLURAL_EN[OrderUnit(unit).value]} of {label}? Please say yes or no.",
            language,
        )

    def _strength_not_listed(
        self, state, candidates, text: str, quantity, unit, language: str
    ) -> TurnDraft | None:
        """"PANTOCID 40" when the list has PANTOCID TAB with no strength: offer it.

        Saying "not found" for a medicine that is in the list made callers give
        up. Nothing is added without the caller's yes or choice.
        """
        names = {normalize_text(c.medicine_name) for c in candidates}
        if not candidates or len(names) != 1:
            return None
        if not self._selection_attributes(text, candidates).strengths:
            return None
        if any(attributes_from_candidate(c).strengths for c in candidates):
            # A different listed strength ("DOLO 200" when only 650MG is
            # listed) is never offered in its place: a different dose.
            return None
        name = candidates[0].medicine_name
        status = "STRENGTH_GUESS" if len(candidates) == 1 else "CLARIFICATION_REQUIRED"
        state.pending = VoicePendingState(
            kind="candidate",
            candidates=candidates,
            quantity=quantity,
            unit=unit,
            requested_text=text,
            match_status=status,
        )
        question = (
            self._strength_guess_question(state.pending, language)
            if status == "STRENGTH_GUESS"
            else self._variant_question(candidates, language)
        )
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            candidates=candidates,
            assistant_text=_text(
                f"{name} की यह strength हमारी लिस्ट में नहीं है। ",
                f"That strength of {name} is not in our list. ",
                language,
            ) + question,
        )

    def _listed_strengths_draft(
        self, state, candidates, text: str, quantity, unit, language: str
    ) -> TurnDraft | None:
        """"वेलेंटास 200" when the list has 25, 50 and 100: say so and ask which.

        A different dose is never offered as a yes/no; the caller has to say
        the strength they want. Nothing is added on this turn.
        """
        names = {normalize_text(c.medicine_name) for c in candidates}
        heard = self._selection_attributes(text, candidates).strengths
        if not candidates or len(names) != 1 or not heard:
            return None
        if not any(attributes_from_candidate(c).strengths for c in candidates):
            return None
        name = candidates[0].medicine_name
        said = "/".join(sorted(heard))
        state.summary_digest = None
        # "VALENTAS 200 हमारी लिस्ट में नहीं है", but "DOLO 650 की 200 strength".
        missing_hi = f"{name} की {said} strength" if re.search(r"\d", name) else f"{name} {said}"
        missing_en = f"Strength {said} of {name}" if re.search(r"\d", name) else f"{name} {said}"
        if len(candidates) == 1:
            state.pending = None
            label = spoken_name(candidates[0])
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    f"{missing_hi} हमारी लिस्ट में नहीं है। लिस्ट में {label} है।",
                    f"{missing_en} is not in our list. We have {label}.",
                    language,
                ),
            )
        state.pending = VoicePendingState(
            kind="candidate",
            candidates=candidates,
            quantity=quantity,
            unit=unit,
            requested_text=text,
            match_status="CLARIFICATION_REQUIRED",
        )
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            candidates=candidates,
            assistant_text=_text(
                f"{missing_hi} हमारी लिस्ट में नहीं है। ",
                f"{missing_en} is not in our list. ",
                language,
            ) + self._variant_question(candidates, language),
        )

    def _strength_candidates(self, value: str) -> list[MedicineMatchCandidate]:
        candidates = []
        for medicine in self.matching.repository.list_all_active():
            candidate = MedicineMatchCandidate(
                medicine_id=medicine.medicine_id,
                medicine_name=medicine.medicine_name,
                description=medicine.description,
                match_type="EXACT_NAME",
            )
            if value in attributes_from_candidate(candidate).strengths:
                candidates.append(candidate)
        return sorted(candidates, key=lambda c: c.medicine_id)

    def _offer_by_strength(
        self, voice, state, value: str, language: str, quantity=None, unit=None,
        ask_first: bool = False,
    ) -> TurnDraft:
        """Only the strength was heard: offer the catalogue medicine(s) that
        have it. One match asks its quantity (the caller hears the name and
        can say नहीं); several ask which; none asks for the name."""
        candidates = self._strength_candidates(value)
        self._log_matching("strength_only", value, candidates, None, "STRENGTH_ONLY")
        if not candidates:
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    "कौन सी दवा चाहिए? कृपया दवा का नाम भी बताएं।",
                    "Which medicine do you want? Please say its name too.",
                    language,
                ),
            )
        if len(candidates) == 1 and ask_first and quantity is not None:
            # Live call: "200 K 2 PAKTA CHAHIYAY" (no name heard). Ask like a
            # pharmacist would instead of adding a guess or saying not found.
            unit = unit or default_unit_for(candidates[0].description)
            state.pending = VoicePendingState(
                kind="candidate",
                candidates=candidates,
                quantity=quantity,
                unit=unit,
                requested_text=value,
                match_status="STRENGTH_GUESS",
            )
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                candidates=candidates,
                assistant_text=self._strength_guess_question(state.pending, language),
            )
        if len(candidates) == 1:
            return self._add_candidate(voice, state, candidates[0], quantity, unit, language)
        names = list(dict.fromkeys(normalize_text(c.medicine_name) for c in candidates))
        status = "CLARIFICATION_REQUIRED" if len(names) == 1 else "NAMES_HEARD"
        state.pending = VoicePendingState(
            kind="candidate",
            candidates=candidates,
            quantity=quantity,
            unit=unit,
            requested_text=value,
            match_status=status,
        )
        if status == "CLARIFICATION_REQUIRED":
            prompt = self._candidate_prompt(candidates, language, fuzzy=False)
        else:
            labels = [spoken_name(c, alternatives=candidates) for c in candidates]
            prompt = self._which_medicine_prompt(labels, language)
        return TurnDraft(
            intent=VoiceIntent.ADD_ITEM,
            language=language,
            candidates=candidates,
            assistant_text=prompt,
        )

    def _which_medicine_prompt(self, names: list[str], language: str) -> str:
        return _text(
            "कौन सी दवा चाहिए: " + " या ".join(names) + "? सिर्फ़ दवा का नाम बताएं।",
            "Which medicine do you want: " + " or ".join(names) + "? Just say its name.",
            language,
        )

    def _without_medicine_name(
        self, text: str, candidate: MedicineMatchCandidate
    ) -> str:
        """The caller's words minus the medicine's own name and aliases:
        "DOLO 650 2" -> "2", "dolo650" -> "", "डोलो के दो पत्ते" -> "के दो पत्ते"."""
        name = normalize_text(candidate.medicine_name or "")
        if not name:
            return text
        phrases = {name} | {
            alias for alias, target in self.matching._aliases().items() if target == name
        }
        for phrase in sorted(phrases, key=len, reverse=True):
            pattern = r"(?<!\w)" + r"\W*".join(re.escape(t) for t in phrase.split()) + r"(?!\w)"
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        return text

    def _names_another_medicine(self, text: str, current_names: set[str]):
        """Live call: while BRUFEN's quantity was pending, the caller said
        "ARACHITOL INJ ... BOX" and BRUFEN got the unit "box". A different
        catalogue name means the caller moved on: start adding that one."""
        mentioned = self.matching.suggestions_from_utterance(text)
        current = {normalize_text(name) for name in current_names}
        if not mentioned or any(normalize_text(c.medicine_name) in current for c in mentioned):
            return None

        def add_new(voice, state, language):
            state.pending = None
            parsed = self._parse_intent(text, language, None)
            return self._handle_add(voice, state, parsed, text, language)

        return add_new

    def _handle_quantity_pending(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
    ) -> TurnDraft:
        pending = state.pending
        if pending is None or pending.medicine_id is None:
            state.pending = None
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=_text(
                    "कृपया दवा का नाम दोबारा बताएं।",
                    "Please say the medicine name again.",
                    language,
                ),
            )
        pending_candidate = MedicineMatchCandidate(
            medicine_id=pending.medicine_id,
            medicine_name=pending.medicine_name or "",
            description=pending.description or "",
            match_type="EXACT_NAME",
        )
        switched = self._names_another_medicine(text, {pending.medicine_name or ""})
        if switched is not None:
            if pending.chosen:
                self._defer(state, pending)
            return switched(voice, state, language)

        parsed_new = self._parse_intent(text, language, None)
        mentions_pending = self._mentions_candidate_name(text, pending_candidate)
        new_medicine = bool(parsed_new.medicine_query) and not mentions_pending
        if new_medicine and self._explicit_add_request(text):
            state.pending = None
            return self._handle_add(voice, state, parsed_new, text, language)

        # Live call 2026-11-20: repeating "DOLO 650" was read as 650 strips, and
        # "DOLO 650 2" as strengths {650, 2}. The count and any strength come
        # from what the caller said besides the medicine's own name.
        count_text = self._without_medicine_name(text, pending_candidate)
        quantity, unit, remaining = extract_quantity_unit(count_text)
        if quantity is None:
            quantity = extract_quantity(count_text)
        if quantity is None and set(normalize_text(count_text).split()) & DO_AS_ENGLISH_WORDS:
            # Answer to "how many?": Whisper often writes दो as "those" or "doe".
            quantity = 2
        if quantity is None:
            # "DO SHRIP", "the two strip": the count is there, the unit garbled.
            quantity = first_quantity_word(split_spoken_hundreds(count_text)[1])
        # "650." or "छह सौ पचास" while DOLO 650's count is asked: Whisper kept
        # only the strength. Another medicine's strength means the caller
        # moved on; this medicine's own strength is not a count.
        said_strengths = spoken_hundreds(count_text)
        if quantity is not None and quantity > self.settings.max_order_item_quantity:
            said_strengths = said_strengths | {str(quantity)}
        own_strengths = attributes_from_candidate(pending_candidate).strengths
        if said_strengths:
            if said_strengths <= own_strengths:
                if quantity is not None and str(quantity) in said_strengths:
                    quantity = None
            elif len(said_strengths) == 1:
                value = next(iter(said_strengths))
                if any(c.medicine_id != pending.medicine_id for c in self._strength_candidates(value)):
                    # "char sau ke do patte" for a pending BRUFEN 200: BRUFEN 400,
                    # and the two strips are kept.
                    count = None if quantity is None or str(quantity) in said_strengths else quantity
                    state.pending = None
                    return self._offer_by_strength(
                        voice, state, value, language, count, unit if count else None
                    )
        strength_text = remaining
        if quantity is not None:
            strength_text = re.sub(rf"(?<!\d){quantity}(?!\d)", " ", remaining, count=1)

        spoken = self._selection_attributes(strength_text, [pending_candidate])
        enforce_attributes = bool(
            parsed_new.medicine_id
            or spoken.strength_units
            or mentions_pending
            or (new_medicine and self._explicit_add_request(text))
        )
        if enforce_attributes and not strictly_matches(pending_candidate, spoken):
            self._log_matching(
                "quantity_pending", text, [pending_candidate], None,
                "ATTRIBUTE_CONFLICT",
            )
            return self._not_found_draft(state, language)

        if has_ambiguous_quantity_answer(count_text):
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=self._repeat_quantity_text(language),
            )

        quantity = quantity or pending.quantity
        unit = unit or pending.unit
        if quantity is None:
            # Live calls: Whisper mangled the count ("NG", "DOCH", "PIPE
            # POTCH") and the same question was asked up to 7 times. Each miss
            # now gets a clearer question; after four the agent starts over.
            retries = pending.retries + 1
            if retries >= 4:
                state.pending = None
                return TurnDraft(
                    intent=VoiceIntent.ADD_ITEM,
                    language=language,
                    assistant_text=_text(
                        "माफ़ कीजिए, मैं समझ नहीं पाया। कौन सी दवा चाहिए? दवा का नाम दोबारा बताएं।",
                        "Sorry, I did not catch that. Which medicine do you want? Please say its name again.",
                        language,
                    ),
                )
            state.pending = pending.model_copy(update={"retries": retries, "unit": unit})
            return TurnDraft(
                intent=VoiceIntent.ADD_ITEM,
                language=language,
                assistant_text=self._quantity_retry_prompt(
                    pending_candidate, unit, retries, language
                ),
            )
        self._log_matching(
            "quantity_pending", text, [pending_candidate], pending_candidate,
            "STRICT_MATCH",
        )
        return self._add_candidate(
            voice, state, pending_candidate, quantity, unit, language
        )

    def _handle_update(
        self,
        voice: VoiceSession,
        state: VoiceState,
        parsed: ParsedIntent,
        text: str,
        language: str,
    ) -> TurnDraft:
        state.guard.correction_started()
        state.summary_digest = None
        if has_ambiguous_quantity(text):
            summary = self.orders.get_summary(voice.order_session.session_id)
            return TurnDraft(
                intent=VoiceIntent.UPDATE_ITEM,
                language=language,
                assistant_text=self._repeat_quantity_text(language),
                order_summary=self._safe_summary(summary, language),
            )
        items = [{"id": i.id, "medicine_id": i.medicine_id,
                  "medicine_name": i.medicine.medicine_name,
                  "description": i.medicine.description} for i in voice.order_session.items]
        from app.services.voice.intent import normalize_spoken_strengths
        correction_text = normalize_spoken_strengths(text)
        draft_names = {normalize_text(item["medicine_name"]) for item in items}
        for alias, target in sorted(self.matching._aliases().items(), key=lambda pair: -len(pair[0])):
            if target in draft_names:
                correction_text = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", lambda _: target, correction_text, flags=re.IGNORECASE)
        swapped = self._replace_medicine(voice, state, text, items, language)
        if swapped is not None:
            return swapped
        result = detect_correction(correction_text, items)
        if result.intent == "CLARIFY_MEDICINE":
            # "दोनों 650 की चार की जगह सात" (DOLO 650 misheard): a strength said
            # that exactly one order item has names that item.
            said = {w for w in normalize_text(text).split() if w.isdigit() and int(w) > self.settings.max_order_item_quantity}
            owners = [i for i in items if said & description_strength_tokens(f"{i['medicine_name']} {i['description'] or ''}")]
            new_quantity, new_unit = parse_quantity(text)
            if said and len(owners) == 1 and new_quantity:
                result = replace(result, intent="UPDATE_ITEM", item_id=owners[0]["id"],
                                 medicine_id=owners[0]["medicine_id"], quantity=new_quantity, unit=new_unit)
        explicit_reference = contains_medicine_id(text) or extract_ordinal(text) or mentions_last_item(text)
        if result.intent == "UPDATE_ITEM":
            item = next(i for i in voice.order_session.items if i.id == result.item_id)
            prompt = None
        elif explicit_reference:
            item, prompt = self._resolve_item(voice, state, text, language)
        elif result.intent == "CLARIFY_MEDICINE" and items:
            names = list(dict.fromkeys(spoken_name(MedicineMatchCandidate(
                medicine_id=i["medicine_id"], medicine_name=i["medicine_name"],
                description=i["description"], match_type="EXACT_NAME")) for i in items))
            item, prompt = None, _text(
                "किस दवा की मात्रा बदलनी है: " + ", ".join(names) + "? दवा का नाम और नई मात्रा बताइए।",
                "Which medicine should I change: " + ", ".join(names) + "? Say its name and the new quantity.",
                language,
            )
        else:
            # Live call: a Hindi caller got this English-only message.
            item, prompt = None, _text(
                "कृपया दवा, strength, नई मात्रा और इकाई साफ़ बताएं।",
                result.message or "Please specify the exact medicine, strength, new quantity and unit.",
                language,
            )
        if item is None:
            return TurnDraft(
                intent=VoiceIntent.UPDATE_ITEM,
                language=language,
                assistant_text=prompt or "",
            )

        if result.intent == "UPDATE_ITEM":
            quantity, unit = result.quantity, OrderUnit(result.unit) if result.unit else None
        else:
            quantity, unit, _ = extract_quantity_unit(text)
        if quantity is None:
            quantity = extract_quantity(text)
        if quantity is None and unit is None:
            quantity = parsed.quantity
            unit = parsed.unit
        if quantity is None and unit is None:
            return TurnDraft(
                intent=VoiceIntent.UPDATE_ITEM,
                language=language,
                assistant_text=_text(
                    "कृपया नई मात्रा या इकाई बताएं, जैसे मात्रा तीन स्ट्रिप।",
                    "Please say the new quantity or unit, for example three strips.",
                    language,
                ),
            )

        order_session = self.orders.update_item(
            voice.order_session.session_id,
            item.id,
            OrderItemUpdate(quantity=quantity, unit=unit),
        )
        state.last_item_id = item.id
        state.guard.correction_completed()
        state.summary_digest = None
        state.pending = None
        summary = self._safe_summary_from_session(
            order_session, language
        )
        updated = next(
            (
                current
                for current in order_session.items
                if current.id == item.id
            ),
            item,
        )
        return TurnDraft(
            intent=VoiceIntent.UPDATE_ITEM,
            language=language,
            assistant_text=_text(
                f"आइटम अपडेट हो गया: {spoken_name(updated)} "
                f"{updated.quantity} {_unit_label(updated.unit, language)}.",
                f"Updated the item: {spoken_name(updated)} "
                f"{updated.quantity} {_unit_label(updated.unit, language)}.",
                language,
            ),
            order_summary=summary,
        )

    _INSTEAD = re.compile(r"\s*(?:की|के|का)?\s*(?:जगह|बजाय|jagah|jagha)\s*|\s+instead\s+of\s+", re.IGNORECASE)

    def _replace_medicine(self, voice, state, text: str, items: list[dict], language: str) -> TurnDraft | None:
        """Live Exotel call 13:25: "ब्रोफिन 200 की जगह ब्रोफिन 400 दो पत्ते" kept
        BRUFEN 200 with a new count. A different strength after "की जगह" is a
        new medicine: the old item is removed and the new one is asked as usual."""
        parts = self._INSTEAD.split(text, 1)
        if len(parts) != 2:
            return None
        limit = self.settings.max_order_item_quantity
        numbers = lambda s: {w for w in normalize_text(normalize_spoken_strengths(s)).split() if w.isdigit() and int(w) > limit}
        old_said, new_said = numbers(parts[0]), numbers(parts[1])
        if not old_said or not new_said or new_said <= old_said:
            return None
        owners = [i for i in items if old_said & description_strength_tokens(f"{i['medicine_name']} {i['description'] or ''}")]
        if len(owners) != 1:
            return None
        old = owners[0]
        self.orders.delete_item(voice.order_session.session_id, old["id"])
        state.guard.correction_completed()
        state.summary_digest = None
        state.pending = None
        name = spoken_name(MedicineMatchCandidate(medicine_id=old["medicine_id"], medicine_name=old["medicine_name"],
                                                  description=old["description"], match_type="EXACT_NAME"))
        draft = self._handle_text(voice, state, parts[1], language)
        return replace(draft, intent=VoiceIntent.UPDATE_ITEM, assistant_text=_text(
            f"{name} हटा दी। ", f"Removed {name}. ", language) + draft.assistant_text)

    def _handle_remove(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
    ) -> TurnDraft:
        item, prompt = self._resolve_item(voice, state, text, language)
        if item is None:
            return TurnDraft(
                intent=VoiceIntent.REMOVE_ITEM,
                language=language,
                assistant_text=prompt or "",
            )

        medicine_name = (
            spoken_name(item.medicine)
            if item.medicine is not None
            else _text("दवा", "the medicine", language)
        )
        order_session = self.orders.delete_item(
            voice.order_session.session_id, item.id
        )
        state.pending = None
        state.summary_digest = None
        state.last_item_id = (
            max((current.id for current in order_session.items), default=None)
        )
        summary = self._safe_summary_from_session(
            order_session, language
        )
        return TurnDraft(
            intent=VoiceIntent.REMOVE_ITEM,
            language=language,
            assistant_text=_text(
                f"{medicine_name} को ऑर्डर से हटा दिया।",
                f"Removed {medicine_name} from the order.",
                language,
            ),
            order_summary=summary,
        )

    def _show_summary(
        self,
        voice: VoiceSession,
        state: VoiceState,
        language: str,
    ) -> TurnDraft:
        if state.guard.pending_correction:
            # Live call 2026-11-23: a correction that was never understood
            # refused eleven "order confirm"s. Drop it and read the order back.
            state.guard.correction_completed()
            draft = self._show_summary(voice, state, language)
            return replace(draft, assistant_text=_text(
                "पिछला बदलाव समझ नहीं आया, इसलिए कुछ नहीं बदला। ", "The last change was not understood, so nothing was changed. ", language,
            ) + draft.assistant_text)
        summary = self.orders.get_summary(
            voice.order_session.session_id
        )
        if summary.can_confirm:
            state.guard.summary_presented(summary.summary_digest)
        left_out = ""
        in_order = {normalize_text(i.medicine_name) for i in summary.items}
        state.unresolved = [
            name for name in state.unresolved
            if not any(score >= 0.6 and normalize_text(c.medicine_name) in in_order
                       for c, score in self.matching.score_candidates(name, 1))
        ]
        if state.unresolved and summary.items:
            # Live Exotel call 13:38: CEROM and ANAFORTAN were never added and
            # "हाँ जी" confirmed without a word about them.
            left_out = _text("ये दवाइयाँ ऑर्डर में नहीं जुड़ीं: " + ", ".join(state.unresolved) + "। ",
                             "These were not added: " + ", ".join(state.unresolved) + ". ", language)
            state.unresolved = []
        safe_summary = self._safe_summary(summary, language)
        state.summary_digest = (
            summary.summary_digest if summary.can_confirm else None
        )
        return TurnDraft(
            intent=VoiceIntent.SHOW_SUMMARY,
            language=language,
            assistant_text=left_out + self._summary_text(summary, language),
            order_summary=safe_summary,
        )

    def _handle_confirm(
        self,
        voice: VoiceSession,
        state: VoiceState,
        language: str,
    ) -> TurnDraft:
        state.deferred = []
        if state.guard.pending_correction:
            state.pending = None
            state.queued = []
            return self._show_summary(voice, state, language)
        if state.pending is not None:
            # Live call: Whisper heard "हाँ" as "CREMAFFIN", which opened a new
            # item choice, and every "confirm" after that was refused. The
            # half-chosen item was never added: drop it and read the order
            # back, so the caller confirms exactly what they hear.
            dropped = state.pending.medicine_name or (
                state.pending.candidates[0].medicine_name if state.pending.candidates else "")
            state.pending = None
            state.queued = []
            draft = self._show_summary(voice, state, language)
            return replace(draft, assistant_text=_text(
                f"{dropped} तय नहीं हुई, इसलिए नहीं जोड़ी। ", f"{dropped} was not decided, so it was not added. ", language,
            ) + draft.assistant_text) if dropped else draft

        order_session = voice.order_session
        if (
            voice.status == CONFIRMED
            or order_session.status == SessionStatus.CONFIRMED.value
        ):
            order = self._existing_order(order_session)
            if order is not None:
                return self._already_confirmed(order, language)
            return TurnDraft(
                intent=VoiceIntent.CONFIRM_ORDER,
                language=language,
                assistant_text=_text(
                    "यह ऑर्डर पहले ही कन्फर्म हो चुका है।",
                    "This order is already confirmed.",
                    language,
                ),
            )

        if state.summary_digest is None or not state.guard.can_confirm(state.summary_digest):
            return self._show_summary(voice, state, language)

        summary = self.orders.get_summary(order_session.session_id)
        if not summary.can_confirm or not summary.confirmation_token:
            state.summary_digest = None
            return TurnDraft(
                intent=VoiceIntent.SHOW_SUMMARY,
                language=language,
                assistant_text=_text(
                    "ऑर्डर अभी कन्फर्म नहीं हो सकता क्योंकि ड्राफ्ट खाली है या सत्र सक्रिय नहीं है।",
                    "The order cannot be confirmed because the draft is empty or the session is not active.",
                    language,
                ),
            )
        if summary.summary_digest != state.summary_digest:
            state.summary_digest = summary.summary_digest
            state.guard.summary_presented(summary.summary_digest)
            safe_summary = self._safe_summary(summary, language)
            return TurnDraft(
                intent=VoiceIntent.SHOW_SUMMARY,
                language=language,
                assistant_text=_text(
                    "समीक्षा के बाद ऑर्डर बदल गया है। कृपया नया सारांश देखें और फिर कन्फर्म कहें। ",
                    "The order changed after your review. Please review the new summary, then confirm again. ",
                    language,
                )
                + self._summary_text(summary, language),
                order_summary=safe_summary,
            )

        try:
            order, _ = self.orders.confirm_order(
                order_session.session_id,
                OrderConfirmationRequest(
                    confirmation_token=summary.confirmation_token,
                    confirmation_source=ORDER_CONFIRMATION_SOURCE,
                    explicit_confirmation=True,
                ),
            )
        except OrderError as exc:
            if exc.status_code != 409:
                raise
            state.summary_digest = None
            return TurnDraft(
                intent=VoiceIntent.SHOW_SUMMARY,
                language=language,
                assistant_text=_text(
                    "ऑर्डर की स्थिति बदल गई है। कृपया सारांश दोबारा देखें और फिर कन्फर्म करें।",
                    "The order state changed. Please review the summary again, then confirm.",
                    language,
                ),
            )

        voice.status = CONFIRMED
        voice.ended_at = voice.ended_at or utc_now()
        state.pending = None
        state.summary_digest = None
        return TurnDraft(
            intent=VoiceIntent.CONFIRM_ORDER,
            language=language,
            assistant_text=CONFIRMATION_RESPONSE.get(language, CONFIRMATION_RESPONSE["hi"]),
            order=order,
        )

    def _handle_cancel(
        self,
        voice: VoiceSession,
        state: VoiceState,
        language: str,
    ) -> TurnDraft:
        order_session = voice.order_session
        if (
            voice.status == CANCELLED
            or order_session.status == SessionStatus.CANCELLED.value
        ):
            return TurnDraft(
                intent=VoiceIntent.CANCEL_ORDER,
                language=language,
                assistant_text=_text(
                    "यह ऑर्डर पहले ही रद्द है।",
                    "This order is already cancelled.",
                    language,
                ),
            )
        if (
            voice.status == CONFIRMED
            or order_session.status == SessionStatus.CONFIRMED.value
        ):
            return TurnDraft(
                intent=VoiceIntent.CANCEL_ORDER,
                language=language,
                assistant_text=_text(
                    "यह ऑर्डर कन्फर्म हो चुका है और अब रद्द नहीं किया जा सकता।",
                    "This order is already confirmed and cannot be cancelled here.",
                    language,
                ),
            )

        self.orders.cancel_session(order_session.session_id)
        voice.status = CANCELLED
        voice.ended_at = voice.ended_at or utc_now()
        state.pending = None
        state.summary_digest = None
        return TurnDraft(
            intent=VoiceIntent.CANCEL_ORDER,
            language=language,
            assistant_text=_text(
                "ऑर्डर रद्द कर दिया गया है। कोई ऑर्डर नहीं भेजा जाएगा।",
                "The order has been cancelled. No order will be sent.",
                language,
            ),
        )

    def _handle_end(
        self,
        voice: VoiceSession,
        state: VoiceState,
        language: str,
    ) -> TurnDraft:
        if voice.status == ENDED:
            return TurnDraft(
                intent=VoiceIntent.END_CALL,
                language=language,
                assistant_text=_text(
                    "कॉल पहले ही समाप्त है।",
                    "The voice session has already ended.",
                    language,
                ),
            )
        if voice.status == ACTIVE:
            try:
                self.orders.cancel_session(
                    voice.order_session.session_id
                )
            except OrderError:
                pass
            voice.status = ENDED
        voice.ended_at = voice.ended_at or utc_now()
        state.pending = None
        state.summary_digest = None
        return TurnDraft(
            intent=VoiceIntent.END_CALL,
            language=language,
            assistant_text=_text(
                "कॉल समाप्त कर रहा हूँ। धन्यवाद।",
                "Ending the call. Thank you.",
                language,
            ),
        )

    def _handle_help(
        self, state: VoiceState, language: str
    ) -> TurnDraft:
        pending_note = ""
        if state.pending is not None:
            pending_note = _text(
                " पहले लंबित दवा विकल्प या मात्रा पूरी करें।",
                " Please finish the pending medicine choice or quantity first.",
                language,
            )
        return TurnDraft(
            intent=VoiceIntent.HELP,
            language=language,
            assistant_text=_text(
                "मैं दवा जोड़ सकता हूँ, मात्रा बदल सकता हूँ, आइटम हटा सकता हूँ, सारांश दिखा सकता हूँ, और स्पष्ट पुष्टि के बाद ऑर्डर कन्फर्म कर सकता हूँ। उदाहरण: ब्रूफेन दो स्ट्रिप जोड़ो।",
                "I can add medicines, change quantities, remove items, show a summary, and confirm an order after explicit approval. Example: add two strips of BRUFEN.",
                language,
            )
            + pending_note,
        )

    def _select_candidate(
        self,
        pending: VoicePendingState,
        text: str,
    ) -> MedicineMatchCandidate | None:
        spoken = self._selection_attributes(text, pending.candidates)
        candidates = [
            candidate for candidate in pending.candidates
            if strictly_matches(candidate, spoken)
        ]
        medicine_id = contains_medicine_id(text)
        if medicine_id is not None:
            for candidate in candidates:
                if candidate.medicine_id == medicine_id:
                    return candidate
            return None

        fuzzy = pending.match_status != "NAME_HEARD" and any(
            candidate.match_type == "FUZZY_SUGGESTION"
            for candidate in candidates
        ) and len({normalize_text(c.medicine_name) for c in pending.candidates}) > 1
        explicit_name = [
            candidate
            for candidate in candidates
            if self._mentions_candidate_name(text, candidate)
        ]
        strength_tokens = self._selection_strengths(text, pending.candidates)

        strength_selected = self._select_candidate_from_strength(
            candidates, text
        )
        if strength_selected is not None:
            if fuzzy and not explicit_name:
                return None
            return strength_selected

        if fuzzy:
            if len(explicit_name) == 1:
                return explicit_name[0]
            if len(explicit_name) > 1 and strength_tokens:
                matching = [
                    candidate
                    for candidate in explicit_name
                    if strength_tokens
                    & description_strength_tokens(candidate.description)
                ]
                if len(matching) == 1:
                    return matching[0]
            return None

        word_selected = self._select_candidate_from_words(pending.candidates, text)
        if word_selected is not None and strictly_matches(word_selected, spoken):
            return word_selected
        form_selected = self._select_candidate_by_form(pending.candidates, text)
        if form_selected is not None:
            return form_selected
        if len(explicit_name) == 1:
            return explicit_name[0]
        ordinal = extract_ordinal(text)
        if ordinal is not None and 1 <= ordinal <= len(pending.candidates):
            ordinal_candidate = pending.candidates[ordinal - 1]
            if strictly_matches(ordinal_candidate, spoken):
                return ordinal_candidate
        return None

    def _select_candidate_from_words(
        self,
        candidates: list[MedicineMatchCandidate],
        text: str,
    ) -> MedicineMatchCandidate | None:
        """Pick a variant by a word only its description has: "mixed fruit",
        "plus" or "mint" for the three CREMAFFIN syrups."""
        from rapidfuzz import fuzz

        spoken = {fold_latin(t) for t in normalize_text(text).split() if len(t) >= 3}
        if not spoken or len(candidates) < 2:
            return None
        words = [
            {t for t in normalize_text(c.description or "").split() if t.isalpha() and len(t) >= 3}
            for c in candidates
        ]
        matches = []
        for index, own in enumerate(words):
            others = set().union(*(w for j, w in enumerate(words) if j != index))
            distinct = own - others
            if any(
                token == word or (len(word) >= 4 and fuzz.ratio(token, word) >= 80)
                or (len(phonetic_key(word)) >= 2 and phonetic_key(token) == phonetic_key(word))
                for token in spoken for word in distinct
            ):
                matches.append(candidates[index])
        return matches[0] if len(matches) == 1 else None

    def _exact_name_with_strength(
        self,
        mentioned: list[MedicineMatchCandidate],
        query: str,
        text: str,
    ) -> MedicineMatchCandidate | None:
        """Live call: "brufan, 200MG, 15S, 2-strip file". The name is an exact
        catalogue alias and the strength is spoken in digits; one stray word
        ("file") must not throw away the strength. Garbled strengths have no
        digits and still need an explicit choice."""
        if len({normalize_text(c.medicine_name) for c in mentioned}) != 1:
            return None
        name = normalize_text(mentioned[0].medicine_name)
        names = {name} | {alias for alias, target in self.matching._aliases().items() if target == name}
        tokens = normalize_text(query).split()
        known = set()
        for phrase in names:
            parts = phrase.split()
            for index in range(len(tokens) - len(parts) + 1):
                if tokens[index:index + len(parts)] == parts:
                    known.update(range(index, index + len(parts)))
        if not known or len(tokens) - len(known) > 1:
            return None
        return self._select_candidate_from_strength(mentioned, text)

    def _select_candidate_from_strength(
        self,
        candidates: list[MedicineMatchCandidate],
        text: str,
    ) -> MedicineMatchCandidate | None:
        strength_tokens = self._selection_strengths(text, candidates)
        if not strength_tokens:
            return None
        spoken = attributes_from_text(text, strengths=strength_tokens)
        matches = [
            candidate
            for candidate in candidates
            if strictly_matches(candidate, spoken)
            and strength_tokens
            & description_strength_tokens(candidate.description)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _mentions_candidate_name(
        self, text: str, candidate: MedicineMatchCandidate
    ) -> bool:
        normalized_text = normalize_text(text)
        normalized_name = normalize_text(candidate.medicine_name)
        if not normalized_name:
            return False
        text_tokens = normalized_text.split()
        names = [normalized_name] + [alias for alias, target in self.matching._aliases().items() if target == normalized_name]
        return any(text_tokens[index:index+len(name.split())] == name.split()
                   for name in names for index in range(len(text_tokens)-len(name.split())+1))

    def _resolve_item(
        self,
        voice: VoiceSession,
        state: VoiceState,
        text: str,
        language: str,
    ) -> tuple[OrderItem | None, str | None]:
        items = sorted(
            voice.order_session.items, key=lambda item: item.id or 0
        )
        if not items:
            return (
                None,
                _text(
                    "ऑर्डर में अभी कोई आइटम नहीं है।",
                    "There are no items in the order yet.",
                    language,
                ),
            )

        medicine_id = contains_medicine_id(text)
        if medicine_id is not None:
            for item in items:
                if item.medicine_id == medicine_id:
                    return item, None
            return None, "That medicine is not in this draft."

        ordinal = extract_ordinal(text)
        if ordinal is not None and 1 <= ordinal <= len(items):
            return items[ordinal - 1], None

        if mentions_last_item(text):
            if state.last_item_id is not None:
                for item in items:
                    if item.id == state.last_item_id:
                        return item, None
            return items[-1], None

        normalized_text = normalize_text(text)
        name_matches = []
        for item in items:
            medicine_name = (
                item.medicine.medicine_name
                if item.medicine is not None
                else item.medicine_id
            )
            normalized_name = normalize_text(medicine_name)
            if normalized_name and normalized_name in normalized_text:
                name_matches.append(item)
        if len(name_matches) == 1:
            return name_matches[0], None
        if len(name_matches) > 1:
            return (
                None,
                _text(
                    "एक से अधिक आइटम मेल खाते हैं। कृपया आइटम नंबर बताएं।",
                    "More than one item matches. Please specify the item number.",
                    language,
                ),
            )

        if len(items) == 1:
            return items[0], None
        if state.last_item_id is not None:
            for item in items:
                if item.id == state.last_item_id:
                    return item, None
        return (
            None,
            _text(
                "कृपया बताएं कि कौन सा आइटम बदलना या हटाना है, जैसे आइटम दो या आखिरी आइटम।",
                "Please specify which item to change or remove, for example item two or the last item.",
                language,
            ),
        )

    def _candidate_prompt(
        self,
        candidates: list[MedicineMatchCandidate],
        language: str,
        *,
        fuzzy: bool,
    ) -> str:
        options = self._format_candidates(candidates)
        if fuzzy and len({normalize_text(c.medicine_name) for c in candidates}) > 1:
            return _text(
                "कौन सी दवा चाहिए? दवा का नाम और strength, या medicine ID बताएं: ",
                "Which medicine do you want? Say its name and strength, or the medicine ID: ",
                language,
            ) + options
        return self._variant_question(candidates, language)

    def _candidate_retry_prompt(
        self,
        pending: VoicePendingState,
        language: str,
    ) -> str:
        if pending.match_status == "NAMES_HEARD":
            names = list(dict.fromkeys(c.medicine_name for c in pending.candidates))
            return self._which_medicine_prompt(names, language)
        if pending.match_status == "STRENGTH_GUESS":
            return self._strength_guess_question(pending, language)
        fuzzy = pending.match_status == "NOT_FOUND"
        if fuzzy and len({normalize_text(c.medicine_name) for c in pending.candidates}) > 1:
            return _text(
                "कौन सी दवा चाहिए? दवा का नाम और strength, या medicine ID बताएं: ",
                "Which medicine do you want? Say its name and strength, or the medicine ID: ",
                language,
            ) + self._format_candidates(pending.candidates)
        return self._variant_question(pending.candidates, language) + _text(
            " आप पहला या दूसरा भी बोल सकते हैं।",
            " You can also say first or second.",
            language,
        )

    def _format_candidates(
        self, candidates: list[MedicineMatchCandidate]
    ) -> str:
        return " ".join(
            f"{index}) {spoken_name(candidate, alternatives=candidates)}"
            for index, candidate in enumerate(candidates, start=1)
        )

    def _quantity_retry_prompt(self, candidate, unit, retries: int, language: str) -> str:
        label = spoken_name(candidate)
        unit = unit or default_unit_for(candidate.description)
        if unit is None:
            return _text(
                f"माफ़ कीजिए, साफ़ सुनाई नहीं दिया। {label} की मात्रा और इकाई बताएं, जैसे दो डिब्बे।",
                f"Sorry, I did not catch that. For {label}, please say the quantity and unit, for example two boxes.",
                language,
            )
        unit_hi = _unit_label(unit, "hi")
        unit_en = _PLURAL_EN[OrderUnit(unit).value]
        if retries == 1:
            return _text(
                f"माफ़ कीजिए, साफ़ सुनाई नहीं दिया। {label} की कितनी {unit_hi} चाहिए? बस संख्या बोलें, जैसे दो।",
                f"Sorry, I did not catch that. How many {unit_en} of {label}? Just say a number, like two. Please say the quantity.",
                language,
            )
        return _text(
            f"{label} के लिए बस संख्या बोलें, जैसे एक, दो या तीन। यह दवा नहीं चाहिए तो नहीं बोलें।",
            f"For {label}, just say a number such as one, two or three, or say no if you do not want it. Please say the quantity.",
            language,
        )

    def _repeat_quantity_text(self, language: str) -> str:
        return _text(
            "मात्रा स्पष्ट नहीं है। कृपया एक ही मात्रा दोबारा बताएं, जैसे पांच स्ट्रिप।",
            "The quantity was unclear. Please say one exact quantity again, for example five strips.",
            language,
        )

    def _quantity_prompt(
        self,
        candidate: MedicineMatchCandidate,
        quantity: int | None,
        unit: OrderUnit | None,
        language: str,
    ) -> str:
        label = spoken_name(candidate)
        if quantity is None and unit is None:
            natural = default_unit_for(candidate.description)
            if natural is not None:
                # "कितनी स्ट्रिप चाहिए?": a number alone is a full answer.
                return _text(
                    f"{label} की कितनी {_unit_label(natural, 'hi')} चाहिए?",
                    f"How many {_PLURAL_EN[OrderUnit(natural).value]} of {label}? Please say the quantity.",
                    language,
                )
            return _text(
                f"{label} के लिए मात्रा और इकाई बताएं, जैसे दो डिब्बे।",
                f"For {label}, please say the quantity and unit, for example two boxes.",
                language,
            )
        if unit is None:
            return _text(
                f"{label} की मात्रा {quantity} है। कृपया इकाई बताएं: टैबलेट, स्ट्रिप, बोतल, बॉक्स, या यूनिट।",
                f"The quantity for {label} is {quantity}. Please say the unit: tablet, strip, bottle, box, or unit.",
                language,
            )
        return _text(
            f"{label} की कितनी {_unit_label(unit, 'hi')} चाहिए?",
            f"How many {_PLURAL_EN[OrderUnit(unit).value]} of {label}? Please say the quantity.",
            language,
        )

    def _summary_text(
        self, summary: OrderSummaryRead, language: str
    ) -> str:
        if not summary.items:
            return _text(
                "आपका ऑर्डर अभी खाली है। कृपया पहले दवा जोड़ें।",
                "Your order is empty. Please add a medicine first.",
                language,
            )
        items = "; ".join(
            f"{index}) {spoken_name(item, alternatives=summary.items)} - "
            f"{item.quantity} {_unit_label(item.unit, language)}"
            for index, item in enumerate(summary.items, start=1)
        )
        if summary.can_confirm:
            return _text(
                f"कृपया दवा और मात्रा ध्यान से सुनें। आपके ऑर्डर में: {items}. सही हो तो कन्फर्म कहें।",
                f"Please listen to the medicine names and exact quantities. Your order has: {items}. If correct, say confirm.",
                language,
            )
        return _text(
            f"आपके ऑर्डर में: {items}. यह ऑर्डर अभी कन्फर्म नहीं हो सकता।",
            f"Your order has: {items}. This order cannot be confirmed yet.",
            language,
        )

    def _safe_summary(
        self, summary: OrderSummaryRead, language: str
    ) -> VoiceOrderSummaryRead:
        return VoiceOrderSummaryRead(
            status=summary.status,
            items=summary.items,
            line_item_count=summary.line_item_count,
            can_confirm=summary.can_confirm,
            message=self._summary_text(summary, language),
        )

    def _safe_summary_from_session(
        self, session: OrderSessionRead, language: str
    ) -> VoiceOrderSummaryRead:
        can_confirm = (
            session.status == SessionStatus.ACTIVE and bool(session.items)
        )
        message = _text(
            "ऑर्डर ड्राफ्ट में आइटम हैं।",
            "The order draft contains items.",
            language,
        )
        if not session.items:
            message = _text(
                "ऑर्डर ड्राफ्ट अभी खाली है।",
                "The order draft is empty.",
                language,
            )
        return VoiceOrderSummaryRead(
            status=session.status,
            items=session.items,
            line_item_count=len(session.items),
            can_confirm=can_confirm,
            message=message,
        )

    def _existing_order(self, order_session: OrderSession):
        order = self.order_repository.get_order_by_session_db_id(
            order_session.id
        )
        if order is None:
            return None
        return self.orders.get_order(order.order_id)

    def _already_confirmed(self, order, language: str) -> TurnDraft:
        return TurnDraft(
            intent=VoiceIntent.CONFIRM_ORDER,
            language=language,
            assistant_text=_text(
                "यह ऑर्डर पहले ही कन्फर्म हो चुका है।",
                "This order is already confirmed.",
                language,
            ),
            order=order,
        )

    def _find_added_item_id(
        self,
        session: OrderSessionRead,
        medicine_id: str,
        unit: OrderUnit,
    ) -> int | None:
        for item in session.items:
            if item.medicine_id == medicine_id and item.unit == unit:
                return item.id
        return None

    def _get_voice(
        self, public_voice_session_id: str, *, for_update: bool = False
    ) -> VoiceSession:
        voice = self.voice_repository.get_session(
            public_voice_session_id, for_update=for_update
        )
        if voice is None:
            raise VoiceError(404, "Voice session not found.")
        return voice

    def _require_active(self, voice: VoiceSession) -> None:
        if voice.status != ACTIVE:
            raise VoiceError(
                409,
                f"Voice session is not active ({voice.status}).",
            )

    def _sync_status(self, voice: VoiceSession) -> bool:
        if voice.status != ACTIVE:
            return False
        order_session = voice.order_session
        order_status = order_session.status
        if (
            order_status == SessionStatus.ACTIVE.value
            and _as_utc(voice.expires_at) <= utc_now()
        ):
            order_session.status = SessionStatus.EXPIRED.value
            order_session.confirmation_token_hash = None
            order_session.confirmation_digest = None
            order_session.confirmation_issued_at = None
            order_status = SessionStatus.EXPIRED.value
        elif (
            order_status == SessionStatus.ACTIVE.value
            and _as_utc(order_session.expires_at) <= utc_now()
        ):
            refreshed = self.orders.get_session(
                order_session.session_id
            )
            order_status = refreshed.status.value

        changed = False
        if order_status == SessionStatus.CONFIRMED.value:
            voice.status = CONFIRMED
            voice.ended_at = (
                voice.ended_at
                or order_session.confirmed_at
                or utc_now()
            )
            changed = True
        elif order_status == SessionStatus.CANCELLED.value:
            voice.status = CANCELLED
            voice.ended_at = voice.ended_at or utc_now()
            changed = True
        elif order_status == SessionStatus.EXPIRED.value:
            voice.status = EXPIRED
            voice.ended_at = voice.ended_at or utc_now()
            changed = True
        return changed

    def _load_state(self, voice: VoiceSession) -> VoiceState:
        try:
            state = VoiceState.model_validate_json(voice.state_json)
        except Exception:
            state = VoiceState(language=voice.language)
        if state.language not in {"hi", "en"}:
            state.language = "hi"
        return state

    def _serialize_session(self, voice: VoiceSession) -> VoiceSessionRead:
        language = voice.language if voice.language in {"hi", "en"} else "hi"
        if voice.status == ACTIVE and voice.turn_count == 0:
            message = initial_greeting_text(language)
        elif voice.status == CONFIRMED:
            message = _text(
                "ऑर्डर कन्फर्म है।",
                "The order is confirmed.",
                language,
            )
        elif voice.status == CANCELLED:
            message = _text(
                "ऑर्डर रद्द है।",
                "The order is cancelled.",
                language,
            )
        elif voice.status == ENDED:
            message = _text(
                "वॉइस सेशन समाप्त है।",
                "The voice session has ended.",
                language,
            )
        elif voice.status == EXPIRED:
            message = _text(
                "वॉइस सेशन समाप्त हो गया है।",
                "The voice session has expired.",
                language,
            )
        else:
            message = _text(
                "वॉइस सेशन सक्रिय है।",
                "The voice session is active.",
                language,
            )
        return VoiceSessionRead(
            voice_session_id=voice.voice_session_id,
            order_session_id=voice.order_session.session_id,
            status=voice.status,
            language=language,
            turn_count=voice.turn_count,
            created_at=voice.created_at,
            updated_at=voice.updated_at,
            expires_at=voice.expires_at,
            ended_at=voice.ended_at,
            message=message,
        )

    def _turn_response(
        self,
        voice: VoiceSession,
        draft: TurnDraft,
        *,
        tts_audio: VoiceTTSRead | None,
        tts_error: str | None,
    ) -> VoiceTurnResponse:
        return VoiceTurnResponse(
            voice_session_id=voice.voice_session_id,
            order_session_id=voice.order_session.session_id,
            status=voice.status,
            intent=draft.intent,
            language=draft.language,
            assistant_text=draft.assistant_text,
            candidates=[
                VoiceCandidateRead(
                    ordinal=index,
                    medicine_id=candidate.medicine_id,
                    medicine_name=candidate.medicine_name,
                    description=candidate.description,
                    match_type=candidate.match_type,
                )
                for index, candidate in enumerate(
                    draft.candidates, start=1
                )
            ],
            order_summary=draft.order_summary,
            order=draft.order,
            tts=tts_audio,
            tts_error=tts_error,
        )

    def _maybe_synthesize(
        self, text: str, language: str, speak: bool
    ) -> tuple[VoiceTTSRead | None, str | None]:
        if not speak:
            return None, None
        if not self.settings.voice_tts_enabled:
            return None, "Speech synthesis is disabled."
        try:
            tts = self._tts
            if tts is None:
                tts = build_tts(self.settings)
                self._tts = tts
            if tts is None:
                return None, "Speech synthesis is unavailable."
            from app.services.telephony.speech import synthesize_full
            audio: SynthesizedAudio = synthesize_full(
                tts, text, language, self.settings.voice_max_tts_chars
            )
        except VoiceError as exc:
            logger.warning("Speech synthesis unavailable: %s", exc.detail)
            return None, exc.detail
        except Exception as exc:
            logger.warning(
                "Speech synthesis failed: %s", exc.__class__.__name__
            )
            return None, "Speech synthesis failed."
        return (
            VoiceTTSRead(
                audio_base64=base64.b64encode(audio.data).decode("ascii"),
                sample_rate=audio.sample_rate,
                voice=audio.voice,
            ),
            None,
        )

    def _get_stt(self) -> SpeechToText:
        if self._stt is None:
            self._stt = build_stt(self.settings)
        from app.services.voice.stt import FasterWhisperSTT
        from app.services.voice.sarvam_stt import SarvamSTT
        if isinstance(self._stt, (FasterWhisperSTT, SarvamSTT)):
            names = sorted({m.medicine_name for m in self.medicine_repository.list_all_active()})
            canonical = {normalize_text(name) for name in names}
            aliases = [alias for alias, target in self.matching._aliases().items() if target in canonical]
            self._stt.set_vocabulary(names + aliases)
        return self._stt

    def _tts_files_available(self) -> bool:
        from pathlib import Path

        return (
            Path(self.settings.kokoro_model_path).is_file()
            and Path(self.settings.kokoro_voices_path).is_file()
        )

    def _validate_audio(
        self, audio_bytes: bytes, content_type: str | None
    ) -> None:
        if not audio_bytes:
            raise VoiceError(400, "Audio file is empty.")
        if len(audio_bytes) > self.settings.voice_max_audio_bytes:
            raise VoiceError(
                413,
                "Audio file is too large. Maximum size is "
                f"{self.settings.voice_max_audio_bytes} bytes.",
            )
        if content_type:
            normalized = content_type.lower()
            allowed = normalized.startswith("audio/") or normalized in {
                "application/octet-stream",
                "video/webm",
                "video/mp4",
            }
            if not allowed:
                raise VoiceError(
                    400,
                    "Unsupported audio content type. Please upload webm, wav, ogg, or mp4 audio.",
                )

    def _ensure_enabled(self) -> None:
        if not self.settings.voice_enabled:
            raise VoiceError(503, "Voice features are disabled.")
