"""Plivo Audio Streaming: adapter, answer URL and a whole phone order."""
import base64
import warnings

import numpy as np
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.database.models import CallLog, Order
from app.services.telephony import plivo_adapter
from app.services.telephony.audio_bridge import PLAYBACK_FRAME_BYTES
from app.services.telephony.mulaw import pcm_to_ulaw, ulaw_to_pcm
from app.services.telephony.plivo_adapter import PlivoAdapter, answer_xml, caller_phone, remember_caller

# Shared fixtures: autouse telephony settings and the simulated STT/TTS gateway.
from test_telephony import simulated_gateway, telephony_settings  # noqa: F401

TOKEN = "plivo-secret"
STREAM = "87654321-4321-4321-4321-cba987654321"
CALL = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture(autouse=True)
def plivo_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "plivo_stream_token", TOKEN)
    monkeypatch.setattr(s, "plivo_auth_id", "")
    monkeypatch.setattr(s, "telephony_public_base_url", "https://api.example.com")
    plivo_adapter._callers.clear()


def plivo_start(call=CALL, stream=STREAM, encoding="audio/x-mulaw", rate=8000, extra=None):
    packet = {"event": "start", "sequenceNumber": 1, "start": {
        "callId": call, "streamId": stream, "accountId": "MAXXXX", "tracks": ["inbound"],
        "mediaFormat": {"encoding": encoding, "sampleRate": rate}}}
    if extra is not None:
        packet["extra_headers"] = extra
    return packet


def plivo_media(sequence, pcm, stream=STREAM):
    return {"event": "media", "sequenceNumber": sequence, "streamId": stream,
            "media": {"track": "inbound", "timestamp": "1705312200000", "chunk": sequence,
                      "payload": base64.b64encode(pcm_to_ulaw(pcm)).decode()}}


def test_mulaw_matches_reference_codec():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        audioop = pytest.importorskip("audioop")
    pcm = np.arange(-32768, 32768, dtype="<i2").tobytes()
    assert pcm_to_ulaw(pcm) == audioop.lin2ulaw(pcm, 2)
    assert ulaw_to_pcm(bytes(range(256))) == audioop.ulaw2lin(bytes(range(256)), 2)


def test_mulaw_roundtrip_keeps_speech_level():
    tone = (np.sin(np.arange(1600) / 4) * 8000).astype("<i2")
    back = np.frombuffer(ulaw_to_pcm(pcm_to_ulaw(tone.tobytes())), dtype="<i2")
    assert np.max(np.abs(back.astype(int) - tone.astype(int))) < 300


@pytest.mark.parametrize("raw, expected", [
    ("919876543210", "+919876543210"), ("+919876543210", "+919876543210"),
    ("9876543210", "+9876543210"), ("anonymous", None), ("", None), (None, None), ("0123", None),
])
def test_caller_phone(raw, expected):
    assert caller_phone(raw) == expected


def test_start_uses_number_from_answer_request():
    adapter = PlivoAdapter()
    remember_caller(CALL, "919876543210")
    assert adapter.start(adapter.normalize(plivo_start())) == (CALL, STREAM, "+919876543210")


def test_start_falls_back_to_extra_headers():
    adapter = PlivoAdapter()
    packet = adapter.normalize(plivo_start(extra="callerPhone=919876543210"))
    assert adapter.start(packet)[2] == "+919876543210"
    assert adapter.start(adapter.normalize(plivo_start(extra="callerPhone=none")))[2] is None


@pytest.mark.parametrize("change", [
    {"encoding": "audio/x-l16"}, {"rate": 16000}, {"call": "bad id!"}, {"stream": ""},
])
def test_start_rejects_unexpected_streams(change):
    adapter = PlivoAdapter()
    with pytest.raises(ValueError):
        adapter.start(adapter.normalize(plivo_start(**change)))


def test_start_rejects_other_account():
    adapter = PlivoAdapter(auth_id="MAOTHER")
    with pytest.raises(ValueError):
        adapter.start(adapter.normalize(plivo_start()))


def test_normalize_converts_media_and_events():
    adapter = PlivoAdapter()
    pcm = (np.ones(160, dtype="<i2") * 2000).tobytes()
    media = adapter.normalize(plivo_media(5, pcm))
    assert media["event"] == "media" and media["stream_sid"] == STREAM and media["sequence_number"] == 5
    decoded = np.frombuffer(base64.b64decode(media["media"]["payload"]), dtype="<i2")
    assert len(decoded) == 160 and abs(int(decoded[0]) - 2000) < 100
    played = adapter.normalize({"event": "playedStream", "sequenceNumber": 9, "streamId": STREAM, "name": "reply-3"})
    assert played == {"event": "mark", "stream_sid": STREAM, "mark": {"name": "reply-3"}}
    assert adapter.normalize({"event": "clearedAudio", "streamId": STREAM})["event"] == "clear"
    assert adapter.normalize({"event": "dtmf", "streamId": STREAM})["event"] == "ignored"
    assert adapter.normalize({"event": "somethingNew"})["event"] == "ignored"


