"""Simulated transport tests. None of these are live PSTN calls."""
import base64
import io
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app.api.telephony_routes import get_call_session_factory
from app.core.config import get_settings
from app.core.exceptions import NoSpeechError, VoiceError
from app.database.models import CallLog, Order, OrderSession, VoiceSession
from app.main import app
from app.schemas.voice import VoiceSessionCreate, VoiceTurnRequest
from app.services.telephony.audio_bridge import (
    PLAYBACK_FRAME_BYTES,
    UtteranceBuffer,
    cache_greeting_pcm,
    clear_greeting_cache,
    decode,
    frames,
    get_greeting_pcm,
    pcm_frames,
    wav,
)
from app.services.telephony.call_manager import CallManager
from app.services.telephony.exotel_adapter import ExotelAdapter
from app.services.telephony.exotel_client import ExotelClient, outbound_config_issues
from app.services.voice.types import SynthesizedAudio, TranscriptionResult
from app.services.voice_service import VoiceService, initial_greeting_text

AUTH = {"Authorization": "Basic " + base64.b64encode(b"demo:stream-secret").decode()}
ADMIN = {"Authorization": "Bearer monitor-secret"}


@pytest.fixture(autouse=True)
def telephony_settings(monkeypatch):
    s = get_settings()
    for key, value in {"telephony_enabled": True, "telephony_provider": "mock",
                       "telephony_stream_username": "demo", "telephony_stream_token": "stream-secret",
                       "telephony_monitor_token": "monitor-secret", "telephony_max_call_seconds": 30,
                       "greeting_delay_ms": 0, "exotel_minimal_media": True}.items():
        monkeypatch.setattr(s, key, value)


class STT:
    def __init__(self, text="add two strips of clomin"):
        self.text = text

    def transcribe(self, audio_bytes, **kwargs):
        with wave.open(io.BytesIO(audio_bytes)) as reader:
            assert reader.getframerate() == 8000
            assert reader.getnchannels() == 1
        return TranscriptionResult(transcript=self.text, language="en")


class TTS:
    def synthesize(self, text, **kwargs):
        return SynthesizedAudio(data=wav(bytes(1600)), sample_rate=8000, voice="mock")


def start(reference="test-call", stream="stream-1", phone="+919876543210"):
    return {"event": "start", "stream_sid": stream, "start": {
        "call_sid": reference, "stream_sid": stream, "from": phone,
        "account_sid": "account", "media_format": {"encoding": "raw", "sample_rate": 8000}}}


def send_audio(ws, sequence=1):
    voiced = (np.ones(1600, dtype="<i2") * 2000).tobytes()
    ws.send_json({"event": "media", "stream_sid": "stream-1", "sequence_number": sequence,
                  "media": {"payload": base64.b64encode(voiced + bytes(8 * 1600)).decode()}})


def send_voiced(ws, sequence, frames=1):
    voiced = (np.ones(1600, dtype="<i2") * 2000).tobytes() * frames
    ws.send_json({"event": "media", "stream_sid": "stream-1", "sequence_number": sequence,
                  "media": {"payload": base64.b64encode(voiced).decode()}})


def drain(ws):
    count = 0
    while True:
        packet = ws.receive_json()
        if packet["event"] == "media":
            assert len(decode(packet["media"]["payload"])) == PLAYBACK_FRAME_BYTES
            count += 1
        if packet["event"] == "mark":
            ws.send_json(packet)
            return packet, count


@pytest.fixture
def exotel_settings(monkeypatch):
    s = get_settings()
    values = {
        "telephony_provider": "exotel",
        "telephony_enabled": True,
        "exotel_account_sid": "account-sid",
        "exotel_api_key": "api-key",
        "exotel_api_token": "api-token",
        "exotel_api_base_url": "https://api.exotel.com",
        "exotel_caller_id": "+911133445566",
        "telephony_public_base_url": "https://demo.example.com",
        "telephony_webhook_secret": "hook-secret",
        "telephony_stream_username": "demo",
        "telephony_stream_token": "stream-secret",
        "telephony_allowed_destinations": "",
    }
    for key, value in values.items():
        monkeypatch.setattr(s, key, value)
    return s


