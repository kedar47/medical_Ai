import io
import logging
import threading
import wave
from collections import OrderedDict
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import VoiceError
from app.services.voice.types import SynthesizedAudio

logger = logging.getLogger(__name__)

# Words espeak spells out letter by letter in a Hindi reply ("TAB" -> "टी ए
# बी"), said the way a pharmacist says them.
_HINDI_SPOKEN = {"TAB": "टैबलेट", "TABS": "टैबलेट", "MAXTRA": "मैक्स्ट्रा"}


def _hindi_speakable(text: str) -> str:
    import re

    return re.sub(
        r"(?<![A-Za-z])[A-Z]+(?![A-Za-z])",
        lambda m: _HINDI_SPOKEN.get(m.group(0), m.group(0)),
        text,
    )


class KokoroTextToSpeech:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._engine = None
        self._lock = threading.Lock()
        # Replies repeat ("Please say the correct full name..."), and on a
        # 2 vCPU server each one costs seconds of synthesis. Keep recent clips.
        self._cache: OrderedDict[tuple, SynthesizedAudio] = OrderedDict()
        self._cache_size = 64

    @property
    def model_path(self) -> Path:
        return Path(self.settings.kokoro_model_path)

    @property
    def voices_path(self) -> Path:
        return Path(self.settings.kokoro_voices_path)

    def files_available(self) -> bool:
        return self.model_path.is_file() and self.voices_path.is_file()

    def preload(self) -> None:
        self._get_engine()
        self.synthesize("a", language="en")

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            if not self.files_available():
                raise VoiceError(
                    503,
                    "Speech synthesis model files are missing.",
                )
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:
                raise VoiceError(
                    503,
                    "Speech synthesis is unavailable because kokoro-onnx is "
                    "not installed.",
                ) from exc
            try:
                self._engine = Kokoro(
                    str(self.model_path),
                    str(self.voices_path),
                )
            except Exception as exc:
                raise VoiceError(
                    503,
                    "Speech synthesis model could not be loaded.",
                ) from exc
        return self._engine

    @staticmethod
    def _phonemes(engine, text: str, lang: str) -> str | None:
        """Phonemes without espeak's language-switch flags.

        Kokoro's own phonemizer keeps them: "BRUFEN" in a Hindi reply becomes
        "(en)bɹˈuːfən(hi)", and the model reads the flags aloud. A live call
        (the client, 2026-11-22) heard "एन ब्रुफन ही" for every English word.
        Removing the flags keeps the English pronunciation of the name.
        None falls back to Kokoro's own path.
        """
        try:
            import phonemizer
            from kokoro_onnx import tokenizer as kokoro_tokenizer

            with kokoro_tokenizer._espeak_lock:
                raw = phonemizer.phonemize(
                    text,
                    lang,
                    preserve_punctuation=True,
                    with_stress=True,
                    language_switch="remove-flags",
                )
            vocab = engine.tokenizer.vocab
            return "".join(p for p in raw if p in vocab).strip() or None
        except Exception:
            logger.warning("voice.tts.phonemes_fallback", exc_info=True)
            return None

    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio:
        engine = self._get_engine()
        # Catalogue descriptions contain alignment padding. Collapsing whitespace
        # prevents long artificial pauses and needless synthesis time.
        text = " ".join(text.split())
        voice = (
            self.settings.voice_tts_voice_hi
            if language == "hi"
            else self.settings.voice_tts_voice_en
        )
        kokoro_language = "hi" if language == "hi" else "en-us"
        if kokoro_language == "hi":
            text = _hindi_speakable(text)
        key = (text, voice, self.settings.voice_tts_speed, kokoro_language)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        try:
            phonemes = self._phonemes(engine, text, kokoro_language)
            with self._lock:
                samples, sample_rate = engine.create(
                    phonemes if phonemes else text,
                    voice=voice,
                    speed=self.settings.voice_tts_speed,
                    lang=kokoro_language,
                    is_phonemes=bool(phonemes),
                )
        except Exception as exc:
            raise VoiceError(
                503,
                "Speech synthesis failed for the requested language.",
            ) from exc

        if samples is None or len(samples) == 0:
            raise VoiceError(503, "Speech synthesis produced no audio.")
        try:
            import numpy as np
        except ImportError as exc:
            raise VoiceError(
                503,
                "Speech synthesis is unavailable because numpy is not "
                "installed.",
            ) from exc

        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype("<i2").tobytes()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(int(sample_rate))
            output.writeframes(pcm)
        audio = SynthesizedAudio(
            data=buffer.getvalue(),
            sample_rate=int(sample_rate),
            voice=voice,
        )
        with self._lock:
            self._cache[key] = audio
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return audio
