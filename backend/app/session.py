"""Browser sessions for the interface.

The interface can't put X-API-Key on <video> and <img> requests, so the user
types the key once and gets a signed, HttpOnly cookie instead. The jury API
(/api/v1/*) still takes only the header.

Token: base64url(JSON {"exp": unix seconds}) + "." + base64url(HMAC-SHA256).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

COOKIE_NAME = "kc_session"
# Login attempts allowed per client address within the window.
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signing_key(session_secret: str, api_keys: tuple[str, ...]) -> bytes:
    if session_secret:
        return session_secret.encode()
    # No explicit secret: derive one from the keys, so rotating keys ends sessions.
    material = "\n".join(sorted(api_keys)).encode()
    return hashlib.sha256(b"kassa-control-session\n" + material).digest()


def issue(key: bytes, hours: float, now: float | None = None) -> tuple[str, int]:
    exp = int((time.time() if now is None else now) + hours * 3600)
    payload = _b64(json.dumps({"exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}", exp


def verify(key: bytes, token: str | None, now: float | None = None) -> int | None:
    """Return the expiry of a valid, unexpired token, else None."""
    if not token or token.count(".") != 1:
        return None
    payload, sig = token.split(".")
    expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        exp = int(json.loads(_unb64(payload))["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    return exp if exp > (time.time() if now is None else now) else None


class LoginThrottle:
    """In-memory, per-process limit on login attempts per client address."""

    def __init__(self, attempts: int = LOGIN_ATTEMPTS, window: float = LOGIN_WINDOW_SECONDS):
        self.attempts = attempts
        self.window = window
        self._seen: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        seen = self._seen[client]
        while seen and now - seen[0] > self.window:
            seen.popleft()
        if len(seen) >= self.attempts:
            return False
        seen.append(now)
        return True
