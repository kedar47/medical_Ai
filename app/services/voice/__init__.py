import threading

from app.core.config import Settings
from app.services.voice.intent import build_intent_parser
from app.services.voice.stt import FasterWhisperSTT
from app.services.voice.tts import KokoroTextToSpeech
from app.services.voice.types import (
    IntentParser,
    SpeechToText,
    TextToSpeech,
    TranscriptionResult,
    SynthesizedAudio,
)

__all__ = [
    "FasterWhisperSTT",
    "IntentParser",
    "KokoroTextToSpeech",
    "SpeechToText",
    "SynthesizedAudio",
    "TextToSpeech",
    "TranscriptionResult",
    "build_intent_parser",
    "build_stt",
    "build_tts",
    "reset_shared_engines",
]


_shared_engines: dict[str, object] = {}
_shared_lock = threading.Lock()


def build_stt(settings: Settings) -> SpeechToText:
    """Return the process-wide STT engine.

    Loading Whisper is the dominant source of first-turn latency. VoiceService
    is constructed per HTTP request, so a per-instance adapter would reload the
    model on every turn. Reusing one adapter loads it once per worker process.
    """
    with _shared_lock:
        adapter = _shared_engines.get("stt")
        if adapter is not None:
            return adapter
        adapter = FasterWhisperSTT(settings)
        if settings.voice_stt_provider == "sarvam" and settings.sarvam_api_key:
            from app.services.voice.sarvam_stt import SarvamSTT

            adapter = SarvamSTT(settings, fallback=adapter)
        _shared_engines["stt"] = adapter
        return adapter


def build_tts(settings: Settings) -> TextToSpeech | None:
    if not settings.voice_tts_enabled:
        return None
    with _shared_lock:
        adapter = _shared_engines.get("tts")
        if isinstance(adapter, KokoroTextToSpeech):
            return adapter
        adapter = KokoroTextToSpeech(settings)
        _shared_engines["tts"] = adapter
        return adapter


def reset_shared_engines() -> None:
    with _shared_lock:
        _shared_engines.clear()
