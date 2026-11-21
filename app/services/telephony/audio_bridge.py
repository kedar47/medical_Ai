import base64
import binascii
import io
import wave

import numpy as np

RATE = 8000
FRAME_BYTES = 1600
PLAYBACK_FRAME_BYTES = 3200
PLAYBACK_INTERVAL_SECONDS = PLAYBACK_FRAME_BYTES / (RATE * 2)


def decode(payload: str) -> bytes:
    if not isinstance(payload, str) or len(payload) > 140000:
        raise ValueError("Audio packet too large")
    try:
        pcm = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid audio encoding") from exc
    if not pcm or len(pcm) % 2 or len(pcm) > 100000:
        raise ValueError("Invalid PCM packet")
    return pcm


def wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(pcm)
    return output.getvalue()


_GREETING_CACHE: dict[tuple[str, str, float], bytes] = {}


def _wav_to_pcm(wav_bytes: bytes) -> bytes:
    if len(wav_bytes) > 8_000_000:
        raise ValueError("TTS output too large")
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
            raise ValueError("Expected mono PCM16 WAV")
        rate = reader.getframerate()
        if rate not in {8000, 16000, 24000, 48000}:
            raise ValueError("Unsupported sample rate")
        samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(np.float64)
    # Windowed-sinc lowpass before integer decimation avoids aliasing telephone audio.
    factor = rate // RATE
    if factor > 1 and samples.size:
        x = np.arange(-48 * factor, 48 * factor + 1)
        kernel = np.sinc(x / factor) * np.hamming(len(x))
        kernel /= kernel.sum()
        samples = np.convolve(np.pad(samples, (len(kernel), len(kernel))), kernel, mode="same")[len(kernel):-len(kernel):factor]
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


def pcm_frames(pcm: bytes) -> list[str]:
    return [base64.b64encode(pcm[i:i + PLAYBACK_FRAME_BYTES].ljust(PLAYBACK_FRAME_BYTES, b"\0")).decode()
            for i in range(0, len(pcm), PLAYBACK_FRAME_BYTES)]


def frames(wav_bytes: bytes) -> list[str]:
    return pcm_frames(_wav_to_pcm(wav_bytes))


def cache_greeting_pcm(text: str, voice: str, wav_bytes: bytes, speed: float = 1.0) -> int:
    pcm = _wav_to_pcm(wav_bytes)
    if not pcm:
        raise ValueError("TTS output produced no audio")
    remainder = len(pcm) % PLAYBACK_FRAME_BYTES
    if remainder:
        pcm += b"\0" * (PLAYBACK_FRAME_BYTES - remainder)
    _GREETING_CACHE[(text, voice, speed)] = pcm
    return len(pcm)


def get_greeting_pcm(text: str, voice: str, speed: float = 1.0) -> bytes | None:
    return _GREETING_CACHE.get((text, voice, speed))


def clear_greeting_cache() -> None:
    _GREETING_CACHE.clear()


_REPLY_CACHE: dict[tuple[str, str, float], tuple[bytes, int, str]] = {}


def cache_reply(text: str, voice: str, speed: float, data: bytes, sample_rate: int, speaker: str) -> None:
    _REPLY_CACHE[(text, voice, speed)] = (data, sample_rate, speaker)


def get_reply(text: str, voice: str, speed: float) -> tuple[bytes, int, str] | None:
    return _REPLY_CACHE.get((text, voice, speed))


def clear_reply_cache() -> None:
    _REPLY_CACHE.clear()


class UtteranceBuffer:
    """Bounded energy endpointing, not clinical/phonetic recognition.

    Caller speech is emitted after 700 ms silence or max_seconds (default 12).
    Silence and packet fragments are bounded even if an attacker never pauses.
    """
    def __init__(self, threshold: int = 450, max_seconds: int = 12):
        self.threshold = threshold
        # 12 s suits local Whisper. Sarvam takes up to 30 s, and a caller
        # reading out a list of medicines is not cut off mid-name.
        self.max_frames = max(10, max_seconds * 10)
        self.pending = bytearray()
        self.audio = bytearray()
        self.silence = 0
        self.speaking = False
        self.last_voiced_bytes = 0

    def reset(self) -> None:
        self.pending.clear()
        self.audio.clear()
        self.silence = 0
        self.speaking = False
        self.last_voiced_bytes = 0

    def feed(self, pcm: bytes) -> tuple[list[bytes], bool]:
        self.pending.extend(pcm)
        turns, onset, voiced_bytes = [], False, 0
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            values = np.frombuffer(frame, dtype="<i2").astype(np.float64)
            voiced = float(np.sqrt(np.mean(values * values))) > self.threshold
            if voiced and not self.speaking:
                self.speaking, onset = True, True
            if self.speaking:
                self.audio.extend(frame)
                if voiced:
                    voiced_bytes += FRAME_BYTES
                self.silence = 0 if voiced else self.silence + FRAME_BYTES
                if self.silence >= 7 * FRAME_BYTES or len(self.audio) >= self.max_frames * FRAME_BYTES:
                    turns.append(bytes(self.audio))
                    self.audio.clear()
                    self.speaking, self.silence = False, 0
        self.last_voiced_bytes = voiced_bytes
        return turns, onset
