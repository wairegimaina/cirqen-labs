"""Attempt throttling for the login and password-reset forms.

Counts failures per identity (a username, an email, a client IP) in a fixed
window and blocks that identity for a cool-off period once the limit is hit.
State lives in the dedicated file-based ``throttle`` cache so protection keeps
working when Redis is down.
"""
import time
from dataclasses import dataclass

from django.core.cache import caches


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or "unknown"


@dataclass(frozen=True)
class Throttle:
    scope: str
    limit: int  # failures allowed inside the window
    window: int  # seconds over which failures are counted
    block: int  # seconds an identity stays blocked once over the limit

    def _key(self, ident):
        return f"throttle:{self.scope}:{str(ident).strip().lower()}"

    @property
    def _cache(self):
        return caches["throttle"]

    def blocked_for(self, ident):
        """Seconds until ``ident`` may try again; 0 when it is not blocked."""
        state = self._cache.get(self._key(ident)) or {}
        return max(0, int(state.get("blocked_until", 0) - time.time()))

    def hit(self, ident):
        """Record one failure; return True if this failure triggered a block."""
        now = time.time()
        key = self._key(ident)
        state = self._cache.get(key) or {}
        if now - state.get("window_start", 0) > self.window:
            state = {"window_start": now, "count": 0}
        state["count"] = state.get("count", 0) + 1
        blocked = state["count"] >= self.limit
        if blocked:
            state["blocked_until"] = now + self.block
        self._cache.set(key, state, max(self.window, self.block))
        return blocked

    def reset(self, ident):
        self._cache.delete(self._key(ident))


def minutes(seconds):
    return max(1, -(-seconds // 60))


# Per account: five wrong passwords lock that account for 15 minutes.
LOGIN_PER_USER = Throttle("login-user", limit=5, window=15 * 60, block=15 * 60)
# Per client: a ceiling on guessing across many usernames. The desktop build
# serves every user from 127.0.0.1, so this is set well above normal use.
LOGIN_PER_IP = Throttle("login-ip", limit=30, window=15 * 60, block=15 * 60)
# Reset codes are six digits: few guesses, and few new codes, per hour.
RESET_VERIFY_PER_IP = Throttle("reset-verify-ip", limit=10, window=60 * 60, block=60 * 60)
RESET_REQUEST_PER_EMAIL = Throttle("reset-request-email", limit=3, window=60 * 60, block=60 * 60)
RESET_REQUEST_PER_IP = Throttle("reset-request-ip", limit=10, window=60 * 60, block=60 * 60)
