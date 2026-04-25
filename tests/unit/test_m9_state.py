"""M9 — dashboard pure-logic state classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dashboard.state import (
    DEGRADED_S,
    HEALTHY_S,
    STATUS_BLOCKED_ON_DEPENDENCY,
    STATUS_DONE,
    STATUS_GEN_BLOCKED_ON_DEPENDENCY,
    STATUS_IN_FLIGHT,
    STATUS_NEEDS_GENERATION,
    STATUS_NEEDS_VERIFICATION,
    STATUS_USER_BLOCKED,
    STATUS_VERIFIED,
    classify_theorem,
    liveness_label,
)


# ---------------------------------------------------------------------------
# liveness_label
# ---------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_liveness_healthy_within_60s() -> None:
    now = datetime(2026, 4, 24, tzinfo=timezone.utc)
    five_s_ago = now - timedelta(seconds=5)
    assert liveness_label(_iso(five_s_ago), now=now) == "healthy"


def test_liveness_degraded_above_60s() -> None:
    now = datetime(2026, 4, 24, tzinfo=timezone.utc)
    two_min_ago = now - timedelta(seconds=120)
    assert liveness_label(_iso(two_min_ago), now=now) == "degraded"


def test_liveness_down_above_5min() -> None:
    now = datetime(2026, 4, 24, tzinfo=timezone.utc)
    ten_min_ago = now - timedelta(seconds=600)
    assert liveness_label(_iso(ten_min_ago), now=now) == "down"


def test_liveness_down_for_missing() -> None:
    assert liveness_label(None) == "down"
    assert liveness_label("") == "down"


def test_liveness_down_for_unparseable() -> None:
    assert liveness_label("not-an-iso-date") == "down"


def test_liveness_z_suffix_normalised() -> None:
    now = datetime(2026, 4, 24, tzinfo=timezone.utc)
    five_s_ago = now - timedelta(seconds=5)
    raw = five_s_ago.isoformat(timespec="milliseconds")
    raw = raw.replace("+00:00", "Z")
    assert liveness_label(raw, now=now) == "healthy"


def test_liveness_thresholds_match_arch() -> None:
    # The §6.7.1 contract: 60s healthy, 300s degraded.
    assert HEALTHY_S == 60.0
    assert DEGRADED_S == 300.0


# ---------------------------------------------------------------------------
# classify_theorem covers all 8 status keywords.
# ---------------------------------------------------------------------------
def _node(**kw):
    base = dict(
        label="thm:t",
        kind="theorem",
        pass_count=0,
        desired=3,
        deps=[],
        deps_pass_counts={},
        in_flight=False,
        repair_hint="",
    )
    base.update(kw)
    return classify_theorem(**base)


def test_classify_done() -> None:
    assert _node(pass_count=3) == STATUS_DONE


def test_classify_verified_partial() -> None:
    assert _node(pass_count=1) == STATUS_VERIFIED
    assert _node(pass_count=2) == STATUS_VERIFIED


def test_classify_verified_with_unready_deps_is_blocked() -> None:
    # PHASE1 M9: a node verified once but with deps reset by cascade
    # cannot progress further; surface as blocked_on_dependency so the
    # operator sees the stuck state rather than misleading "verified".
    assert (
        _node(
            pass_count=1,
            deps=["def:x"],
            deps_pass_counts={"def:x": 0},
        )
        == STATUS_BLOCKED_ON_DEPENDENCY
    )


def test_classify_needs_verification() -> None:
    assert _node(pass_count=0, deps=[], deps_pass_counts={}) == STATUS_NEEDS_VERIFICATION


def test_classify_blocked_on_dependency() -> None:
    assert (
        _node(
            pass_count=0,
            deps=["def:x"],
            deps_pass_counts={"def:x": -1},
        )
        == STATUS_BLOCKED_ON_DEPENDENCY
    )


def test_classify_needs_generation() -> None:
    assert (
        _node(
            pass_count=-1,
            deps=["def:x"],
            deps_pass_counts={"def:x": 5},
        )
        == STATUS_NEEDS_GENERATION
    )


def test_classify_generation_blocked_on_dependency() -> None:
    assert (
        _node(
            pass_count=-1,
            deps=["def:x"],
            deps_pass_counts={"def:x": 0},
        )
        == STATUS_GEN_BLOCKED_ON_DEPENDENCY
    )


def test_classify_user_blocked_definition() -> None:
    assert _node(kind="definition", pass_count=-1) == STATUS_USER_BLOCKED
    assert _node(kind="external_theorem", pass_count=-1) == STATUS_USER_BLOCKED


def test_classify_in_flight_overrides() -> None:
    assert _node(pass_count=3, in_flight=True) == STATUS_IN_FLIGHT
    assert _node(pass_count=-1, in_flight=True) == STATUS_IN_FLIGHT
