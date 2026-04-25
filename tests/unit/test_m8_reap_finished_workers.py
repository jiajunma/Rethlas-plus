"""M8 / §6.7.1 — coordinator-side reap records terminal outcomes.

When a wrapper subprocess exits without writing ``publishing`` (silent
timeout or crash), :func:`coordinator.main._reap_finished_workers`
must:

1. Map exit_code 124 to ``status = "timed_out"`` per §6.7.1 step 4
   (silent-timeout kill).
2. Map any other non-zero exit to ``status = "crashed"`` per §6.7.1
   step 5.
3. Record the terminal outcome into the supplied ``OutcomeWindow`` so
   the §7.4 / §7.5 "3 consecutive" attention triggers can fire.
4. Delete the job file.

Without this bookkeeping the dashboard ``stuck_target`` attention
items (``"<kind> frozen on <label>"`` / ``"<kind> unstable on <label>"``)
never appear, even when a wrapper has timed out three times in a row.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from common.runtime.jobs import (
    JobRecord,
    STATUS_CRASHED,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    STATUS_TIMED_OUT,
    job_file_path,
    write_job_file,
)
from common.runtime.reaper import OutcomeWindow
from coordinator.main import _reap_finished_workers


class _FakeProc:
    """Just enough of subprocess.Popen for the reaper."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def _make_record(*, job_id: str, kind: str, target: str, status: str) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        kind=kind,
        target=target,
        mode="single" if kind == "verifier" else "fresh",
        dispatch_hash="ab" * 32,
        pid=12345,
        pgid=12345,
        started_at="2026-04-25T00:00:00.000Z",
        updated_at="2026-04-25T00:00:00.000Z",
        status=status,
        log_path=f"runtime/logs/{job_id}.codex.log",
    )


def _fake_state(tmp_path: Path) -> SimpleNamespace:
    jobs_dir = tmp_path / "runtime" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        ws=SimpleNamespace(runtime_jobs=jobs_dir),
        in_flight_workers={},
        outcome_window=OutcomeWindow(),
    )


def test_exit_124_records_timed_out(tmp_path: Path) -> None:
    state = _fake_state(tmp_path)
    rec = _make_record(
        job_id="ver-1", kind="verifier", target="thm:t", status=STATUS_RUNNING
    )
    write_job_file(job_file_path(state.ws.runtime_jobs, rec.job_id), rec)
    state.in_flight_workers[rec.job_id] = _FakeProc(returncode=124)

    _reap_finished_workers(state)

    assert state.in_flight_workers == {}
    # File was deleted.
    assert not job_file_path(state.ws.runtime_jobs, rec.job_id).exists()
    # Outcome window saw timed_out — three of these in a row triggers
    # the §7.4 "frozen" attention item.
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="verifier", status=STATUS_TIMED_OUT
    ) == 1
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="verifier", status=STATUS_CRASHED
    ) == 0


def test_non_zero_exit_records_crashed(tmp_path: Path) -> None:
    state = _fake_state(tmp_path)
    rec = _make_record(
        job_id="gen-1", kind="generator", target="thm:t", status=STATUS_RUNNING
    )
    write_job_file(job_file_path(state.ws.runtime_jobs, rec.job_id), rec)
    state.in_flight_workers[rec.job_id] = _FakeProc(returncode=1)

    _reap_finished_workers(state)

    assert not job_file_path(state.ws.runtime_jobs, rec.job_id).exists()
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="generator", status=STATUS_CRASHED
    ) == 1


def test_three_consecutive_timeouts_fire_frozen_trigger(tmp_path: Path) -> None:
    """§7.4 F4: three consecutive ``timed_out`` outcomes on the same
    (target, kind) is the precise condition the dashboard surfaces as
    ``"<kind> frozen on <label>"``."""
    state = _fake_state(tmp_path)
    for i in range(3):
        rec = _make_record(
            job_id=f"ver-{i}", kind="verifier", target="thm:t", status=STATUS_RUNNING
        )
        write_job_file(job_file_path(state.ws.runtime_jobs, rec.job_id), rec)
        state.in_flight_workers[rec.job_id] = _FakeProc(returncode=124)
        _reap_finished_workers(state)
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="verifier", status=STATUS_TIMED_OUT
    ) == 3


def test_publishing_status_is_left_alone(tmp_path: Path) -> None:
    """The applied_poller owns terminal cleanup for publishing jobs."""
    state = _fake_state(tmp_path)
    rec = _make_record(
        job_id="ver-pub", kind="verifier", target="thm:t", status=STATUS_PUBLISHING
    )
    write_job_file(job_file_path(state.ws.runtime_jobs, rec.job_id), rec)
    state.in_flight_workers[rec.job_id] = _FakeProc(returncode=0)

    _reap_finished_workers(state)

    # File preserved; outcome NOT recorded by the reaper (applied_poller will).
    assert job_file_path(state.ws.runtime_jobs, rec.job_id).exists()
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="verifier", status=STATUS_TIMED_OUT
    ) == 0


def test_wrapper_crashed_status_still_recorded(tmp_path: Path) -> None:
    """If the wrapper hand-wrote ``crashed`` (e.g. decode error) before
    exiting, the reaper still records the terminal outcome — otherwise
    §7.5 "3 consecutive crashes" never fires for decode-error spirals."""
    state = _fake_state(tmp_path)
    rec = _make_record(
        job_id="gen-x", kind="generator", target="thm:t", status=STATUS_CRASHED
    )
    write_job_file(job_file_path(state.ws.runtime_jobs, rec.job_id), rec)
    state.in_flight_workers[rec.job_id] = _FakeProc(returncode=3)

    _reap_finished_workers(state)

    assert not job_file_path(state.ws.runtime_jobs, rec.job_id).exists()
    assert state.outcome_window.consecutive_status(
        target="thm:t", kind="generator", status=STATUS_CRASHED
    ) == 1
