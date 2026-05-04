from __future__ import annotations

from pathlib import Path

from common.runtime.role_queue import enqueue_role_item, list_role_queue


def test_role_queue_round_trip(tmp_path: Path) -> None:
    item = enqueue_role_item(
        tmp_path,
        kind="learner",
        mode="learn_source_spans",
        target="src:toy#spans",
        input_packet={"source_id": "src:toy"},
    )
    rows = list_role_queue(tmp_path, "learner")
    assert len(rows) == 1
    path, parsed = rows[0]
    assert path.name == f"{item.queue_id}.json"
    assert parsed.target == "src:toy#spans"
    assert parsed.context_hash.startswith("sha256:")
