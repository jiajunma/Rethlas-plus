"""Deterministic UTC clock for event-id / timing tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable


class FakeClock:
    """Step-able UTC clock. Calling the instance returns the current time.

    >>> c = FakeClock(datetime(2026, 4, 25, tzinfo=timezone.utc))
    >>> c().year
    2026
    >>> _ = c.advance(milliseconds=1)
    >>> c.step_back(milliseconds=2)  # allowed — simulates NTP slew
    """

    def __init__(self, start: datetime | None = None) -> None:
        start = start or datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self._now = start.astimezone(timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance(
        self,
        *,
        milliseconds: int = 0,
        seconds: int = 0,
        microseconds: int = 0,
    ) -> datetime:
        self._now = self._now + timedelta(
            milliseconds=milliseconds,
            seconds=seconds,
            microseconds=microseconds,
        )
        return self._now

    def step_back(
        self,
        *,
        milliseconds: int = 0,
        seconds: int = 0,
        microseconds: int = 0,
    ) -> datetime:
        self._now = self._now - timedelta(
            milliseconds=milliseconds,
            seconds=seconds,
            microseconds=microseconds,
        )
        return self._now


def counter_rng(start: int = 0) -> Callable[[int], bytes]:
    """Deterministic ``rng(n)`` substitute for :func:`secrets.token_bytes`.

    Returns a callable that yields big-endian byte-patterns derived from
    a monotonically increasing counter, so ``uid`` values are predictable
    across runs. Suitable for tests that assert a full ``event_id``
    string.
    """
    counter = [start]

    def rng(n: int) -> bytes:
        counter[0] += 1
        return counter[0].to_bytes(n, "big")

    return rng


__all__ = ["FakeClock", "counter_rng"]
