"""Authenticated streaming and private demo monitoring.

Exotel starts calls through authenticated WSS start events. No invented webhook
signature or unauthenticated dynamic URL resolver is provided.
"""
import asyncio
import base64
import contextlib
import hmac
import json
import logging
import threading
import time
import anyio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.exceptions import VoiceError
from app.database.connection import get_db, SessionLocal
from app.database.models import CallLog
from app.services.telephony.audio_bridge import (
    FRAME_BYTES,
    PLAYBACK_INTERVAL_SECONDS,
    RATE,
    UtteranceBuffer,
    decode,
    frames,
    pcm_frames,
)
from app.services.telephony.call_manager import CallManager, TERMINAL
from app.services.telephony.exotel_adapter import ExotelAdapter
from app.services.telephony.exotel_client import outbound_config_issues
from app.services.telephony.mock_adapter import MockAdapter
from app.services.telephony.schemas import MockIncoming
from app.services.telephony.speech import speech_parts

router = APIRouter(prefix="/telephony", tags=["telephony"])
logger = logging.getLogger(__name__)
BARGE_IN_SPEECH_BYTES = 4 * FRAME_BYTES
# This CPU demo intentionally accepts one media call per API process.
CALL_SLOT = threading.BoundedSemaphore(1)


async def _single_clip(audio):
    yield audio


def authorize(value, expected):
    return bool(expected) and hmac.compare_digest(value or "", expected)


