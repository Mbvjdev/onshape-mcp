"""Tests for the global RateLimiter."""

import time
import pytest

from onshape_mcp.rate_limiter import (
    RateLimiter,
    get_rate_limiter,
    reset_rate_limiter,
)


def test_preemptive_delay():
    """When max_calls is reached, acquire() blocks until window slides."""
    rl = RateLimiter(max_calls=3, window=0.5, min_interval=0.0)
    start = time.monotonic()
    for _ in range(3):
        rl.acquire()
    # 4th call must wait until first timestamp ages out (~0.5s)
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4, f"expected >=0.4s preemptive wait, got {elapsed:.3f}"


def test_sliding_window():
    """Timestamps outside the window are purged so we can call again immediately."""
    rl = RateLimiter(max_calls=2, window=0.2, min_interval=0.0)
    rl.acquire()
    rl.acquire()
    assert rl.available == 0
    time.sleep(0.25)
    assert rl.available == 2  # window expired


def test_backoff():
    """report_429 puts the limiter into backoff; acquire() then blocks."""
    rl = RateLimiter(
        max_calls=10, window=60.0, min_interval=0.0,
        backoff_base=0.3, backoff_max=1.0,
    )
    rl.report_429()
    assert rl.is_in_backoff is True
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25, f"expected backoff >=0.25s, got {elapsed:.3f}"


def test_min_interval():
    """Consecutive acquires must be >= min_interval apart."""
    rl = RateLimiter(max_calls=10, window=60.0, min_interval=0.2)
    rl.acquire()
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.18, f"expected >=0.2s spacing, got {elapsed:.3f}"


def test_singleton():
    """get_rate_limiter returns the same instance across calls."""
    reset_rate_limiter()
    a = get_rate_limiter()
    b = get_rate_limiter()
    assert a is b
    reset_rate_limiter()
    c = get_rate_limiter()
    assert c is not a  # reset gave us a new singleton


def test_report_success_clears_consecutive_429s():
    rl = RateLimiter(backoff_base=0.01, backoff_max=0.05)
    rl.report_429()
    rl.report_429()
    assert rl._consecutive_429s == 2
    rl.report_success()
    assert rl._consecutive_429s == 0
