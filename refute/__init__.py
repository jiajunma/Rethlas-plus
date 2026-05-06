"""``refute`` worker package — counterexample-search role (DESIGN §8).

Companion to ``verifier/``. Exposes prompt + decoder primitives that a
future ``refute.role`` (codex-orchestrated) and ``refute.cli`` will
compose. The decoder maps JSON output to
``rethlas_scoring.refute.RefuteVerdict`` so the scheduler/policy layers
can consume refute results uniformly.

Phase II.5 + S4-light: this package ships **prompt + decoder only**.
Codex orchestration (``role.py`` / ``cli.py``) and KB event types
(``refute.run_completed`` etc.) arrive in S4-full once the
verifier-side schema for storing refute Evidence rows is designed.
"""
