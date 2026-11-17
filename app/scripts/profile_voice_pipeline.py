"""Measure local voice pipeline stages without paid APIs or synthetic data.

Runs Kokoro TTS and Faster-Whisper STT on the configured local models, times
each stage, and optionally writes the measurements as JSON. The catalogue used
for matching is the project CSV, so no demo medicine is invented here.
"""

import argparse
import csv
import io
import json
import os
import platform
import sys
import time
import wave
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.enums import OrderUnit
from app.database.models import Base, Medicine
from app.schemas.order import (
    OrderConfirmationRequest,
    OrderItemAdd,
    OrderItemUpdate,
    OrderSessionCreate,
)
from app.schemas.voice import VoiceSessionCreate, VoiceTurnRequest
from app.services.matching_service import MedicineMatchingService
from app.services.order_service import OrderService
from app.services.telephony.audio_bridge import UtteranceBuffer, frames
from app.services.voice import build_stt, build_tts
from app.services.voice.intent import DeterministicIntentParser
from app.services.voice_service import VoiceService

HINDI_TEXT = "नमस्ते, मुझे दो स्ट्रिप ब्रूफेन 400 और एक स्ट्रिप क्लोमिन चाहिए।"
HINDI_ADD_TURN = "मुझे दो स्ट्रिप ब्रूफेन 400 चाहिए"
CATALOGUE = Path("data/demo_medicines.csv")


def seconds(started: float) -> float:
    return round(time.perf_counter() - started, 4)


def load_catalogue(db: Session) -> int:
    if not CATALOGUE.is_file():
        raise SystemExit(f"Catalogue not found: {CATALOGUE}")
    count = 0
    with CATALOGUE.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            db.add(
                Medicine(
                    medicine_id=row["medicine_id"],
                    medicine_name=row["medicine_name"],
                    description=row["description"],
                    source_serial_number=row["source_serial_number"],
                    source_row=int(row["source_row"]),
                )
            )
            count += 1
    db.commit()
    return count


