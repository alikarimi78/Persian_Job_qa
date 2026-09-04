import math
import threading
import time
from collections import deque
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from .config import settings
from .models import User
from .security import get_current_user

TOO_MANY_ATTEMPTS = "تعداد تلاش‌ها بیش از حد مجاز است؛ لطفاً {seconds} ثانیه دیگر مجدداً تلاش نمایید."
TOO_MANY_SEARCHES = "تعداد جستجوها بیش از حد مجاز است؛ لطفاً {seconds} ثانیه دیگر مجدداً تلاش نمایید."


class RateLimiter:
    def __init__(self, limit: int, window: float, message: str,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = float(window)
        self.message = message
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    @property
    def active(self) -> bool:
        return settings.RATE_LIMIT_ENABLED and self.limit > 0

    def check(self, key: str) -> None:
        if not self.active:
            return
        with self._lock:
            now = self.clock()
            self._sweep(now)
            hits = self._hits.get(key)
            if hits is None:
                return
            self._trim(hits, now)
            if len(hits) < self.limit:
                return
            oldest = hits[len(hits) - self.limit]
            retry = max(1, math.ceil(oldest + self.window - now))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            self.message.format(seconds=retry),
            headers={"Retry-After": str(retry)},
        )

    def hit(self, key: str) -> None:
        if not self.active:
            return
        with self._lock:
            now = self.clock()
            hits = self._hits.setdefault(key, deque())
            self._trim(hits, now)
            hits.append(now)

    def spend(self, key: str) -> None:
        self.check(key)
        self.hit(key)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


    def _trim(self, hits: deque[float], now: float) -> None:
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        cutoff = now - self.window
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


login_limiter = RateLimiter(settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_WINDOW,
                            TOO_MANY_ATTEMPTS)
search_limiter = RateLimiter(settings.SEARCH_RATE_LIMIT, settings.SEARCH_RATE_WINDOW,
                             TOO_MANY_SEARCHES)


def client_ip(request: Request) -> str:
    if settings.TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def login_key(request: Request, username: str) -> str:
    return f"{client_ip(request)}|{username.strip().lower()}"


def search_rate_limit(user: User = Depends(get_current_user)) -> User:
    search_limiter.spend(f"user:{user.id}")
    return user
