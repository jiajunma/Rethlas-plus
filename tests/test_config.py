"""Cross-backend isolation tests (issue #13)."""

from __future__ import annotations

import pytest

from rethlas_kb.config import (
    ConfigError,
    ISOLATED_ROLE_PAIRS,
    validate_backend_isolation,
)


# ---------------------------------------------------------------------------
# Pair set
# ---------------------------------------------------------------------------
def test_isolated_pair_set_includes_gap_filler_vs_verifier() -> None:
    """The constraint pairs are the ones that risk same-source-bias."""
    pairs = {frozenset(p) for p in ISOLATED_ROLE_PAIRS}
    assert frozenset(("proof-gap-filler", "proof-verifier")) in pairs
    assert frozenset(("proof-gap-filler", "source-claim-verifier")) in pairs


def test_isolated_pair_set_excludes_statement_and_counterexample() -> None:
    """statement-verifier and counterexample-hunter are intentionally outside the constraint."""
    pairs = {frozenset(p) for p in ISOLATED_ROLE_PAIRS}
    for pair in pairs:
        assert "statement-verifier" not in pair
        assert "counterexample-hunter" not in pair


# ---------------------------------------------------------------------------
# Clean configs pass
# ---------------------------------------------------------------------------
def test_empty_bindings_pass() -> None:
    """Empty config has nothing to check — clean."""
    assert validate_backend_isolation({}) == []


def test_partial_config_passes_when_pairs_not_both_bound() -> None:
    """Only proof-verifier bound, gap-filler not — pair not violated."""
    assert validate_backend_isolation({"proof-verifier": "claude"}) == []


def test_different_backends_per_pair_pass() -> None:
    bindings = {
        "proof-gap-filler": "claude",
        "proof-verifier": "codex",
        "source-claim-verifier": "codex",
    }
    assert validate_backend_isolation(bindings) == []


def test_unrelated_roles_in_bindings_are_ignored() -> None:
    """Extra roles in bindings don't trigger anything."""
    bindings = {
        "statement-verifier": "codex",
        "counterexample-hunter": "codex",
        "proof-gap-filler": "claude",
        "proof-verifier": "codex",
    }
    assert validate_backend_isolation(bindings) == []


# ---------------------------------------------------------------------------
# Violating configs fail loud
# ---------------------------------------------------------------------------
def test_gap_filler_and_proof_verifier_same_backend_raises() -> None:
    bindings = {
        "proof-gap-filler": "codex",
        "proof-verifier": "codex",
    }
    with pytest.raises(ConfigError) as exc:
        validate_backend_isolation(bindings)
    msg = str(exc.value)
    assert "proof-gap-filler" in msg
    assert "proof-verifier" in msg
    assert "codex" in msg
    assert "same-source-bias" in msg


def test_gap_filler_and_source_claim_same_backend_raises() -> None:
    bindings = {
        "proof-gap-filler": "claude",
        "source-claim-verifier": "claude",
    }
    with pytest.raises(ConfigError, match="same-source-bias"):
        validate_backend_isolation(bindings)


def test_two_simultaneous_violations_listed_together() -> None:
    bindings = {
        "proof-gap-filler": "codex",
        "proof-verifier": "codex",
        "source-claim-verifier": "codex",
    }
    with pytest.raises(ConfigError) as exc:
        validate_backend_isolation(bindings)
    msg = str(exc.value)
    assert "2 pairs" in msg


# ---------------------------------------------------------------------------
# allow_same_backend override
# ---------------------------------------------------------------------------
def test_allow_same_backend_returns_warnings_not_error() -> None:
    bindings = {
        "proof-gap-filler": "codex",
        "proof-verifier": "codex",
    }
    warnings = validate_backend_isolation(bindings, allow_same_backend=True)
    assert len(warnings) == 1
    assert "proof-gap-filler" in warnings[0]
    assert "proof-verifier" in warnings[0]


def test_allow_same_backend_with_two_violations_returns_two_warnings() -> None:
    bindings = {
        "proof-gap-filler": "codex",
        "proof-verifier": "codex",
        "source-claim-verifier": "codex",
    }
    warnings = validate_backend_isolation(bindings, allow_same_backend=True)
    assert len(warnings) == 2


def test_allow_same_backend_with_clean_config_returns_empty() -> None:
    bindings = {
        "proof-gap-filler": "claude",
        "proof-verifier": "codex",
    }
    assert validate_backend_isolation(bindings, allow_same_backend=True) == []


# ---------------------------------------------------------------------------
# CLI --allow-same-backend flag is recognized (issue #13 scaffolding for v1.3)
# ---------------------------------------------------------------------------
def test_cli_fill_gap_accepts_allow_same_backend_flag() -> None:
    """The flag exists in argparse so future config-enforcement (issue #14-15)
    has a stable surface to override."""
    import io
    import sys
    from rethlas_kb import cli

    old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO("")
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        # --help should list the flag
        try:
            cli.main(["fill-gap", "--help"])
        except SystemExit:
            pass
        help_text = sys.stdout.getvalue() + sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err

    assert "--allow-same-backend" in help_text
