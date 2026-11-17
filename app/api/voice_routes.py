from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.voice import (
    VoiceSessionCreate,
    VoiceSessionRead,
    VoiceStatusRead,
    VoiceTranscriptRead,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from app.services.voice_service import VoiceService

router = APIRouter(tags=["voice"])


def get_voice_service(db: Session = Depends(get_db)) -> VoiceService:
    return VoiceService(db)


@router.post(
    "/voice/sessions",
    response_model=VoiceSessionRead,
    status_code=201,
)
def create_voice_session(
    payload: VoiceSessionCreate,
    service: VoiceService = Depends(get_voice_service),
) -> VoiceSessionRead:
    return service.create_session(payload)


@router.get(
    "/voice/sessions/{voice_session_id}",
    response_model=VoiceSessionRead,
)
def get_voice_session(
    voice_session_id: str,
    service: VoiceService = Depends(get_voice_service),
) -> VoiceSessionRead:
    return service.get_session(voice_session_id)


@router.post(
    "/voice/sessions/{voice_session_id}/turns",
    response_model=VoiceTurnResponse,
)
def create_voice_turn(
    voice_session_id: str,
    payload: VoiceTurnRequest,
    service: VoiceService = Depends(get_voice_service),
) -> VoiceTurnResponse:
    return service.process_text(voice_session_id, payload)


@router.post(
    "/voice/sessions/{voice_session_id}/audio-turns",
    response_model=VoiceTurnResponse,
)
def create_voice_audio_turn(
    voice_session_id: str,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    speak: bool = Form(default=True),
    service: VoiceService = Depends(get_voice_service),
) -> VoiceTurnResponse:
    return service.process_audio(
        voice_session_id,
        file.file.read(),
        filename=file.filename,
        content_type=file.content_type,
        language=language,
        speak=speak,
    )


@router.post(
    "/voice/transcribe",
    response_model=VoiceTranscriptRead,
)
def transcribe_voice_audio(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    service: VoiceService = Depends(get_voice_service),
) -> VoiceTranscriptRead:
    return service.transcribe_audio(
        file.file.read(),
        filename=file.filename,
        content_type=file.content_type,
        language=language,
    )


@router.post(
    "/voice/sessions/{voice_session_id}/end",
    response_model=VoiceSessionRead,
)
def end_voice_session(
    voice_session_id: str,
    service: VoiceService = Depends(get_voice_service),
) -> VoiceSessionRead:
    return service.end_session(voice_session_id)


@router.get("/voice/status", response_model=VoiceStatusRead)
def get_voice_status(
    service: VoiceService = Depends(get_voice_service),
) -> VoiceStatusRead:
    return service.status()


@router.get("/voice-demo", include_in_schema=False)
def voice_demo() -> FileResponse:
    path = Path(__file__).resolve().parents[1] / "static" / "voice_demo.html"
    return FileResponse(path)
