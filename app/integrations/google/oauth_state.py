from __future__ import annotations

import threading
import time


class OAuthStateStore:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float]] = {}

    def put(self, state: str, code_verifier: str) -> None:
        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            self._purge_expired_locked()
            self._entries[state] = (code_verifier, expires_at)

    def pop(self, state: str | None) -> str | None:
        if not state:
            return None

        with self._lock:
            self._purge_expired_locked()
            entry = self._entries.pop(state, None)

        if not entry:
            return None

        code_verifier, _ = entry
        return code_verifier

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [state for state, (_, expires_at) in self._entries.items() if expires_at <= now]
        for state in expired:
            self._entries.pop(state, None)


oauth_state_store = OAuthStateStore()
