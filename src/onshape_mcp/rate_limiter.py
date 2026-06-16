"""Rate limiter for Onshape API — global singleton with sliding window + exponential backoff.

CRITICAL: Onshape rate-limits at the ACCOUNT level, not per-key or per-session.
This module provides a process-global singleton so all API calls (both raw REST
from OnshapeClient and onpy's internal calls) share the same rate limit bucket.

Tuned conservatively from real-world testing:
- 10 calls per 60 seconds (free tier tolerance)
- Minimum 2 seconds between calls
- onpy does 2+ internal API calls per operation (FeatureScript + feature POST),
  so "10 calls" ≈ 3-4 user-level operations per minute.
"""

import time
import threading
from collections import deque
from typing import Optional


class RateLimiter:
    """Thread-safe sliding-window rate limiter with exponential backoff.

    GLOBAL SINGLETON — use get_rate_limiter() instead of creating new instances.
    """

    def __init__(
        self,
        max_calls: int = 10,
        window: float = 60.0,
        min_interval: float = 2.0,
        backoff_base: float = 5.0,
        backoff_max: float = 120.0,
    ):
        self.max_calls = max_calls
        self.window = window
        self.min_interval = min_interval
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

        self._timestamps: deque = deque()
        self._lock = threading.Lock()
        self._backoff_until: float = 0.0
        self._last_call: float = 0.0
        self._consecutive_429s: int = 0

    def acquire(self):
        """Block until it's safe to make an API call.

        Delays preemptively if we're approaching the rate limit,
        or if we're in backoff from a previous 429.

        Thread-safe: computes wait time under lock, releases lock during sleep.
        """
        while True:
            sleep_time = 0.0
            with self._lock:
                now = time.monotonic()

                # 1. Purge old timestamps outside the window
                cutoff = now - self.window
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()

                # 2. If in backoff, compute wait
                if now < self._backoff_until:
                    sleep_time = self._backoff_until - now

                # 3. If at capacity, compute wait
                elif len(self._timestamps) >= self.max_calls:
                    oldest = self._timestamps[0]
                    wait = oldest + self.window - now + 0.1
                    if wait > 0:
                        sleep_time = wait

                # 4. Enforce minimum interval
                else:
                    elapsed = now - self._last_call
                    if elapsed < self.min_interval:
                        sleep_time = self.min_interval - elapsed

                # 5. If no sleep needed, record and return
                if sleep_time <= 0:
                    self._timestamps.append(now)
                    self._last_call = now
                    return

            # Release lock before sleeping — other threads can make progress
            time.sleep(sleep_time)
            # Loop back to re-check conditions

    def report_429(self):
        """Call after receiving a 429. Triggers exponential backoff."""
        with self._lock:
            self._consecutive_429s += 1
            delay = min(
                self.backoff_base * (2 ** (self._consecutive_429s - 1)),
                self.backoff_max,
            )
            self._backoff_until = time.monotonic() + delay

    def report_success(self):
        """Reset consecutive 429 counter after a successful call."""
        with self._lock:
            self._consecutive_429s = 0

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        pass

    @property
    def available(self) -> int:
        """How many calls are available right now (approximate)."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return max(0, self.max_calls - len(self._timestamps))

    @property
    def is_in_backoff(self) -> bool:
        """True if we're currently in backoff from a 429."""
        with self._lock:
            return time.monotonic() < self._backoff_until


# ── Global singleton ──────────────────────────────────────────────

_global_limiter: Optional[RateLimiter] = None
_global_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter singleton.

    All Onshape API calls MUST go through this limiter since the rate
    limit is account-wide, not per-client or per-session.
    """
    global _global_limiter
    with _global_lock:
        if _global_limiter is None:
            _global_limiter = RateLimiter()
        return _global_limiter


def reset_rate_limiter():
    """Reset the global rate limiter (useful for testing after a long wait)."""
    global _global_limiter
    with _global_lock:
        _global_limiter = RateLimiter()
