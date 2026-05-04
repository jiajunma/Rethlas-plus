from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common.phase3.artifacts import write_json_atomic
from tests.fixtures.tmp_workspace import make_workspace


PYTHON = sys.executable


def test_review_list_and_show(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    write_json_atomic(
        tmp_path / "reviews" / "evt.json",
        {
            "schema": "rethlas-referee-artifact-v1",
            "event_id": "evt",
            "event_type": "referee.review_completed",
            "target": "thm:toy",
            "payload": {
                "review_id": "review_toy_001",
                "verdict": "needs_revision",
                "issue_summary": {"issue_count": 1, "blocks_acceptance": True},
            },
        },
    )
    listed = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "review", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert "review_toy_001" in listed.stdout
    assert "blocks=True" in listed.stdout

    shown = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "show",
            "review_toy_001",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert '"review_id": "review_toy_001"' in shown.stdout
