# Phase I Implementation Plan

**Status.** Draft aligned with the frozen `ARCHITECTURE.md`.

This file is the execution plan for Phase I. It is intentionally more
implementation-oriented than the architecture document:

- milestones are ordered by dependency;
- every milestone has explicit tests;
- milestone exits are hard gates, not vague progress markers.

If this file and `ARCHITECTURE.md` diverge, fix one of them immediately.

---

## Goal

A minimum viable Rethlas that can:

- initialize a workspace;
- publish user truth events through CLI;
- project `events/` into `dag.kz`, `AppliedEvent`, and `nodes/`;
- run generator and verifier workers under coordinator supervision;
- expose a read-only dashboard;
- rebuild state from truth after crash or clone;
- lint the workspace against Phase I invariants.

**Explicitly out of scope for Phase I:**

- Claude / multi-backend consensus
- semantic embedding search
- Cytoscape / interactive DAG UI
- Blueprint LaTeX export
- importer for external libraries
- inducedorbit data migration
- multi-machine concurrency
- clock-skew detection

---

## Delivery Principle

Phase I is correctness-first. No downstream milestone may rely on a component
that lacks its own tests.

Test layers used throughout:

- `unit`: pure functions, schema validation, small state transitions
- `integration`: filesystem + Kuzu + subprocess boundaries inside one process tree
- `system`: full workspace runs through CLI entry points
- `fault-injection`: crash / timeout / restart / stale-state scenarios

Recommended test layout:

```text
tests/
├── unit/
├── integration/
├── system/
└── fixtures/
```

Recommended helper infrastructure:

- fake clock / deterministic timestamp helper
- fake Codex runner that returns scripted stdout/stderr/log behavior
- temporary workspace fixture
- temporary Kuzu workspace fixture
- helper to write event files with exact byte content for `event_sha256` tests

---

## Milestones

## M0 — Repo And Package Scaffold

**Deliverables**

- Move `agents/generation` → `generator`
- Move `agents/verification` → `verifier`
- Move `agents/common` → `common` if still present
- Create top-level packages:
  - `coordinator/`
  - `librarian/`
  - `dashboard/`
  - `linter/`
  - `cli/`
- Add `pyproject.toml` with `rethlas` entry point
- Add root `producers.toml`

**Tests**

- `unit`: import smoke for all top-level packages
- `system`: `rethlas --help` shows the expected subcommands

**Exit**

- Editable install works
- CLI binary resolves

---

## M1 — Core Model, Config, Event IO

**Deliverables**

- `common/config/`
  - parse full `rethlas.toml`
  - validate bounds for:
    - `desired_pass_count`
    - `generator_workers`
    - `verifier_workers`
    - `codex_silent_timeout_seconds`
    - dashboard bind
- `common/events/`
  - filename parse/format
  - `.json` truth event parse/format
  - event-id allocation
  - atomic write helper (`.tmp` + rename)
  - event byte hashing helper for `AppliedEvent.event_sha256`
- `common/kb/types.py`
  - `Node`
  - `Event`
  - `AppliedEvent`
  - `ApplyOutcome`
  - runtime dataclasses where useful
- `common/kb/hashing.py`
  - deterministic `statement_hash`
  - deterministic `verification_hash`

**Tests**

- `unit`: config parses valid file and rejects invalid values
- `unit`: event filename/body round-trip
- `unit`: event-id allocation keeps producer-local monotonicity when wall clock repeats or steps backward
- `unit`: `statement_hash` / `verification_hash` are stable across key order and newline normalization
- `unit`: raw-byte `event_sha256` helper matches exact file bytes

**Exit**

- All pure model/config/hash/event helpers are tested and stable

**Checkpoint A**

- Truth serialization and hash identity are frozen enough that Kuzu projection can be built on top without churn

---

## M2 — Kuzu Backend And Projection Semantics

**Deliverables**

- `common/kb/interface.py`
  - read API for nodes / edges / closures
  - `repair_count(label)`
  - `AppliedEvent` queries
- `common/kb/kuzu_backend.py`
  - schema init for:
    - `Node`
    - `DependsOn`
    - `ProjectionState`
    - `AppliedEvent` (including `detail` and `event_sha256`)
  - transactional apply path
  - rebuild path
- `librarian/projector.py`
  - all `pass_count` semantics
  - all `repair_count` semantics
  - Merkle propagation
  - `repair_hint` overwrite/append/clear rules
- `librarian/validator.py`
  - structural checks
  - semantic checks
  - `apply_failed(reason, detail)` mapping

**Tests**

- `integration`: schema init creates all required tables/columns
- `integration`: `AppliedEvent` and KB mutations commit/rollback together
- `integration`: replaying the same event twice is idempotent
- `integration`: replay determinism under restart boundaries
  - apply event stream in one pass
  - apply the same stream split across multiple restarts
  - final `Node`, `DependsOn`, `AppliedEvent`, and rendered `nodes/`
    outputs must be identical
