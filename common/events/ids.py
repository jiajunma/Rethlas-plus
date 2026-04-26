"""Event-id allocation with producer-local monotonicity (ARCHITECTURE §3.2, §9.1).

Event-id shape::

    {iso_ms}-{seq}-{uid}

Monotonicity guarantee (per producer, per workspace):
- ``iso_ms`` values are non-decreasing within a single producer process.
- If two allocations land in the same millisecond, ``seq`` advances.
- If the wall clock steps backwards, the allocator reuses the latest
  ``iso_ms`` it has already emitted (it will not travel into the past).

This matches the §3.2 "(iso_ms, seq, uid) lexicographic sort is a faithful
global causal-order extension" contract: the ordering between events from
the same producer is always the emission order, even when NTP slews the
clock backwards by a few ms or the system is virtualised.

The allocator is **not** cross-producer: the filename carries the producer
identity, and global ordering is decided by the full ``(iso_ms, seq, uid)``
tuple plus producer. Callers (user CLI, generator wrapper, verifier
wrapper) each keep their own allocator.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

_UID_BYTES = 8  # 8 bytes → 16 hex chars per §3.2.


def _default_clock() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso_ms_of(dt: datetime) -> str:
    # Canonical format: YYYYMMDDTHHMMSS.mmm (§3.2).
    if dt.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime (UTC)")
    dt_utc = dt.astimezone(timezone.utc)
    # ``%f`` is microseconds; truncate to milliseconds.
    micro = dt_utc.strftime("%Y%m%dT%H%M%S.%f")
    return micro[:-3]


def _next_iso_ms(iso_ms: str) -> str:
    dt = datetime.strptime(iso_ms, "%Y%m%dT%H%M%S.%f").replace(tzinfo=timezone.utc)
    return _iso_ms_of(dt + timedelta(milliseconds=1))


@dataclass(frozen=True, slots=True)
class AllocatedEventId:
    iso_ms: str
    seq: int
    uid: str

    @property
    def event_id(self) -> str:
        return f"{self.iso_ms}-{self.seq:04d}-{self.uid}"


class EventIdAllocator:
    """Thread-safe, producer-local allocator.

    Parameters
    ----------
    clock:
        Callable returning the current UTC :class:`datetime`. The default
        reads the wall clock; tests pass a fake clock for deterministic
        behaviour (see ``tests/fixtures/fake_clock.py``).
    rng:
        Callable returning ``_UID_BYTES`` random bytes. Defaulted to
        :func:`secrets.token_bytes`; tests pass a scripted counter.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _default_clock,
        rng: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._clock = clock
        self._rng = rng
        self._lock = threading.Lock()
        self._last_iso_ms: str | None = None
        self._last_seq: int = 0

    def allocate(self) -> AllocatedEventId:
        now = self._clock()
        iso_ms = _iso_ms_of(now)
        uid = self._rng(_UID_BYTES).hex()

        with self._lock:
            if self._last_iso_ms is None or iso_ms > self._last_iso_ms:
                # Clock advanced — reset seq.
                self._last_iso_ms = iso_ms
                self._last_seq = 1
            elif iso_ms == self._last_iso_ms:
                # Same millisecond — advance seq.
                if self._last_seq >= 9999:
                    self._last_iso_ms = _next_iso_ms(self._last_iso_ms)
                    self._last_seq = 1
                else:
                    self._last_seq += 1
            else:
                # Clock regressed — clamp to the latest iso_ms we have
                # already committed to. Without this, a step-back would
                # let the allocator emit events whose lexicographic order
                # contradicts emission order.
                if self._last_seq >= 9999:
                    self._last_iso_ms = _next_iso_ms(self._last_iso_ms)
                    self._last_seq = 1
                else:
                    self._last_seq += 1

            iso_ms = self._last_iso_ms

            seq = self._last_seq

        return AllocatedEventId(iso_ms=iso_ms, seq=seq, uid=uid)


def allocate_event_id(
    *,
    clock: Callable[[], datetime] | None = None,
    rng: Callable[[int], bytes] | None = None,
) -> AllocatedEventId:
    """Convenience allocator for one-shot callers.

    Callers that emit many events (generator wrapper publishing a batch,
    verifier wrapper inside a tight loop) should instantiate an
    :class:`EventIdAllocator` instead so per-process monotonicity is kept.
    """
    alloc = EventIdAllocator(
        clock=clock or _default_clock,
        rng=rng or secrets.token_bytes,
    )
    return alloc.allocate()


# Legacy export — some tests may prefer a module-level function to mock.
__all__ = [
    "AllocatedEventId",
    "EventIdAllocator",
    "allocate_event_id",
]

# Silence unused-import linters in environments that also inspect os.
_ = os