def sanitize_start_packet(packet, account_sid):
    if not isinstance(packet, dict):
        return {"type": type(packet).__name__}
    start = packet.get("start") if isinstance(packet.get("start"), dict) else {}

    def clean(value, key=""):
        lowered = str(key).lower()
        if any(word in lowered for word in ("token", "password", "secret", "authorization", "api_key", "apikey")):
            return "****"
        if lowered in {"account_sid", "call_sid", "stream_sid", "from", "to", "phone_number"}:
            return bool(value)
        if isinstance(value, dict):
            return {str(item_key): clean(item_value, item_key) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [clean(item, key) for item in value[:20]]
        if isinstance(value, str) and len(value) > 128:
            return value[:128] + "..."
        return value

    return {
        "event": clean(packet.get("event"), "event"),
        "sequence_number": clean(packet.get("sequence_number"), "sequence_number"),
        "stream_sid_present": bool(packet.get("stream_sid")),
        "start": clean(start, "start"),
        "account_match": start.get("account_sid") == account_sid,
    }


def admin(request: Request):
    if not authorize(request.headers.get("authorization"), "Bearer " + get_settings().telephony_monitor_token if get_settings().telephony_monitor_token else ""):
        raise HTTPException(401, "Authentication required")


def enabled():
    s = get_settings()
    if not s.telephony_enabled:
        raise HTTPException(503, "Telephony disabled")
    return s


def manager(db=Depends(get_db)):
    return CallManager(db)


@router.get("/status")
def status(db=Depends(get_db)):
    s = get_settings()
    configured = bool(
        s.telephony_stream_token
        and s.telephony_stream_username
        and (s.telephony_provider == "mock" or s.exotel_account_sid)
    )
    live_connected = db.scalar(
        select(func.count()).select_from(CallLog).where(
            CallLog.provider == "exotel",
            CallLog.stream_sid.is_not(None),
        )
    ) > 0
    return {
        "enabled": s.telephony_enabled,
        "provider": s.telephony_provider,
        "configured": configured,
        "simulated": s.telephony_provider == "mock",
        "live_verified": live_connected and s.telephony_provider == "exotel",
        "live_connected": live_connected and s.telephony_provider == "exotel",
        "inbound_ready": configured and s.telephony_provider == "exotel" and bool(s.telephony_public_base_url),
        "outbound_ready": s.telephony_provider == "exotel" and not outbound_config_issues(s),
        "missing_configuration": outbound_config_issues(s) if s.telephony_provider == "exotel" else [],
    }


@router.get("/calls/{call_id}", dependencies=[Depends(admin)])
def call_status(call_id: str, service=Depends(manager)):
    call = service.get(call_id)
    return serialize_call(call)


def serialize_call(call):
    return {key: getattr(call, key) for key in (
        "call_id", "provider", "provider_call_reference", "voice_session_id", "order_session_id",
        "status", "started_at", "ended_at", "disconnect_reason", "confirmed_order_id", "error_category", "last_latency_ms")}


@router.get("/calls", dependencies=[Depends(admin)])
def recent_calls(db=Depends(get_db)):
    return [serialize_call(call) for call in db.scalars(select(CallLog).order_by(CallLog.started_at.desc()).limit(20))]


@router.post("/mock/incoming", dependencies=[Depends(admin)])
def mock_incoming(payload: MockIncoming, service=Depends(manager)):
    s = enabled()
    if s.telephony_provider != "mock":
        raise HTTPException(404, "Mock provider disabled")
    call = service.incoming("mock", payload.call_reference, payload.customer_phone)
    return {"call_id": call.call_id, "simulated": True, "status": call.status}


@router.post("/status-callback")
async def status_callback(request: Request, token: str | None = Query(default=None), service=Depends(manager)):
    s = get_settings()
    if not authorize(token, s.telephony_webhook_secret):
        raise HTTPException(401, "Authentication required")
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid callback payload")
    else:
        payload = dict(await request.form())
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid callback payload")
    reference = payload.get("CallSid")
    if not isinstance(reference, str) or not 1 <= len(reference) <= 128:
        raise HTTPException(400, "Missing call reference")
    service.apply_status(reference, payload.get("Status"), payload.get("EventType"))
    return {"received": True}


def get_call_session_factory():
    return SessionLocal


@router.websocket("/media")
async def media(ws: WebSocket, factory=Depends(get_call_session_factory)):
    if not CALL_SLOT.acquire(blocking=False):
        await ws.close(code=1013)
        return
    try:
        # Shield cleanup from the ASGI request cancellation scope on disconnect.
        with anyio.CancelScope(shield=True):
            await handle_media(ws, factory)
    finally:
        CALL_SLOT.release()


async def handle_media(ws: WebSocket, factory):
    s = get_settings()
    expected = "Basic " + base64.b64encode(f"{s.telephony_stream_username}:{s.telephony_stream_token}".encode()).decode()
    reasons = []
    if not s.telephony_enabled:
        reasons.append("disabled")
    if not s.telephony_stream_username:
        reasons.append("missing_stream_user")
    if not s.telephony_stream_token:
        reasons.append("missing_stream_token")
    if s.telephony_provider == "exotel" and not s.exotel_account_sid:
        reasons.append("missing_account_sid")
    if not authorize(ws.headers.get("authorization"), expected):
        reasons.append("auth_mismatch")
    if reasons:
        logger.warning("telephony.media.rejected reasons=%s path=%s auth_present=%s",
                       ",".join(reasons), ws.url.path, bool(ws.headers.get("authorization")))
        await ws.close(code=1008)
        return
    logger.info("telephony.media.accepted path=%s sample_rate=%s",
                ws.url.path, ws.query_params.get("sample-rate"))
    adapter = ExotelAdapter(s.exotel_account_sid) if s.telephony_provider == "exotel" else MockAdapter()
    await ws.accept()
    cancelled = threading.Event()
    call_id, stream = None, None
    stop_reason, error = "network_disconnect", None
    close_source, close_code, close_reason = "none", None, None
    stop_packet = None
    started, last_speech = time.monotonic(), time.monotonic()
    buffer = UtteranceBuffer(s.telephony_vad_threshold)
    queue = asyncio.Queue(maxsize=s.telephony_turn_queue_size)
    playback = None
    playback_kind = None
    playback_frames_sent = 0
    processing = None
    worker = None
    expected_mark = None
    generation = 0
    sequence = 0
    incoming_sequence = -1
    packet = None
    turn_inflight = False
    terminal_reply = False
    greeting_active = False
    barge_in_voiced_bytes = 0
    barge_in_handled = False

    def run(method, *args):
        with factory() as db:
            return getattr(CallManager(db), method)(*args)

    async def send_ws(message, context):
        try:
            await ws.send_json(message)
        except Exception:
            logger.exception("telephony.media.send_failed context=%s event=%s",
                             context, message.get("event"))
            raise

    def cancel_playback(reason, **fields):
        if playback is None or playback.done():
            logger.info("telephony.playback.cancel_skipped reason=%s kind=%s frames_sent=%s",
                        reason, playback_kind, playback_frames_sent)
            return False
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        logger.info("telephony.playback.cancel reason=%s kind=%s frames_sent=%s %s",
                    reason, playback_kind, playback_frames_sent, details)
        playback.cancel()
        return True

    async def play(audio, terminal=False, text_length=0, tts_sample_rate=None, raw_pcm=False, kind="reply"):
        nonlocal expected_mark, sequence, stop_reason, error, playback_kind, playback_frames_sent
        nonlocal barge_in_voiced_bytes, barge_in_handled
        started_playback = time.monotonic()
        playback_kind, playback_frames_sent = kind, 0
        barge_in_voiced_bytes, barge_in_handled = 0, False
        # `audio` is one complete clip (greeting) or an async stream of clips,
        # one per sentence, that is still being synthesized while it plays.
        sources = audio if hasattr(audio, "__aiter__") else _single_clip(audio)
        try:
            to_chunks = pcm_frames if raw_pcm else frames
            async with contextlib.aclosing(sources):
                first_clip = True
                async for clip in sources:
                    chunks = await asyncio.to_thread(to_chunks, clip)
                    if first_clip:
                        first_clip = False
                        logger.info("telephony.playback.start kind=%s text_length=%s tts_sample_rate=%s "
                                    "total_bytes=%s frame_count=%s first_audio_ms=%s",
                                    kind, text_length, tts_sample_rate, len(clip), len(chunks),
                                    int((time.monotonic() - started_playback) * 1000))
                        if kind == "greeting" and s.greeting_delay_ms:
                            logger.info("telephony.playback.greeting_delay_start delay_ms=%s", s.greeting_delay_ms)
                            await asyncio.sleep(s.greeting_delay_ms / 1000)
                            logger.info("telephony.playback.greeting_delay_done delay_ms=%s", s.greeting_delay_ms)
                    await send_frames(chunks, started_playback, kind)
            expected_mark = f"{'done' if terminal else 'reply'}-{sequence}"
            await send_ws(adapter.mark(stream, expected_mark), "mark")
            logger.info("telephony.playback.done kind=%s frames_sent=%s elapsed_ms=%s",
                        kind, playback_frames_sent, int((time.monotonic() - started_playback) * 1000))
        except asyncio.CancelledError:
            logger.info("telephony.playback.cancelled kind=%s frames_sent=%s elapsed_ms=%s",
                        kind, playback_frames_sent, int((time.monotonic() - started_playback) * 1000))
            raise
        except Exception:
            stop_reason, error = "server_error", "PLAYBACK"
            cancel_playback("error", frames_sent=playback_frames_sent)
            logger.exception("telephony.playback.error kind=%s frames_sent=%s elapsed_ms=%s",
                             kind, playback_frames_sent, int((time.monotonic() - started_playback) * 1000))
            raise

    async def send_frames(chunks, started_playback, kind):
        nonlocal sequence, playback_frames_sent
        for payload in chunks:
            sequence += 1
            message = adapter.media(stream, payload, sequence, minimal=s.exotel_minimal_media)
            await send_ws(message, "media")
            playback_frames_sent += 1
            sent = playback_frames_sent
            if sent == 1:
                decoded = base64.b64decode(payload)
                redacted = dict(message)
                redacted["media"] = dict(message["media"])
                redacted["media"]["payload"] = (
                    f"<base64:{len(payload)} chars,hex:{decoded[:16].hex()}>"
                )
                logger.info("telephony.media.outgoing_first message=%s",
                            json.dumps(redacted, separators=(",", ":")))
                logger.info("telephony.media.format_check event=%s stream_sid_match=%s raw_pcm=%s "
                            "pcm_hex=%s payload_chars=%s frame_bytes=%s frame_multiple_320=%s "
                            "keys=%s media_keys=%s",
                            message.get("event"), message.get("stream_sid") == stream,
                            decoded[:4] != b"RIFF", decoded[:16].hex(), len(payload), len(decoded),
                            len(decoded) >= 3200 and len(decoded) % 320 == 0,
                            sorted(message), sorted(message["media"]))
            if sent == 1 or sent % 50 == 0:
                logger.info("telephony.playback.frame_sent kind=%s index=%s sequence=%s payload_chars=%s "
                            "sent_at_ms=%s",
                            kind, sent, sequence, len(payload),
                            int((time.monotonic() - started_playback) * 1000))
            await asyncio.sleep(PLAYBACK_INTERVAL_SECONDS)

    async def speak_ahead(text, language):
        """Yield one WAV clip per sentence, synthesizing the next while one plays."""
        parts = speech_parts(text)
        clips = asyncio.Queue()
        started_tts = time.monotonic()

        async def produce():
            try:
                for index, part in enumerate(parts):
                    audio = await asyncio.to_thread(run, "speak", part, language)
                    if index == 0:
                        logger.info("telephony.tts.first_part synthesis_ms=%s parts=%s chars=%s",
                                    int((time.monotonic() - started_tts) * 1000), len(parts), len(part))
                    await clips.put(audio.data)
                logger.info("telephony.tts.done synthesis_ms=%s parts=%s",
                            int((time.monotonic() - started_tts) * 1000), len(parts))
                await clips.put(None)
            except Exception as exc:
                await clips.put(exc)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await clips.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            producer.cancel()

    async def consume():
        nonlocal playback, processing, turn_inflight, generation, terminal_reply, barge_in_voiced_bytes
        while True:
            queued_generation, pcm = await queue.get()
            turn_inflight = True
            current = generation
            try:
                if queued_generation != current:
                    logger.info("telephony.reply.skipped reason=stale_queued_turn queued_generation=%s generation=%s",
                                queued_generation, current)
                    continue
                processing = asyncio.create_task(asyncio.to_thread(run, "process", call_id, pcm, cancelled, False))
                turn = await asyncio.shield(processing)
                if cancelled.is_set():
                    logger.info("telephony.reply.skipped reason=cancelled generation=%s", current)
                    continue
                if turn is None:
                    logger.info("telephony.reply.skipped reason=null_turn generation=%s", current)
                    continue
                terminal_reply = turn.status.value != "ACTIVE"
                if current != generation:
                    logger.info("telephony.reply.skipped reason=stale_generation current=%s generation=%s",
                                current, generation)
                    cancel_playback("new_turn", caller_turn_bytes=len(pcm))
                    await asyncio.to_thread(run, "invalidate_summary", call_id)
                    continue
                barge_in_voiced_bytes = 0
                playback = asyncio.create_task(play(speak_ahead(turn.assistant_text, turn.language),
                                                    terminal=turn.status.value != "ACTIVE",
                                                    text_length=len(turn.assistant_text),
                                                    kind="reply"))
                try:
                    await playback
                except asyncio.CancelledError:
                    if cancelled.is_set():
                        raise
            finally:
                turn_inflight = False
                queue.task_done()

    try:
        while True:
            remaining = s.telephony_max_call_seconds - (time.monotonic() - started)
            if remaining <= 0:
                stop_reason, error = "maximum_duration", "TIMEOUT"
                break
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                if worker and worker.done():
                    worker.result()
                if time.monotonic() - last_speech > s.telephony_silence_seconds and not turn_inflight:
                    stop_reason, error = "silence_timeout", "TIMEOUT"
                    break
                continue
            if len(raw) > 150000:
                raise ValueError("Packet too large")
            packet = json.loads(raw)
            if not isinstance(packet, dict):
                raise ValueError("Invalid packet")
            event = packet.get("event")
            if time.monotonic() - started <= 2.0:
                logger.info("telephony.media.raw_incoming event=%s raw=%s", event, raw)
            if event == "connected" and call_id is None:
                continue
            if event == "start":
                if call_id:
                    raise ValueError("Repeated start")
                meta = packet.get("start") if isinstance(packet.get("start"), dict) else {}
                fmt = meta.get("media_format") if isinstance(meta.get("media_format"), dict) else {}
                logger.info("telephony.media.start_packet account_match=%s encoding=%s sample_rate=%s channels=%s bit_rate=%s",
                            meta.get("account_sid") == s.exotel_account_sid, fmt.get("encoding"),
                            fmt.get("sample_rate"), fmt.get("channels"), fmt.get("bit_rate"))
                reference, stream, phone = adapter.start(packet)
                logger.info("telephony.media.start provider=%s call_ref=%s stream=%s phone_present=%s sample_rate=%s",
                            s.telephony_provider, reference, stream, bool(phone),
                            packet.get("start", {}).get("media_format", {}).get("sample_rate"))
                intended_call_id = ws.query_params.get("bot_call_id")
                if intended_call_id and not intended_call_id.startswith("call_"):
                    raise ValueError("Invalid call hint")
                def begin():
                    with factory() as db:
                        service = CallManager(db)
                        call = service.incoming(s.telephony_provider, reference, phone, intended_call_id=intended_call_id)
                        service.claim(call.call_id, stream)
                        return call.call_id
                call_id = await asyncio.to_thread(begin)
                greeting_started = time.monotonic()
                greeting = await asyncio.to_thread(run, "greeting", call_id)
                greeting_active = True
                barge_in_voiced_bytes = 0
                playback = asyncio.create_task(play(greeting["audio"],
                                                    text_length=greeting["text_length"],
                                                    tts_sample_rate=greeting["sample_rate"],
                                                    raw_pcm=greeting.get("raw_pcm", False),
                                                    kind="greeting"))
                logger.info("telephony.greeting.playback_scheduled delay_ms=%s bytes=%s",
                            int((time.monotonic() - greeting_started) * 1000), len(greeting["audio"]))
                continue
            if not call_id or packet.get("stream_sid") != stream:
                raise ValueError("Missing or mismatched stream")
            if worker and worker.done():
                worker.result()
            if event == "media":
                seq = int(packet.get("sequence_number", -1))
                if seq <= incoming_sequence:
                    raise ValueError("Out-of-order media")
                incoming_sequence = seq
                audio = decode(packet.get("media", {}).get("payload"))
                if greeting_active:
                    if playback and playback.done():
                        greeting_active = False
                        buffer.reset()
                        last_speech = time.monotonic()
                        worker = asyncio.create_task(consume())
                        logger.info("telephony.media.greeting_done frames_sent=%s sequence=%s",
                                    playback_frames_sent, seq)
                    else:
                        logger.info("telephony.media.audio_discarded reason=greeting sequence=%s bytes=%s",
                                    seq, len(audio))
                        continue
                if terminal_reply:
                    logger.info("telephony.media.audio_discarded reason=terminal_reply sequence=%s bytes=%s",
                                seq, len(audio))
                    continue
                turns, onset = buffer.feed(audio)
                speaking = bool(playback and not playback.done())
                voiced_bytes = buffer.last_voiced_bytes
                if voiced_bytes:
                    last_speech = time.monotonic()
                if speaking:
                    if voiced_bytes:
                        barge_in_voiced_bytes += voiced_bytes
                        if barge_in_voiced_bytes >= BARGE_IN_SPEECH_BYTES and not barge_in_handled:
                            barge_in_handled = True
                            generation += 1
                            cancel_playback("barge_in", caller_turn_bytes=len(buffer.audio),
                                            voiced_bytes=barge_in_voiced_bytes, sequence=seq)
                            expected_mark = None
                            await send_ws(adapter.clear(stream), "clear")
                            if not turn_inflight:
                                await asyncio.to_thread(run, "invalidate_summary", call_id)
                        elif not barge_in_handled:
                            logger.info("telephony.media.barge_in_pending voiced_bytes=%s threshold=%s sequence=%s",
                                        barge_in_voiced_bytes, BARGE_IN_SPEECH_BYTES, seq)
                    else:
                        barge_in_voiced_bytes = 0
                elif onset:
                    logger.info("telephony.media.audio_buffered reason=no_active_playback turn_inflight=%s "
                                "caller_turn_bytes=%s sequence=%s",
                                turn_inflight, len(buffer.audio), seq)
                if speaking and len(turns) > 1:
                    logger.warning("telephony.media.turn_dropped reason=playback queue_size=%s dropped=%s sequence=%s",
                                   queue.qsize(), len(turns) - 1, seq)
                    turns = turns[:1]
                for turn_pcm in turns:
                    if queue.full():
                        logger.warning("telephony.media.turn_dropped reason=queue_full queue_size=%s turn_bytes=%s sequence=%s",
                                       queue.qsize(), len(turn_pcm), seq)
                        continue
                    queue.put_nowait((generation, turn_pcm))
                    if turn_inflight:
                        logger.info("telephony.media.turn_queued reason=stt_or_reply_busy queue_size=%s turn_bytes=%s sequence=%s",
                                    queue.qsize(), len(turn_pcm), seq)
            elif event == "mark":
                logger.info("telephony.media.mark_received name=%s expected=%s",
                            packet.get("mark", {}).get("name"), expected_mark)
                if packet.get("mark", {}).get("name") == expected_mark:
                    completed = expected_mark.startswith("done-")
                    expected_mark = None
                    if completed:
                        stop_reason = "conversation_completed"
                        break
                else:
                    logger.info("telephony.media.mark_ignored reason=stale_mark name=%s expected=%s",
                                packet.get("mark", {}).get("name"), expected_mark)
            elif event == "stop":
                stop_packet = packet
                details = packet.get("stop") if isinstance(packet.get("stop"), dict) else {}
                logger.info("telephony.media.stop_event raw=%s reason=%s error=%s",
                            json.dumps(packet, separators=(",", ":")),
                            details.get("reason"), details.get("error"))
                close_source, close_code = "stop_event", getattr(ws, "close_code", None)
                close_reason = details.get("reason")
                stop_reason = "provider_stop"
                break
            elif event == "clear":
                # Do not silently erase an order. End this stream and cancel its draft.
                cancel_playback("clear", caller_turn_bytes=len(buffer.audio))
                expected_mark = None
                stop_reason = "provider_clear"
                break
            elif event == "dtmf":
                continue  # Never treat DTMF as explicit spoken order confirmation.
            else:
                raise ValueError("Unsupported event")
    except WebSocketDisconnect as exc:
        stop_reason = "websocket_disconnect"
        close_source = "websocket_disconnect"
        close_code = getattr(exc, "code", None) or getattr(ws, "close_code", None)
        close_reason = getattr(exc, "reason", None) or getattr(ws, "close_reason", None)
        logger.info("telephony.media.websocket_disconnect code=%s reason=%s", close_code, close_reason)
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        stop_reason, error = "invalid_packet", "PROTOCOL"
        logger.warning("telephony.media.invalid_packet exception=%s message=%s packet=%s",
                       type(exc).__name__, str(exc), sanitize_start_packet(packet, s.exotel_account_sid))
    except VoiceError:
        stop_reason, error = "voice_failure", "VOICE"
    except SQLAlchemyError:
        stop_reason, error = "database_failure", "DATABASE"
    except Exception as exc:
        logger.exception("telephony.media.processing_failure exception=%s", type(exc).__name__)
        stop_reason, error = "processing_failure", "PROCESSING"
    finally:
        cancelled.set()
        if playback:
            cancel_playback("error" if error else "shutdown", caller_turn_bytes=len(buffer.audio))
        for task in (playback, worker):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Cancelling to_thread cannot stop a model mid-inference; wait for its
        # cancellation checkpoint before ending/cancelling the shared order session.
        if processing:
            try:
                await processing
            except (asyncio.CancelledError, Exception):
                pass
        if call_id:
            try:
                await asyncio.to_thread(run, "finish", call_id, stop_reason, error)
            except SQLAlchemyError:
                # No payload/phone/token in logs. Recovery procedure is documented.
                logger.error("Call cleanup database failure: %s", call_id)
        logger.info("telephony.media.closed reason=%s stop_reason=%s error=%s duration_ms=%s "
                    "close_source=%s close_code=%s close_reason=%s",
                    "server_error" if error else stop_reason, stop_reason, error,
                    int((time.monotonic() - started) * 1000), close_source, close_code, close_reason)
        close_code_sent = 1011 if error else 1000
        close_reason_sent = "server_error" if error else "normal"
        logger.info("telephony.media.close_sent code=%s reason=%s", close_code_sent, close_reason_sent)
        try:
            await ws.close(code=close_code_sent, reason=close_reason_sent)
        except RuntimeError:
            pass
