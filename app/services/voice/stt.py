import io
import os
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

from app.core.config import Settings
from app.core.exceptions import VoiceError
from app.services.voice.types import TranscriptionResult


_WHISPER_SAMPLE_RATE = 16000


def _resample_wav_to_16k(audio_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            if reader.getcomptype() != "NONE" or reader.getsampwidth() != 2:
                return audio_bytes
            rate = reader.getframerate()
            channels = reader.getnchannels()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError, ValueError):
        return audio_bytes
    if rate >= _WHISPER_SAMPLE_RATE or not frames:
        return audio_bytes
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    factor = _WHISPER_SAMPLE_RATE / rate
    output_size = max(1, int(round(samples.size * factor)))
    positions = np.arange(output_size, dtype=np.float64) / factor
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, samples.size - 1)
    weight = positions - left
    resampled = samples[left] * (1.0 - weight) + samples[right] * weight
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_WHISPER_SAMPLE_RATE)
        writer.writeframes(np.clip(np.rint(resampled), -32768, 32767).astype("<i2").tobytes())
    return output.getvalue()


class FasterWhisperSTT:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._vocabulary: tuple[str, ...] = ()

    def set_vocabulary(self, names: list[str]) -> None:
        # Names only: never bias recognition toward a dosage or order quantity.
        vocabulary = tuple(
            dict.fromkeys(
                " ".join(name.split())[:80] for name in names if name.strip()
            )
        )[:40]
        if vocabulary != self._vocabulary:
            self._vocabulary = vocabulary

    def preload(self) -> None:
        self._get_model()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceError(
                    503,
                    "Speech recognition is unavailable because faster-whisper "
                    "is not installed.",
                ) from exc
            try:
                self._model = WhisperModel(
                    self.settings.voice_stt_model,
                    device=self.settings.voice_stt_device,
                    compute_type=self.settings.voice_stt_compute_type,
                )
            except Exception as exc:
                raise VoiceError(
                    503,
                    "Speech recognition model could not be loaded.",
                ) from exc
        return self._model

    def _transcribe_once(
        self,
        model,
        temporary_path: str,
        language: str | None,
        initial_prompt: str | None,
        hotwords: str | None,
        vad_parameters: dict | None,
    ) -> tuple[str, object]:
        with self._inference_lock:
            segments, info = model.transcribe(
                temporary_path,
                language=language,
                beam_size=self.settings.voice_stt_beam_size,
                vad_filter=self.settings.voice_stt_vad_filter,
                vad_parameters=vad_parameters,
                # Do not condition on a previous (unrelated) utterance.
                condition_on_previous_text=False,
                initial_prompt=initial_prompt,
                hotwords=hotwords,
            )
            transcript = " ".join(
                segment.text.strip() for segment in segments
            ).strip()
        return transcript, info

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        model = self._get_model()
        audio_bytes = _resample_wav_to_16k(audio_bytes)
        suffix = Path(filename or "").suffix.lower()
        if not suffix or len(suffix) > 8:
            suffix = ".webm"
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False
            ) as temporary_file:
                temporary_file.write(audio_bytes)
                temporary_path = temporary_file.name
            try:
                hotwords = None
                if (
                    self.settings.voice_stt_hotwords_enabled
                    and self._vocabulary
                ):
                    hotwords = ", ".join(self._vocabulary)[:600]
                prompt_parts = []
                if self.settings.voice_stt_initial_prompt.strip():
                    prompt_parts.append(self.settings.voice_stt_initial_prompt.strip())
                # Hint with English-letter names only. Devanagari aliases in the
                # prompt make Whisper loop on English speech (garbage output and
                # temperature retries: 70-100 s instead of ~2.5 s on 2 vCPU).
                # The aliases still apply in text matching after transcription.
                latin_names = [name for name in self._vocabulary if name.isascii()]
                if latin_names:
                    prompt_parts.append("Medicine names: " + ", ".join(latin_names))
                initial_prompt = " ".join(prompt_parts)[:600] or None
                vad_parameters = None
                if self.settings.voice_stt_vad_filter:
                    vad_parameters = {
                        "threshold": self.settings.voice_stt_vad_threshold,
                        "min_silence_duration_ms": self.settings.voice_stt_vad_min_silence_ms,
                        "speech_pad_ms": self.settings.voice_stt_vad_speech_pad_ms,
                    }
                requested = (
                    language
                    if language in {"hi", "en"}
                    else (
                        self.settings.voice_stt_language
                        if self.settings.voice_stt_language in {"hi", "en"}
                        else None
                    )
                )
                fallback = (
                    self.settings.voice_stt_fallback_language
                    if self.settings.voice_stt_fallback_language in {"hi", "en"}
                    else None
                )
                attempts: list[str | None] = []
                for candidate in (requested, fallback):
                    if candidate and candidate not in attempts:
                        attempts.append(candidate)
                if not attempts:
                    attempts.append(None)
                transcript, info = "", None
                for attempt_language in attempts:
                    transcript, info = self._transcribe_once(
                        model, temporary_path, attempt_language,
                        initial_prompt, hotwords, vad_parameters,
                    )
                    if transcript:
                        break
            except Exception as exc:
                raise VoiceError(
                    400,
                    "The audio could not be transcribed. Please try again.",
                ) from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

        if not transcript:
            raise VoiceError(
                400,
                "No speech was detected in the audio. Please try again.",
            )
        return TranscriptionResult(
            transcript=transcript,
            language=info.language or requested or "en",
            language_probability=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", None),
        )
