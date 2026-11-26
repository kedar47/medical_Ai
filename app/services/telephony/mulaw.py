"""G.711 mu-law <-> 16-bit linear PCM (little-endian), 8 kHz telephone audio.

Plivo recommends mu-law for its audio streams; the rest of the call pipeline
works on s16le PCM (the Exotel format), so the Plivo adapter converts at the
edge. Lookup tables keep each 20 ms frame conversion to one numpy index.
"""
import numpy as np

_BIAS = 0x84


def _decode_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign != 0, -sample, sample).astype("<i2")


def _encode_table() -> np.ndarray:
    # Sun g711 st_14linear2ulaw, the same rounding as CPython's audioop.
    x = np.arange(-32768, 32768, dtype=np.int32) >> 2
    mask = np.where(x < 0, 0x7F, 0xFF)
    value = np.minimum(np.abs(x), 8159) + (_BIAS >> 2)
    segment = np.searchsorted(np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF]), value)
    ulaw = (segment << 4) | ((value >> (segment + 1)) & 0x0F)
    return (np.where(segment >= 8, 0x7F, ulaw) ^ mask).astype(np.uint8)


_DECODE = _decode_table()
_ENCODE = _encode_table()


def ulaw_to_pcm(data: bytes) -> bytes:
    return _DECODE[np.frombuffer(data, dtype=np.uint8)].tobytes()


def pcm_to_ulaw(pcm: bytes) -> bytes:
    if len(pcm) % 2:
        raise ValueError("PCM must be 16-bit")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32) + 32768
    return _ENCODE[samples].tobytes()
