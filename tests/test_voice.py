from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.voice_routes import get_voice_service
from app.core.exceptions import VoiceError
from app.database.models import VoiceSession
from app.main import app
from app.services.voice.types import SynthesizedAudio, TranscriptionResult
from app.services.voice_service import VoiceService


class FakeSTT:
    def __init__(self, transcript: str = "add one strip of clomin") -> None:
        self.transcript = transcript
        self.calls: list[dict] = []

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        self.calls.append(
            {
                "size": len(audio_bytes),
                "filename": filename,
                "language": language,
            }
        )
        return TranscriptionResult(
            transcript=self.transcript,
            language="hi",
            language_probability=0.94,
            duration_seconds=1.4,
        )


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio:
        self.calls.append((text, language))
        return SynthesizedAudio(
            data=b"RIFFfake-wav",
            sample_rate=24000,
            voice="fake_voice",
        )


class FailingTTS:
    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio:
        raise VoiceError(503, "TTS test failure.")


def use_voice_adapters(
    session_factory,
    *,
    stt=None,
    tts=None,
    intent_parser=None,
) -> None:
    def dependency():
        db = session_factory()
        try:
            yield VoiceService(
                db,
                stt=stt,
                tts=tts,
                intent_parser=intent_parser,
            )
        finally:
            db.close()

    app.dependency_overrides[get_voice_service] = dependency


