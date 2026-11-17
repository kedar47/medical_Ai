"""Exotel REST calls used only for real provider dialling.

The bidirectional audio protocol stays in exotel_adapter.py; this client places
the telephone call and lets Exotel open the authenticated WebSocket.
"""
import logging
import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from app.core.config import Settings
from app.core.exceptions import VoiceError

logger = logging.getLogger(__name__)

_CALL_SID_PATTERN = re.compile(r"^[\w-]{1,128}$")


def _public_location(settings: Settings) -> tuple[str, str]:
    parsed = urlsplit(settings.telephony_public_base_url.strip())
    if parsed.scheme not in {"https", "wss"} or not parsed.netloc:
        raise VoiceError(503, "Public HTTPS/WSS base URL is not configured")
    if parsed.query or parsed.fragment:
        raise VoiceError(503, "Public base URL must not contain a query or fragment")
    return parsed.scheme, parsed.netloc


def _path(settings: Settings, suffix: str) -> str:
    parsed = urlsplit(settings.telephony_public_base_url.strip())
    return parsed.path.rstrip("/") + suffix


def media_stream_url(settings: Settings, call_id: str) -> str:
    if not settings.telephony_stream_username or not settings.telephony_stream_token:
        raise VoiceError(503, "WebSocket stream credentials are not configured")
    scheme, netloc = _public_location(settings)
    user = quote(settings.telephony_stream_username, safe="")
    token = quote(settings.telephony_stream_token, safe="")
    query = urlencode({"sample-rate": 8000, "bot_call_id": call_id})
    return urlunsplit(("wss", f"{user}:{token}@{netloc}", _path(settings, "/telephony/media"), query, ""))


def status_callback_url(settings: Settings) -> str:
    if not settings.telephony_webhook_secret:
        raise VoiceError(503, "Telephony webhook secret is not configured")
    _, netloc = _public_location(settings)
    query = urlencode({"token": settings.telephony_webhook_secret})
    return urlunsplit(("https", netloc, _path(settings, "/telephony/status-callback"), query, ""))


def outbound_config_issues(settings: Settings) -> list[str]:
    issues = []
    if not settings.telephony_enabled:
        issues.append("TELEPHONY_ENABLED")
    if settings.telephony_provider != "exotel":
        issues.append("TELEPHONY_PROVIDER=exotel")
    for name, value in (
        ("EXOTEL_ACCOUNT_SID", settings.exotel_account_sid),
        ("EXOTEL_API_KEY", settings.exotel_api_key),
        ("EXOTEL_API_TOKEN", settings.exotel_api_token),
        ("EXOTEL_CALLER_ID", settings.exotel_caller_id),
        ("TELEPHONY_PUBLIC_BASE_URL", settings.telephony_public_base_url),
        ("TELEPHONY_STREAM_USERNAME", settings.telephony_stream_username),
        ("TELEPHONY_STREAM_TOKEN", settings.telephony_stream_token),
        ("TELEPHONY_WEBHOOK_SECRET", settings.telephony_webhook_secret),
    ):
        if not value:
            issues.append(name)
    base = settings.exotel_api_base_url.strip()
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        issues.append("EXOTEL_API_BASE_URL")
    return issues


class ExotelClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self._client = client or httpx.Client(timeout=15.0)

    def connect(self, *, customer_phone: str, call_id: str) -> dict:
        issues = outbound_config_issues(self.settings)
        if issues:
            raise VoiceError(503, "Live telephony is not configured", {"missing": issues})
        s = self.settings
        url = (
            f"{s.exotel_api_base_url.rstrip('/')}/v1/Accounts/"
            f"{quote(s.exotel_account_sid, safe='')}/Calls/connect"
        )
        data = [
            ("From", customer_phone),
            ("CallerId", s.exotel_caller_id),
            ("StreamUrl", media_stream_url(s, call_id)),
            ("StreamType", "bidirectional"),
            ("TimeLimit", str(s.telephony_max_call_seconds)),
            ("CustomField", call_id),
            ("StatusCallback", status_callback_url(s)),
            ("StatusCallbackEvents[]", "answered"),
            ("StatusCallbackEvents[]", "terminal"),
            ("StatusCallbackContentType", "application/json"),
        ]
        try:
            response = self._client.post(
                url,
                auth=(s.exotel_api_key, s.exotel_api_token),
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            logger.error("Exotel dial request failed: %s", exc.__class__.__name__)
            raise VoiceError(502, "Telephony provider could not be reached") from exc
        if response.status_code >= 400:
            logger.error("Exotel dial rejected with HTTP %s", response.status_code)
            raise VoiceError(502, f"Telephony provider rejected the call (HTTP {response.status_code})")
        try:
            payload = response.json()
            sid = payload["Call"]["Sid"]
        except (ValueError, KeyError, TypeError) as exc:
            raise VoiceError(502, "Telephony provider returned an invalid call response") from exc
        if not isinstance(sid, str) or not _CALL_SID_PATTERN.fullmatch(sid):
            raise VoiceError(502, "Telephony provider returned an invalid call reference")
        return {"provider_call_reference": sid, "provider_status": str(payload["Call"].get("Status", ""))}