def test_outgoing_messages_follow_plivo_protocol():
    adapter = PlivoAdapter()
    pcm = (np.ones(PLAYBACK_FRAME_BYTES // 2, dtype="<i2") * 1000).tobytes()
    message = adapter.media(STREAM, base64.b64encode(pcm).decode(), 1)
    assert message["event"] == "playAudio"
    assert message["media"]["contentType"] == "audio/x-mulaw" and message["media"]["sampleRate"] == 8000
    assert len(base64.b64decode(message["media"]["payload"])) == PLAYBACK_FRAME_BYTES // 2
    assert adapter.clear(STREAM) == {"event": "clearAudio", "streamId": STREAM}
    assert adapter.mark(STREAM, "done-4") == {"event": "checkpoint", "streamId": STREAM, "name": "done-4"}


def test_answer_xml_is_valid_and_escaped():
    import xml.etree.ElementTree as ET
    root = ET.fromstring(answer_xml("wss://api.example.com/telephony/plivo/media/a&b", "+919876543210"))
    stream = root.find("Stream")
    assert stream.text == "wss://api.example.com/telephony/plivo/media/a&b"
    assert stream.get("bidirectional") == "true" and stream.get("keepCallAlive") == "true"
    assert stream.get("contentType") == "audio/x-mulaw;rate=8000"
    assert stream.get("extraHeaders") == "callerPhone=919876543210"


def test_answer_url_requires_token(client):
    assert client.post("/telephony/plivo/answer/wrong", data={"CallUUID": CALL}).status_code == 401
    response = client.post("/telephony/plivo/answer/" + TOKEN,
                           data={"CallUUID": CALL, "From": "919876543210", "To": "912269874685",
                                 "Direction": "inbound"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "wss://api.example.com/telephony/plivo/media/" + TOKEN in response.text
    assert plivo_adapter.recall_caller(CALL) == "+919876543210"
    assert client.get("/telephony/status").json()["plivo_ready"] is True


def test_media_socket_rejects_wrong_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/telephony/plivo/media/wrong") as ws:
            ws.receive_json()


def drain_plivo(ws):
    count = 0
    while True:
        packet = ws.receive_json()
        if packet["event"] == "playAudio":
            assert len(base64.b64decode(packet["media"]["payload"])) == PLAYBACK_FRAME_BYTES // 2
            count += 1
        if packet["event"] == "checkpoint":
            ws.send_json({"event": "playedStream", "sequenceNumber": 0, "streamId": packet["streamId"],
                          "name": packet["name"]})
            return packet, count


def test_complete_plivo_phone_order(simulated_gateway, seed_catalogue, db_session, client):  # noqa: F811
    client, stt = simulated_gateway
    client.post("/telephony/plivo/answer/" + TOKEN, data={"CallUUID": CALL, "From": "919876543210"})
    voiced = (np.ones(1600, dtype="<i2") * 2000).tobytes()
    sequence = 1
    with client.websocket_connect("/telephony/plivo/media/" + TOKEN) as ws:
        ws.send_json(plivo_start())
        assert drain_plivo(ws)[1] > 0
        ws.send_json({"event": "dtmf", "sequenceNumber": 2, "streamId": STREAM,
                      "dtmf": {"track": "inbound", "digit": "5"}})
        for text in ["add two strips of clomin", "show summary", "yes confirm"]:
            stt.text = text
            # 20 ms frames as Plivo sends them: speech, then silence to end the turn.
            for chunk in [voiced[i:i + 320] for i in range(0, len(voiced), 320)] + [bytes(320)] * 40:
                sequence += 1
                ws.send_json(plivo_media(sequence, chunk))
            packet, _ = drain_plivo(ws)
        assert packet["name"].startswith("done-")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    db_session.expire_all()
    call = db_session.scalar(select(CallLog))
    order = db_session.scalar(select(Order))
    assert call.provider == "plivo" and call.status == "COMPLETED"
    assert call.customer_phone == "+919876543210"
    assert call.confirmed_order_id == order.order_id
    assert order.call_reference_id == CALL
    assert order.confirmation_source == "TELEPHONY_PLIVO"
