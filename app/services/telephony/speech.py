"""Full readbacks via the existing local TTS, with bounded synthesis chunks."""
import io
import re
import wave

from app.core.exceptions import VoiceError

# Split after a sentence end (Hindi danda, . ? ! :) or before a numbered
# option such as "2) BRUFEN 400MG", so a phone reply can start playing after
# its first sentence is synthesized instead of after the whole reply.
_SPEECH_BREAK = re.compile(r"(?<=[।.?!:])\s+|\s+(?=\d{1,2}\)\s)")


def speech_parts(text: str, min_chars: int = 24) -> list[str]:
    text = " ".join(text.split())
    parts: list[str] = []
    for piece in (p.strip() for p in _SPEECH_BREAK.split(text)):
        if not piece:
            continue
        if parts and len(parts[-1]) < min_chars:
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    return parts or ([text] if text else [])


def synthesize_full(tts, text, language, limit=600):
    if not tts:
        raise VoiceError(503, "TTS is unavailable")
    if len(text) > 12000:
        raise VoiceError(503, "Readback exceeds telephone limit")
    parts = []
    remaining = text
    while remaining:
        end = min(len(remaining), limit)
        if end < len(remaining):
            end = remaining.rfind(" ", 0, end) or end
            if end < 1:
                end = min(len(remaining), limit)
        part, remaining = remaining[:end], remaining[end:].lstrip()
        parts.append(tts.synthesize(part, language=language))
    if len(parts) == 1:
        return parts[0]
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(parts[0].sample_rate)
        for part in parts:
            with wave.open(io.BytesIO(part.data), "rb") as reader:
                if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != parts[0].sample_rate:
                    raise VoiceError(503, "TTS format changed during summary")
                writer.writeframes(reader.readframes(reader.getnframes()))
            if output.tell() > 8_000_000:
                raise VoiceError(503, "Readback audio too large")
    from app.services.voice.types import SynthesizedAudio
    return SynthesizedAudio(data=output.getvalue(), sample_rate=parts[0].sample_rate, voice=parts[0].voice)