@pytest.fixture
def dashboard_auth(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dashboard_enabled", True)
    monkeypatch.setattr(s, "dashboard_token", "dashboard-secret")
    return {"Authorization": "Bearer dashboard-secret"}


class FakeResponse:
    status_code = 200

    def json(self):
        return {"Call": {"Sid": "exotel-out", "Status": "queued"}}


class FakeHTTP:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse()


class FakeProvider:
    def __init__(self):
        self.requests = []

    def connect(self, **kwargs):
        self.requests.append(kwargs)
        return {"provider_call_reference": "exotel-out", "provider_status": "queued"}


@pytest.fixture
def simulated_gateway(client, session_factory, monkeypatch):
    original = VoiceService.__init__
    stt = STT()
    settings = get_settings()
    clear_greeting_cache()
    for language in ("hi", "en"):
        voice = settings.voice_tts_voice_hi if language == "hi" else settings.voice_tts_voice_en
        cache_greeting_pcm(initial_greeting_text(language), voice, wav(bytes(1600)), settings.voice_tts_speed)
    def init(self, db, **kwargs):
        original(self, db, stt=stt, tts=TTS())
    monkeypatch.setattr(VoiceService, "__init__", init)
    app.dependency_overrides[get_call_session_factory] = lambda: session_factory
    yield client, stt
    clear_greeting_cache()


def test_disabled_and_invalid_auth(client):
    assert client.get("/telephony/status").json()["live_verified"] is False
    assert client.get("/telephony/calls/x").status_code == 401
    assert client.post("/telephony/mock/incoming", json={"call_reference": "x"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/telephony/media"):
            pass
    get_settings().telephony_enabled = False
    assert client.post("/telephony/mock/incoming", headers=ADMIN, json={"call_reference": "x"}).status_code == 503
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/telephony/media", headers=AUTH):
            pass
    assert client.get("/voice/status").status_code == 200


def test_incoming_mapping_retry_and_private_monitor(client, db_session):
    payload = {"call_reference": "retry", "customer_phone": "restricted"}
    first = client.post("/telephony/mock/incoming", headers=ADMIN, json=payload).json()
    second = client.post("/telephony/mock/incoming", headers=ADMIN, json=payload).json()
    assert first == second
    call = db_session.scalar(select(CallLog))
    voice = db_session.scalar(select(VoiceSession))
    order = db_session.scalar(select(OrderSession))
    assert call.voice_session_id == voice.voice_session_id
    assert call.order_session_id == order.session_id
    assert call.customer_phone is None
    result = client.get(f"/telephony/calls/{call.call_id}", headers=ADMIN).json()
    assert "customer_phone" not in result
    assert "stream-secret" not in str(result)
    listed = client.get("/telephony/calls", headers=ADMIN)
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert "customer_phone" not in listed.text
    assert client.get("/telephony/calls").status_code == 401
    assert client.get("/telephony/calls/nope", headers=ADMIN).status_code == 404


@pytest.mark.parametrize("payload", ["!", "", base64.b64encode(b"a").decode(), "A" * 140001], ids=["bad-base64", "empty", "odd-length", "oversize"])
def test_invalid_audio(payload):
    with pytest.raises(ValueError):
        decode(payload)


@pytest.mark.parametrize("rate", [8000, 16000, 24000, 48000])
def test_encode_resample_and_roundtrip(rate):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes((10000 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype("<i2").tobytes())
    encoded = frames(output.getvalue())
    assert len(encoded) == 5
    assert all(len(decode(frame)) == PLAYBACK_FRAME_BYTES for frame in encoded)


def test_greeting_pcm_cache_resamples_and_frames():
    clear_greeting_cache()
    try:
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24000)
            writer.writeframes((10000 * np.sin(2 * np.pi * 440 * np.arange(24000) / 24000)).astype("<i2").tobytes())
        size = cache_greeting_pcm("cached greeting", "voice-a", output.getvalue())
        pcm = get_greeting_pcm("cached greeting", "voice-a")
        assert pcm is not None and len(pcm) == size
        assert len(pcm) % PLAYBACK_FRAME_BYTES == 0
        assert get_greeting_pcm("other greeting", "voice-a") is None
        chunks = pcm_frames(pcm)
        assert chunks
        assert all(len(decode(chunk)) >= PLAYBACK_FRAME_BYTES and len(decode(chunk)) % 320 == 0
                   for chunk in chunks)
    finally:
        clear_greeting_cache()


def test_audio_buffer_bounded():
    buf = UtteranceBuffer()
    for _ in range(1000):
        assert buf.feed(bytes(1600))[0] == []
    assert len(buf.audio) == 0
    voiced = (np.ones(800, dtype="<i2") * 1000).tobytes()
    turns = []
    for _ in range(130):
        result, _ = buf.feed(voiced)
        turns.extend(result)
    assert len(turns) == 1 and len(turns[0]) == 192000
    assert len(buf.audio) < 192000


@pytest.mark.parametrize("field,value", [("call_sid", ""), ("account_sid", "wrong"), ("media_format", {"encoding": "mulaw", "sample_rate": 8000})])
def test_exotel_protocol_validation(field, value):
    packet = start()
    packet["start"][field] = value
    with pytest.raises(ValueError):
        ExotelAdapter("account").start(packet)


def test_exotel_accepts_documented_bit_rate():
    packet = start()
    packet["start"]["media_format"]["bit_rate"] = "128"
    call, stream, _ = ExotelAdapter("account").start(packet)
    assert call == "test-call" and stream == "stream-1"


def test_exotel_accepts_real_packet_shape():
    packet = {"event": "start", "stream_sid": "stream-real", "start": {
        "call_sid": "call-real", "stream_sid": "stream-real", "from": "+919876543210",
        "account_sid": "account", "media_format": {
            "encoding": "base64", "sample_rate": 8000, "channels": None, "bit_rate": "128kbps"}}}
    call, stream, phone = ExotelAdapter("account").start(packet)
    assert call == "call-real" and stream == "stream-real" and phone == "+919876543210"
    packet["start"]["media_format"]["encoding"] = "BASE64"
    assert ExotelAdapter("account").start(packet)[:2] == ("call-real", "stream-real")


def test_exotel_media_uses_documented_field_types():
    message = ExotelAdapter().media("stream-1", base64.b64encode(b"\0" * 3200).decode(), 3)
    assert message["event"] == "media"
    assert message["stream_sid"] == "stream-1"
    assert message["sequence_number"] == 3
    assert message["media"]["chunk"] == 3
    assert message["media"]["timestamp"] == "200"
    assert set(message) == {"event", "stream_sid", "sequence_number", "media"}
    assert set(message["media"]) == {"chunk", "timestamp", "payload"}
    minimal = ExotelAdapter().media("stream-1", "payload", 3, minimal=True)
    assert minimal == {"event": "media", "stream_sid": "stream-1", "media": {"payload": "payload"}}


def test_exotel_rejects_stereo_audio():
    packet = start()
    packet["start"]["media_format"]["channels"] = "2"
    with pytest.raises(ValueError):
        ExotelAdapter("account").start(packet)


def test_complete_mock_phone_order(simulated_gateway, seed_catalogue, db_session):
    client, stt = simulated_gateway
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(start())
        assert drain(ws)[1] > 0
        requests = ["add two strips of clomin", "add one strip of brufen", "200 mg",
                    "change clomin tab quantity to three strips", "show summary", "yes confirm"]
        for sequence, text in enumerate(requests, 1):
            stt.text = text
            send_audio(ws, sequence)
            packet, _ = drain(ws)
        assert packet["mark"]["name"].startswith("done-")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    db_session.expire_all()
    call = db_session.scalar(select(CallLog))
    order = db_session.scalar(select(Order))
    assert call.status == "COMPLETED" and call.ended_at
    assert call.confirmed_order_id == order.order_id
    assert order.call_reference_id == "test-call"
    assert order.confirmation_source == "TELEPHONY_MOCK"
    assert len(order.line_items) == 2
    assert next(line for line in order.line_items if line.medicine_id == "MED-00003").quantity == 3
    assert db_session.scalar(select(func.count()).select_from(Order)) == 1


@pytest.mark.parametrize("text", ["change unknown quantity to three strips", "change clomin tab quantity to 101 strips", "change clomin tab quantity to zero strips"])
def test_failed_corrections_block_confirmation(simulated_gateway, seed_catalogue, db_session, text):
    client, stt = simulated_gateway
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        for seq, request in enumerate(["add one strip of clomin", "show summary", text, "show summary", "yes confirm"], 1):
            stt.text = request
            send_audio(ws, seq)
            drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0
    call = db_session.scalar(select(CallLog))
    draft = db_session.scalar(select(OrderSession))
    assert draft.status == "CANCELLED"
    assert call.status == "DISCONNECTED" and call.ended_at


def test_noise_without_speech_is_skipped_and_call_continues(simulated_gateway, seed_catalogue, db_session):
    # Live call 2026-11-18: a 0.8 s noise burst held no speech, STT raised
    # "No speech was detected" and the whole call ended as a voice failure.
    client, stt = simulated_gateway
    stt.text = "add two strips of clomin"
    calls = {"count": 0}
    original = stt.transcribe

    def noisy_first(audio_bytes, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise NoSpeechError(400, "No speech was detected in the audio. Please try again.")
        return original(audio_bytes, **kwargs)

    stt.transcribe = noisy_first
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws, 1)
        send_audio(ws, 2)
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert calls["count"] == 2
    call = db_session.scalar(select(CallLog))
    assert call.error_category is None and call.status != "FAILED"
    assert db_session.scalar(select(OrderSession)).items


def test_unknown_medicine_not_added(simulated_gateway, seed_catalogue, db_session):
    client, stt = simulated_gateway
    stt.text = "add two strips of nonexistentxyz"
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws)
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert not db_session.scalar(select(OrderSession)).items


@pytest.mark.parametrize("packet", [{"event": "media", "stream_sid": "stream-1", "sequence_number": 1, "media": {"payload": "!"}},
                                    {"event": "media", "stream_sid": "other"}, {"event": "unsupported", "stream_sid": "stream-1"}])
def test_invalid_packet_cleanup(simulated_gateway, packet, db_session):
    client, _ = simulated_gateway
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        ws.send_json(packet)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    call = db_session.scalar(select(CallLog))
    assert call.status == "FAILED" and call.error_category == "PROTOCOL"


def test_disconnect_and_reconnect_rejected(simulated_gateway, db_session, caplog):
    client, _ = simulated_gateway
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    messages = [record.message for record in caplog.records]
    assert any("telephony.media.websocket_disconnect" in message for message in messages)
    assert any("close_source=websocket_disconnect" in message for message in messages)
    assert db_session.scalar(select(func.count()).select_from(CallLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(VoiceSession)) == 1
    assert db_session.scalar(select(CallLog)).status == "DISCONNECTED"


@pytest.mark.parametrize("component", ["transcribe_audio", "greeting"])
def test_voice_failures_cleanup(simulated_gateway, db_session, monkeypatch, component):
    client, _ = simulated_gateway
    def fail(*args, **kwargs):
        raise VoiceError(503, "simulated component failure")
    monkeypatch.setattr(CallManager if component == "greeting" else VoiceService, component, fail)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        if component == "transcribe_audio":
            drain(ws)
            send_audio(ws)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    call = db_session.scalar(select(CallLog))
    assert call.status == "FAILED" and call.error_category == "VOICE"


def test_queue_full_drops_turn_without_closing_call(simulated_gateway, db_session, monkeypatch, caplog):
    client, _ = simulated_gateway
    entered = threading.Event()
    release = threading.Event()
    original_process = CallManager.process

    def blocked_process(self, call_id, pcm, cancelled):
        entered.set()
        assert release.wait(10)
        return original_process(self, call_id, pcm, cancelled)

    monkeypatch.setattr(CallManager, "process", blocked_process)
    # Turn 1 is in flight; turns 2-3 fill the queue (size 2); turn 4 is dropped.
    monkeypatch.setattr(get_settings(), "telephony_turn_queue_size", 2)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws, 1)
        assert entered.wait(10)
        for sequence in (2, 3, 4):
            send_audio(ws, sequence)
        release.set()
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    assert any("telephony.media.turn_dropped" in record.message for record in caplog.records)
    call = db_session.scalar(select(CallLog))
    assert call.status != "FAILED" and call.error_category != "PROTOCOL"


def test_timeout_cleanup(simulated_gateway, db_session, monkeypatch):
    client, _ = simulated_gateway
    monkeypatch.setattr(get_settings(), "telephony_silence_seconds", 0)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert db_session.scalar(select(CallLog)).error_category == "TIMEOUT"


def test_browser_correction_integration_and_new_session(client, seed_catalogue):
    session = client.post("/voice/sessions", json={"language": "en"}).json()
    path = f"/voice/sessions/{session['voice_session_id']}/turns"
    def turn(text):
        result = client.post(path, json={"text": text, "speak": False})
        assert result.status_code == 200, result.text
        return result.json()
    turn("add one strip of clomin")
    turn("show summary")
    result = turn("clomin tab ki quantity three strips kar do")
    assert result["intent"] == "update_item"
    assert result["order_summary"]["items"][0]["quantity"] == 3
    assert turn("yes confirm")["order"] is None  # Fresh summary first.
    result = turn("yes confirm")
    assert result["order"]
    audio_path = f"/voice/sessions/{session['voice_session_id']}/audio-turns"
    assert client.post(audio_path, files={"file": ("a.wav", wav(bytes(1600)), "audio/wav")}).status_code == 409
    fresh = client.post("/voice/sessions", json={"language": "en"}).json()
    assert fresh["voice_session_id"] != session["voice_session_id"]
    assert "Start new order" in client.get("/voice-demo").text


def test_postgres_concurrent_incoming(session_factory):
    with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            pytest.skip("PostgreSQL advisory locking required")
    def create():
        with session_factory() as db:
            return CallManager(db).incoming("mock", "concurrent").call_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: create(), range(2)))
    assert result[0] == result[1]
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CallLog)) == 1
        assert db.scalar(select(func.count()).select_from(VoiceSession)) == 1


