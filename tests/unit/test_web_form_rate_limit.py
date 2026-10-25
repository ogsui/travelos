"""Deterministic rate limiting for public web-form intake."""

from travelos.channels.web_form import InMemoryWindowLimiter


def test_window_limiter_blocks_at_limit_and_recovers_after_expiry() -> None:
    now = [100.0]
    limiter = InMemoryWindowLimiter(limit=2, window_seconds=10, clock=lambda: now[0])

    assert limiter.allow("opaque-ip-digest") is True
    assert limiter.allow("opaque-ip-digest") is True
    assert limiter.allow("opaque-ip-digest") is False
    assert limiter.allow("different-digest") is True

    now[0] = 111.0
    assert limiter.allow("opaque-ip-digest") is True
