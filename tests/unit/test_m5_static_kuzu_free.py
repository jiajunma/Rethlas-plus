"""M5 static guard — workers and runtime substrate must be Kuzu-free.

ARCHITECTURE §4.1 invariant: only the librarian opens ``dag.kz``. The
generator and verifier workers, plus everything they import under
``common/runtime/``, must never import ``common.kb`` (Kuzu backend,
hashing, types live there but the workers must be content with the
scaffolded job file).

We grep recursively rather than load the modules so the test does not
trigger the violation it is checking for.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


_FORBIDDEN_PATTERNS = (
    re.compile(r"^\s*from\s+common\.kb"),
    re.compile(r"^\s*import\s+common\.kb"),
)


def _walk(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        d = REPO_ROOT / root
        if not d.is_dir():
            continue
        files.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def test_common_runtime_does_not_import_common_kb() -> None:
    """No file under common/runtime/ may import common.kb."""
    offenders: list[tuple[Path, int, str]] = []
    for path in _walk("common/runtime"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pat in _FORBIDDEN_PATTERNS:
                if pat.search(line):
                    offenders.append((path, i, line))
    assert not offenders, "Kuzu-free invariant violated: " + str(offenders)


def test_generator_role_does_not_import_common_kb() -> None:
    """When generator/role.py exists (M6+), it must remain Kuzu-free."""
    role = REPO_ROOT / "generator" / "role.py"
    if not role.is_file():
        return  # not yet implemented; M6 will land + the assert kicks in
    for i, line in enumerate(role.read_text(encoding="utf-8").splitlines(), 1):
        for pat in _FORBIDDEN_PATTERNS:
            assert not pat.search(line), f"{role}:{i}: {line}"


def test_verifier_role_does_not_import_common_kb() -> None:
    """When verifier/role.py exists (M7+), it must remain Kuzu-free."""
    role = REPO_ROOT / "verifier" / "role.py"
    if not role.is_file():
        return
    for i, line in enumerate(role.read_text(encoding="utf-8").splitlines(), 1):
        for pat in _FORBIDDEN_PATTERNS:
            assert not pat.search(line), f"{role}:{i}: {line}"
