"""Real local models, simulated 8 kHz telephone audio; never a live phone test."""
import json
import time

from app.core.config import get_settings
from app.services.telephony.audio_bridge import frames, decode, wav
from app.services.telephony.speech import synthesize_full
from app.services.voice import build_stt, build_tts


def main():
    settings = get_settings()
    tts, stt = build_tts(settings), build_stt(settings)
    for language, text in [("en", "Hello, I need two strips of BRUFEN."),
                            ("hi", "नमस्ते, मुझे दो स्ट्रिप ब्रूफेन चाहिए।"),
                            ("hi", "ब्रूफेन की क्वांटिटी तीन स्ट्रिप कर दो।")]:
        started = time.perf_counter()
        synthesized = synthesize_full(tts, text, language)
        tts_seconds = time.perf_counter() - started
        started = time.perf_counter()
        pcm = b"".join(decode(packet) for packet in frames(synthesized.data))
        bridge_seconds = time.perf_counter() - started
        started = time.perf_counter()
        transcript = stt.transcribe(wav(pcm), filename="simulated-telephone.wav", language=language)
        stt_seconds = time.perf_counter() - started
        print(json.dumps({"simulated": True, "language": language, "text": text,
                          "recognized": transcript.transcript, "audio_seconds": len(pcm) / 16000,
                          "tts_seconds": round(tts_seconds, 3), "bridge_seconds": round(bridge_seconds, 3),
                          "stt_seconds": round(stt_seconds, 3)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
