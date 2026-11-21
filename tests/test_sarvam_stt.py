"""Sarvam speech-to-text adapter: success, no speech, and Whisper backup."""
import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import NoSpeechError
from app.services.voice.sarvam_stt import SARVAM_STT_URL, SarvamSTT
from app.services.voice.types import TranscriptionResult


class FakeWhisper:
    def __init__(self):
        self.calls = 0
        self.vocabulary = None

    def transcribe(self, audio_bytes, *, filename=None, language=None, allow_fallback=True):
        self.calls += 1
        return TranscriptionResult(transcript="whisper text", language=language or "hi")

    def set_vocabulary(self, names):
        self.vocabulary = list(names)

    def preload(self):
        pass


def settings():
    return Settings(DATABASE_URL="sqlite://", sarvam_api_key="test-key", voice_stt_provider="sarvam")


def adapter(handler):
    whisper = FakeWhisper()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SarvamSTT(settings(), whisper, client=client), whisper


def test_sarvam_transcript_is_used_and_key_sent_in_header():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-subscription-key")
        seen["body"] = request.content
        return httpx.Response(200, json={"transcript": " दो स्ट्रिप  ब्रूफेन 400 ", "language_code": "hi-IN"})

    stt, whisper = adapter(handler)
    result = stt.transcribe(b"RIFFdata", filename="turn.wav", language="hi")
    assert result.transcript == "दो स्ट्रिप ब्रूफेन 400"
    assert result.language == "hi"
    assert seen["url"] == SARVAM_STT_URL
    assert seen["key"] == "test-key"
    assert b"saarika:v2.5" in seen["body"]
    assert whisper.calls == 0


def test_english_answer_is_marked_english():
    stt, _ = adapter(lambda r: httpx.Response(200, json={"transcript": "Dolo 650", "language_code": "en-IN"}))
    assert stt.transcribe(b"x", filename="a.wav").language == "en"


@pytest.mark.parametrize("transcript", ["", "  ", "।", "..."])
def test_empty_transcript_is_no_speech_not_a_whisper_retry(transcript):
    stt, whisper = adapter(lambda r: httpx.Response(200, json={"transcript": transcript}))
    with pytest.raises(NoSpeechError):
        stt.transcribe(b"x", filename="a.wav")
    assert whisper.calls == 0


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"error": "bad key"}),
    httpx.Response(429, json={"error": "quota"}),
    httpx.Response(500, text="oops"),
    httpx.Response(200, text="not json"),
])
def test_any_sarvam_failure_falls_back_to_whisper(response):
    stt, whisper = adapter(lambda r: response)
    assert stt.transcribe(b"x", filename="a.wav", language="hi").transcript == "whisper text"
    assert whisper.calls == 1


def test_network_error_falls_back_to_whisper():
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)

    stt, whisper = adapter(handler)
    assert stt.transcribe(b"x", filename="a.wav").transcript == "whisper text"
    assert whisper.calls == 1


def test_vocabulary_goes_to_backup_engine():
    stt, whisper = adapter(lambda r: httpx.Response(200, json={"transcript": "x"}))
    stt.set_vocabulary(["BRUFEN", "DOLO 650"])
    assert whisper.vocabulary == ["BRUFEN", "DOLO 650"]


def test_build_stt_uses_sarvam_only_with_a_key():
    from app.services.voice import build_stt, reset_shared_engines

    reset_shared_engines()
    try:
        assert isinstance(build_stt(settings()), SarvamSTT)
        reset_shared_engines()
        no_key = Settings(DATABASE_URL="sqlite://", voice_stt_provider="sarvam", sarvam_api_key="")
        assert not isinstance(build_stt(no_key), SarvamSTT)
    finally:
        reset_shared_engines()