- `integration`: `pass_count` transitions for:
  - user add/revise
  - generator batch
  - verifier accepted
  - verifier gap/critical
  - verifier hash mismatch
- `integration`: `repair_count` transitions for:
  - repeated wrong verdicts on same statement
  - proof-only rewrite
  - statement rewrite
  - upstream statement change causing cascade reset
- `integration`: `repair_hint` behavior:
  - verifier section overwrite
  - user section append
  - hash-changing rewrite clears hint/report
- `integration`: `apply_failed` reasons:
  - `label_conflict`
  - `cycle`
  - `ref_missing`
  - `hint_target_missing`
  - `hint_target_unreachable`
  - `hash_mismatch`
  - `kind_mutation`
  - `self_reference`
- `integration`: `AppliedEvent.detail` and `event_sha256` populated correctly

**Exit**

- `KB = f(events/)` works deterministically for all Phase I event kinds

**Checkpoint B**

- Truth → projection semantics are stable and audited enough for daemonization

---

## M3 — Workspace CLI And User Publish Path

**Deliverables**

- `cli/main.py`
- `cli/init.py`
- user publishing commands:
  - `rethlas add-node`
  - `rethlas revise-node`
  - `rethlas attach-hint`
- `cli/rebuild.py`
  - writes `runtime/state/rebuild_in_progress.flag` before the
    destructive step (delete `knowledge_base/`), deletes the flag on
    successful completion; on crash the flag lingers so that the
    next supervise's librarian can detect and force-rerun rebuild
    (ARCHITECTURE §6.5 / §11.2)
- workspace `.gitignore`
- annotated `rethlas.toml` template

**Tests**

- `system`: `rethlas init` on empty workspace creates expected tree
- `system`: `rethlas init --force` overwrites config only, never `events/`
- `system`: user publish CLI writes canonical event file
- `integration`: publish CLI polls `AppliedEvent` and reports:
  - `applied`
  - `apply_failed(reason)`
  - timeout while supervise not running
  - timeout while supervise running but librarian behind
- `system`: `rethlas rebuild` refuses while `supervise.lock` is held
- `system`: `rethlas rebuild` takes lock when running standalone
- `fault-injection`: `rethlas rebuild` killed after it writes the flag
  but before deleting it — the flag persists on disk; pairs with the
  M4 test below that catches the flag on next startup and re-runs
  rebuild before accepting normal work

**Exit**

- Workspace lifecycle and user truth publication work end-to-end through CLI

---

## M4 — Librarian Daemon, Replay, Reconciliation, Rebuild

**Deliverables**

- `librarian/main.py`
  - event watcher
  - ordered replay
  - startup replay
  - startup `nodes/` reconciliation
  - `librarian.json`
- `librarian/renderer.py`
- `librarian/cli.py`
- rebuild flag handling:
  - `runtime/state/rebuild_in_progress.flag`
  - startup forced rebuild path after interrupted rebuild

**Tests**

- `unit`: `renderer.render(node)` produces **byte-identical** output
  across repeated invocations on the same `Node` — stable YAML key
  order, `depends_on` ASCII-sorted, Unix `\n` line endings, UTF-8
  NFC normalization, trailing newline (ARCHITECTURE §4.2 rendering
  contract). Without this, startup reconciliation + linter E would
  flap every run.
- `integration`: startup replay processes unseen events and skips already-decided ones
- `integration`: crash window after Kuzu commit but before render is healed by startup reconciliation
- `integration`: orphan `nodes/*.md` files are deleted by reconciliation
- `integration`: `librarian.json.startup_phase` transitions:
  - `replaying`
  - `reconciling`
  - `ready`
- `integration`: `projection_backlog` is correct
- `fault-injection`: interrupted rebuild leaves flag; next supervise-started librarian forces clean rebuild path
- `system`: Kuzu-dependent dashboard endpoints must become 503 while `rebuild_in_progress`

**Exit**

- Librarian can recover projection state from truth and repair stale `nodes/`

**Checkpoint C**

- Recoverability from truth is real, not just conceptual

---

## M5 — Runtime Substrate And Job Lifecycle

**Deliverables**

- `common/runtime/codex_runner.py`
- runtime job schema helpers
- runtime log helpers
- wrapper heartbeat updater
- timeout handling
- orphan reaper helpers
- sliding-window outcome tracking helpers

**Tests**

