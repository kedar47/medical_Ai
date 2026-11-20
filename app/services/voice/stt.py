import io
import os
import re
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

from app.core.config import Settings
from app.core.exceptions import NoSpeechError, VoiceError
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


def _is_junk_script(text: str) -> bool:
    """Noise decoded as another script ("੭੭੭੭", Telugu loops) or as Hindi vowel
    signs with no letter before them ("4-4 ु ु 4 ु ु"). Callers speak Hindi or
    English, written in Devanagari or Latin."""
    letters = [c for c in text if c.isalnum()]
    usual = [c for c in letters if c.isascii() or "ऀ" <= c <= "ॿ"]
    if letters and len(usual) < 0.6 * len(letters):
        return True
    return bool(re.search(r"(?:^|\s)[ा-्ँ-ः]", text))


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

    def _echoes_hint_names(self, transcript: str) -> bool:
        """True when Whisper copied the medicine-name hint instead of speech."""
        from rapidfuzz import fuzz

        names = [name.casefold() for name in self._vocabulary if name.isascii()]
        if not names:
            return False
        pieces = [" ".join(piece.casefold().split()).strip(" .") for piece in transcript.split(",")]
        hits = sum(
            1 for piece in pieces
            if piece and any(fuzz.ratio(piece, name) >= 80 for name in names)
        )
        # The hint echoed without commas: "CREMAFFIN CEROM 500 CETIRIZINE
        # CLOMIN DOLO 650." Count whole names, ignoring ones inside a longer
        # match ("dolo" in "dolo 650").
        lowered = " ".join(transcript.casefold().replace(",", " ").split())
        found = {
            name for name in names
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lowered)
        }
        distinct = {
            name for name in found
            if not any(name != other and name in other for other in found)
        }
        return max(hits, len(distinct)) >= 3

    def _temperatures(self) -> tuple[float, ...]:
        values = []
        for part in self.settings.voice_stt_temperatures.split(","):
            try:
                value = float(part)
            except ValueError:
                continue
            if 0.0 <= value <= 1.0:
                values.append(value)
        return tuple(values) or (0.0,)

    def _max_new_tokens(self, audio_bytes: bytes) -> int | None:
        """Token budget for a WAV clip, from its length. None if unknown."""
        per_second = self.settings.voice_stt_max_tokens_per_second
        if not per_second:
            return None
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
                seconds = reader.getnframes() / float(reader.getframerate())
        except (wave.Error, EOFError, ValueError, ZeroDivisionError):
            return None  # Browser WebM: length unknown before decoding.
        budget = self.settings.voice_stt_max_tokens_base + int(per_second * seconds)
        return min(budget, 440)

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
        max_new_tokens: int | None = None,
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
                temperature=self._temperatures(),
                best_of=self.settings.voice_stt_best_of,
                compression_ratio_threshold=self.settings.voice_stt_max_compression_ratio,
                max_new_tokens=max_new_tokens,
            )
            # When every attempt failed, Whisper still returns its last try.
            # A looping segment ("CREMAFFIN, CREMAFFIN, ...") is dropped.
            limit = self.settings.voice_stt_max_compression_ratio
            transcript = " ".join(
                segment.text.strip() for segment in segments
                if getattr(segment, "compression_ratio", 0.0) <= limit
            ).strip()
        return transcript, info

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str | None = None,
        language: str | None = None,
        allow_fallback: bool = True,
    ) -> TranscriptionResult:
        model = self._get_model()
        audio_bytes = _resample_wav_to_16k(audio_bytes)
        max_new_tokens = self._max_new_tokens(audio_bytes)
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
                    # No commas: with "Medicine names: A, B, C" Whisper kept
                    # continuing the list on phone audio (", brufin 400",
                    # "brufan, dolo650, ..."). Measured on the demo server.
                    prompt_parts.append("Medicines " + " ".join(latin_names) + ".")
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
                if not allow_fallback:
                    # Phone calls: when Hindi hears nothing, an English retry
                    # turns "हाँ" into a medicine name ("CREMAFFIN") and doubles
                    # the wait. Hindi mode already handles English speech.
                    fallback = None
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
                        max_new_tokens,
                    )
                    if transcript:
                        break
                # Noise, not speech: punctuation alone (".") or the hint list
                # echoed back ("CREMAFINE, CORFLAM, MADRIN, CREMAFFIN, ...").
                if not any(character.isalnum() for character in transcript):
                    transcript = ""
                elif self._echoes_hint_names(transcript) or _is_junk_script(transcript):
                    transcript = ""
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
            raise NoSpeechError(
                400,
                "No speech was detected in the audio. Please try again.",
            )
        return TranscriptionResult(
            transcript=transcript,
            language=info.language or requested or "en",
            language_probability=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", None),
        )
