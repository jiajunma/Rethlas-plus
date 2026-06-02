#!/usr/bin/env python3
"""Validate a referee_report_v1 JSON file."""

from __future__ import annotations

import sys
from pathlib import Path

from referee.decoder import RefereeDecodeError, parse_referee_report


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: validate_referee_report.py PATH\n")
        return 2
    raw = Path(args[0]).read_text(encoding="utf-8")
    try:
        report = parse_referee_report(raw)
    except RefereeDecodeError as exc:
        sys.stderr.write(f"invalid: {exc.reason}: {exc.detail}\n")
        return 1
    sys.stdout.write(
        "ok: "
        f"{report.review_id} verdict={report.verdict} "
        f"blocks={report.blocks_acceptance} "
        f"theorem_nodes={len(report.theorem_nodes)} "
        f"dependency_edges={len(report.theorem_dependency_edges)} "
        f"location_notes={len(report.node_location_notes)} "
        f"typos={len(report.typo_findings)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