@pytest.mark.parametrize("text", ["add one strip of clomin", "एक स्ट्रिप क्लोमिन जोड़ो", "ek strip clomin add karo"])
def test_multilingual_mock_audio_turns(simulated_gateway, seed_catalogue, db_session, text):
    client, stt = simulated_gateway
    stt.text = text
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws)
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    items = db_session.scalar(select(OrderSession)).items
    assert len(items) == 1 and items[0].quantity == 1


def test_long_summary_is_not_truncated():
    from app.services.telephony.speech import synthesize_full
    class RecordingTTS(TTS):
        def __init__(self):
            self.texts = []
        def synthesize(self, text, **kwargs):
            self.texts.append(text)
            return super().synthesize(text, **kwargs)
    tts = RecordingTTS()
    text = " ".join(f"Medicine number {i}, quantity two strips." for i in range(30))
    audio = synthesize_full(tts, text, "en", 100)
    assert " ".join(tts.texts) == text
    assert len(tts.texts) > 1
    assert frames(audio.data)


def test_disconnect_after_saved_order_preserves_order(db_session, seed_catalogue):
    stt = STT()
    voice = VoiceService(db_session, stt=stt, tts=TTS())
    service = CallManager(db_session, voice)
    call = service.incoming("mock", "after-save")
    service.claim(call.call_id, "stream")
    for text in ["add one strip of clomin", "show summary", "yes confirm"]:
        stt.text = text
        turn = service.process(call.call_id, bytes(1600), threading.Event())
    assert turn.order
    service.finish(call.call_id, "network_disconnect")
    service.finish(call.call_id, "provider_retry")
    assert db_session.scalar(select(func.count()).select_from(Order)) == 1
    assert service.get(call.call_id).status == "COMPLETED"


