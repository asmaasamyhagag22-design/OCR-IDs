"""Passwords, session tokens and CSRF tokens, from the standard library only.

Passwords are hashed with scrypt. A session is a random token in an HttpOnly
cookie; the database holds only its sha256, so a copy of the sessions
collection cannot be replayed. Each session carries its own CSRF token, which
every state-changing form and request must echo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD_LENGTH = 10
ROLES = ("reviewer", "supervisor", "admin")
ROLE_LABELS = {"reviewer": "مراجِع", "supervisor": "مشرف", "admin": "مدير النظام"}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                   dklen=len(_unb64(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


#: Verified against when the username does not exist, so a wrong username and a wrong
#: password take the same time.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_or_dummy(password: str, stored: str | None) -> bool:
    if stored is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, stored)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


class LoginThrottle:
    """At most ``limit`` failed logins per username in ``window_s``; then refused until the window passes."""

    #: Names tracked at most; beyond it, names without a failure inside the window are dropped first.
    MAX_TRACKED = 10_000

    def __init__(self, limit: int = 5, window_s: int = 300) -> None:
        self.limit, self.window_s = limit, window_s
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < self.window_s]

    def blocked(self, username: str) -> bool:
        """Read-only: a name that never failed is not stored, so unknown names cost no memory."""
        key = username.strip().lower()
        with self._lock:
            recent = self._recent(key, time.monotonic())
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)
            return len(recent) >= self.limit

    def failed(self, username: str) -> None:
        key = username.strip().lower()
        with self._lock:
            now = time.monotonic()
            self._failures[key] = self._recent(key, now) + [now]
            if len(self._failures) > self.MAX_TRACKED:
                self._failures = {k: v for k, v in self._failures.items() if now - v[-1] < self.window_s}
                while len(self._failures) > self.MAX_TRACKED:  # all recent: drop the oldest last failure
                    del self._failures[min(self._failures, key=lambda k: self._failures[k][-1])]

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username.strip().lower(), None)
