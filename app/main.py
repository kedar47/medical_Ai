import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.matching_routes import router as matching_router
from app.api.medicine_routes import router as medicine_router
from app.api.order_routes import router as order_router
from app.api.voice_routes import router as voice_router
from app.api.telephony_routes import router as telephony_router
from app.core.config import get_settings
from app.core.exceptions import OrderError, VoiceError
from app.core.logging import configure_logging
from app.database.connection import get_db, init_db
from app.schemas.medicine import HealthResponse

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


def _preload_voice_models() -> None:
    try:
        from app.services.telephony.audio_bridge import cache_greeting_pcm
        from app.services.voice import build_stt, build_tts
        from app.services.voice_service import initial_greeting_text

        build_stt(settings).preload()
        tts = build_tts(settings)
        if tts is not None:
            tts.preload()
            for language in ("hi", "en"):
                text = initial_greeting_text(language)
                voice = (
                    settings.voice_tts_voice_hi
                    if language == "hi"
                    else settings.voice_tts_voice_en
                )
                audio = tts.synthesize(text, language=language)
                size = cache_greeting_pcm(
                    text, voice, audio.data, settings.voice_tts_speed
                )
                logger.info("telephony.greeting.cached language=%s bytes=%s source_sample_rate=%s pcm_sample_rate=8000",
                            language, size, audio.sample_rate)
        logger.info("Voice models preloaded")
    except Exception as exc:  # Startup must not fail when a model is absent.
        logger.warning("Voice model preload skipped: %s", exc.__class__.__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        logger.info("Database tables initialized")
    except SQLAlchemyError as exc:
        logger.error(
            "Database initialization failed: %s", exc.__class__.__name__
        )
    if settings.voice_enabled and settings.voice_preload_models:
        await asyncio.to_thread(_preload_voice_models)
    yield


app = FastAPI(
    title=settings.app_name,
    version="4.0.0",
    description=(
        "Phase 3 backend for the AI-Powered Medicine Order Calling Agent. "
        "Adds a browser-based Hindi/English/Hinglish voice layer with local "
        "speech recognition and synthesis on top of deterministic medicine "
        "matching, order drafts, explicit confirmation, and persisted orders. "
        "Phase 4 adds authenticated Exotel Voicebot and mock telephony transport."
    ),
    lifespan=lifespan,
)

app.include_router(medicine_router)
app.include_router(matching_router)
app.include_router(order_router)
app.include_router(voice_router)
app.include_router(telephony_router)
from app.api.dashboard_routes import router as dashboard_router
app.include_router(dashboard_router)


@app.exception_handler(OrderError)
async def order_error_handler(request: Request, exc: OrderError):
    content = {"detail": exc.detail}
    content.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(VoiceError)
async def voice_error_handler(request: Request, exc: VoiceError):
    content = {"detail": exc.detail}
    content.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    logger.error(
        "Database error while handling %s %s: %s",
        request.method,
        request.url.path,
        exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Database service unavailable"},
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.error("Database health check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unavailable"},
        )

    return HealthResponse(status="ok", database="connected")
