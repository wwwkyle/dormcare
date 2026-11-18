"""Runtime Dify credentials, editable by an administrator instead of in .env.

The deployment used to require `DIFY_API_KEY` and `DIFY_API_BASE_URL` in a
local .env file. That makes every redeployment depend on somebody with shell
access. The system-settings page now owns those two values: what an operator
saves here wins, and the environment is kept only as a fallback so existing
installations keep working and unit tests can still patch os.environ.

The values live in the same JSON store as the accounts, so a page-entered
configuration survives a restart and is isolated per DORMCARE_DATA file.
Nothing in `status()` ever includes the key itself — it is the only view a
browser is allowed to see.
"""

import os
import threading
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.dify.ai/v1"
SECTION = "dify"
MAX_KEY = 400
MAX_BASE = 300

_lock = threading.RLock()
_store = None
# Empty until an operator saves something. A blank value here means "fall back
# to the environment", which is also how a page reset restores the old setup.
_saved = {"api_key": "", "base_url": ""}


def bind(store):
    """Attach the store once at boot; before that the module is env-only."""
    global _store, _saved
    with _lock:
        _store = store
        record = store.settings().get(SECTION) if store else {}
        record = record if isinstance(record, dict) else {}
        _saved = {
            "api_key": str(record.get("api_key") or "")[:MAX_KEY],
            "base_url": str(record.get("base_url") or "")[:MAX_BASE],
        }


def _env_key():
    return os.environ.get("DIFY_API_KEY", "").strip()


def _env_base():
    return os.environ.get("DIFY_API_BASE_URL", "").strip().rstrip("/")


def key():
    """The key the outbound client must use; page value beats environment."""
    with _lock:
        return _saved["api_key"] or _env_key()


def base_url():
    """API root the outbound client must use, without a trailing slash."""
    with _lock:
        saved = _saved["base_url"]
    return saved or _env_base() or DEFAULT_BASE_URL


def enabled():
    return bool(key())


def status():
    """Admin-visible state of the two fields, never the secret itself."""
    with _lock:
        saved_key = _saved["api_key"]
        saved_base = _saved["base_url"]
    env_key = _env_key()
    env_base = _env_base()
    return {
        "configured": bool(saved_key or env_key),
        "source": "saved" if saved_key else ("env" if env_key else ""),
        "base_url": saved_base or env_base or DEFAULT_BASE_URL,
        "base_source": "saved" if saved_base else ("env" if env_base else "default"),
        "has_env_key": bool(env_key),
    }


def _clean_key(raw):
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        return "", "dify_missing_key"
    if len(value) > MAX_KEY or any(ch.isspace() for ch in value):
        return "", "dify_bad_key"
    return value, None


def _clean_base(raw):
    """Accept the same endpoints dify_client.chat accepts — no surprises later."""
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        return "", None            # fall back to the environment or the default
    if len(value) > MAX_BASE:
        return "", "dify_bad_base"
    try:
        address = urlsplit(value)
        local = address.hostname in ("localhost", "127.0.0.1", "::1")
        if (not address.hostname or address.username or address.password
                or address.query or address.fragment
                or (address.scheme != "https" and not (local and address.scheme == "http"))):
            return "", "dify_bad_base"
    except ValueError:
        return "", "dify_bad_base"
    return value.rstrip("/"), None


def configure(api_key, base_url_value):
    """Validate and persist both fields. Returns (status, error_code)."""
    clean_key, error = _clean_key(api_key)
    if error:
        return None, error
    clean_base, error = _clean_base(base_url_value)
    if error:
        return None, error
    with _lock:
        _saved["api_key"] = clean_key
        _saved["base_url"] = clean_base
        if _store:
            _store.update_settings(SECTION, dict(_saved))
    return status(), None


def reset():
    """Drop only the page-entered values; an environment key still applies."""
    global _saved
    with _lock:
        _saved = {"api_key": "", "base_url": ""}
        if _store:
            _store.update_settings(SECTION, dict(_saved))
    return status()
