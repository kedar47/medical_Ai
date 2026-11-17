"""Exotel Voicebot JSON protocol, default raw/slin s16le 8 kHz mono.

Source: https://docs.exotel.com/exotel-agentstream/voicebot-applet
Authentication is documented HTTP Basic on the WSS handshake, not a signature.
"""
import re


_ENCODINGS = {"base64", "audio/x-raw", "raw", "pcm", "slin", "raw/slin", "audio/pcm"}


def _parse_bit_rate(value):
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*(?:k(?:bps)?|bps)?\s*", str(value), re.IGNORECASE)
    return int(match.group(1)) if match else None


class ExotelAdapter:
    def __init__(self, account_sid: str = ""):
        self.account_sid = account_sid

    def start(self, packet: dict) -> tuple[str, str, str | None]:
        start = packet.get("start")
        if not isinstance(start, dict):
            raise ValueError("Missing start")
        call, stream = start.get("call_sid"), packet.get("stream_sid")
        if not all(isinstance(v, str) and re.fullmatch(r"[\w-]{1,128}", v) for v in (call, stream)):
            raise ValueError("Invalid identifiers")
        if start.get("stream_sid", stream) != stream:
            raise ValueError("Stream mismatch")
        if self.account_sid and start.get("account_sid") != self.account_sid:
            raise ValueError("Account mismatch")
        fmt = start.get("media_format", {})
        encoding = fmt.get("encoding")
        if not isinstance(encoding, str) or encoding.strip().lower() not in _ENCODINGS:
            raise ValueError("Unsupported audio encoding")
        sample_rate = fmt.get("sample_rate")
        if sample_rate is not None and str(sample_rate).strip() != "8000":
            raise ValueError("Only 8 kHz PCM supported")
        channels = fmt.get("channels")
        if channels is None:
            channels = 1
        if str(channels).strip() != "1":
            raise ValueError("Only mono audio supported")
        _parse_bit_rate(fmt.get("bit_rate"))
        phone = start.get("from")
        # Restricted/local caller IDs stay unavailable; never guess a country code.
        phone = phone if isinstance(phone, str) and re.fullmatch(r"\+[1-9]\d{7,14}", phone) else None
        return call, stream, phone

    def media(self, stream: str, payload: str, sequence: int, minimal: bool = False) -> dict:
        if minimal:
            return {"event": "media", "stream_sid": stream, "media": {"payload": payload}}
        return {"event": "media", "stream_sid": stream, "sequence_number": sequence,
                "media": {"chunk": sequence, "timestamp": str((sequence - 1) * 100), "payload": payload}}

    def clear(self, stream: str) -> dict:
        return {"event": "clear", "stream_sid": stream}

    def mark(self, stream: str, name: str) -> dict:
        return {"event": "mark", "stream_sid": stream, "mark": {"name": name}}