def test_disconnect_during_stt_cannot_confirm(db_session, seed_catalogue):
    cancelled = threading.Event()
    class HangupSTT(STT):
        def transcribe(self, audio_bytes, **kwargs):
            cancelled.set()
            return TranscriptionResult(transcript="yes confirm", language="en")
    voice = VoiceService(db_session, stt=HangupSTT(), tts=TTS())
    service = CallManager(db_session, voice)
    call = service.incoming("mock", "during-stt")
    service.claim(call.call_id, "stream")
    for text in ["add one strip of clomin", "show summary"]:
        voice.process_text(call.voice_session_id, VoiceTurnRequest(text=text, speak=False))
    assert service.process(call.call_id, bytes(1600), cancelled) is None
    service.finish(call.call_id, "network_disconnect")
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0


def test_interrupted_summary_requires_new_readback(db_session, seed_catalogue):
    service = CallManager(db_session, VoiceService(db_session, tts=TTS()))
    call = service.incoming("mock", "interrupted")
    voice = service.voice
    for text in ["add one strip of clomin", "show summary"]:
        voice.process_text(call.voice_session_id, VoiceTurnRequest(text=text, speak=False))
    service.invalidate_summary(call.call_id)
    result = voice.process_text(call.voice_session_id, VoiceTurnRequest(text="yes confirm", speak=False))
    assert result.order is None and result.order_summary


