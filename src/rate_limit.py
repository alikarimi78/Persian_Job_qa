"""Rate limiting for the two endpoints that cost something: `/auth/login` and `/search`.

A sliding window kept in this process — one deque of timestamps per key, pruned as it is
read. No Redis and no extra dependency, which is the right size for a deployment that is
one uvicorn container: the state is per process, so N workers mean N budgets, and the
limits below are chosen loose enough that this is a detail rather than a hole.

The two endpoints are limited on different keys, because they are protected from
different things:

- **login** — the key is *source + username* and only a **wrong** password spends from
  it, so this caps password guessing against one account without ever locking out
  somebody who simply signs in a lot. Rate-limiting successful logins would do nothing
  for security and would eventually refuse a legitimate user.
- **search** — the key is the account, and every call spends, because here the point is
  the cost: each search encodes the question and calls the LLM.

`RATE_LIMIT_ENABLED` is read on every call rather than captured at import, so a test (or
a deployment behind its own limiter) can switch the whole thing off.
"""

import math
import threading
import time
from collections import deque
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from .auth import get_current_user
from .config import settings
from .models import User

# Persian on purpose, unlike the rest of src/'s messages: the client shows a `detail` it
# has no case for verbatim (`src/utils/errors.js`), and this one is read by the person
# standing at the login form.
TOO_MANY_ATTEMPTS = "تعداد تلاش‌ها بیش از حد مجاز است؛ لطفاً {seconds} ثانیه دیگر مجدداً تلاش نمایید."
TOO_MANY_SEARCHES = "تعداد جستجوها بیش از حد مجاز است؛ لطفاً {seconds} ثانیه دیگر مجدداً تلاش نمایید."


class RateLimiter:
    """`limit` events per `window` seconds, per key.

    `check()` asks whether there is budget left and never spends any; `hit()` spends
    exactly one. They are separate so the login handler can refuse before it does the
    (deliberately slow) bcrypt comparison, and then charge only if the password was
    wrong. `spend()` is the two together, for callers that charge every request.

    `clock` is injectable so tests can move time instead of sleeping through a window.
    """

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
        """A limit of zero or less means "no ceiling" — the endpoint is unmetered."""
        return settings.RATE_LIMIT_ENABLED and self.limit > 0

    def check(self, key: str) -> None:
        """Raises 429 when the key is out of budget. Costs nothing when it is not."""
        if not self.active:
            return
        with self._lock:
            now = self.clock()
            self._sweep(now)
            # `get`, not `setdefault`: asking about a key must not create one, or
            # shouting unknown usernames at /auth/login would grow the dict for free.
            hits = self._hits.get(key)
            if hits is None:
                return
            self._trim(hits, now)
            if len(hits) < self.limit:
                return
            # The hit that has to fall out of the window before a slot opens up
            oldest = hits[len(hits) - self.limit]
            retry = max(1, math.ceil(oldest + self.window - now))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            self.message.format(seconds=retry),
            # Standard, and the only machine-readable part of the refusal: how long the
            # caller has to wait. Everything else about the budget is deliberately not
            # disclosed.
            headers={"Retry-After": str(retry)},
        )

    def hit(self, key: str) -> None:
        """Spends one from the key's budget."""
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
        """Gives the whole budget back — what a successful login does, so that a person
        who mistyped their password four times is not one mistake away from a refusal
        for the rest of the window."""
        with self._lock:
            self._hits.pop(key, None)

    # ---- internals; both are called under the lock ----

    def _trim(self, hits: deque[float], now: float) -> None:
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        """Forgets keys nobody has touched for a whole window, once per window. Without
        it the dict grows by one entry per username ever guessed at."""
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
    """Who the request came from, as far as it can be known.

    Behind the deployment's nginx every connection is the proxy's, so without
    `TRUST_FORWARDED_FOR` the whole world shares one login key — which is why that
    switch exists, and why it has to be on there. It is off by default because the
    header is client-controlled: trust it only where a proxy that appends to it is the
    only route in.

    The **last** entry is the one taken, not the first. nginx's usual
    `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` *appends* the address
    it actually saw to whatever the caller sent, so the first entry is a claim the
    caller made up and the last is the one hop we have evidence for. Reading the first
    would let anyone mint a fresh budget per request by inventing a header. This assumes
    a single trusted proxy; behind two, the key becomes the inner proxy's address, which
    is coarse but still not forgeable.
    """
    if settings.TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def login_key(request: Request, username: str) -> str:
    return f"{client_ip(request)}|{username.strip().lower()}"


def search_rate_limit(user: User = Depends(get_current_user)) -> User:
    """`/search`'s dependency, in place of `get_current_user`: authenticate first, then
    charge the account. An anonymous caller gets the 401 it deserves rather than a 429,
    and the budget belongs to an account instead of to an IP a whole organization shares.
    """
    search_limiter.spend(f"user:{user.id}")
    return user
