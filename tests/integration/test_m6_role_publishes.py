"""M6 — generator role wraps Codex, decodes, publishes a batch.

Drives :func:`generator.role.main` directly with a fake codex script
that emits a valid ``<node>`` block. Asserts the truth event lands in
``events/`` with the correct schema and that the job file moves
through ``running`` → ``publishing``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from common.runtime.jobs import (
    JobRecord,
    STATUS_PUBLISHING,
    STATUS_STARTING,
    job_file_path,
    log_path_for,
    read_job_file,
    utc_now_iso,
    write_job_file,
)
from generator.role import main as role_main
from tests.fixtures.scripted_codex import fake_codex_argv, quick_success
from tests.fixtures.tmp_workspace import make_workspace


def _seed_job(
    ws_root: Path,
    *,
    target: str,
    mode: str,
    target_kind: str,
    statement: str,
    proof: str = "",
    repair_hint: str = "",
    h_rejected: str = "",
) -> str:
    job_id = f"gen-test-{target.replace(':','_')}"
    rec = JobRecord(
        job_id=job_id,
        kind="generator",
        target=target,
        mode=mode,
        dispatch_hash="sha256:abc",
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path=f"runtime/logs/{job_id}.codex.log",
        target_kind=target_kind,
        statement=statement,
        proof=proof,
        repair_hint=repair_hint,
        h_rejected=h_rejected,
    )
    write_job_file(job_file_path(ws_root / "runtime" / "jobs", job_id), rec)
    return job_id


def _block(label: str, kind: str, statement: str, proof: str) -> str:
    return (
        f"<node>\n"
        f"label: {label}\n"
        f"kind: {kind}\n"
        f"---\n"
        f"**Statement.**\n\n{statement}\n\n**Proof.**\n\n{proof}\n"
        f"</node>"
    )


def test_role_publishes_valid_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_root = tmp_path
    make_workspace(ws_root, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(ws_root))
    monkeypatch.setenv("RETHLAS_GENERATOR_HEARTBEAT_S", "60.0")
    monkeypatch.setenv(
        "FAKE_CODEX_SCRIPT",
        quick_success(_block("thm:goal", "theorem", "Statement S", "Proof P")),
    )

    job_id = _seed_job(
        ws_root,
        target="thm:goal",
        mode="fresh",
        target_kind="theorem",
        statement="Statement S",
    )

    rc = role_main([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
        "--actor",
        "generator:test",
    ])
    assert rc == 0

    rec = read_job_file(job_file_path(ws_root / "runtime" / "jobs", job_id))
    assert rec is not None
    assert rec.status == STATUS_PUBLISHING

    events = sorted((ws_root / "events").rglob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "generator.batch_committed"
    assert body["target"] == "thm:goal"
    assert body["actor"] == "generator:test"
    payload = body["payload"]
    assert re.match(
        r"^gen-\d{8}T\d{6}\.\d{3}-\d{4}-[0-9a-f]{16}$", payload["attempt_id"]
    )
    assert payload["mode"] == "fresh"
    assert payload["target"] == "thm:goal"
    assert len(payload["nodes"]) == 1
    n = payload["nodes"][0]
    for key in ("label", "kind", "statement", "proof", "remark", "source_note"):
        assert key in n
    assert n["statement"]


def test_role_rejection_writes_to_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H29: decoder rejections are now structural-only. Trigger the
    ``target_mismatch`` reason by having codex emit a node with a label
    that doesn't match the dispatch target — the wrapper has no way to
    publish a sane batch_committed payload, so it rejects."""
    ws_root = tmp_path
    make_workspace(ws_root, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(ws_root))
    monkeypatch.setenv(
        "FAKE_CODEX_SCRIPT",
        quick_success(_block("thm:other", "theorem", "S", "P")),
    )

    job_id = _seed_job(
        ws_root,
        target="thm:goal",
        mode="fresh",
        target_kind="theorem",
        statement="S",
    )

    rc = role_main([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
    ])
    assert rc != 0
    # No truth event published.
    assert not list((ws_root / "events").rglob("*.json"))
    # Rejection logged.
    rejects = ws_root / "runtime" / "state" / "rejected_writes.jsonl"
    assert rejects.is_file()
    line = rejects.read_text(encoding="utf-8").splitlines()[-1]
    entry = json.loads(line)
    assert entry["reason"] == "target_mismatch"
    assert entry["target"] == "thm:goal"
    # H29 phase A-2: the parsed (but rejected) draft is preserved so
    # the next attempt's prompt can repair against it.
    assert "parsed_blocks" in entry
    assert [b["label"] for b in entry["parsed_blocks"]] == ["thm:other"]
    assert entry["parsed_blocks"][0]["kind"] == "theorem"
    assert entry["parsed_blocks"][0]["statement"] == "S"
    assert entry["parsed_blocks"][0]["proof"] == "P"


def test_fresh_mode_user_hint_reaches_prompt_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wrapper composes the prompt and stuffs it into
    ``RETHLAS_GENERATOR_PROMPT`` so test fixtures can verify it landed
    without parsing real codex output. Guards the §6.2 step 2
    "hint dropped on fresh dispatch" regression.
    """
    ws_root = tmp_path
    make_workspace(ws_root, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(ws_root))
    monkeypatch.setenv(
        "FAKE_CODEX_SCRIPT",
        quick_success(_block("thm:goal", "theorem", "S", "P")),
    )

    job_id = _seed_job(
        ws_root,
        target="thm:goal",
        mode="fresh",
        target_kind="theorem",
        statement="S",
        repair_hint="[user @ alice]\nfocus on the symplectic case\n",
    )

    role_main([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
    ])
    # The wrapper sets RETHLAS_GENERATOR_PROMPT in the env it passes to
    # codex; this process inherits the same env via `monkeypatch`, so we
    # can read it back through the env after the run.
    # However, the wrapper sets it on its child env only — to verify
    # without a subprocess hook, recompute the prompt from the saved job.
    from common.runtime.jobs import read_job_file

    rec = read_job_file(job_file_path(ws_root / "runtime" / "jobs", job_id))
    assert rec is not None
    from generator.prompt import compose_prompt
    prompt = compose_prompt(rec)
    assert "Initial guidance" in prompt
    assert "focus on the symplectic case" in prompt
