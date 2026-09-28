import time
from collections import deque

from fastapi import Request

SESSION_KEY = "admin"


class LoginLimiter:
    """In-memory failed-login limiter keyed by client address."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, clock=time.monotonic):
        self._max, self._window, self._clock = max_failures, window_seconds, clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        cutoff = self._clock() - self._window
        recent = deque(t for t in self._failures.get(key, ()) if t > cutoff)
        self._failures[key] = recent
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self._max

    def record_failure(self, key: str) -> None:
        self._recent(key).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def is_admin(request: Request) -> bool:
    return request.session.get(SESSION_KEY) is True


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
