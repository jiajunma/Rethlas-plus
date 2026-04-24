"""M0 — the built wheel must ship ``common/producers.toml``.

Runs ``python -m build --wheel`` in a temp copy of the project tree
and inspects the resulting wheel archive. Guards against the common
packaging regression where ``producers.toml`` is present in the
source tree but missing after ``pip install``.

Skipped if the ``build`` package is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

build = pytest.importorskip("build", reason="`build` not installed; skipping wheel test")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COPY_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    "agents",  # legacy tree, not part of the Phase I package
    "docs",
    "formalization",
    "coordination",
}


def _mirror_project(dst: Path) -> None:
    def ignore(dir_path: str, names: list[str]) -> list[str]:
        return [n for n in names if n in COPY_EXCLUDE_DIRS]

    shutil.copytree(PROJECT_ROOT, dst, ignore=ignore, dirs_exist_ok=False)


def test_wheel_contains_producers_toml(tmp_path: Path) -> None:
    src = tmp_path / "project"
    _mirror_project(src)

    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path / "dist"), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"wheel build failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    wheels = list((tmp_path / "dist").glob("rethlas-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
        assert "common/producers.toml" in names, (
            f"producers.toml missing from wheel; contents:\n  " + "\n  ".join(names)
        )
        data = zf.read("common/producers.toml")
        assert b"[[producer]]" in data
        assert b'kind                 = "user"' in data