- `integration`: wrapper heartbeat updates `runtime/jobs/{job_id}.json.updated_at` every 60 s
- `integration`: log mtime timeout marks `timed_out`
- `integration`: orphaned job file is detected and cleaned
- `integration`: terminal statuses write then delete job files in the documented order
- `integration`: startup runtime cleanup removes stale job files and stale coordinator/librarian snapshots but preserves JSONL histories and logs
- `integration`: spawning a fake wrapper via the runtime helper passes
  `RETHLAS_WORKSPACE=<abs path>` env var and the `job_id` positional
  argument; any other env vars present at coordinator spawn time
  (e.g. a stubbed `OPENAI_API_KEY`) flow through unchanged to the
  wrapper process (ARCHITECTURE §6.7.1 job lifecycle step 1)
- `unit`: consecutive outcome window logic for:
  - `crashed`
  - `timed_out`
  - repeated same-reason `apply_failed`

**Exit**

- Runtime observability and cleanup semantics are stable enough for real workers

---

## M6 — Generator Worker

**Deliverables**

- `generator/role.py`
- updated `generator/AGENTS.md`
- updated `generator/.codex/config.toml`
- updated `generator/mcp/server.py` to exact Phase I toolset
- decoder with full batch validation

**Tests**

- `integration`: fake Codex output with valid `<node>` blocks produces exactly one `generator.batch_committed`
- `integration`: decoder rejects:
  - malformed block
  - wrong prefix/kind pairing
  - existing non-target label
  - unresolved `\ref{}`
  - repair-no-op hash
- `integration`: batch-internal topological hashing works
- `integration`: staged publish is atomic
- `integration`: wrapper pre-dispatch validation returns `precheck_failed` without calling Codex when conditions drift
- `integration`: decoder rejections append to `runtime/state/rejected_writes.jsonl`
- `system`: `rethlas generator --target ... --mode fresh|repair` works in a temp workspace with fake Codex

**Exit**

- Generator worker is deterministic around decode/publish/runtime bookkeeping

---

## M7 — Verifier Worker

**Deliverables**

- `verifier/role.py`
- updated `verifier/AGENTS.md`
- pruned verifier MCP usage per architecture

**Tests**

- `integration`: valid verdict JSON produces `verifier.run_completed`
- `integration`: malformed verdict JSON becomes `status = "crashed"` and no truth event
- `integration`: pre-dispatch validation rejects stale/invalid dispatches with `precheck_failed`
- `integration`: wrapper mirrors `AppliedEvent` outcome into runtime job file
- `system`: `rethlas verifier --target ...` works with fake Codex

**Exit**

- Verifier worker obeys the runtime/job contract and truth contract

---

## M8 — Coordinator / Supervise

**Deliverables**

- merged coordinator+supervisor implementation
- worker-pool dispatch loop
  - dispatch is vacancy-driven (pool / pull model), not a persistent queue
- `coordinator.json`
- child daemon management
- startup cleanup
- startup dispatch gate

**Tests**

- `integration`: second `rethlas supervise` in same workspace fails on `supervise.lock`
- `integration`: startup dispatch gate suppresses workers until librarian reports `startup_phase = ready` and `rebuild_in_progress = false`
- `integration`: no concurrent same-target dispatch across both pools
- `integration`: two independent pools dispatch up to:
  - `generator_workers`
  - `verifier_workers`
- `integration`: `idle_reason_code` transitions cover:
  - `all_done`
  - `user_blocked`
  - `generation_dep_blocked`
  - `verification_dep_blocked`
  - `in_flight_only`
  - `corruption_or_drift`
  - `librarian_starting`
- `integration`: child restart policy
  - librarian restart once then coordinator exits on rapid re-crash
  - dashboard restart three times then degrade
- `integration`: child startup grace period does not mark a just-spawned but
  still-initializing librarian/dashboard as down before the grace window expires
- `integration`: startup cleanup removes zombie runtime state from prior crash
- `integration`: consecutive `crashed`, `timed_out`, and same-reason `apply_failed` counters flow into Human Attention state
- `system`: `rethlas supervise` can run a tiny workspace to steady state

**Exit**

- Coordinator is a correct singleton parent and pool-based dispatcher

**Checkpoint D**

- All long-running process and lock semantics are proven before dashboard depends on them

---

## M9 — Dashboard

**Deliverables**

- `dashboard/server.py`
- `dashboard/state_watcher.py`
- `dashboard/kuzu_reader.py`
- `dashboard/templates/`
- `cli/dashboard.py`

**Tests**

- `integration`: `/api/coordinator` returns raw `coordinator.json`
- `integration`: `/api/overview` joins runtime state + Kuzu correctly
- `integration`: `/api/theorems` status vocabulary covers:
  - `done`
  - `verified`
  - `needs_verification`
  - `blocked_on_dependency`
  - `needs_generation`
  - `generation_blocked_on_dependency`
  - `user_blocked`
  - `in_flight`
- `integration`: `/api/rejected` merges:
  - `rejected_writes.jsonl`
  - `AppliedEvent(status=apply_failed)`
  - `drift_alerts.jsonl`
