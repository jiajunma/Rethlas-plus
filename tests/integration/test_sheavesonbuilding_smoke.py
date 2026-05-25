"""Sheavesonbuilding smoke test (issue #8).

End-to-end check that the walking skeleton can run against a real
research blueprint with a real LLM in the loop. Gated by:

- ``@pytest.mark.slow`` — opt in with ``pytest -m slow``.
- ``skipif`` — the blueprint dir must exist and the codex CLI must
  be on PATH.

The test does NOT gate on the LLM's actual verdict (it may legitimately
say "needs_definition" or anything else on a research-in-progress
node). We only assert the round-trip succeeded and produced a parseable
review with a valid decision token.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import BackendError
from rethlas_kb.backends.codex import CodexBackend
from rethlas_kb_agents.statement_verifier import (
    StatementReviewParseError,
    StatementVerifier,
)
from rethlas_kb_agents.statement_verifier.decoder import VALID_DECISIONS


SHEAVES_ROOT = Path("/Users/hoxide/mydoc/sheavesonbuilding").expanduser()
SMOKE_NODE_ID = "cellular_categories.sheaves_cosheaves"

# Transient external failures we want to skip-not-fail on. Catching
# these stops a flaky upstream from breaking a developer's local test
# loop. Real bugs in our wrapper still surface as failures.
_TRANSIENT_MARKERS = (
    "exceeded retry limit",
    "429 Too Many Requests",
    "rate limit",
    "timed out",
    "ECONNRESET",
    "service unavailable",
    "503 Service Unavailable",
    "502 Bad Gateway",
)


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not SHEAVES_ROOT.exists(),
        reason=f"sheavesonbuilding repo not found at {SHEAVES_ROOT}",
    ),
    pytest.mark.skipif(
        shutil.which("codex") is None,
        reason="codex CLI not installed on this host",
    ),
]


def _skip_if_transient(message: str) -> None:
    lower = message.lower()
    for marker in _TRANSIENT_MARKERS:
        if marker.lower() in lower:
            pytest.skip(f"transient upstream failure: {marker} (msg: {message[:200]})")


def test_statement_verifier_round_trips_against_sheavesonbuilding(
    tmp_path: Path,
) -> None:
    """Full walking-skeleton round-trip with real codex on a real node."""
    adapter = KbAdapter(SHEAVES_ROOT)

    # Sanity-check the node exists and looks like real research content.
    node = adapter.read_node(SMOKE_NODE_ID)
    assert node.body.strip(), f"target node {SMOKE_NODE_ID} has empty body"

    verifier = StatementVerifier(
        backend=CodexBackend(),
        timeout_seconds=600,  # research-math prompts can be long
    )

    try:
        review = verifier.run(SMOKE_NODE_ID, adapter)
    except BackendError as exc:
        _skip_if_transient(str(exc))
        raise
    except StatementReviewParseError as exc:
        # Codex sometimes returns an API-error event instead of a verdict
        # (rate limit, transient 5xx). The decoder correctly reports
        # "no review JSON found" — we want to skip rather than fail
        # the developer's local run on a flaky upstream.
        # The raw LLM output is on exc.raw — that's where the rate-limit
        # message lives (the exception's own detail is just our parser's
        # description of what went wrong).
        _skip_if_transient(exc.raw or exc.detail or str(exc))
        raise

    # Don't gate on the LLM's verdict — research-in-progress nodes can
    # legitimately come back as needs_definition / generality_concern.
    assert review.decision in VALID_DECISIONS, (
        f"backend returned unknown decision: {review.decision!r}"
    )
    assert review.rationale.strip(), "rationale must be non-empty"
    assert 0.0 <= review.confidence <= 1.0
