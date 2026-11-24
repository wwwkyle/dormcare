"""Tiny standard-library client for DeepSeek's ASR endpoint.

The browser records a 16-bit PCM WAV, the server validates it (format, size and
the 60-second product limit) and forwards it as multipart/form-data. Only the
transcript comes back; the API key never leaves the server and remote error
bodies are never echoed to the browser.
"""

import http.client
import json
import os
import secrets
import struct
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import asr_config

DEFAULT_ENDPOINT = "https://api.deepseek.com/v1/tools/asr"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 60
SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}


class ASRError(Exception):
    """A safe-to-display failure code; never contains the key or remote body."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _chunk(raw, offset):
    if offset + 8 > len(raw):
        raise ASRError("bad_audio")
    name = raw[offset:offset + 4]
    size = struct.unpack_from("<I", raw, offset + 4)[0]
    start, end = offset + 8, offset + 8 + size
    if end > len(raw):
        raise ASRError("bad_audio")
    return name, start, end


def parse_wav(raw):
    """Validate a 16-bit PCM WAV and return its duration in seconds.

    DeepSeek accepts PCM WAV or Ogg Opus. The browser encoder deliberately
    produces PCM WAV, so anything else is refused here rather than relying on
    the remote service to explain a 400.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ASRError("bad_audio")
    raw = bytes(raw)
    if len(raw) < 44 or len(raw) > MAX_AUDIO_BYTES:
        raise ASRError("too_large" if len(raw) > MAX_AUDIO_BYTES else "bad_audio")
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ASRError("bad_audio")

    fmt = data = None
    offset = 12
    while offset + 8 <= len(raw):
        name, start, end = _chunk(raw, offset)
        if name == b"fmt " and fmt is None:
            fmt = (start, end)
        elif name == b"data" and data is None:
            data = (start, end)
        size = end - start
        offset = end + (size & 1)
    if not fmt or not data or fmt[1] - fmt[0] < 16:
        raise ASRError("bad_audio")

    try:
        encoding, channels, rate, _byte_rate, block_align, bits = struct.unpack_from(
            "<HHIIHH", raw, fmt[0])
    except struct.error:
        raise ASRError("bad_audio") from None
    if (encoding != 1 or bits != 16 or channels not in (1, 2)
            or rate not in SAMPLE_RATES or block_align != channels * 2):
        raise ASRError("bad_audio")

    data_bytes = data[1] - data[0]
    if data_bytes <= 0:
        raise ASRError("bad_audio")
    duration = data_bytes / float(rate * channels * 2)
    if duration > MAX_AUDIO_SECONDS + 0.001:
        raise ASRError("too_long")
    return duration


def _endpoint():
    address = (os.environ.get("DEEPSEEK_ASR_URL") or DEFAULT_ENDPOINT).strip().rstrip("/")
    try:
        parsed = urlsplit(address)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
            raise ValueError("invalid endpoint")
    except ValueError:
        raise ASRError("invalid_configuration") from None
    return address


def transcribe(raw):
    """Send one WAV clip and return {"text": ..., "duration_ms": ...}."""
    key = asr_config.key()
    if not key:
        raise ASRError("not_configured")
    duration = parse_wav(raw)

    boundary = "dormcare-asr-" + secrets.token_hex(16)
    body = (
        "--" + boundary + '\r\nContent-Disposition: form-data; name="file"; '
        'filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    ).encode("ascii") + bytes(raw) + ("\r\n--" + boundary + "--\r\n").encode("ascii")
    request = urllib.request.Request(
        _endpoint(), data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary,
                 "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=60) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("response too large")
        result = json.loads(payload)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("invalid response")
    except urllib.error.HTTPError as exc:
        code = {400: "bad_audio", 401: "unauthorized", 403: "unauthorized",
                413: "too_large", 429: "rate_limited",
                503: "service_busy", 504: "service_busy"}.get(exc.code, "failed")
        raise ASRError(code) from None
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        raise ASRError("failed") from None

    text = result["text"].strip()
    reported = result.get("duration_ms")
    if not isinstance(reported, int) or isinstance(reported, bool) or reported < 0:
        reported = int(round(duration * 1000))
    return {"text": text, "duration_ms": reported}
