import argparse
import sys
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import VoiceError
from app.services.voice.stt import FasterWhisperSTT
from app.services.voice.tts import KokoroTextToSpeech

DEFAULT_HINDI_TEXT = (
    "नमस्ते, मुझे दो स्ट्रिप ब्रूफेन और एक स्ट्रिप क्लोमिन चाहिए।"
)
DEFAULT_ENGLISH_TEXT = "Hello, I need two strips of BRUFEN."


def _read_text(args: argparse.Namespace) -> str:
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8").strip()
    if args.text:
        return args.text.strip()
    return (
        DEFAULT_HINDI_TEXT
        if args.language == "hi"
        else DEFAULT_ENGLISH_TEXT
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Run a local Kokoro TTS and Faster-Whisper STT smoke test."
    )
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--language", choices=["hi", "en"], default="hi")
    parser.add_argument("--output", default="voice_smoke.wav")
    parser.add_argument("--skip-stt", action="store_true")
    args = parser.parse_args()

    text = _read_text(args)
    if not text:
        print("No text provided.", file=sys.stderr)
        return 2

    settings = get_settings()
    print(f"Language: {args.language}")
    print(f"Text: {text}")

    tts = KokoroTextToSpeech(settings)
    started = time.perf_counter()
    try:
        audio = tts.synthesize(text, language=args.language)
    except VoiceError as exc:
        print(f"TTS failed: {exc.detail}", file=sys.stderr)
        return 1
    tts_seconds = time.perf_counter() - started
    Path(args.output).write_bytes(audio.data)
    audio_seconds = len(audio.data) / 2 / audio.sample_rate
    print(
        f"TTS: voice={audio.voice} sample_rate={audio.sample_rate} "
        f"audio_seconds={audio_seconds:.2f} synth_seconds={tts_seconds:.2f} "
        f"rtf={tts_seconds / max(audio_seconds, 0.01):.2f}"
    )
    print(f"Wrote: {Path(args.output).resolve()}")

    if args.skip_stt:
        return 0

    stt = FasterWhisperSTT(settings)
    started = time.perf_counter()
    try:
        transcript = stt.transcribe(
            audio.data,
            filename=args.output,
            language=args.language,
        )
    except VoiceError as exc:
        print(f"STT failed: {exc.detail}", file=sys.stderr)
        return 1
    stt_seconds = time.perf_counter() - started
    print(
        f"STT: language={transcript.language} "
        f"probability={transcript.language_probability} "
        f"seconds={stt_seconds:.2f}"
    )
    print(f"Transcript: {transcript.transcript}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
