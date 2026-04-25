"""Coordinator-side wrapper spawn helper (ARCHITECTURE §6.7.1 step 1).

Coordinator spawns a generator/verifier wrapper subprocess with:

- Full environment inheritance (so ``OPENAI_API_KEY``, ``PATH``,
  proxy settings flow through to Codex).
- An additional ``RETHLAS_WORKSPACE=<absolute path>`` env var.
- The ``job_id`` as a positional argument.

This module exists so M8 (coordinator) can compose the spawn without
re-implementing the contract — and so M5 tests can verify the env /
positional rules independently of the coordinator.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


def build_wrapper_env(
    *,
    workspace: Path | str,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the env dict the coordinator hands to the wrapper.

    Inherits the current process env, overlays
    ``RETHLAS_WORKSPACE=<abs path>``, then any caller-provided
    ``extra`` (used by tests).
    """
    env = os.environ.copy()
    env["RETHLAS_WORKSPACE"] = str(Path(workspace).resolve())
    if extra:
        env.update(extra)
    return env


def spawn_wrapper(
    *,
    workspace: Path | str,
    wrapper_argv: Sequence[str],
    job_id: str,
    extra_env: Mapping[str, str] | None = None,
    stdin=subprocess.DEVNULL,
    stdout=None,
    stderr=None,
) -> subprocess.Popen:
    """Launch a wrapper subprocess per §6.7.1 step 1.

    ``wrapper_argv`` is the wrapper command (e.g.
    ``[sys.executable, "-m", "generator.role"]``); the helper appends
    ``job_id`` as the trailing positional argument.
    """
    env = build_wrapper_env(workspace=workspace, extra=extra_env)
    return subprocess.Popen(
        [*wrapper_argv, job_id],
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )


__all__ = ["build_wrapper_env", "spawn_wrapper"]
