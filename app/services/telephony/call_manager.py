import hashlib
import logging
import time
import uuid
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import VoiceError
from app.database.models import CallLog, CallTurn, Order, VoiceSession, utc_now
from app.schemas.voice import VoiceSessionCreate
from app.services.voice_service import VoiceService, _as_utc

logger = logging.getLogger(__name__)
TERMINAL = {"COMPLETED", "DISCONNECTED", "FAILED"}
PROVIDER_TERMINAL = {"completed", "failed", "busy", "no-answer", "no answer", "canceled"}


@lru_cache(maxsize=1)
def local_engines():
    # Model objects are lazy and process-local. Do not reload them on each packet/turn.
    from app.services.voice import build_stt, build_tts
    return build_stt(get_settings()), build_tts(get_settings())


class CallManager:
    def __init__(self, db, voice=None):
        self.db = db
        if voice is None:
            stt, tts = local_engines()
            voice = VoiceService(db, stt=stt, tts=tts)
        self.voice = voice

    def incoming(self, provider, reference, phone=None, language="hi", intended_call_id=None):
        # Session-level advisory lock survives the existing services' commits.
        # It prevents orphan voice/order sessions when provider starts race.
        key = int.from_bytes(hashlib.sha256(f"{provider}:{reference}".encode()).digest()[:8], "big", signed=True)
        pg = self.db.bind.dialect.name == "postgresql"
        connection = self.db.bind.connect() if pg else None
        try:
            if pg:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            call = None
            if intended_call_id:
                call = self.db.scalar(select(CallLog).where(CallLog.call_id == intended_call_id))
                if call is not None:
                    if call.provider != provider:
                        raise VoiceError(409, "Call belongs to another provider")
                    if call.provider_call_reference.startswith("pending_"):
                        call.provider_call_reference = reference
                        self.db.commit()
                    elif call.provider_call_reference != reference:
                        raise VoiceError(409, "Call reference does not match the outbound call")
                    return call
            call = self.db.scalar(select(CallLog).where(CallLog.provider == provider, CallLog.provider_call_reference == reference))
            if call:
                return call
            from app.schemas.order import PHONE_PATTERN
            phone = phone if isinstance(phone, str) and PHONE_PATTERN.fullmatch(phone) else None
            session = self.voice.create_session(VoiceSessionCreate(customer_phone=phone, language=language))
            call = CallLog(call_id=f"call_{uuid.uuid4().hex}", provider=provider,
                           provider_call_reference=reference, customer_phone=phone,
                           voice_session_id=session.voice_session_id,
                           order_session_id=session.order_session_id, status="RINGING")
            self.db.add(call)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                # SQLite fallback. PostgreSQL creates exactly one session under the advisory lock.
                self.voice.end_session(session.voice_session_id)
                call = self.db.scalar(select(CallLog).where(CallLog.provider == provider, CallLog.provider_call_reference == reference))
                if call is None:
                    raise
            return call
        finally:
            if pg:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.close()

    def claim(self, call_id, stream):
        result = self.db.execute(update(CallLog).where(CallLog.call_id == call_id, CallLog.status == "RINGING").values(status="CONNECTED", stream_sid=stream))
        self.db.commit()
        if result.rowcount != 1:
            raise VoiceError(409, "Call is already connected or ended")
        return self.get(call_id)

    def get(self, call_id):
        call = self.db.scalar(select(CallLog).where(CallLog.call_id == call_id).execution_options(populate_existing=True))
        if not call:
            raise VoiceError(404, "Call not found")
        return call

    def start_outbound(self, customer_phone, language="hi", client=None):
        from app.services.telephony.exotel_client import ExotelClient, outbound_config_issues

        settings = get_settings()
        issues = outbound_config_issues(settings)
        if issues:
            raise VoiceError(503, "Live telephony is not configured", {"missing": issues})
        allowed = [p.strip() for p in settings.telephony_allowed_destinations.split(",") if p.strip()]
        if allowed and customer_phone not in allowed:
            raise VoiceError(403, "Destination is not in the configured demo allowlist")

        session = self.voice.create_session(
            VoiceSessionCreate(customer_phone=customer_phone, language=language)
        )
        call_id = f"call_{uuid.uuid4().hex}"
        call = CallLog(
            call_id=call_id,
            provider="exotel",
            provider_call_reference=f"pending_{uuid.uuid4().hex}",
            customer_phone=customer_phone,
            voice_session_id=session.voice_session_id,
            order_session_id=session.order_session_id,
            status="RINGING",
        )
        self.db.add(call)
        self.db.commit()
        try:
            provider = (client or ExotelClient(settings)).connect(
                customer_phone=customer_phone, call_id=call_id
            )
        except Exception:
            call.status = "FAILED"
            call.error_category = "PROVIDER"
            call.ended_at = utc_now()
            self.db.commit()
            self.voice.end_session(session.voice_session_id)
            raise
        call.provider_call_reference = provider["provider_call_reference"]
        self.db.commit()
        logger.info(
            "Outbound call requested call_id=%s provider_reference=%s",
            call_id,
            call.provider_call_reference,
        )
        return {
            "call_id": call_id,
            "provider": call.provider,
            "provider_call_reference": call.provider_call_reference,
            "status": call.status,
            "language": language,
        }

    def record_turn(self, call_id, transcript, assistant_text, intent, language):
        call = self.get(call_id)
        sequence = self.db.scalar(
            select(func.count()).select_from(CallTurn).where(CallTurn.call_id == call_id)
        ) or 0
        self.db.add(
            CallTurn(
                call_id=call_id,
                sequence=sequence + 1,
                transcript=transcript[: get_settings().voice_max_transcript_chars],
                assistant_text=assistant_text[:4000],
                intent=intent.value if hasattr(intent, "value") else str(intent),
                language=language,
            )
        )
        self.db.commit()

    def apply_status(self, provider_reference, status, event_type=None):
        status = (status or "").strip().lower()
        call = self.db.scalar(
            select(CallLog).where(
                CallLog.provider == "exotel",
                CallLog.provider_call_reference == provider_reference,
            )
        )
        if call is None:
            return None
        if event_type == "answered" and call.status == "RINGING":
            call.status = "CONNECTED"
            self.db.commit()
            return call
        if status in PROVIDER_TERMINAL and call.status not in TERMINAL:
            error = None if status == "completed" else "PROVIDER"
            self.finish(call.call_id, f"provider_{status}", error)
            logger.info(
                "Provider terminal status applied call_id=%s status=%s",
                call.call_id,
                status,
            )
        return call

    def process(self, call_id, pcm, cancelled):
        from app.services.telephony.audio_bridge import wav
        from app.schemas.voice import VoiceTurnRequest
        call = self.get(call_id)
        if call.status in TERMINAL or cancelled.is_set():
            return None
        if _as_utc(call.started_at) + timedelta(seconds=get_settings().telephony_max_call_seconds) <= utc_now():
            raise VoiceError(408, "Call duration exceeded")
        call.status = "PROCESSING"
        self.db.commit()
        started = time.perf_counter()
        voice_session = self.voice.get_session(call.voice_session_id)
        stt_started = time.perf_counter()
        transcript = self.voice.transcribe_audio(
            wav(pcm),
            filename="telephone.wav",
            content_type="audio/wav",
            language=voice_session.language,
        )
        stt_ms = int((time.perf_counter() - stt_started) * 1000)
        logger.info(
            "telephony.stt.transcript call_id=%s stt_ms=%s audio_bytes=%s requested_language=%s "
            "detected_language=%s language_probability=%s duration_seconds=%s transcript=%s",
            call_id, stt_ms, len(pcm), voice_session.language, transcript.language,
            transcript.language_probability, transcript.duration_seconds, transcript.transcript,
        )
        if cancelled.is_set():
            return None
        turn = self.voice.process_text(call.voice_session_id, VoiceTurnRequest(text=transcript.transcript, speak=False))
        self.record_turn(call_id, transcript.transcript, turn.assistant_text, turn.intent, turn.language)
        call = self.get(call_id)
        call.last_latency_ms = int((time.perf_counter() - started) * 1000)
        call.status = "COMPLETED" if turn.order else ("CONFIRMING" if turn.order_summary and turn.order_summary.can_confirm else "ACTIVE")
        if turn.order:
            call.confirmed_order_id = turn.order.order_id
            order = self.db.scalar(select(Order).where(Order.order_id == turn.order.order_id))
            order.call_reference_id = call.provider_call_reference
            order.confirmation_source = f"TELEPHONY_{call.provider.upper()}"
        elif turn.status.value != "ACTIVE":
            call.status = "DISCONNECTED"
        self.db.commit()
        if cancelled.is_set():
            return None
        from app.services.telephony.speech import synthesize_full
        from app.services.voice import build_tts
        from app.schemas.voice import VoiceTTSRead
        import base64
        tts_started = time.perf_counter()
        audio = synthesize_full(self.voice._tts or build_tts(get_settings()), turn.assistant_text,
                                turn.language, get_settings().voice_max_tts_chars)
        logger.info("telephony.tts.done synthesis_ms=%s sample_rate=%s",
                    int((time.perf_counter() - tts_started) * 1000), audio.sample_rate)
        turn.tts = VoiceTTSRead(audio_base64=base64.b64encode(audio.data).decode(),
                                sample_rate=audio.sample_rate, voice=audio.voice)
        call.last_latency_ms = int((time.perf_counter() - started) * 1000)
        self.db.commit()
        return turn

    def greeting(self, call_id):
        call = self.get(call_id)
        session = self.voice.get_session(call.voice_session_id)
        from app.services.telephony.audio_bridge import RATE, get_greeting_pcm
        settings = get_settings()
        voice = (
            settings.voice_tts_voice_hi
            if session.language == "hi"
            else settings.voice_tts_voice_en
        )
        pcm = get_greeting_pcm(session.message, voice, settings.voice_tts_speed)
        if pcm is None:
            raise VoiceError(503, "Greeting audio is not cached.")
        logger.info("telephony.greeting.cache_hit language=%s bytes=%s sample_rate=%s",
                    session.language, len(pcm), RATE)
        call.status = "ACTIVE"
        self.db.commit()
        return {"audio": pcm, "text_length": len(session.message), "sample_rate": RATE, "raw_pcm": True}

    def finish(self, call_id, reason, error=None):
        call = self.get(call_id)
        # Recover a committed order even if transport/log update failed afterwards.
        voice = self.db.scalar(select(VoiceSession).where(VoiceSession.voice_session_id == call.voice_session_id))
        order = self.db.scalar(select(Order).where(Order.session_id == voice.order_session_id))
        if order:
            call.confirmed_order_id = order.order_id
            call.status = "COMPLETED"
            order.call_reference_id = call.provider_call_reference
            order.confirmation_source = f"TELEPHONY_{call.provider.upper()}"
        else:
            self.voice.end_session(call.voice_session_id)
            call.status = "FAILED" if error else "DISCONNECTED"
        call.ended_at = call.ended_at or utc_now()
        call.disconnect_reason = reason[:64]
        call.error_category = error
        self.db.commit()

    def invalidate_summary(self, call_id):
        # An interrupted/failed readback cannot authorize the next yes.
        from app.services.voice.types import VoiceState
        call = self.get(call_id)
        voice = self.db.scalar(select(VoiceSession).where(VoiceSession.voice_session_id == call.voice_session_id))
        state = VoiceState.model_validate_json(voice.state_json)
        state.summary_digest = None
        state.guard.summary_read_back = False
        state.guard.summary_version = None
        voice.state_json = state.model_dump_json()
        self.db.commit()