- `integration`: golden JSON fixtures for:
  - `/api/overview`
  - `/api/theorems`
  - `/api/node/{label}`
  on representative fixture workspaces; outputs are reviewed and
  snapshotted to catch accidental semantics drift
- `integration`: `/api/events` reverse-chronological query strategy works
- `integration`: SSE emits envelopes for:
  - event file change
  - job change
  - state change
  - applied-event change
- `integration`: Kuzu-dependent endpoints return 503 + `Retry-After: 5` during rebuild
- `system`: standalone `rethlas dashboard` refuses when supervise lock is held

**Exit**

- Dashboard is a correct read-only observability layer, not a scheduler shadow

---

## M10 — Linter

**Deliverables**

- `linter/checks.py`
- `linter/main.py`
- `cli/linter.py`

**Tests**

- `integration`: categories A-F each fail on targeted bad fixtures
- `integration`: `--repair-nodes` fixes only category E issues
- `integration`: linter refuses with live `supervise.lock` unless `--allow-concurrent`
- `system`: JSON report is written and exit code is non-zero on violations

**Exit**

- Linter can independently detect projection drift, rendering drift, and event inventory drift

---

## M11 — System Validation And Fault Matrix

**Deliverables**

- scripted system tests for end-to-end correctness
- scripted fault-injection tests for restart/recovery

**Required scenarios**

1. Fresh workspace:
   - add definition
   - add theorem with empty proof
   - supervise drives theorem to `DESIRED_COUNT`
2. User-supplied proof path:
   - add theorem with proof
   - verifier path starts at `pass_count = 0`
3. Wrong verdict path:
   - theorem goes to `-1`
   - generator repairs
   - verifier rechecks
4. Upstream statement change:
   - dependent `verification_hash` changes
   - dependent `pass_count` resets per spec
   - dependent `repair_count` resets
5. Generator structural failure:
   - repeated same-reason `apply_failed`
   - dashboard attention entry appears
6. Timeout/crash path:
   - repeated `timed_out`
   - repeated `crashed`
   - dashboard attention entry appears
7. Restart path:
   - kill supervise
   - restart
   - stale runtime cleaned
8. Rebuild crash path:
   - simulate interrupted rebuild after flag written
   - next supervise forces rebuild before normal work
9. `nodes/` crash window:
   - simulate Kuzu commit without render
   - next startup reconciliation repairs files
10. Inventory drift path:
   - mutate event file after apply
   - linter category F catches mismatch
11. Replay determinism path:
   - same fixture event stream replayed through multiple restart cuts
   - final Kuzu state + `AppliedEvent` + `nodes/` bytes are identical
12. Dashboard golden path:
   - fixture workspace with active jobs, apply_failed rows, and mixed
     theorem states
   - `/api/overview`, `/api/theorems`, `/api/node/{label}` match
     committed golden snapshots
13. Cross-generator label race:
   - two generator workers dispatched concurrently on distinct
     proof-requiring targets, both inventing the **same** brand-new
     auxiliary label
   - first-to-apply wins; second gets
     `apply_failed(reason=label_conflict)` with `detail` naming the
     conflicting label and the winning event_id
   - repeat with fresh dispatches three times; after the 3rd
     consecutive `apply_failed(label_conflict)` on the losing
     target, a Human Attention entry appears labelled
     `"<kind> stuck on <label>: 3× label_conflict"` (ARCHITECTURE
     §6.7, §7.5 consecutive-outcome rule extended to same-reason
     apply_failed)

**Exit**

- Full system test suite passes on CI
- Fault matrix demonstrates no correctness loss under crash/restart scenarios

---

## Test Gate Summary

Phase I is not done until all of the following are green:

- unit tests
- integration tests
- system tests
- fault-injection tests
- linter on a clean fixture workspace

Minimum CI stages:

1. `pytest tests/unit`
2. `pytest tests/integration`
3. `pytest tests/system`
4. `pytest tests/system -k fault`
5. `pytest tests/integration -k golden`
6. `rethlas linter` against a golden clean fixture

---

## Phase I Done Criteria

- [ ] `rethlas init` creates a valid workspace
- [ ] user CLI publishes truth events and reports `applied` / `apply_failed`
- [ ] librarian projects truth into `dag.kz`, `AppliedEvent`, and `nodes/`
- [ ] coordinator runs as workspace singleton under `supervise.lock`
- [ ] generator and verifier worker pools obey configured capacities
- [ ] dashboard exposes all Phase I read-only endpoints
- [ ] linter categories A-F are implemented
- [ ] `rethlas rebuild` reconstructs projection state from truth
- [ ] system/fault matrix passes in CI

---

## Notes

- No dedicated skill in the current skill list cleanly covers “write a
  software implementation plan from an architecture doc”, so this plan is
  produced directly.
- If architecture changes again, update this file immediately before any
  implementation starts drifting.
