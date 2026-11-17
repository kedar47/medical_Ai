from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AI-Powered Medicine Order Calling Agent API"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = Field(..., validation_alias="DATABASE_URL")
    sql_echo: bool = False
    order_session_ttl_minutes: int = Field(default=30, ge=1)
    max_order_item_quantity: int = Field(default=100, ge=1)
    fuzzy_match_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    fuzzy_confirmation_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    fuzzy_match_limit: int = Field(default=5, ge=1, le=20)
    medicine_aliases: dict[str, str] = Field(default_factory=dict)

    # Comma-separated browser origins allowed to call the API cross-origin,
    # e.g. "https://medicalai.softgoway.com". Empty disables CORS entirely.
    cors_allowed_origins: str = ""

    dashboard_enabled: bool = False
    dashboard_token: str = ""

    telephony_enabled: bool = False
    telephony_provider: str = Field(default="exotel", pattern="^(exotel|mock)$")
    telephony_public_base_url: str = ""
    telephony_webhook_secret: str = ""  # Reserved; no synthetic signature validation.
    telephony_stream_username: str = ""
    telephony_stream_token: str = ""
    telephony_monitor_token: str = ""
    telephony_demo_number: str = ""  # Configuration only; never evidence of activation.
    exotel_account_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_api_base_url: str = "https://api.exotel.com"
    exotel_caller_id: str = ""
    telephony_allowed_destinations: str = ""
    telephony_max_call_seconds: int = Field(default=600, ge=10, le=3600)
    telephony_silence_seconds: int = Field(default=45, ge=5, le=300)
    telephony_vad_threshold: int = Field(default=450, ge=100, le=10000)
    telephony_turn_queue_size: int = Field(default=4, ge=2, le=16)
    greeting_delay_ms: int = Field(default=1500, ge=0, le=10000)
    exotel_minimal_media: bool = True

    voice_enabled: bool = True
    voice_session_ttl_minutes: int = Field(default=30, ge=1)
    voice_max_audio_bytes: int = Field(default=10_000_000, ge=1)
    voice_max_transcript_chars: int = Field(default=1_000, ge=1)
    voice_max_turns: int = Field(default=100, ge=1)
    voice_max_tts_chars: int = Field(default=600, ge=1)
    voice_preload_models: bool = False
    voice_stt_model: str = "small"
    voice_stt_device: str = "cpu"
    voice_stt_compute_type: str = "int8"
    voice_stt_beam_size: int = Field(default=1, ge=1, le=5)
    voice_stt_language: str = "hi"
    voice_stt_fallback_language: str = "en"
    voice_stt_initial_prompt: str = ""
    voice_stt_vad_filter: bool = True
    voice_stt_vad_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    voice_stt_vad_min_silence_ms: int = Field(default=700, ge=100, le=3000)
    voice_stt_vad_speech_pad_ms: int = Field(default=400, ge=0, le=2000)
    # Faster-Whisper hotwords are opt-in: measured locally they can make short
    # turns 15-20x slower and copy the hint into the transcript.
    voice_stt_hotwords_enabled: bool = False
    voice_tts_enabled: bool = True
    voice_tts_voice_hi: str = "hf_alpha"
    voice_tts_voice_en: str = "af_heart"
    voice_tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    kokoro_model_path: str = "models/kokoro/kokoro-v1.0.int8.onnx"
    kokoro_voices_path: str = "models/kokoro/voices-v1.0.bin"
    voice_llm_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout_seconds: float = Field(default=8.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
