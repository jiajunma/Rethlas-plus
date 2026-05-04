from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.fixtures.scripted_codex import fake_codex_argv, quick_success


PYTHON = sys.executable


def _learner_json() -> str:
    return json.dumps(
        {
            "output_schema": "learner_batch_v1",
            "source_id": "src:toy",
            "run_id": "learn_toy_001",
            "context_hash": "sha256:" + "a" * 64,
            "source_spans": [
                {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
            ],
            "candidate_nodes": [
                {
                    "label": "def:toy_object",
                    "kind": "definition",
                    "statement": "A toy object.",
                    "source_refs": [
                        {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                    ],
                }
            ],
            "verification_requests": [
                {"target": "def:toy_object", "kind": "verify_definition"}
            ],
        }
    )


def test_supervise_dispatches_learner_queue(tmp_path: Path) -> None:
    init = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "init"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    context = tmp_path / "learner_context.json"
    context.write_text(
        json.dumps(
            {
                "source_id": "src:toy",
                "source_spans": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ),
        encoding="utf-8",
    )
    queued = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "learner",
            "--source",
            "src:toy",
            "--context-json",
            str(context),
            "--queue",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert queued.returncode == 0, queued.stderr
    assert "queued learner-" in queued.stdout

    env = os.environ.copy()
    env["RETHLAS_COORDINATOR_MAX_TICKS"] = "8"
    env["RETHLAS_LIBRARIAN_HEARTBEAT_S"] = "0.2"
    env["RETHLAS_COORDINATOR_TICK_S"] = "0.1"
    env["RETHLAS_COORDINATOR_DASHBOARD_DISABLED"] = "1"
    env["RETHLAS_TEST_TIME_SCALE"] = "0.2"
    env["FAKE_CODEX_SCRIPT"] = quick_success(_learner_json())
    env["RETHLAS_FAKE_CODEX_ARGV"] = " ".join(fake_codex_argv())
    supervised = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "supervise"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    assert supervised.returncode == 0, supervised.stderr
    artifact_dir = tmp_path / "knowledge_base" / "phase3" / "learner_batches"
    assert list(artifact_dir.glob("*.json"))
    assert not list((tmp_path / "runtime" / "queues" / "learner").glob("*.json"))
