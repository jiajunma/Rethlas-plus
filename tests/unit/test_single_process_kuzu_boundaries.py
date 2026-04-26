"""Static guards for the single-process Kuzu model.

Only librarian-owned code may import or directly mention the Kuzu Python API.
Other runtime components must query librarian IPC instead.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


NON_LIBRARIAN_RUNTIME_FILES = [
    "cli/publish.py",
    "coordinator/main.py",
    "coordinator/applied_poller.py",
    "coordinator/kb_client.py",
    "dashboard/kb_client.py",
    "dashboard/kuzu_reader.py",
    "dashboard/server.py",
    "dashboard/state_watcher.py",
    "linter/main.py",
]


def test_non_librarian_runtime_files_do_not_import_kuzu() -> None:
    for rel in NON_LIBRARIAN_RUNTIME_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "import kuzu" not in text, rel
        assert "kuzu.Database" not in text, rel
        assert "kuzu.Connection" not in text, rel
