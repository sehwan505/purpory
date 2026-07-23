"""Freshness policy for curated and graph-seeded topics."""

from __future__ import annotations

import time

DEFAULT_STALE_DAYS = 60
SECONDS_PER_DAY = 86_400


def is_stale(
    set_at: int,
    *,
    now: int | None = None,
    stale_after_days: int = DEFAULT_STALE_DAYS,
) -> bool:
    """Return whether a topic has exceeded the configured freshness window."""
    if stale_after_days < 0:
        raise ValueError("stale_after_days must be non-negative")
    current = int(time.time()) if now is None else int(now)
    age = max(0, current - int(set_at))
    return age > stale_after_days * SECONDS_PER_DAY
