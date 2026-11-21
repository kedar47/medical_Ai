"""Sarvam AI speech-to-text, with the local Whisper engine as automatic backup.

Sarvam's Saarika models are trained for Indian languages, Hinglish and 8 kHz
telephone audio. On the demo's phone-quality test clips they returned the
exact words ("दो स्ट्रिप ब्रूफेन 400 चाहिए।") in about 0.4 s, where Whisper
small on the 2 vCPU server took ~2.5 s and often misheard medicine names.

Any Sarvam failure (network, timeout, quota, bad response) falls back to
Whisper for that utterance, so a call never breaks because of the provider.
The API key is read from settings (SARVAM_API_KEY) and never logged.
"""
import logging

import httpx

from app.core.config import Settings
from app.core.exceptions import NoSpeechError
from app.services.voice.types import TranscriptionResult

logger = logging.getLogger(__name__)

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


class SarvamSTT:
    def __init__(self, settings: Settings, fallback, client: httpx.Client | None = None):
        self.settings = settings
        self.fallback = fallback
        self._client = client or httpx.Client(timeout=settings.sarvam_timeout_seconds)

    # The rest of the app talks to every STT engine the same way.
    def set_vocabulary(self, names: list[str]) -> None:
        self.fallback.set_vocabulary(names)

    def preload(self) -> None:
        self.fallback.preload()  # keep the backup warm

    @property
    def _vocabulary(self):
        return getattr(self.fallback, "_vocabulary", ())

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None = None,
        language: str | None = None,
        allow_fallback: bool = True,
    ) -> TranscriptionResult:
        try:
            return self._sarvam(audio_bytes, filename or "audio.wav")
        except NoSpeechError:
            raise
        except Exception as exc:
            logger.warning("voice.stt.sarvam_failed error=%s; using local Whisper", type(exc).__name__)
            return self.fallback.transcribe(
                audio_bytes, filename=filename, language=language, allow_fallback=allow_fallback
            )

    def _sarvam(self, audio_bytes: bytes, filename: str) -> TranscriptionResult:
        content_type = "audio/wav" if filename.lower().endswith(".wav") else "application/octet-stream"
        response = self._client.post(
            SARVAM_STT_URL,
            headers={"api-subscription-key": self.settings.sarvam_api_key},
            files={"file": (filename, audio_bytes, content_type)},
            data={
                "model": self.settings.sarvam_stt_model,
                # "unknown": Sarvam detects Hindi or English itself, so English
                # speech comes back in English letters, Hindi in Devanagari.
                "language_code": self.settings.sarvam_stt_language,
            },
        )
        response.raise_for_status()
        payload = response.json()
        transcript = " ".join(str(payload.get("transcript") or "").split())
        if not any(character.isalnum() for character in transcript):
            raise NoSpeechError(400, "No speech was detected in the audio. Please try again.")
        code = str(payload.get("language_code") or "")[:2].lower()
        return TranscriptionResult(
            transcript=transcript,
            language=code if code in {"hi", "en"} else "hi",
            language_probability=None,
            duration_seconds=None,
        )
