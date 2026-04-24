"""M1 — event-id allocation: clock advance, repeat, and backward step."""

from __future__ import annotations

from common.events.ids import EventIdAllocator
from tests.fixtures.fake_clock import FakeClock, counter_rng


def test_fresh_clock_starts_seq_at_1() -> None:
    clock = FakeClock()
    alloc = EventIdAllocator(clock=clock, rng=counter_rng())
    eid = alloc.allocate()
    assert eid.seq == 1
    assert eid.iso_ms == "20260425T120000.000"


def test_same_millisecond_advances_seq() -> None:
    clock = FakeClock()
    alloc = EventIdAllocator(clock=clock, rng=counter_rng())
    e1 = alloc.allocate()
    e2 = alloc.allocate()
    assert e1.iso_ms == e2.iso_ms
    assert e1.seq == 1 and e2.seq == 2
    assert e1.uid != e2.uid  # distinct random bytes


def test_clock_advance_resets_seq() -> None:
    clock = FakeClock()
    alloc = EventIdAllocator(clock=clock, rng=counter_rng())
    _ = alloc.allocate()  # seq=1
    _ = alloc.allocate()  # seq=2
    clock.advance(milliseconds=1)
    e3 = alloc.allocate()
    assert e3.seq == 1, "seq must reset once the clock advances to a new ms"


def test_clock_step_back_preserves_monotonicity() -> None:
    clock = FakeClock()
    alloc = EventIdAllocator(clock=clock, rng=counter_rng())
    clock.advance(milliseconds=5)
    e1 = alloc.allocate()
    assert e1.iso_ms == "20260425T120000.005"
    assert e1.seq == 1

    clock.step_back(milliseconds=3)  # NTP slew backwards
    e2 = alloc.allocate()
    # iso_ms MUST NOT go backward — allocator clamps to the latest committed.
    assert e2.iso_ms == e1.iso_ms
    # seq advances within that same clamped ms.
    assert e2.seq == 2


def test_full_event_id_string_format() -> None:
    clock = FakeClock()
    alloc = EventIdAllocator(clock=clock, rng=counter_rng())
    e = alloc.allocate()
    assert e.event_id == f"{e.iso_ms}-{e.seq:04d}-{e.uid}"
    # uid is 16 lowercase hex chars
    assert len(e.uid) == 16
    assert all(c in "0123456789abcdef" for c in e.uid)
