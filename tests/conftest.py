"""Shared pytest configuration.

Phase I admission / publish tests spawn ``rethlas`` CLI subprocesses.
The user-CLI path polls ``AppliedEvent`` for up to 30 seconds when no
librarian is running — far too slow for the test suite. We set the
:envvar:`RETHLAS_PUBLISH_POLL_TIMEOUT_S` to a short value so tests that
deliberately exercise the "timeout" path return quickly.

Tests that need a longer timeout can override via ``monkeypatch.setenv``.
"""

from __future__ import annotations

import os


# Default for the M3 / M4+ test suite: 2 seconds is enough for the
# happy-path poll to finish once an AppliedEvent row is written, and
# short enough that the "supervise not running" / "librarian behind"
# paths don't dominate wall-clock time.
os.environ.setdefault("RETHLAS_PUBLISH_POLL_TIMEOUT_S", "2.0")