def test_cached_greeting_plays_without_tts(simulated_gateway, monkeypatch, caplog):
    client, _ = simulated_gateway

    def fail_tts(*args, **kwargs):
        raise AssertionError("greeting must use the startup cache")

    monkeypatch.setattr(TTS, "synthesize", fail_tts)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        mark, count = drain(ws)
        assert count > 0 and mark["stream_sid"] == "stream-1"
        ws.send_json({"event": "stop", "stream_sid": "stream-1", "stop": {
            "call_sid": "test-call", "account_sid": "account", "reason": "callended", "error": "bad-media"}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    messages = [record.message for record in caplog.records]
    assert not any("telephony.tts.done" in message for message in messages)
    assert any("telephony.playback.start kind=greeting" in message for message in messages)
    outgoing = next(message for message in messages if "telephony.media.outgoing_first" in message)
    assert '"event":"media"' in outgoing
    assert '"stream_sid":"stream-1"' in outgoing
    assert "<base64:" in outgoing
    assert "sequence_number" not in outgoing and "timestamp" not in outgoing
    fmt = next(message for message in messages if "telephony.media.format_check" in message)
    assert "event=media" in fmt
    assert "stream_sid_match=True" in fmt
    assert "raw_pcm=True" in fmt
    assert "frame_multiple_320=True" in fmt
    assert "keys=['event', 'media', 'stream_sid']" in fmt
    assert "media_keys=['payload']" in fmt
    frame = next(message for message in messages if "telephony.playback.frame_sent" in message)
    assert "sent_at_ms=" in frame
    assert any("telephony.media.raw_incoming event=start" in message for message in messages)
    stop = next(message for message in messages if "telephony.media.stop_event" in message)
    assert "callended" in stop and "bad-media" in stop
    assert any("close_source=stop_event" in message for message in messages)
    assert any("telephony.media.close_sent code=1000" in message for message in messages)


def test_full_media_experiment(simulated_gateway, monkeypatch, caplog):
    client, _ = simulated_gateway
    monkeypatch.setattr(get_settings(), "exotel_minimal_media", False)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    outgoing = next(record.message for record in caplog.records
                    if "telephony.media.outgoing_first" in record.message)
    assert "sequence_number" in outgoing and "timestamp" in outgoing


def test_greeting_delay_experiment(simulated_gateway, monkeypatch, caplog):
    client, _ = simulated_gateway
    monkeypatch.setattr(get_settings(), "greeting_delay_ms", 200)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        started = time.monotonic()
        ws.send_json(start())
        drain(ws)
        assert time.monotonic() - started >= 0.15
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert any("telephony.playback.greeting_delay_start delay_ms=200" in record.message
               for record in caplog.records)


def test_no_cancel_during_greeting(simulated_gateway, caplog):
    client, _ = simulated_gateway
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        send_voiced(ws, 1, frames=5)
        events = []
        while True:
            packet = ws.receive_json()
            events.append(packet["event"])
            if packet["event"] == "mark":
                ws.send_json(packet)
                break
        assert "clear" not in events
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert any("telephony.media.audio_discarded reason=greeting" in record.message
               for record in caplog.records)
    assert not any("telephony.playback.cancel reason=barge_in" in record.message
                   for record in caplog.records)


def test_reply_barge_in_requires_sustained_speech(simulated_gateway, monkeypatch, caplog):
    client, stt = simulated_gateway

    def long_tts(self, text, **kwargs):
        return SynthesizedAudio(data=wav(bytes(32000)), sample_rate=8000, voice="mock")

    monkeypatch.setattr(TTS, "synthesize", long_tts)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        stt.text = "add one strip of clomin"
        send_audio(ws, 1)
        assert ws.receive_json()["event"] == "media"
        send_voiced(ws, 2, frames=1)
        assert ws.receive_json()["event"] != "clear"
        for sequence in range(3, 7):
            send_voiced(ws, sequence, frames=1)
        while True:
            packet = ws.receive_json()
            if packet["event"] == "clear":
                break
            assert packet["event"] == "media"
        assert packet["stream_sid"] == "stream-1"
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert any("telephony.playback.cancel reason=barge_in" in record.message
               and "caller_turn_bytes=" in record.message for record in caplog.records)


def test_database_failure_recorded(simulated_gateway, monkeypatch, db_session):
    from sqlalchemy.exc import OperationalError
    client, _ = simulated_gateway
    def fail(*args):
        raise OperationalError("simulated", {}, Exception("unavailable"))
    monkeypatch.setattr(CallManager, "process", fail)
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert db_session.scalar(select(CallLog)).error_category == "DATABASE"


def test_exotel_client_builds_real_dial_request(exotel_settings):
    http = FakeHTTP()
    result = ExotelClient(exotel_settings, http).connect(
        customer_phone="+919876543210", call_id="call_test"
    )
    assert result["provider_call_reference"] == "exotel-out"
    assert outbound_config_issues(exotel_settings) == []
    url, request = http.requests[0]
    assert url == "https://api.exotel.com/v1/Accounts/account-sid/Calls/connect"
    assert request["auth"] == ("api-key", "api-token")
    data = dict(request["data"])
    assert [v for k, v in request["data"] if k == "StatusCallbackEvents[]"] == ["answered", "terminal"]
    assert data["From"] == "+919876543210"
    assert data["CallerId"] == "+911133445566"
    assert data["StreamType"] == "bidirectional"
    assert data["CustomField"] == "call_test"
    assert data["StreamUrl"].startswith("wss://demo:stream-secret@demo.example.com/telephony/media?")
    assert "sample-rate=8000" in data["StreamUrl"]
    assert "bot_call_id=call_test" in data["StreamUrl"]
    assert data["StatusCallback"].startswith("https://demo.example.com/telephony/status-callback?token=hook-secret")


def test_exotel_client_rejects_incomplete_configuration(exotel_settings, monkeypatch):
    monkeypatch.setattr(exotel_settings, "exotel_api_token", "")
    http = FakeHTTP()
    with pytest.raises(VoiceError) as exc:
        ExotelClient(exotel_settings, http).connect(
            customer_phone="+919876543210", call_id="call_test"
        )
    assert "EXOTEL_API_TOKEN" in exc.value.extra["missing"]
    assert not http.requests


def test_start_outbound_creates_provider_backed_call(db_session, exotel_settings):
    service = CallManager(db_session, VoiceService(db_session, stt=STT(), tts=TTS()))
    provider = FakeProvider()
    result = service.start_outbound("+919876543210", "en", client=provider)
    call = db_session.scalar(select(CallLog).where(CallLog.call_id == result["call_id"]))
    session = db_session.scalar(
        select(VoiceSession).where(VoiceSession.voice_session_id == call.voice_session_id)
    )
    assert call.status == "RINGING"
    assert call.provider == "exotel"
    assert call.provider_call_reference == "exotel-out"
    assert call.customer_phone == "+919876543210"
    assert session.language == "en"
    assert provider.requests[0]["call_id"] == result["call_id"]


def test_bot_call_id_reuses_outbound_call(simulated_gateway, db_session, exotel_settings):
    client, _ = simulated_gateway
    service = CallManager(db_session, VoiceService(db_session, stt=STT(), tts=TTS()))
    outbound = service.start_outbound("+919876543210", "hi", client=FakeProvider())
    packet = start(reference=outbound["provider_call_reference"])
    packet["start"]["account_sid"] = "account-sid"
    with client.websocket_connect(
        f"/telephony/media?bot_call_id={outbound['call_id']}", headers=AUTH
    ) as ws:
        ws.send_json(packet)
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    db_session.expire_all()
    call = db_session.scalar(select(CallLog).where(CallLog.call_id == outbound["call_id"]))
    assert call.provider_call_reference == outbound["provider_call_reference"]
    assert call.stream_sid == "stream-1"
    assert db_session.scalar(select(func.count()).select_from(CallLog)) == 1


def test_status_callback_applies_provider_terminal_state(client, db_session, exotel_settings):
    service = CallManager(db_session, VoiceService(db_session, stt=STT(), tts=TTS()))
    call = service.start_outbound(
        "+919876543210", "hi", client=FakeProvider()
    )
    payload = {"CallSid": call["provider_call_reference"], "Status": "busy"}
    assert client.post("/telephony/status-callback", json=payload).status_code == 401
    response = client.post("/telephony/status-callback?token=hook-secret", json=payload)
    assert response.status_code == 200 and response.json() == {"received": True}
    db_session.expire_all()
    saved = db_session.scalar(select(CallLog).where(CallLog.call_id == call["call_id"]))
    assert saved.status == "FAILED"
    assert saved.error_category == "PROVIDER"
    assert saved.disconnect_reason == "provider_busy"


def test_dashboard_outbound_uses_operator_auth_and_validates_phone(
    client, dashboard_auth, exotel_settings, monkeypatch
):
    seen = []
    def fake_start(self, customer_phone, language):
        seen.append((customer_phone, language))
        return {"call_id": "call_out"}
    monkeypatch.setattr(CallManager, "start_outbound", fake_start)
    assert client.post("/dashboard/calls/outbound", json={"customer_phone": "123"}).status_code == 401
    bad = client.post(
        "/dashboard/calls/outbound",
        headers=dashboard_auth,
        json={"customer_phone": "123"},
    )
    assert bad.status_code == 422
    good = client.post(
        "/dashboard/calls/outbound",
        headers=dashboard_auth,
        json={"customer_phone": "+919876543210", "language": "hi"},
    )
    assert good.status_code == 200 and good.json()["call_id"] == "call_out"
    assert seen == [("+919876543210", "hi")]


def test_call_detail_includes_conversation_transcripts(
    simulated_gateway, seed_catalogue, db_session, dashboard_auth
):
    client, stt = simulated_gateway
    stt.text = "add one strip of clomin"
    with client.websocket_connect("/telephony/media", headers=AUTH) as ws:
        ws.send_json(start())
        drain(ws)
        send_audio(ws)
        drain(ws)
        ws.send_json({"event": "stop", "stream_sid": "stream-1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    call = db_session.scalar(select(CallLog))
    response = client.get(f"/dashboard/calls/{call.call_id}", headers=dashboard_auth)
    assert response.status_code == 200
    transcripts = response.json()["transcripts"]
    assert [turn["speaker"] for turn in transcripts] == ["CALLER", "AGENT"]
    assert "clomin" in transcripts[0]["text"].lower()
    assert transcripts[0]["text"] == stt.text
