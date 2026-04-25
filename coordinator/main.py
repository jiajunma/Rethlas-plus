"""Coordinator main loop (ARCHITECTURE §6.4 / PHASE1 M8).

The loop:

1. Acquire ``runtime/locks/supervise.lock`` (singleton).
2. Run startup cleanup (M5 :func:`cleanup_runtime`).
3. Spawn librarian child; wait for ``startup_phase = ready`` and
   ``rebuild_in_progress = false``.
4. Tick loop:
   - Forward any new events under ``events/`` to librarian via
     ``APPLY`` commands.
   - Reconcile any wrappers in ``status = publishing`` against
     ``AppliedEvent``.
   - Dispatch new generator / verifier work up to the pool capacity.
   - Update ``coordinator.json`` heartbeat.
5. SIGTERM / SIGINT → set status = stopping, drain in-flight, send
   librarian SHUTDOWN, release lock, exit.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import kuzu

from cli.workspace import WorkspacePaths, workspace_paths
from common.config.loader import RethlasConfig, load_config
from common.runtime.jobs import (
    JobRecord,
    STATUS_APPLIED,
    STATUS_APPLY_FAILED,
    STATUS_CRASHED,
    STATUS_PUBLISHING,
    STATUS_STARTING,
    STATUS_TIMED_OUT,
    TERMINAL_STATUSES,
    delete_job_file,
    job_file_path,
    list_jobs,
    make_job_id,
    read_job_file,
    update_job_file,
    utc_now_iso,
    write_job_file,
)
from common.runtime.reaper import OutcomeWindow, reap_orphans
from common.runtime.spawn import spawn_wrapper
from common.runtime.startup import cleanup_runtime
from coordinator.applied_poller import reconcile_publishing_jobs
from coordinator.children import LibrarianChild, spawn_librarian
from coordinator.dashboard_child import (
    DashboardSupervisor,
    STATUS_DEGRADED as DASHBOARD_STATUS_DEGRADED,
)
from coordinator.dispatcher import (
    GeneratorCandidate,
    VerifierCandidate,
    select_generator_targets,
    select_verifier_targets,
)
from coordinator.events_watcher import EventsWatcher, WatcherCorruption
from coordinator.heartbeat import (
    CoordinatorHeartbeat,
    IDLE_ALL_DONE,
    IDLE_CORRUPTION,
    IDLE_GEN_DEP_BLOCKED,
    IDLE_IN_FLIGHT_ONLY,
    IDLE_LIBRARIAN_STARTING,
    IDLE_NONE,
    IDLE_USER_BLOCKED,
    IDLE_VER_DEP_BLOCKED,
    STATUS_DEGRADED,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STOPPING,
    write_heartbeat,
)
from coordinator.lock import SuperviseLock, SuperviseLockError
from coordinator.precheck import (
    CandidateInput,
    DispatchContext,
    PrecheckFailure,
    precheck_generator,
    precheck_verifier,
)
from librarian.heartbeat import PHASE_READY, read_heartbeat as read_librarian_hb


# Tick cadence — production default 1s, tests can shorten via env.
def _tick_interval_s() -> float:
    raw = os.environ.get("RETHLAS_COORDINATOR_TICK_S")
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return 1.0


def _librarian_ready_timeout_s() -> float:
    raw = os.environ.get("RETHLAS_LIBRARIAN_READY_TIMEOUT_S")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


@dataclass
class CoordinatorState:
    ws: WorkspacePaths
    config: RethlasConfig
    librarian: LibrarianChild
    watcher: EventsWatcher
    started_at: str
    loop_seq: int = 0
    in_flight_workers: dict[str, subprocess.Popen] = None  # type: ignore[assignment]
    outcome_window: OutcomeWindow = None  # type: ignore[assignment]
    stopping: bool = False
    pending_corruption: bool = False
    last_corruption_detail: str = ""
    dashboard: DashboardSupervisor | None = None
    # ARCHITECTURE §6.4 librarian restart policy: restart once; if it
    # dies again within 3 minutes, coordinator exits with code 3.
    last_librarian_restart_monotonic: float = 0.0

    def __post_init__(self) -> None:
        if self.in_flight_workers is None:
            self.in_flight_workers = {}
        if self.outcome_window is None:
            self.outcome_window = OutcomeWindow()


# ---------------------------------------------------------------------------
# Heartbeat helpers
# ---------------------------------------------------------------------------
def _collect_children(
    state: "CoordinatorState", lib_pid: int, lib_status: str
) -> dict[str, dict[str, Any]]:
    """Assemble the §6.4.2 ``children`` dict.

    Librarian status comes from the live child handle. Dashboard status
    comes from :class:`DashboardSupervisor` when the coordinator is
    managing the dashboard child (PHASE1 M9); otherwise we fall back
    to reading ``dashboard.json`` so a standalone-mode dashboard is
    still surfaced.
    """
    children: dict[str, dict[str, Any]] = {
        "librarian": {
            "pid": lib_pid,
            "status": lib_status,
            "updated_at": utc_now_iso(),
        }
    }
    if state.dashboard is not None:
        # Coordinator-managed dashboard: supervisor is authoritative for
        # the ``status`` (e.g. ``starting`` / ``backoff`` / ``degraded``)
        # so the operator sees restart-then-degrade transitions.
        children["dashboard"] = {
            "pid": state.dashboard.child_pid(),
            "status": state.dashboard.status,
            "updated_at": utc_now_iso(),
        }
        return children
    dash_path = state.ws.runtime_state / "dashboard.json"
    try:
        raw = dash_path.read_text(encoding="utf-8")
        body = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        body = None
    if isinstance(body, dict):
        children["dashboard"] = {
            "pid": int(body.get("pid", 0) or 0),
            "status": body.get("status", "unknown") or "unknown",
            "updated_at": body.get("updated_at", "") or "",
        }
    return children


def _write_heartbeat(
    state: CoordinatorState,
    *,
    status: str,
    idle_reason_code: str = IDLE_NONE,
    idle_reason_detail: str = "",
    dispatchable_gen: int = 0,
    dispatchable_ver: int = 0,
    unfinished: int = 0,
    user_blocked: int = 0,
    gen_blocked: int = 0,
    ver_blocked: int = 0,
) -> None:
    lib_pid = state.librarian.pid if state.librarian.is_alive() else 0
    lib_status = "running" if state.librarian.is_alive() else "down"
    in_flight = list_jobs(state.ws.runtime_jobs)
    active_gen = sum(1 for r in in_flight if r.kind == "generator" and r.status not in TERMINAL_STATUSES)
    active_ver = sum(1 for r in in_flight if r.kind == "verifier" and r.status not in TERMINAL_STATUSES)

    # ARCHITECTURE §6.4.2 / §7.4 / §7.5 / §6.7: surface (target, kind)
    # pairs whose recent outcomes hit the 3x consecutive trigger, and
    # publish a labelled attention list dashboard consumes.
    attention_targets: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for (target, kind), dq in state.outcome_window._buf.items():
        if (target, kind) in seen_keys:
            continue
        seen_keys.add((target, kind))
        added = False
        # Same-status 3x trigger (crashed / timed_out).
        for status_marker, label_template in (
            ("crashed", "{kind} unstable on {target}"),
            ("timed_out", "{kind} frozen on {target}"),
        ):
            count = state.outcome_window.consecutive_status(
                target=target, kind=kind, status=status_marker
            )
            if count >= 3:
                attention_targets.append(
                    {
                        "kind": kind,
                        "target": target,
                        "trigger": status_marker,
                        "reason": "",
                        "count": count,
                        "message": label_template.format(kind=kind, target=target),
                    }
                )
                added = True
                break
        if added:
            continue
        # Same-reason apply_failed 3x trigger (label_conflict / cycle / ...).
        last_reasons = {r for s, r in dq if s == "apply_failed" and r}
        for reason in last_reasons:
            count = state.outcome_window.consecutive_apply_failed_reason(
                target=target, kind=kind, reason=reason
            )
            if count >= 3:
                attention_targets.append(
                    {
                        "kind": kind,
                        "target": target,
                        "trigger": "apply_failed",
                        "reason": reason,
                        "count": count,
                        "message": f"{kind} stuck on {target}: {count}× {reason}",
                    }
                )
                break
    repair_spinning = len(attention_targets)

    # Recent hash_mismatch count (§6.4.2). Counts apply_failed entries in
    # the sliding OutcomeWindow whose reason is hash_mismatch — a proxy
    # for "verifier verdicts that landed too late after a statement
    # change". Useful for the dashboard's "Current Scheduling State"
    # panel.
    recent_hash_mismatch = 0
    for dq in state.outcome_window._buf.values():
        for status, reason in dq:
            if status == "apply_failed" and reason == "hash_mismatch":
                recent_hash_mismatch += 1

    hb = CoordinatorHeartbeat(
        pid=os.getpid(),
        started_at=state.started_at,
        updated_at=utc_now_iso(),
        status=status,
        loop_seq=state.loop_seq,
        desired_pass_count=state.config.scheduling.desired_pass_count,
        codex_silent_timeout_seconds=state.config.scheduling.codex_silent_timeout_seconds,
        active_generator_jobs=active_gen,
        active_verifier_jobs=active_ver,
        dispatchable_generator_count=dispatchable_gen,
        dispatchable_verifier_count=dispatchable_ver,
        unfinished_node_count=unfinished,
        idle_reason_code=idle_reason_code,
        idle_reason_detail=idle_reason_detail,
        user_blocked_count=user_blocked,
        generation_blocked_on_dependency_count=gen_blocked,
        verification_dep_blocked_count=ver_blocked,
        repair_spinning_count=repair_spinning,
        recent_hash_mismatch_count=recent_hash_mismatch,
        attention_targets=attention_targets,
        children=_collect_children(state, lib_pid, lib_status),
    )
    write_heartbeat(state.ws.runtime_state / "coordinator.json", hb)


# ---------------------------------------------------------------------------
# KB read-only snapshot for dispatch
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _KBSnapshot:
    candidates: list[CandidateInput]


def _snapshot_kb(ws: WorkspacePaths) -> _KBSnapshot | None:
    """Open the KB read-only and pull the per-node dispatch context.

    Returns ``None`` when the KB doesn't exist yet (fresh workspace).
    """
    db_path = ws.dag_kz
    if not db_path.is_dir() and not db_path.exists():
        return _KBSnapshot(candidates=[])
    try:
        db = kuzu.Database(str(db_path), read_only=True)
        conn = kuzu.Connection(db)
    except Exception:
        return None
    try:
        # Pull every node + its DependsOn dep statement_hashes.
        res = conn.execute(
            """
            MATCH (n:Node)
            OPTIONAL MATCH (n)-[:DependsOn]->(d:Node)
            RETURN n.label, n.kind, n.statement, n.proof, n.statement_hash,
                   n.verification_hash, n.pass_count, n.repair_count,
                   n.repair_hint, n.verification_report,
                   collect(d.label), collect(d.statement_hash), collect(d.pass_count)
            """
        )
        out: list[CandidateInput] = []
        while res.has_next():
            row = res.get_next()
            dep_labels = row[10] or []
            dep_hashes = row[11] or []
            dep_counts = row[12] or []
            deps = {
                lbl: sh
                for lbl, sh in zip(dep_labels, dep_hashes)
                if lbl is not None
            }
            dep_pass_counts = {
                lbl: int(pc) if pc is not None else -1
                for lbl, pc in zip(dep_labels, dep_counts)
                if lbl is not None
            }
            out.append(
                CandidateInput(
                    target=row[0],
                    target_kind=row[1],
                    statement=row[2] or "",
                    proof=row[3] or "",
                    statement_hash=row[4] or "",
                    verification_hash=row[5] or "",
                    pass_count=int(row[6]) if row[6] is not None else -1,
                    repair_count=int(row[7]) if row[7] is not None else 0,
                    repair_hint=row[8] or "",
                    verification_report=row[9] or "",
                    dep_statement_hashes=deps,
                    dep_pass_counts=dep_pass_counts,
                    last_rejected_verification_hash=row[5] or "",
                )
            )
        return _KBSnapshot(candidates=out)
    finally:
        del conn
        del db


# ---------------------------------------------------------------------------
# Forward events to librarian
# ---------------------------------------------------------------------------
def _forward_new_events(state: CoordinatorState) -> None:
    try:
        pending = state.watcher.poll()
    except WatcherCorruption as exc:
        state.pending_corruption = True
        state.last_corruption_detail = f"{exc.detail} ({exc.path})"
        return
    for ev in pending:
        try:
            reply = state.librarian.request(
                {"cmd": "APPLY", "event_id": ev.event_id, "path": str(ev.path)},
                timeout=30.0,
            )
        except RuntimeError:
            return
        if reply is None:
            return
        state.watcher.ack(ev.path)
        if reply.get("reply") == "CORRUPTION":
            state.pending_corruption = True
            state.last_corruption_detail = reply.get("detail", "")
            return


# ---------------------------------------------------------------------------
# Dispatch a single worker job
# ---------------------------------------------------------------------------
def _dispatch_job(
    state: CoordinatorState,
    *,
    kind: str,
    mode: str,
    ctx: DispatchContext,
) -> None:
    """Write a job file and spawn the wrapper. Wrapper exits on its own;
    the AppliedEvent poller observes the publishing→applied transition.
    """
    from datetime import datetime, timezone
    import secrets

    now = datetime.now(tz=timezone.utc)
    iso_ms = now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}"
    uid = secrets.token_hex(8)
    job_id = make_job_id(kind, iso_ms=iso_ms, uid=uid)
    log_rel = f"runtime/logs/{job_id}.codex.log"

    rec = JobRecord(
        job_id=job_id,
        kind=kind,
        target=ctx.target,
        mode=mode,
        dispatch_hash=ctx.verification_hash,
        pid=0,  # patched after spawn
        pgid=0,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path=log_rel,
        target_kind=ctx.target_kind,
        statement=ctx.statement,
        proof=ctx.proof,
        dep_statement_hashes=ctx.dep_statement_hashes,
        verification_report=ctx.verification_report,
        repair_hint=ctx.repair_hint,
        repair_count=ctx.repair_count,
        h_rejected=ctx.h_rejected,
    )
    write_job_file(job_file_path(state.ws.runtime_jobs, job_id), rec)

    module = "generator.role" if kind == "generator" else "verifier.role"
    codex_argv = os.environ.get("RETHLAS_FAKE_CODEX_ARGV", "")
    timeout_s = state.config.scheduling.codex_silent_timeout_seconds
    # The wrapper's argparse takes ``job_id`` as the positional argument
    # and ``--codex-argv`` / ``--silent-timeout-s`` as optional flags.
    # spawn_wrapper appends job_id; we put the optional flags BEFORE so
    # the parser sees ``--codex-argv ARG ... JOB_ID``.
    wrapper_argv = [sys.executable, "-m", module]
    if codex_argv:
        wrapper_argv.extend(["--codex-argv", codex_argv])
    wrapper_argv.extend(["--silent-timeout-s", str(timeout_s)])

    proc = spawn_wrapper(
        workspace=state.ws.root,
        wrapper_argv=wrapper_argv,
        job_id=job_id,
    )
    state.in_flight_workers[job_id] = proc

    # Patch pid/pgid into job file now that we know them.
    rec.pid = proc.pid
    rec.pgid = proc.pid
    rec.updated_at = utc_now_iso()
    write_job_file(job_file_path(state.ws.runtime_jobs, job_id), rec)


# ---------------------------------------------------------------------------
# Reap finished workers
# ---------------------------------------------------------------------------
def _reap_finished_workers(state: CoordinatorState) -> None:
    """Reap dead wrapper subprocesses; record terminal outcomes (§6.7.1 step 4-5).

    For wrappers that exited without writing ``publishing`` (timeout or
    crash), the coordinator owns the terminal state-write per §6.7.1:

    - exit_code 124  -> ``status = "timed_out"`` (silent-timeout kill)
    - any other      -> ``status = "crashed"``

    The outcome is recorded into :class:`OutcomeWindow` so §7.4 / §7.5
    "3 consecutive" attention triggers can fire, and the file is
    deleted. Jobs in ``publishing`` are left alone — the AppliedEvent
    poller (:func:`reconcile_publishing_jobs`) handles those.
    """
    finished = [(jid, proc) for jid, proc in state.in_flight_workers.items() if proc.poll() is not None]
    for jid, proc in finished:
        del state.in_flight_workers[jid]
        path = job_file_path(state.ws.runtime_jobs, jid)
        rec = read_job_file(path)
        if rec is None:
            # File already gone (race with another reaper path).
            continue
        if rec.status == STATUS_PUBLISHING:
            # Successful publish in flight — applied_poller owns terminal.
            continue
        if rec.status in (STATUS_APPLIED, STATUS_APPLY_FAILED):
            # Already reconciled by applied_poller; just clean up.
            delete_job_file(path)
            continue
        # Wrapper either wrote ``crashed`` itself (decode error /
        # subprocess crash / silent timeout) or was killed mid-run.
        # Coordinator owns the canonical terminal-state write per
        # §6.7.1 step 4-5: exit_code 124 => ``timed_out``, otherwise
        # ``crashed``. We record the outcome regardless of what the
        # wrapper wrote — without this the §7.4 / §7.5 "3 consecutive"
        # attention triggers never fire on decode-error spirals or
        # silent-timeout loops.
        if proc.returncode == 124:
            terminal_status = STATUS_TIMED_OUT
            update_job_file(
                path,
                status=STATUS_TIMED_OUT,
                detail="codex silent-timeout (exit 124)",
            )
        else:
            terminal_status = STATUS_CRASHED
            if rec.status != STATUS_CRASHED:
                update_job_file(
                    path,
                    status=STATUS_CRASHED,
                    detail=f"wrapper exit={proc.returncode}",
                )
        state.outcome_window.record(
            target=rec.target,
            kind=rec.kind,
            status=terminal_status,
            reason=rec.reason or "",
        )
        delete_job_file(path)


# ---------------------------------------------------------------------------
# Wait for librarian to be ready
# ---------------------------------------------------------------------------
def _wait_for_librarian_ready(state: CoordinatorState, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not state.librarian.is_alive():
            return False
        hb = read_librarian_hb(state.ws.runtime_state / "librarian.json")
        if hb and hb.get("startup_phase") == PHASE_READY and not hb.get("rebuild_in_progress", False):
            return True
        # Update coordinator heartbeat with librarian_starting.
        _write_heartbeat(
            state,
            status=STATUS_IDLE,
            idle_reason_code=IDLE_LIBRARIAN_STARTING,
            idle_reason_detail="waiting for librarian startup",
        )
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Decide dispatch slate
# ---------------------------------------------------------------------------
def _decide_idle_reason(
    snapshot: _KBSnapshot | None,
    *,
    desired_pass_count: int,
    in_flight: int,
    dispatched_gen: int,
    dispatched_ver: int,
) -> tuple[str, str]:
    if snapshot is None:
        return IDLE_CORRUPTION, "KB read failed"
    nodes = snapshot.candidates
    if not nodes:
        return IDLE_ALL_DONE, "no nodes in workspace"
    unfinished = [c for c in nodes if c.pass_count < desired_pass_count]
    if dispatched_gen + dispatched_ver > 0:
        return IDLE_NONE, ""
    if in_flight > 0:
        return IDLE_IN_FLIGHT_ONLY, "waiting on in-flight workers"
    if not unfinished:
        return IDLE_ALL_DONE, ""
    # Why couldn't we dispatch? Prefer generator-blocked > verifier-blocked > user.
    gen_candidates = [c for c in nodes if c.pass_count == -1 and c.target_kind in {"lemma", "theorem", "proposition"}]
    if gen_candidates:
        not_ready = [c for c in gen_candidates if not c.deps_ready]
        if not_ready:
            return IDLE_GEN_DEP_BLOCKED, f"{len(not_ready)} generator candidates blocked on deps"
    ver_candidates = [c for c in nodes if 0 <= c.pass_count < desired_pass_count]
    if ver_candidates:
        ver_unfinished = [c for c in ver_candidates if not c.verifier_deps_strictly_ahead]
        if any(not c.deps_ready for c in ver_unfinished):
            return IDLE_VER_DEP_BLOCKED, "verifier candidates blocked on deps"
        if ver_unfinished:
            return IDLE_VER_DEP_BLOCKED, "verifier candidates blocked on strict monotone deps"
    return IDLE_USER_BLOCKED, "remaining work needs user action"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def _setup_supervise_logging(ws: WorkspacePaths) -> None:
    """Attach a file handler so ``rethlas.*`` logs land in supervise.log.

    Without this the dashboard supervisor's log.info/warning/error calls
    (and any other library-level rethlas logger output) fall on the
    floor. We mirror dashboard.cli._setup_logging for consistency.
    """
    import logging

    ws.runtime_logs.mkdir(parents=True, exist_ok=True)
    log_file = ws.runtime_logs / "supervise.log"
    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    import time as _time
    handler.formatter.converter = _time.gmtime  # type: ignore[attr-defined]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(
        getattr(h, "baseFilename", None) == handler.baseFilename
        for h in root.handlers
    ):
        root.addHandler(handler)


def run_supervise(workspace: str | None) -> int:
    ws = workspace_paths(workspace)
    if not (ws.events.is_dir() and ws.rethlas_toml.is_file()):
        sys.stderr.write(f"workspace not initialized at {ws.root}\n")
        return 2

    config = load_config(ws.rethlas_toml)
    _setup_supervise_logging(ws)
    cleanup_runtime(ws)

    try:
        lock = SuperviseLock(ws.runtime_locks)
        lock.acquire()
    except SuperviseLockError as exc:
        sys.stderr.write(f"supervise: {exc}\n")
        return 2

    librarian = spawn_librarian(ws.root)
    watcher = EventsWatcher(ws.events)
    state = CoordinatorState(
        ws=ws,
        config=config,
        librarian=librarian,
        watcher=watcher,
        started_at=utc_now_iso(),
    )

    try:
        if not _wait_for_librarian_ready(state, _librarian_ready_timeout_s()):
            sys.stderr.write("supervise: librarian failed to reach ready phase\n")
            librarian.shutdown()
            return 3

        # PHASE1 M9 — coordinator-managed dashboard child.
        # Disabled for tiny supervise tests via env var so they don't
        # need to bind a real port.
        if not os.environ.get("RETHLAS_COORDINATOR_DASHBOARD_DISABLED"):
            state.dashboard = _make_dashboard_supervisor(state)
            state.dashboard.start()

        _write_heartbeat(state, status=STATUS_RUNNING)

        _install_signal_handlers(state)

        tick_s = _tick_interval_s()
        max_ticks_env = os.environ.get("RETHLAS_COORDINATOR_MAX_TICKS")
        max_ticks = int(max_ticks_env) if max_ticks_env else 0  # 0 = run until signal

        while not state.stopping:
            state.loop_seq += 1
            try:
                _tick(state)
            except _LibrarianFatal as exc:
                # §6.4: librarian crashed twice within 3 min. Coordinator
                # exits 3; operator inspects log and either reruns or
                # rebuilds.
                _log_supervise(state, f"librarian fatal: {exc}")
                _write_heartbeat(
                    state,
                    status=STATUS_DEGRADED,
                    idle_reason_code=IDLE_LIBRARIAN_STARTING,
                    idle_reason_detail=str(exc),
                )
                if state.dashboard is not None:
                    state.dashboard.shutdown()
                return 3
            if max_ticks and state.loop_seq >= max_ticks:
                state.stopping = True
            else:
                time.sleep(tick_s)

        _shutdown(state)
        return 0
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _make_dashboard_supervisor(state: "CoordinatorState") -> DashboardSupervisor:
    """Construct the supervisor with overrides honoured for tests."""
    kwargs: dict[str, Any] = {}
    for env_key, kw in (
        ("RETHLAS_DASHBOARD_STARTUP_GRACE_S", "startup_grace_s"),
        ("RETHLAS_DASHBOARD_HEARTBEAT_STALE_S", "heartbeat_stale_s"),
        ("RETHLAS_DASHBOARD_RESTART_BACKOFF_S", "restart_backoff_s"),
    ):
        raw = os.environ.get(env_key)
        if raw:
            try:
                kwargs[kw] = float(raw)
            except ValueError:
                pass
    raw_max = os.environ.get("RETHLAS_DASHBOARD_MAX_RESTARTS")
    if raw_max:
        try:
            kwargs["max_restarts"] = int(raw_max)
        except ValueError:
            pass
    return DashboardSupervisor(
        ws_root=state.ws.root,
        bind=state.config.dashboard.bind,
        **kwargs,
    )


_LIBRARIAN_RECOVERY_WINDOW_S = 180.0  # ARCHITECTURE §6.4 — within 3 min ⇒ fatal


class _LibrarianFatal(Exception):
    """Raised by ``_recover_librarian_if_needed`` when the librarian
    crashed twice within :data:`_LIBRARIAN_RECOVERY_WINDOW_S`.
    Caught at the supervise loop boundary, where coordinator exits 3.
    """


def _recover_librarian_if_needed(state: "CoordinatorState") -> None:
    """ARCHITECTURE §6.4 librarian restart-once policy.

    If the librarian subprocess has died, attempt to restart it once.
    A second crash within :data:`_LIBRARIAN_RECOVERY_WINDOW_S` raises
    :class:`_LibrarianFatal` so the supervise loop can exit code 3
    (workspace unusable without librarian).
    """
    if state.librarian.is_alive():
        return
    now = time.monotonic()
    if state.last_librarian_restart_monotonic and (
        now - state.last_librarian_restart_monotonic < _LIBRARIAN_RECOVERY_WINDOW_S
    ):
        raise _LibrarianFatal(
            "librarian crashed twice within "
            f"{_LIBRARIAN_RECOVERY_WINDOW_S:.0f}s"
        )
    _log_supervise(state, "librarian crashed; restarting (§6.4)")
    state.librarian.close_handles()
    state.librarian = spawn_librarian(state.ws.root)
    state.last_librarian_restart_monotonic = now
    if not _wait_for_librarian_ready(state, _librarian_ready_timeout_s()):
        raise _LibrarianFatal("librarian failed to reach ready phase after restart")


def _tick(state: CoordinatorState) -> None:
    """One coordinator tick."""
    _recover_librarian_if_needed(state)
    if state.dashboard is not None:
        state.dashboard.tick()
    if state.pending_corruption:
        _write_heartbeat(
            state,
            status=STATUS_DEGRADED,
            idle_reason_code=IDLE_CORRUPTION,
            idle_reason_detail=state.last_corruption_detail or "librarian reported corruption",
        )
        return
    _forward_new_events(state)
    _reap_finished_workers(state)
    outcomes = reconcile_publishing_jobs(state.ws.runtime_jobs, state.ws.dag_kz)
    for o in outcomes:
        state.outcome_window.record(
            target=o.target, kind=o.kind, status=o.status, reason=o.reason
        )
    reap_orphans(state.ws.runtime_jobs)

    if state.pending_corruption:
        _write_heartbeat(
            state,
            status=STATUS_DEGRADED,
            idle_reason_code=IDLE_CORRUPTION,
            idle_reason_detail=state.last_corruption_detail or "librarian reported corruption",
        )
        return

    snapshot = _snapshot_kb(state.ws)
    if snapshot is None:
        _write_heartbeat(
            state,
            status=STATUS_DEGRADED,
            idle_reason_code=IDLE_CORRUPTION,
            idle_reason_detail="KB read failed",
        )
        return

    # Build candidate sets.
    in_flight_targets = set()
    for rec in list_jobs(state.ws.runtime_jobs):
        if rec.status not in {"applied", "apply_failed"}:
            in_flight_targets.add(rec.target)

    gen_pool = [
        GeneratorCandidate(label=c.target)
        for c in snapshot.candidates
        if c.pass_count == -1
        and c.target_kind in {"lemma", "theorem", "proposition"}
        and c.deps_ready
    ]
    ver_pool = [
        VerifierCandidate(label=c.target, pass_count=c.pass_count)
        for c in snapshot.candidates
        if 0 <= c.pass_count < state.config.scheduling.desired_pass_count
        and c.deps_ready
        and c.verifier_deps_strictly_ahead
    ]

    gen_capacity = max(
        0,
        state.config.scheduling.generator_workers
        - sum(1 for jid in state.in_flight_workers if jid.startswith("gen-")),
    )
    ver_capacity = max(
        0,
        state.config.scheduling.verifier_workers
        - sum(1 for jid in state.in_flight_workers if jid.startswith("ver-")),
    )
    gen_targets = select_generator_targets(
        gen_pool, capacity=gen_capacity, in_flight_targets=in_flight_targets
    )
    ver_targets = select_verifier_targets(
        ver_pool, capacity=ver_capacity, in_flight_targets=in_flight_targets
    )

    by_label = {c.target: c for c in snapshot.candidates}
    dispatched_gen = 0
    dispatched_ver = 0
    user_blocked = sum(
        1
        for c in snapshot.candidates
        if c.pass_count == -1 and c.target_kind in {"definition", "external_theorem"}
    )
    gen_blocked = sum(
        1
        for c in snapshot.candidates
        if c.pass_count == -1
        and c.target_kind in {"lemma", "theorem", "proposition"}
        and not c.deps_ready
    )
    ver_blocked = sum(
        1
        for c in snapshot.candidates
        if 0 <= c.pass_count < state.config.scheduling.desired_pass_count
        and (not c.deps_ready or not c.verifier_deps_strictly_ahead)
    )

    for lbl in gen_targets:
        cand = by_label[lbl]
        ctx, fail = precheck_generator(cand, in_flight_targets=in_flight_targets)
        if fail is not None:
            _log_supervise(state, "generator precheck failed: %s -> %s: %s" % (
                fail.target, fail.reason, fail.detail
            ))
            continue
        mode = "fresh" if cand.repair_count == 0 else "repair"
        _dispatch_job(state, kind="generator", mode=mode, ctx=ctx)
        in_flight_targets.add(lbl)
        dispatched_gen += 1

    for lbl in ver_targets:
        cand = by_label[lbl]
        ctx, fail = precheck_verifier(cand, in_flight_targets=in_flight_targets)
        if fail is not None:
            _log_supervise(state, "verifier precheck failed: %s -> %s: %s" % (
                fail.target, fail.reason, fail.detail
            ))
            continue
        _dispatch_job(state, kind="verifier", mode="single", ctx=ctx)
        in_flight_targets.add(lbl)
        dispatched_ver += 1

    in_flight = len(state.in_flight_workers)
    code, detail = _decide_idle_reason(
        snapshot,
        desired_pass_count=state.config.scheduling.desired_pass_count,
        in_flight=in_flight,
        dispatched_gen=dispatched_gen,
        dispatched_ver=dispatched_ver,
    )
    status = STATUS_RUNNING if (code == IDLE_NONE or in_flight or dispatched_gen or dispatched_ver) else STATUS_IDLE
    _write_heartbeat(
        state,
        status=status,
        idle_reason_code=code,
        idle_reason_detail=detail,
        dispatchable_gen=len(gen_pool),
        dispatchable_ver=len(ver_pool),
        unfinished=sum(1 for c in snapshot.candidates if c.pass_count < state.config.scheduling.desired_pass_count),
        user_blocked=user_blocked,
        gen_blocked=gen_blocked,
        ver_blocked=ver_blocked,
    )


def _log_supervise(state: CoordinatorState, message: str) -> None:
    state.ws.runtime_logs.mkdir(parents=True, exist_ok=True)
    log = state.ws.runtime_logs / "supervise.log"
    line = f"{utc_now_iso()} {message}\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _install_signal_handlers(state: CoordinatorState) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        state.stopping = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _shutdown(state: CoordinatorState) -> None:
    _write_heartbeat(state, status=STATUS_STOPPING)
    # PHASE1 M9: graceful shutdown order — dashboard first (read-only,
    # fast to terminate), then drain workers, then librarian last so
    # any final apply on workers' truth events still has a writer.
    if state.dashboard is not None:
        # ARCHITECTURE §6.4: graceful shutdown cascade allows up to 10s
        # per child stage before SIGKILL.
        state.dashboard.shutdown(timeout=10.0)
    # Stop new dispatches, drain workers (best-effort: just wait briefly).
    deadline = time.monotonic() + 10.0
    while state.in_flight_workers and time.monotonic() < deadline:
        _reap_finished_workers(state)
        time.sleep(0.2)
    for proc in state.in_flight_workers.values():
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    state.in_flight_workers.clear()
    state.librarian.shutdown(timeout=10.0)
    _write_heartbeat(state, status=STATUS_STOPPING)


__all__ = ["run_supervise"]
