"""Thin CLI wrapper for ``rethlas linter`` (PHASE1 M10)."""

from __future__ import annotations

import argparse


def run_linter(workspace: str | None, args: argparse.Namespace) -> int:
    from linter.main import run_linter as _run

    return _run(
        workspace,
        repair_nodes=getattr(args, "repair_nodes", False),
        allow_concurrent=getattr(args, "allow_concurrent", False),
    )


__all__ = ["run_linter"]
