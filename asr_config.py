"""Runtime DeepSeek ASR credentials, editable from system settings.

The sign-in flow used to rely on the browser's Web Speech API, which varies by
browser and sends audio wherever the browser vendor decides. An administrator
can now enrol a DeepSeek API key on the system-settings page; the server then
transcribes a short WAV clip through DeepSeek's ASR endpoint.

As with the Dify settings, a page-saved key wins over the environment. The
environment is only a fallback for deployments that prefer secrets in `.env`.
The key is never returned to the browser; `status()` is the only public view.
"""

import os
import threading

SECTION = "asr"
MAX_KEY = 400

_lock = threading.RLock()
_store = None
_saved = {"api_key": ""}


def bind(store):
    """Attach the store once at boot; before that the module is env-only."""
    global _store, _saved
    with _lock:
        _store = store
        record = store.settings().get(SECTION) if store else {}
        record = record if isinstance(record, dict) else {}
        _saved = {"api_key": str(record.get("api_key") or "")[:MAX_KEY]}


def _env_key():
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def key():
    with _lock:
        return _saved["api_key"] or _env_key()


def enabled():
    return bool(key())


def status():
    """Admin-visible state of the ASR key, never the key itself."""
    with _lock:
        saved_key = _saved["api_key"]
    env_key = _env_key()
    return {
        "configured": bool(saved_key or env_key),
        "source": "saved" if saved_key else ("env" if env_key else ""),
        "has_env_key": bool(env_key),
        "max_seconds": 60,
    }


def _clean_key(raw):
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        return "", "asr_missing_key"
    if len(value) > MAX_KEY or any(ch.isspace() for ch in value):
        return "", "asr_bad_key"
    return value, None


def configure(api_key):
    """Validate and persist the key. Returns (status, error_code)."""
    clean_key, error = _clean_key(api_key)
    if error:
        return None, error
    with _lock:
        _saved["api_key"] = clean_key
        if _store:
            _store.update_settings(SECTION, dict(_saved))
    return status(), None


def reset():
    """Drop only the page-entered key; an environment key still applies."""
    global _saved
    with _lock:
        _saved = {"api_key": ""}
        if _store:
            _store.update_settings(SECTION, dict(_saved))
    return status()
