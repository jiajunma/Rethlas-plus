"""Read-only CLI for Phase 3 referee review artifacts."""

from __future__ import annotations

import argparse
import json
import sys

from cli.workspace import ensure_initialised, workspace_paths
from common.phase3.artifacts import list_reviews


def run_review(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    if args.review_command == "list":
        rows = list_reviews(ws.root)
        for row in rows:
            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            summary = payload.get("issue_summary", {}) if isinstance(payload.get("issue_summary"), dict) else {}
            sys.stdout.write(
                "\t".join(
                    [
                        str(payload.get("review_id", "") or row.get("event_id", "")),
                        str(row.get("target", "")),
                        str(payload.get("verdict", "")),
                        f"issues={summary.get('issue_count', 0)}",
                        f"blocks={bool(summary.get('blocks_acceptance', False))}",
                    ]
                )
                + "\n"
            )
        return 0

    if args.review_command == "show":
        for row in list_reviews(ws.root):
            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            review_id = payload.get("review_id", "") or row.get("event_id", "")
            if review_id == args.review_id or row.get("event_id", "") == args.review_id:
                sys.stdout.write(json.dumps(row, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
                return 0
        sys.stderr.write(f"review not found: {args.review_id}\n")
        return 1

    sys.stderr.write("review requires a subcommand: list or show\n")
    return 2


__all__ = ["run_review"]