def make_db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["hi", "en"], default="hi")
    parser.add_argument("--skip-stt", action="store_true")
    parser.add_argument("--json", default="docs/voice_pipeline_metrics.json")
    args = parser.parse_args()

    settings = get_settings()
    db = make_db()
    catalogue_size = load_catalogue(db)
    metrics: dict[str, float | int | str] = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count() or 0,
        "python": platform.python_version(),
        "stt_model": settings.voice_stt_model,
        "stt_device": settings.voice_stt_device,
        "stt_compute_type": settings.voice_stt_compute_type,
        "stt_beam_size": settings.voice_stt_beam_size,
        "catalogue_size": catalogue_size,
        "language": args.language,
    }
    print(
        f"Host: {metrics['processor']} ({metrics['cpu_count']} CPUs), "
        f"STT={settings.voice_stt_model}/{settings.voice_stt_compute_type}, "
        f"catalogue={catalogue_size}"
    )

    tts = build_tts(settings)
    if tts is None:
        print("TTS is disabled.", file=sys.stderr)
        return 2

    started = time.perf_counter()
    audio = tts.synthesize(HINDI_TEXT, language=args.language)
    metrics["tts_cold_ms"] = round(seconds(started) * 1000, 1)
    started = time.perf_counter()
    tts.synthesize(HINDI_ADD_TURN, language=args.language)
    metrics["tts_warm_ms"] = round(seconds(started) * 1000, 1)
    audio_seconds = len(audio.data) / 2 / audio.sample_rate
    metrics["tts_audio_seconds"] = round(audio_seconds, 3)
    metrics["tts_rtf_warm"] = round(
        metrics["tts_warm_ms"] / 1000 / max(audio_seconds, 0.01), 3
    )

    started = time.perf_counter()
    with wave.open(io.BytesIO(audio.data), "rb") as reader:
        pcm = reader.readframes(reader.getnframes())
    metrics["wav_decode_ms"] = round(seconds(started) * 1000, 1)

    started = time.perf_counter()
    buffer = UtteranceBuffer(threshold=settings.telephony_vad_threshold)
    frame_bytes = 320
    for offset in range(0, len(pcm), frame_bytes):
        buffer.feed(pcm[offset : offset + frame_bytes])
    metrics["vad_endpointing_ms"] = round(seconds(started) * 1000, 1)

    started = time.perf_counter()
    telephony_frames = frames(audio.data)
    metrics["telephony_framing_ms"] = round(seconds(started) * 1000, 1)
    metrics["telephony_frame_count"] = len(telephony_frames)

    if not args.skip_stt:
        stt = build_stt(settings)
        stt.set_vocabulary(["BRUFEN", "CLOMIN TAB"])
        started = time.perf_counter()
        cold = stt.transcribe(audio.data, filename="profile.wav", language=args.language)
        metrics["stt_cold_ms"] = round(seconds(started) * 1000, 1)
        started = time.perf_counter()
        warm = stt.transcribe(audio.data, filename="profile.wav", language=args.language)
        metrics["stt_warm_ms"] = round(seconds(started) * 1000, 1)
        metrics["stt_transcript"] = warm.transcript
        metrics["stt_cold_transcript"] = cold.transcript
        print(
            f"STT cold={metrics['stt_cold_ms']}ms warm={metrics['stt_warm_ms']}ms "
            f"transcript={warm.transcript!r}"
        )

    parser_ = DeterministicIntentParser()
    parser_.parse(HINDI_ADD_TURN, language=args.language)
    started = time.perf_counter()
    for _ in range(50):
        parser_.parse(HINDI_ADD_TURN, language=args.language)
    metrics["intent_parse_ms"] = round(seconds(started) / 50 * 1000, 3)

    matcher = MedicineMatchingService(db)
    matcher.search("brufen 400")
    started = time.perf_counter()
    for _ in range(50):
        matcher.search("brufen 400")
    metrics["medicine_match_ms"] = round(seconds(started) / 50 * 1000, 3)

    orders = OrderService(db)
    started = time.perf_counter()
    session = orders.create_session(OrderSessionCreate(customer_name="Profile run"))
    metrics["db_create_session_ms"] = round(seconds(started) * 1000, 1)
    started = time.perf_counter()
    session = orders.add_item(
        session.session_id,
        OrderItemAdd(medicine_id="MED-00014", quantity=2, unit=OrderUnit.STRIP),
    )
    metrics["db_add_item_ms"] = round(seconds(started) * 1000, 1)
    started = time.perf_counter()
    session = orders.update_item(
        session.session_id,
        session.items[0].id,
        OrderItemUpdate(quantity=3, unit=OrderUnit.STRIP),
    )
    metrics["db_update_item_ms"] = round(seconds(started) * 1000, 1)
    started = time.perf_counter()
    summary = orders.get_summary(session.session_id)
    metrics["db_summary_ms"] = round(seconds(started) * 1000, 1)
    started = time.perf_counter()
    orders.confirm_order(
        session.session_id,
        OrderConfirmationRequest(
            confirmation_token=summary.confirmation_token or "",
            confirmation_source="PROFILE",
            explicit_confirmation=True,
        ),
    )
    metrics["db_confirm_ms"] = round(seconds(started) * 1000, 1)

    voice = VoiceService(db, tts=tts)
    text_session = voice.create_session(VoiceSessionCreate(language=args.language))
    started = time.perf_counter()
    text_turn = voice.process_text(
        text_session.voice_session_id,
        VoiceTurnRequest(text=HINDI_ADD_TURN, speak=True),
    )
    metrics["text_turn_warm_ms"] = round(seconds(started) * 1000, 1)
    metrics["text_turn_tts_base64_chars"] = (
        len(text_turn.tts.audio_base64) if text_turn.tts else 0
    )
    metrics["text_turn_items"] = len(text_turn.order_summary.items) if text_turn.order_summary else 0
    started = time.perf_counter()
    payload = text_turn.model_dump_json()
    metrics["response_serialization_ms"] = round(seconds(started) * 1000, 1)
    metrics["response_json_chars"] = len(payload)

    if not args.skip_stt:
        audio_session = voice.create_session(VoiceSessionCreate(language=args.language))
        started = time.perf_counter()
        audio_turn = voice.process_audio(
            audio_session.voice_session_id,
            audio.data,
            filename="profile.wav",
            content_type="audio/wav",
            language=args.language,
            speak=True,
        )
        metrics["audio_turn_warm_ms"] = round(seconds(started) * 1000, 1)
        metrics["audio_turn_transcript"] = audio_turn.transcript or ""
        metrics["audio_turn_items"] = (
            len(audio_turn.order_summary.items) if audio_turn.order_summary else 0
        )

    if "stt_warm_ms" in metrics:
        metrics["estimated_warm_voice_turn_ms"] = round(
            float(metrics["stt_warm_ms"]) + float(metrics["text_turn_warm_ms"]), 1
        )

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