def create_voice(
    client: TestClient,
    *,
    language: str = "hi",
    name: str | None = "Asha",
) -> dict:
    response = client.post(
        "/voice/sessions",
        json={
            "customer_phone": "+919876543210",
            "customer_name": name,
            "language": language,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def turn(
    client: TestClient,
    voice_session_id: str,
    text: str,
    *,
    speak: bool = False,
) -> dict:
    response = client.post(
        f"/voice/sessions/{voice_session_id}/turns",
        json={"text": text, "speak": speak},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_voice_status_and_session_lifecycle(
    client: TestClient, seed_catalogue: None
) -> None:
    status = client.get("/voice/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["enabled"] is True
    assert payload["stt"]["provider"] == "faster-whisper"
    assert payload["tts"]["provider"] == "kokoro-onnx"
    assert payload["llm"]["provider"] == "ollama"

    session = create_voice(client)
    assert session["voice_session_id"].startswith("voice_")
    assert session["order_session_id"].startswith("session_")
    assert session["status"] == "ACTIVE"
    assert session["language"] == "hi"
    assert "नमस्ते" in session["message"]

    fetched = client.get(
        f"/voice/sessions/{session['voice_session_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["turn_count"] == 0
    assert client.get("/voice/sessions/missing").status_code == 404


def test_voice_adds_exact_alias_with_quantity_and_unit(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)

    payload = turn(
        client,
        session["voice_session_id"],
        "add two strips of clomin",
    )

    assert payload["intent"] == "add_item"
    assert payload["order_summary"]["line_item_count"] == 1
    item = payload["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00003"
    assert item["medicine_name"] == "CLOMIN TAB"
    assert item["quantity"] == 2
    assert item["unit"] == "STRIP"

    order_session = client.get(
        f"/order-sessions/{session['order_session_id']}"
    ).json()
    assert order_session["items"][0]["quantity"] == 2


def test_voice_asks_for_missing_quantity_then_adds(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    first = turn(client, session["voice_session_id"], "add clomin")

    assert first["order_summary"] is None
    assert "quantity" in first["assistant_text"]

    second = turn(client, session["voice_session_id"], "two strips")
    assert second["order_summary"]["line_item_count"] == 1
    assert second["order_summary"]["items"][0]["quantity"] == 2


def test_voice_clarifies_variants_and_selects_by_ordinal(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    first = turn(
        client,
        session["voice_session_id"],
        "add brufen two strips",
    )

    assert first["order_summary"] is None
    assert len(first["candidates"]) == 3
    assert {candidate["medicine_id"] for candidate in first["candidates"]} == {
        "MED-00013",
        "MED-00014",
        "MED-00015",
    }

    second = turn(client, session["voice_session_id"], "first")
    assert second["order_summary"]["line_item_count"] == 1
    item = second["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00013"
    assert item["quantity"] == 2
    assert item["unit"] == "STRIP"


def test_voice_selects_variant_by_strength(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)

    payload = turn(
        client,
        session["voice_session_id"],
        "add brufen 400 two strips",
    )

    assert payload["order_summary"]["line_item_count"] == 1
    item = payload["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00014"
    assert item["description"] == "400MG 15'S"


def test_voice_fuzzy_suggestions_reject_ordinal_and_accept_medicine_id(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    first = turn(
        client,
        session["voice_session_id"],
        "add brufn one strip",
    )

    assert first["order_summary"] is None
    assert first["candidates"]
    assert all(
        candidate["match_type"] == "FUZZY_SUGGESTION"
        for candidate in first["candidates"]
    )

    rejected = turn(client, session["voice_session_id"], "first")
    assert rejected["order_summary"] is None
    assert "medicine ID" in rejected["assistant_text"]

    accepted = turn(client, session["voice_session_id"], "MED-00014")
    assert accepted["order_summary"]["line_item_count"] == 1
    assert accepted["order_summary"]["items"][0]["medicine_id"] == "MED-00014"


def test_voice_updates_and_removes_last_item(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    turn(client, session["voice_session_id"], "add two strips of clomin")

    updated = turn(
        client,
        session["voice_session_id"],
        "change last item to three strips",
    )
    item = updated["order_summary"]["items"][0]
    assert item["quantity"] == 3
    assert item["unit"] == "STRIP"

    removed = turn(
        client, session["voice_session_id"], "remove last item"
    )
    assert removed["order_summary"]["line_item_count"] == 0


def test_voice_summary_then_confirm_creates_order_without_exposing_token(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    turn(client, session["voice_session_id"], "add clomin two strips")

    summary = turn(client, session["voice_session_id"], "show summary")
    assert summary["intent"] == "show_summary"
    assert summary["order_summary"]["can_confirm"] is True
    assert "confirmation_token" not in summary
    assert "summary_digest" not in summary

    confirmed = turn(client, session["voice_session_id"], "confirm")
    assert confirmed["intent"] == "confirm_order"
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["order"]["order_id"].startswith("order_")
    assert confirmed["order"]["confirmation_source"] == "VOICE_DEMO"
    assert confirmed["order"]["total_line_items"] == 1

    repeated = turn(client, session["voice_session_id"], "confirm")
    assert repeated["order"]["order_id"] == confirmed["order"]["order_id"]


def test_voice_requires_summary_review_before_first_confirm(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    turn(client, session["voice_session_id"], "add clomin two strips")

    first = turn(client, session["voice_session_id"], "confirm")
    assert first["order"] is None
    assert first["intent"] == "show_summary"
    assert first["order_summary"]["can_confirm"] is True

    second = turn(client, session["voice_session_id"], "confirm")
    assert second["order"]["order_id"].startswith("order_")


def test_voice_rejects_confirm_after_draft_changes(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_voice(client)
    turn(client, session["voice_session_id"], "add clomin two strips")
    turn(client, session["voice_session_id"], "show summary")

    added = client.post(
        f"/order-sessions/{session['order_session_id']}/items",
        json={
            "medicine_id": "MED-00013",
            "quantity": 1,
            "unit": "STRIP",
        },
    )
    assert added.status_code == 201

    changed = turn(client, session["voice_session_id"], "confirm")
    assert changed["order"] is None
    assert changed["intent"] == "show_summary"
    assert changed["order_summary"]["line_item_count"] == 2

    confirmed = turn(client, session["voice_session_id"], "confirm")
    assert confirmed["order"]["total_line_items"] == 2


def test_voice_cancel_and_end_close_sessions(
    client: TestClient, seed_catalogue: None
) -> None:
    cancelled_session = create_voice(client)
    turn(
        client,
        cancelled_session["voice_session_id"],
        "add clomin two strips",
    )
    cancelled = turn(
        client, cancelled_session["voice_session_id"], "cancel"
    )
    assert cancelled["status"] == "CANCELLED"
    assert (
        client.get(
            f"/order-sessions/{cancelled_session['order_session_id']}"
        ).json()["status"]
        == "CANCELLED"
    )
    assert client.post(
        f"/voice/sessions/{cancelled_session['voice_session_id']}/end"
    ).status_code == 200

    ended_session = create_voice(client)
    turn(client, ended_session["voice_session_id"], "add clomin two strips")
    ended = client.post(
        f"/voice/sessions/{ended_session['voice_session_id']}/end"
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "ENDED"
    assert (
        client.get(
            f"/order-sessions/{ended_session['order_session_id']}"
        ).json()["status"]
        == "CANCELLED"
    )


def test_voice_audio_turn_uses_stt_and_can_degrade_tts(
    client: TestClient,
    session_factory,
    seed_catalogue: None,
) -> None:
    stt = FakeSTT()
    use_voice_adapters(session_factory, stt=stt)
    session = create_voice(client)

    response = client.post(
        f"/voice/sessions/{session['voice_session_id']}/audio-turns",
        files={"file": ("test.webm", b"fake-audio", "audio/webm")},
        data={"speak": "false"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["transcript"] == "add one strip of clomin"
    assert payload["order_summary"]["items"][0]["quantity"] == 1
    assert stt.calls[0]["size"] == len(b"fake-audio")
    assert stt.calls[0]["language"] == "hi"

    use_voice_adapters(session_factory, stt=FakeSTT(), tts=FailingTTS())
    degraded = client.post(
        f"/voice/sessions/{session['voice_session_id']}/turns",
        json={"text": "show summary", "speak": True},
    )
    assert degraded.status_code == 200
    assert degraded.json()["tts"] is None
    assert "TTS test failure" in degraded.json()["tts_error"]


def test_voice_text_turn_returns_tts_audio(
    client: TestClient,
    session_factory,
    seed_catalogue: None,
) -> None:
    tts = FakeTTS()
    use_voice_adapters(session_factory, tts=tts)
    session = create_voice(client)

    response = client.post(
        f"/voice/sessions/{session['voice_session_id']}/turns",
        json={"text": "help", "speak": True},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["intent"] == "help"
    assert payload["tts"]["sample_rate"] == 24000
    assert payload["tts"]["voice"] == "fake_voice"
    assert payload["tts"]["audio_base64"]
    assert tts.calls[0][1] == "en"


def test_voice_transcribe_endpoint_and_audio_validation(
    client: TestClient,
    session_factory,
) -> None:
    stt = FakeSTT("show summary")
    use_voice_adapters(session_factory, stt=stt)
    response = client.post(
        "/voice/transcribe",
        files={"file": ("sample.wav", b"audio", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["transcript"] == "show summary"

    bad_type = client.post(
        "/voice/transcribe",
        files={"file": ("sample.txt", b"text", "text/plain")},
    )
    assert bad_type.status_code == 400

    empty = client.post(
        "/voice/transcribe",
        files={"file": ("sample.wav", b"", "audio/wav")},
    )
    assert empty.status_code == 400


def test_voice_expired_session_rejects_turns(
    client: TestClient,
    db_session: Session,
    seed_catalogue: None,
) -> None:
    session = create_voice(client)
    voice = db_session.scalar(
        select(VoiceSession).where(
            VoiceSession.voice_session_id == session["voice_session_id"]
        )
    )
    voice.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    response = client.post(
        f"/voice/sessions/{session['voice_session_id']}/turns",
        json={"text": "help", "speak": False},
    )
    assert response.status_code == 409

    fetched = client.get(
        f"/voice/sessions/{session['voice_session_id']}"
    )
    assert fetched.json()["status"] == "EXPIRED"
