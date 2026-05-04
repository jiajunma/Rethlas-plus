#!/usr/bin/env python3
"""Validate a learner_batch_v1 JSON file."""

from __future__ import annotations

import sys
from pathlib import Path

from learner.decoder import LearnerDecodeError, parse_learner_batch


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: validate_learner_batch.py PATH\n")
        return 2
    raw = Path(args[0]).read_text(encoding="utf-8")
    try:
        batch = parse_learner_batch(raw)
    except LearnerDecodeError as exc:
        sys.stderr.write(f"invalid: {exc.reason}: {exc.detail}\n")
        return 1
    sys.stdout.write(
        f"ok: {batch.run_id} candidates={len(batch.candidate_nodes)} issues={len(batch.issues)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
