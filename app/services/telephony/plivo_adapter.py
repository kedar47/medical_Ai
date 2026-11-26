"""Plivo bidirectional Audio Streaming, G.711 mu-law 8 kHz.

Sources (2026-11-26):
https://plivo.com/docs/voice-agents/audio-streaming/concepts/audio-streaming-reference
https://plivo.com/docs/voice-agents/audio-streaming/xml/stream

The media handler is written against Exotel's packet shape. `normalize`
translates each Plivo packet into that shape (and mu-law into s16le PCM), and
the outgoing methods build Plivo's playAudio / clearAudio / checkpoint. The
order flow is unchanged.

Plivo's start event has no caller number. The answer URL request does (From,
CallUUID), so the answer handler remembers it by CallUUID, which the start
event repeats as callId.
"""
import base64
import binascii
import re
import threading
import time
from collections import OrderedDict
from xml.sax.saxutils import escape, quoteattr

from app.services.telephony.mulaw import pcm_to_ulaw, ulaw_to_pcm

CONTENT_TYPE = "audio/x-mulaw"
SAMPLE_RATE = 8000
_ID = re.compile(r"[\w-]{1,128}")

_callers: "OrderedDict[str, tuple[str | None, float]]" = OrderedDict()
_callers_lock = threading.Lock()
_CALLER_TTL_SECONDS = 600


def caller_phone(raw) -> str | None:
    """Plivo sends E.164 digits, often without "+". Never guess a country code."""
    if not isinstance(raw, str):
        return None
    digits = raw.strip().lstrip("+")
    return "+" + digits if re.fullmatch(r"[1-9]\d{9,14}", digits) else None


def remember_caller(call_uuid: str, raw_from) -> None:
    if not isinstance(call_uuid, str) or not _ID.fullmatch(call_uuid):
        return
    now = time.monotonic()
    with _callers_lock:
        _callers[call_uuid] = (caller_phone(raw_from), now)
        _callers.move_to_end(call_uuid)
        while _callers and (len(_callers) > 256 or now - next(iter(_callers.values()))[1] > _CALLER_TTL_SECONDS):
            _callers.popitem(last=False)


def recall_caller(call_uuid: str) -> str | None:
    with _callers_lock:
        entry = _callers.pop(call_uuid, None)
    return entry[0] if entry else None


def answer_xml(stream_url: str, phone: str | None) -> str:
    """Plivo XML that connects the call to our media WebSocket."""
    # extraHeaders values may only be alphanumeric; it is a backup for the
    # caller number in case the answer request and stream land on different
    # processes.
    extra = f' extraHeaders="callerPhone{"=" + phone.lstrip("+") if phone else "=none"}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
        f'contentType={quoteattr(f"{CONTENT_TYPE};rate={SAMPLE_RATE}")}{extra}>'
        f"{escape(stream_url)}</Stream>\n"
        # keepCallAlive holds the call until our side closes the stream; then
        # end it here, otherwise Plivo asks the answer URL again.
        "  <Hangup/>\n</Response>\n"
    )


def _pcm_payload(payload) -> str:
    if not isinstance(payload, str) or len(payload) > 140000:
        raise ValueError("Audio packet too large")
    try:
        ulaw = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid audio encoding") from exc
    if not ulaw:
        raise ValueError("Empty audio packet")
    return base64.b64encode(ulaw_to_pcm(ulaw)).decode()


class PlivoAdapter:
    def __init__(self, auth_id: str = ""):
        self.auth_id = auth_id

    def normalize(self, packet: dict) -> dict:
        event = packet.get("event")
        stream = packet.get("streamId")
        sequence = packet.get("sequenceNumber")
        if event == "start":
            start = packet.get("start") if isinstance(packet.get("start"), dict) else {}
            fmt = start.get("mediaFormat") if isinstance(start.get("mediaFormat"), dict) else {}
            return {"event": "start", "stream_sid": start.get("streamId"), "sequence_number": sequence,
                    "extra_headers": packet.get("extra_headers"),
                    "start": {"call_sid": start.get("callId"), "stream_sid": start.get("streamId"),
                              "account_sid": start.get("accountId"),
                              "media_format": {"encoding": fmt.get("encoding"),
                                               "sample_rate": fmt.get("sampleRate"), "channels": 1}}}
        if event == "media":
            media = packet.get("media") if isinstance(packet.get("media"), dict) else {}
            if media.get("track", "inbound") != "inbound":
                return {"event": "ignored", "reason": "track"}
            return {"event": "media", "stream_sid": stream, "sequence_number": sequence,
                    "media": {"payload": _pcm_payload(media.get("payload"))}}
        if event == "playedStream":
            return {"event": "mark", "stream_sid": stream, "mark": {"name": packet.get("name")}}
        if event == "clearedAudio":
            return {"event": "clear", "stream_sid": stream}
        if event == "stop":
            return {"event": "stop", "stream_sid": stream, "stop": packet.get("stop") or {}}
        # dtmf and any event Plivo adds later must not end a live call.
        return {"event": "ignored", "reason": str(event)[:32]}

    def start(self, packet: dict) -> tuple[str, str, str | None]:
        start = packet.get("start")
        if not isinstance(start, dict):
            raise ValueError("Missing start")
        call, stream = start.get("call_sid"), start.get("stream_sid")
        if not all(isinstance(v, str) and _ID.fullmatch(v) for v in (call, stream)):
            raise ValueError("Invalid identifiers")
        if self.auth_id and start.get("account_sid") != self.auth_id:
            raise ValueError("Account mismatch")
        fmt = start.get("media_format", {})
        encoding = str(fmt.get("encoding") or "").strip().lower()
        if encoding not in {"audio/x-mulaw", "audio/mulaw", "mulaw", "pcmu"}:
            raise ValueError("Unsupported audio encoding")
        rate = fmt.get("sample_rate")
        if rate is not None and str(rate).strip() != str(SAMPLE_RATE):
            raise ValueError("Only 8 kHz audio supported")
        phone = recall_caller(call)
        if phone is None:
            match = re.search(r"callerPhone=(\d{10,15})", str(packet.get("extra_headers") or ""))
            phone = caller_phone(match.group(1)) if match else None
        return call, stream, phone

    def media(self, stream: str, payload: str, sequence: int, minimal: bool = False) -> dict:
        ulaw = pcm_to_ulaw(base64.b64decode(payload))
        return {"event": "playAudio", "media": {"contentType": CONTENT_TYPE, "sampleRate": SAMPLE_RATE,
                                                "payload": base64.b64encode(ulaw).decode()}}

    def clear(self, stream: str) -> dict:
        return {"event": "clearAudio", "streamId": stream}

    def mark(self, stream: str, name: str) -> dict:
        return {"event": "checkpoint", "streamId": stream, "name": name}
