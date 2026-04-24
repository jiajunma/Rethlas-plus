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

**Marker policy.** Non-default test slices are selected by explicit pytest
markers registered in `pyproject.toml`:

- `@pytest.mark.fault` for crash / timeout / recovery scenarios
- `@pytest.mark.golden` for snapshot / golden-output tests

CI selects these via `-m`, not `-k`; tests are **never filtered by name
substring** (`-k fault`, `-k golden`) since that misses tests whose names do
not happen to contain those words.

Test layout:

```text
tests/
├── unit/
├── integration/       # includes fault-marked integration tests
├── system/            # includes fault-marked system tests
└── fixtures/          # shared helper modules (delivered by M1 / M5)
```

Recommended helper infrastructure:

- fake clock / deterministic timestamp helper
- fake Codex runner that returns scripted stdout/stderr/log behavior
- temporary workspace fixture
- temporary Kuzu workspace fixture
- helper to write event files with exact byte content for `event_sha256` tests

### Test time scaling

Every long-running Phase I daemon (coordinator, librarian, dashboard)
and every worker wrapper honors a single environment variable
**`RETHLAS_TEST_TIME_SCALE`** (float, default `1.0`). Any internal
`sleep` / polling interval / timeout threshold that comes from the
spec — 30-s startup grace, 30-s librarian idle heartbeat, 60-s
wrapper heartbeat, `codex_silent_timeout_seconds` (default 1800),
5-min orphan-reaper threshold, dashboard 5-s retry window, etc. —
is multiplied by this scale at runtime.

With `RETHLAS_TEST_TIME_SCALE=0.01` an end-to-end scenario that
takes ~30 min of wall time in production (xhigh-reasoning silent
window, consecutive timeouts, restart cascades) runs in ~18 s on
CI. Without this hook, Phase I's test suite would take hours.

The scale affects **timing only**, never semantics. Fake-Codex
`silent_seconds` reads this env too, and additionally back-dates
the log file's mtime via `os.utime` so the log-mtime timeout path
(`codex_silent_timeout_seconds`) is exercised faithfully under
scaling (a raw `time.sleep(0.3)` does not move `os.stat().st_mtime`,
so the fake must forge it). Every daemon's scaled-timing helper
lives in `common/runtime/timing.py` so the implementation is
centralized.

### Pre-M2 Kuzu stress validation

Rethlas uses **multiple OS processes** (librarian, coordinator,
dashboard, linter, worker wrappers) all accessing the same
`dag.kz/`. That is a stronger requirement than most embedded DBs'
defaults: SQLite handles it via WAL / shm files; DuckDB historically
single-process; LMDB natively multi-reader-single-writer; Kuzu's
multi-process guarantees are not obvious from the docs. Before
committing to the Phase I design, validate Kuzu in **two steps**,
in this order:

**Step 1 — multi-process open.** Two independent Python processes
both open the same `knowledge_base/dag.kz/` and each run a simple
query loop. If this fails (lock errors, "database in use",
segfault), the ARCHITECTURE §4.1 "sole writer: librarian; multiple
readers" model is not achievable directly and Phase I must revise
the storage layer — likely route all reads through librarian via
a small IPC (Unix socket JSON-RPC) rather than direct Kuzu open.
This is a significant design change; knowing at M2 day-1 is
cheap, finding out at M6 is not.

**Step 2 — snapshot isolation (only if Step 1 passes).** One
writer loop + several reader loops hammering `Node` / `AppliedEvent`
queries for ~10⁴ iterations. Readers must never observe a
partially-committed transaction (Node's `pass_count` updated but
not yet `AppliedEvent` row, etc.). If this fails, readers could
see logically-impossible states mid-commit.

  - Cross-process lock is not a simple fix here: an advisory
    `flock` around every Kuzu operation would serialize all DB
    access across processes, killing dashboard responsiveness.
    The cleaner Phase I fallback is the same as Step 1's — a
    librarian-mediated read API — but narrower in scope, only for
    the operations that actually need serialization.

- If Step 1 and Step 2 both pass, proceed with the architecture
  as written.
- If either fails, **stop and revise** before building projector /
  coordinator / dashboard on top.

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
- Create `common/` subpackage skeleton (empty modules with
  `__init__.py`) so M1 can populate them without introducing new
  top-level structure:
  - `common/config/`
  - `common/events/`
  - `common/kb/`
  - `common/runtime/`
- Stub `cli/main.py` with an `argparse`-based dispatcher that at least
  recognises every Phase I subcommand (`init`, `add-node`,
  `revise-node`, `attach-hint`, `supervise`, `dashboard`, `linter`,
  `rebuild`, `generator`, `verifier`) and prints a placeholder
  message + exit 0 for each (real implementations arrive in later
  milestones). This lets `rethlas --help` work from M0 onward.
- Add `pyproject.toml` with:
  - `rethlas` entry point pointing at `cli.main:main`
  - **`include-package-data = true`** (setuptools) and the explicit
    inclusion of `producers.toml` via `MANIFEST.in` or the
    `[tool.setuptools.package-data]` table, so that a `pip install`
    of a wheel also ships the producer registry alongside the code.
    A V4-style test (admission resolves `producers.toml` from the
    installation root) must therefore be run against **both** an
    editable install and a built wheel in CI, to catch the common
    bug where `producers.toml` is only present in the source tree
    and missing after `pip install`.
- Add root `producers.toml`

**Tests**

- `unit`: import smoke for all top-level packages **and** all
  `common/` subpackages
- `system`: `rethlas --help` lists every expected subcommand
- `system`: each stub subcommand (`rethlas <cmd> --help`) exits 0 with
  a recognisable placeholder (prevents M0 from accidentally shipping
  a broken entry point)
- `system`: `rethlas` with **no subcommand** prints help to stderr
  and exits with code **1** (argparse convention — a missing
  required subcommand is an error, not a successful help display)
- `unit`: the admission layer resolves `producers.toml` from the
  **Rethlas installation root** (next to `pyproject.toml`), not
  from the workspace (ARCHITECTURE §2.1, §3.5). Fixture: set up
  a workspace containing a conflicting `producers.toml` → admission
  still uses the installation copy (prevents an admission-bypass
  attack where a compromised workspace overrides the producer
  registry).

**Exit**

- Editable install works
- CLI binary resolves and every Phase I subcommand is at least
  reachable from argparse

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
- **Test fixture baseline** delivered alongside M1 so subsequent
  milestones inherit a stable harness — these are real deliverables,
  not "recommended" helpers:
  - `tests/fixtures/tmp_workspace.py` — pytest context manager that
    creates a temp workspace with `events/`, `knowledge_base/`,
    `runtime/jobs/`, `runtime/logs/`, `runtime/locks/`,
    `runtime/state/` skeleton; optionally seeds a `rethlas.toml`
    matching the annotated template
  - `tests/fixtures/fake_clock.py` — deterministic
    `utc_wall_clock_now()` substitute; step-forward helper; can
    simulate wall-clock backward jumps for H5 tests
  - `tests/fixtures/inject.py` — `inject_applied_event(...)`,
    `inject_node(...)`, `inject_event_file(...)` helpers that
    write directly into Kuzu / `events/` for tests that need to
    bypass admission or librarian (used extensively by M2, M3, M9,
    M10 tests)
  - `tests/fixtures/event_bytes.py` — `write_event_with_bytes(...)`
    helper that writes an event file with exactly specified raw
    bytes (for `event_sha256` determinism tests and linter F
    tampering fixtures)

**Tests**

- `unit`: config parses a valid file with all known fields, and
  rejects each of these specific invalid inputs (ARCHITECTURE §2.4):
  - `desired_pass_count = 0` (must be ≥ 1)
  - `generator_workers = -1`
  - `verifier_workers = 0`
  - `codex_silent_timeout_seconds = 30` (below 60 s floor)
  - `bind = "127.0.0.1"` (missing port)
  - `bind = "0.0.0.0:70000"` (port out of range)
  - unknown field (e.g. `[scheduling] bogus_key = 42`) → startup
    warning logged, field value ignored (not fail)
  - malformed TOML syntax → exit code 4 with line-number error
  - any out-of-range value above → exit code 4 with field-name
    error
- `unit`: event filename/body round-trip
- `unit`: **atomic event write helper** invokes, in order: open
  `.tmp` for write → write bytes → `fsync(tmp_fd)` → close →
  `rename(tmp, canonical)` → open parent dir → `fsync(dir_fd)` →
  close (ARCHITECTURE §9.1 G3). Use `unittest.mock` on `os.fsync` /
  `os.rename` to assert both the call sequence and that both
  fsyncs actually happen; without the directory fsync an event
  can be "written but nameless" after a kernel / power crash.
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
  - final `Node`, `DependsOn`, and `AppliedEvent` tables must be
    identical (M2 scope is Kuzu projection only; `nodes/*.md`
    byte-determinism is M4's renderer test, §M4)
- `integration`: `kuzu_backend.rebuild_from_events(events_dir)`
  contract — takes a fixture workspace, pre-populates all three
  tables with stale contents, calls `rebuild_from_events`, and
  asserts:
  - `Node` / `DependsOn` / `AppliedEvent` table contents after
    rebuild are byte-identical to a fresh-workspace replay of the
    same `events/`
  - No stale rows from the pre-populated state survive
  - Replay order is `(iso_ms, seq, uid)` (scramble input files'
    on-disk order to verify that `rebuild_from_events` does not
    depend on filesystem iteration order)
- `fault-injection`: same `event_id`, different on-disk bytes
  - apply an event once so `AppliedEvent(event_id, event_sha256=H1)`
    exists
  - mutate the canonical event file bytes without changing its
    filename/body `event_id`
  - next librarian replay must detect `event_sha256 != sha256(file)`
    and halt as workspace corruption, not silently treat it as an
    idempotent replay
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
- `integration`: `AppliedEvent.detail` carries useful context per
  reason code (ARCHITECTURE §5.2 detail-format spec):
  - `cycle`: detail contains the cycle path
    (`"thm:a → lem:b → thm:a"` style)
  - `label_conflict`: detail names the conflicting label **and**
    the winning `event_id`
  - `hash_mismatch`: detail contains both the stale hash prefix
    and the current hash prefix as **12 hex chars** each
  - `ref_missing`: detail names the missing `\ref{}` target
  - `hint_target_missing` / `hint_target_unreachable`: detail
    names the target label and its observed `pass_count`
  - `kind_mutation`: detail carries the old and new `kind` values
  - `self_reference`: detail carries the offending label
- `integration`: `AppliedEvent.event_sha256` equals
  `sha256(raw bytes of the event file at apply time)` — test mutates
  the file bytes between two apply calls of identical `event_id` and
  asserts the hashes differ (feeds directly into linter category F)

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
- `system`: **init writes the annotated `rethlas.toml` template
  anchored to ARCHITECTURE §2.4** — the written file must contain
  every field named in §2.4's validation-bounds table, each with
  its documented default value and a one-line human-readable
  comment. Test parses the written file and asserts:
  - `[scheduling].desired_pass_count == 3`
  - `[scheduling].generator_workers == 2`
  - `[scheduling].verifier_workers == 4`
  - `[scheduling].codex_silent_timeout_seconds == 1800`
  - `[dashboard].bind == "127.0.0.1:8765"`
  Additionally, raw file text contains a non-empty `# ...` comment
  on the line immediately above each field (self-documenting init
  output). Without this anchor, the template and ARCHITECTURE can
  drift silently.
- `system`: user publish CLI writes canonical event file
- `integration`: publish CLI polls `AppliedEvent` and reports each
  of the four outcomes from ARCHITECTURE §9.1 D2. Setup for each
  (M3 does not depend on M4 or M8 being implemented — the tests
  use fixtures to simulate the needed states):
  - `applied`: test fixture **writes an `AppliedEvent(status=applied)`
    row directly** into Kuzu (bypassing librarian) so the CLI poll
    resolves with "applied".
  - `apply_failed(reason)`: test fixture writes an
    `AppliedEvent(status=apply_failed, reason=label_conflict, …)`
    row directly; CLI poll reports it.
  - timeout while supervise not running: no fixture setup; CLI
    polls, nothing arrives, the `supervise.lock` file does not
    exist; CLI reports "queued, supervise not running" + exit 0.
  - timeout while supervise running but librarian behind: test
    fixture **takes `runtime/locks/supervise.lock` externally** (a
    simple `flock` held by the test harness) but **does not** write
    any `AppliedEvent` row; CLI sees the lock is held, polls times
    out, and reports "queued, librarian behind" + exit 0.
- `system`: `rethlas rebuild` refuses while `supervise.lock` is held
- `system`: `rethlas rebuild` takes lock when running standalone
- `system`: **`rethlas rebuild` never touches `events/`** — snapshot
  the set of files under `events/` + their byte contents before
  rebuild; run rebuild on a workspace with some events; snapshot
  again; both snapshots must be byte-identical. Only
  `knowledge_base/` gets wiped. `events/` is truth and rebuild is
  never allowed to mutate it.
- `system`: **`--workspace <path>` flag universality** (ARCHITECTURE
  §2.3 D5) — from a cwd unrelated to the target workspace,
  `rethlas --workspace /tmp/alt init` creates the workspace at
  `/tmp/alt`; a follow-up
  `rethlas --workspace /tmp/alt add-node ...` writes its event
  file under `/tmp/alt/events/` (not cwd). Same spot-check for
  `rethlas --workspace /tmp/alt rebuild` to confirm the flag is
  plumbed through every Phase I subcommand.
- `system`: **uninitialized-workspace error path** (ARCHITECTURE
  §2.3 exit code 2) — in a freshly-created directory with no
  `events/` and no `rethlas.toml`, running each of
  `rethlas supervise`, `rethlas add-node ...`,
  `rethlas linter`, `rethlas rebuild` exits with code **2** and
  prints `"workspace not initialized; run \`rethlas init\` first"`
  (or equivalent) to stderr.
- `integration`: user-CLI admission rejects the following per
  ARCHITECTURE §3.1.6 (structural) / §5.2 / §3.5.2 / §5.4. Each
  rejection **exits non-zero** (per §2.3 exit-code table) **and
  appends a line to `runtime/state/rejected_writes.jsonl`** with the
  right `reason` + `detail`, and **no file is created** under
  `events/`:
  - `rethlas add-node --kind external_theorem --source-note ""`
    (non-empty `source_note` required for external_theorem)
  - `rethlas add-node --label thm:main` (placeholder label)
  - `rethlas add-node --label thm:foo --kind lemma`
    (prefix/kind mismatch)
  - `rethlas revise-node --label thm:foo --kind theorem` on an
    existing `thm:foo` that was authored as `kind=lemma`
    (kind immutability, §5.1)
  - `rethlas attach-hint --target lem:already_verified` where
    `lem:already_verified.pass_count >= 1` at admission time
    (hint has no reachable consumer, §5.2 / §3.1.6)
  - malformed label shapes (missing prefix, uppercase, empty slug)
- `integration`: **`producers.toml` enforcement** (ARCHITECTURE §3.5) —
  admission rejects events whose `actor` does not match any
  registered `actor_pattern`, or whose `(producer_kind, event_type)`
  pair is not in that producer's `allowed_event_types`. Fixtures:
  - event with `actor="librarian:xyz"` (not a Phase I truth
    producer) → rejected
  - event with `actor="user:alice", type="user.unknown_action"`
    (type not in allowed list for `user`) → rejected
  Each exits non-zero, appends to `rejected_writes.jsonl`, nothing
  enters `events/`.
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
  - startup replay (ordered)
  - startup `nodes/` reconciliation
  - **passive APPLY command handler** — receives
    `APPLY(event_id, path)` from coordinator over the command
    channel, applies to Kuzu + renders `nodes/*.md`, replies
    `APPLIED` / `APPLY_FAILED` / `CORRUPTION` (ARCHITECTURE §6.5).
    Librarian does **not** watch `events/` itself during runtime;
    the watchdog lives in coordinator (tested in M8).
  - `librarian.json`
- `librarian/renderer.py`
- `librarian/cli.py`
- rebuild flag handling:
  - `runtime/state/rebuild_in_progress.flag`
  - startup forced rebuild path after interrupted rebuild

**Tests**

- `unit`: `renderer.render(node)` produces **byte-identical** output
  across repeated invocations on the same `Node` — fixed YAML key
  order (`label`, `kind`, `pass_count`, `statement_hash`,
  `verification_hash`, `depends_on`), `depends_on` ASCII-sorted,
  Unix `\n` line endings, UTF-8 NFC normalization, trailing newline
  (ARCHITECTURE §4.2 rendering contract). Both hashes appear in
  frontmatter so workers can resolve dep `statement_hash`es without
  reading Kuzu (§4.1 worker invariant). Without byte-determinism,
  startup reconciliation + linter E would flap every run.
- `integration`: startup replay processes unseen events and skips already-decided ones
- `integration`: **APPLY command handling** — after
  `startup_phase = ready`, librarian receives an
  `APPLY(event_id, path)` command from coordinator over the command
  channel; within **5 seconds** the corresponding `AppliedEvent`
  row appears with `status=applied` (or `apply_failed` if the
  fixture event is designed to fail) and, for rendered kinds, the
  expected `nodes/*.md` content is on disk; librarian replies
  `APPLIED` / `APPLY_FAILED` with the same `event_id`. Exercises
  the runtime (non-startup) projection path. The coordinator-side
  watchdog that emits the APPLY command is tested in M8.
- `integration`: **APPLY-during-startup queuing** — coordinator may
  send `APPLY` commands to librarian before `startup_phase = ready`
  (e.g. supervise started coordinator first, or a new event is
  dropped while librarian is still replaying). Librarian **queues**
  any APPLY received while `startup_phase` is `replaying` or
  `reconciling` and processes it exactly once after `ready`.
  Fixture: during replay, coordinator sends an APPLY for an event
  that is also present on disk (so startup replay will find it).
  Verify (a) no double-apply (AppliedEvent has exactly one row per
  event), (b) librarian replies to the queued APPLY after `ready`,
  (c) for an event **not** yet on disk at replay time but sent via
  APPLY, it is applied exactly once after `ready`. Without this,
  supervise start order or mid-startup event arrivals could cause
  lost or double-processed events.
- `integration`: crash window after Kuzu commit but before render is healed by startup reconciliation
- `integration`: orphan `nodes/*.md` files are deleted by reconciliation
- `integration`: `librarian.json.startup_phase` transitions:
  - `replaying`
  - `reconciling`
  - `ready`
- `integration`: `projection_backlog` equals
  `count(files under events/) - count(AppliedEvent rows)`:
  - fixture with 10 event files on disk and 7 already-applied
    `AppliedEvent` rows → librarian reports
    `projection_backlog = 3` in `librarian.json`
  - after librarian catches up, `projection_backlog = 0`
  - drop 3 new event files into `events/{date}/` → backlog
    transiently shows ≥ 3 then returns to 0 after the live
    watchdog path processes them
- `integration`: **idle heartbeat cadence** — librarian rewrites
  `librarian.json.updated_at` **every 30 s** even when `events/`
  is quiet and no work is happening (ARCHITECTURE §6.5). Fixture
  observes at least 3 heartbeat writes over a 90 s window with no
  new events; consecutive `updated_at` gaps stay ≤ 32 s (allowing
  small scheduler jitter).
- `fault-injection`: interrupted rebuild leaves flag; next supervise-started librarian forces clean rebuild path
- `fault-injection`: **`producers.toml` replay-time enforcement** —
  a canonical event file exists under `events/` whose `actor` or
  `(kind, type)` pair does not match `producers.toml` (simulating a
  hand-drop past admission). On startup replay, librarian halts as
  **workspace corruption** per §3.1.6 rather than silently applying
  or skipping; dashboard surfaces the corruption.

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
- **`tests/fixtures/fake_codex.py`** — real deliverable, not a
  vague helper. A Python script that emulates `codex exec` for all
  tests downstream of M5 (M6 / M7 / M8 / M11 depend on it). Takes
  the same CLI args as real Codex (`-C`, `-m`, `--sandbox`, prompt
  positional). Behavior selected at test time via env var
  `FAKE_CODEX_SCRIPT=<json>` whose schema is:
  ```json
  {
    "stdout_lines": [{"text": "...", "delay_s": 0.0}, ...],
    "stderr_lines": [{"text": "...", "delay_s": 0.0}, ...],
    "silent_seconds": 0,
    "exit_code": 0,
    "malformed": false
  }
  ```
  `delay_s` paces output so log mtime timing can be exercised
  (F1 / F2). `silent_seconds` simulates long reasoning (10–20 min)
  without blocking the test suite — the fake accepts a scale
  factor env var `FAKE_CODEX_TIME_SCALE=0.01` so tests run in
  milliseconds while the production default stays at 1.0.
  `malformed=true` emits partial `<node>` or bogus verdict JSON
  to exercise §7.5 crash paths.
- **`tests/fixtures/scripted_codex.py`** — ergonomic test-side
  composer that produces `FAKE_CODEX_SCRIPT` JSON for common
  scenarios (valid generator batch, verdict=accepted, verdict=gap,
  silent-timeout, parse-failure, etc.)

**Tests**

- `integration`: wrapper heartbeat updates `runtime/jobs/{job_id}.json.updated_at` every 60 s; every written timestamp (`started_at`, `updated_at`) is **UTC ISO 8601 ending in `Z`** (ARCHITECTURE §2.4 trailer / G1). Same assertion applied spot-check to `coordinator.json` and `librarian.json` timestamps in M8 / M4 tests via a shared helper.
- `integration`: Codex runner merges `stdout` and `stderr` into the same
  `runtime/logs/{job_id}.codex.log`; writes to either stream refresh the
  observed log mtime used by timeout logic
- `integration`: log mtime timeout marks `timed_out`
- `integration`: orphaned job file is detected and cleaned
- `integration`: terminal statuses write then delete job files in the documented order
- `integration`: startup runtime cleanup removes stale job files and stale coordinator/librarian snapshots but preserves JSONL histories and logs
- `integration`: spawning a fake wrapper via the runtime helper passes
  `RETHLAS_WORKSPACE=<abs path>` env var and the `job_id` positional
  argument; any other env vars present at coordinator spawn time
  (e.g. a stubbed `OPENAI_API_KEY`) flow through unchanged to the
  wrapper process (ARCHITECTURE §6.7.1 job lifecycle step 1)
- `static`: `generator/role.py`, `verifier/role.py`, and every module
  under `common/runtime/` they transitively import **must not import
  `common/kb`** (enforce via a `grep`-style static check in the test
  suite; workers are Kuzu-free per ARCHITECTURE §4.1)
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
  - parser is **tolerant of real Codex output noise**: ANSI escape
    codes may appear (strip before parsing); MCP-tool-call traces
    may precede / interleave `<node>` blocks; banner / version
    lines may precede content. The parser extracts `<node>...</node>`
    blocks by regex (anchored on literal tags, not on position) and
    extracts the final verdict JSON by finding the last `{...}` that
    parses as valid JSON with the expected keys. Implementation
    feeds real recorded Codex stdout samples (from prior
    inducedorbit runs) through the parser as a regression test so
    real-world format variations are caught before production
    dispatch.

**Tests**

- `integration`: fake Codex output with valid `<node>` blocks
  produces exactly one `generator.batch_committed`; emitted event
  body asserts every field required by ARCHITECTURE §3.5.1:
  - `attempt_id` matches `gen-{iso_ms}-{seq}-{uid}` where components
    are separated by single hyphens, e.g.
    `gen-20260424T101530.123-0001-a7b2c912d4f1e380`
  - `target` equals the dispatch target and appears in `nodes[]`
  - `mode ∈ {"fresh", "repair"}`
  - each `nodes[i]` has `label`, `kind`, non-empty `statement`,
    `proof`, `remark`, `source_note` (presence asserted for every
    key; `source_note` non-empty iff `kind=external_theorem`)
  - optional `cost` field: either absent, or has all four
    subfields (`input_tokens`, `output_tokens`, `reasoning_tokens`,
    `cost_usd`) plus `duration_seconds`
- `integration`: decoder rejects each of the 11 failure modes per
  ARCHITECTURE §3.5.1 + §6.2. Each has a dedicated bad-fixture
  test and produces exactly one line in
  `runtime/state/rejected_writes.jsonl` with the right `reason`
  + `detail`; no truth event is published:
  - malformed `<node>` block
  - `kind: external_theorem` in batch (user-only kind)
  - wrong label prefix / kind pairing (e.g. `thm:foo, kind=lemma`)
  - placeholder label (`thm:main`, `lem:helper`, etc.)
  - duplicate label within the same batch
  - batch `target` field not present in `nodes[]`, or `target`
    mismatches the dispatch parameter
  - existing non-target label in `nodes[]` (write-scope invariant,
    H7)
  - self-reference (node `\ref{}`s itself)
  - unresolved `\ref{}` to a label that neither exists in KB nor
    appears in the same batch
  - batch introduces a dependency cycle (within the batch, or
    combined with current KB edges)
  - repair-must-change-hash — post-batch `verification_hash`
    equals the rejected `H_rejected` from the triggering verdict
- `integration`: batch-internal topological hashing works; decoder
  resolves existing deps' `statement_hash` from **`nodes/*.md`
  frontmatter** (ARCHITECTURE §4.2) and batch-new deps from the
  staged batch itself; no Kuzu read happens inside generator
- `integration`: staged publish is atomic
- `integration`: wrapper **reads** its full dispatch context from
  `runtime/jobs/{job_id}.json` (target statement/proof,
  `dispatch_hash`, `dep_statement_hashes`, `repair_hint` whenever
  non-empty in **either** mode, and for `mode=repair` additionally
  `verification_report` / `repair_count` / `H_rejected`) —
  coordinator populated these before spawn (ARCHITECTURE §5.5.2);
  the wrapper trusts and does not re-check against Kuzu
- `integration`: **prompt assembly — fresh mode with user hint**
  (ARCHITECTURE §6.2 step 2). Fixture: job file `mode=fresh`,
  `repair_hint` contains a single user section with a known body.
  After `role.py` assembles the Codex prompt, assert the prompt
  contains an "Initial guidance" section with the user-hint body
  verbatim and no "Repair context" section. Guards against the
  "hint dropped on fresh dispatch" regression.
- `integration`: decoder rejections append to `runtime/state/rejected_writes.jsonl`
- `static`: `generator/role.py` and its transitive imports do **not**
  import `common/kb` (enforces Kuzu-free worker invariant)
- `system`: `rethlas generator --target ... --mode fresh|repair` works in a temp workspace with fake Codex
- `system`: `rethlas generator --target ... --mode xyz` (invalid
  mode) is rejected by argparse itself — exit code 2, usage
  message to stderr, no `runtime/jobs/*.json` created, no Codex
  invocation attempted

**Exit**

- Generator worker is deterministic around decode/publish/runtime bookkeeping

---

## M7 — Verifier Worker

**Deliverables**

- `verifier/role.py`
- updated `verifier/AGENTS.md`
- pruned verifier MCP usage per architecture
- verdict JSON parser is **tolerant of real Codex output** for the
  same reasons as M6: Codex may emit ANSI codes, reasoning prose,
  and tool-call traces before the final verdict. Parser finds the
  last `{...}` block that parses as JSON and contains the expected
  `verdict` / `verification_hash` / `verification_report` keys.
  Regression-tested against real recorded verifier stdout samples.

**Tests**

- `integration`: valid verdict JSON produces
  `verifier.run_completed`; emitted event body asserts every field
  required by ARCHITECTURE §3.5.1:
  - `verdict ∈ {"accepted", "gap", "critical"}`
  - `verification_hash` equals the `dispatch_hash` stored in the job
    file, which coordinator populated from Kuzu at dispatch time
  - `verification_report` has all 5 required subfields (`summary`,
    `checked_items`, `gaps`, `critical_errors`,
    `external_reference_checks`) — keys present, lists may be
    empty
  - `repair_hint` present (may be empty string)
  - consistency: `verdict=accepted` ⇒ `gaps=[]` AND
    `critical_errors=[]`; `verdict=gap` ⇒ `gaps` non-empty;
    `verdict=critical` ⇒ `critical_errors` non-empty
  - optional `cost`: same shape check as M6
- `integration`: malformed verdict JSON becomes `status = "crashed"` and no truth event
- `integration`: emitted `verifier.run_completed` event carries
  `verification_hash` **equal to the `dispatch_hash` from the job
  file** (no Kuzu read in the worker, ARCHITECTURE §6.3); if the
  target's hash has drifted in Kuzu by apply time, librarian's
  hash-match gate yields `apply_failed(hash_mismatch)` — confirmed
  by a dedicated test that forces drift via librarian inject
- `integration`: after `publishing`, wrapper **exits**; coordinator
  (exercised in M8 tests) writes the final `applied` /
  `apply_failed` status into the job file. Verifier M7 test scope
  stops at "wrapper wrote `publishing` and exited cleanly" — the
  mirroring step is coordinator's responsibility
- `static`: `verifier/role.py` and its transitive imports do **not**
  import `common/kb`
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
  - `generation_blocked_on_dependency`
  - `verification_dep_blocked`
  - `in_flight_only`
  - `corruption_or_drift`
  - `librarian_starting`
- `integration`: child restart policy
  - librarian restart once then coordinator exits on rapid re-crash
- `integration`: child startup grace period does not mark a just-spawned but
  still-initializing librarian as down before the grace window expires
- `integration`: **startup grace expiry without first heartbeat** —
  fixture spawns a child that stays alive but never writes its first
  heartbeat file. After the 30 s grace window, coordinator records
  `startup_timeout` for that child and triggers the restart policy
  (§6.4 E2):
  - librarian: restart once; if the second attempt also fails to
    heartbeat within 30 s, coordinator exits with code 3
- `integration`: startup cleanup removes zombie runtime state from prior crash
  - deletes stale `runtime/jobs/*.json`
  - deletes stale `runtime/state/coordinator.json` and
    `runtime/state/librarian.json`
  - preserves `runtime/state/rebuild_in_progress.flag`,
    `runtime/state/rejected_writes.jsonl`,
    `runtime/state/drift_alerts.jsonl`, and `runtime/logs/*.codex.log`
- `integration`: consecutive `crashed`, `timed_out`, and same-reason `apply_failed` counters flow into Human Attention state
- `integration`: **graceful shutdown cascade on SIGTERM / SIGINT**
  (ARCHITECTURE §6.4 G6):
  - children are signalled in reverse dependency order
    (in M8 scope: in-flight workers → librarian)
  - each child is given up to 10 s to exit cleanly before SIGKILL
  - in-flight wrappers write their terminal `status` and delete
    their job files before dying (no leftover
    `runtime/jobs/*.json`)
  - `supervise.lock` is released on exit; a subsequent
    `rethlas supervise` in the same workspace succeeds immediately
- `fault-injection`: second SIGINT during the 10 s wait escalates
  immediately to SIGKILL of remaining children; coordinator still
  releases `supervise.lock` cleanly and writes a final
  `coordinator.json` with `status = "stopping"`
- `fault-injection`: external SIGKILL of coordinator itself — OS
  process-group cleanup kills all children; next
  `rethlas supervise` starts cleanly thanks to M5/M8 runtime
  cleanup (no zombie state visible)
- `integration`: **coordinator tick cadence** — `coordinator.json`
  is rewritten on **every loop tick** (ARCHITECTURE §6.4.2). With
  a quiet workspace, fixture observes `updated_at` advancing in
  consecutive snapshots; consecutive gaps stay well within the
  dashboard staleness healthy window (60 s).
- `integration`: **config no-hot-reload** (ARCHITECTURE §2.4) —
  start `rethlas supervise` with `generator_workers = 2`; while
  running, edit `rethlas.toml` on disk to `generator_workers = 4`;
  assert the in-flight pool capacity stays at 2 across at least 5
  coordinator ticks; stop and restart supervise; only then the new
  value takes effect.
- `integration`: **coordinator is the precheck gate** (ARCHITECTURE
  §5.5.2). Fixture induces one of each precheck-failing condition
  (stale hash, advanced `pass_count`, dep not yet at ≥1, other
  in-flight job on same target, repair `H_rejected` mismatch). For
  each: coordinator skips the candidate **without writing any
  `runtime/jobs/*.json`**; a structured line appears in
  `runtime/logs/supervise.log` naming the failed condition; no
  Codex is spawned; no event is published.
- `integration`: **coordinator populates the job file with full
  pre-validated context** when precheck passes (ARCHITECTURE §6.7.1
  step 1): `target`, `mode`, `kind`, `dispatch_hash`, target's
  `statement`, target's `proof`, `dep_statement_hashes` map for
  every `\ref`-ed dep, `repair_hint` whenever non-empty (both
  modes, §10.2.3), and for generator `mode=repair` additionally
  `verification_report`, `repair_count`, `H_rejected`. Worker
  trusts; never re-reads Kuzu.
- `integration`: **generator mode selection** (ARCHITECTURE §10.2.3).
  Three fixtures on proof-requiring targets:
  - `pass_count = -1`, `repair_count = 0`, `repair_hint = ""`
    (plain fresh `user.node_added` with empty proof) → coordinator
    writes `mode = "fresh"`; job file omits `repair_hint`,
    `verification_report`, `H_rejected` (nothing to ship).
  - `pass_count = -1`, `repair_count = 0`, `repair_hint` has a
    user section (fresh node that received `user.hint_attached`
    before first generator run) → coordinator writes
    `mode = "fresh"` **and** `repair_hint` (the user section
    verbatim). Job file still omits `verification_report` and
    `H_rejected` (no verdict yet). Guards the "hint on fresh node
    lost through `verification_hash` change" regression.
  - `pass_count = -1`, `repair_count = 2` (two stored gap verdicts
    against the current `statement_hash`) → coordinator writes
    `mode = "repair"`; job file carries `verification_report`,
    `repair_hint`, `repair_count = 2`, and `H_rejected` equal to the
    `verification_hash` of the most-recent rejected verdict.
- `integration`: **generator pool ordering by `label` only**
  (ARCHITECTURE §10.2.2). Fixture with three proof-requiring
  candidates all at `pass_count = -1` and all with deps ready:
  `lem:a(repair_count=0)`, `lem:b(repair_count=5)`,
  `lem:c(repair_count=0)`. With `generator_workers = 1` and the
  "no concurrent same-target" rule, coordinator dispatches in order
  `lem:a` → `lem:b` → `lem:c` across three ticks — strict label
  ascending, **no `repair_count` deprioritization**. Guards against
  the earlier starvation-prone ordering; §10.4 keeps the "when to
  give up" decision on the generator, not the scheduler.
- `integration`: **verifier pool ordering by `pass_count`**
  (ARCHITECTURE §10.2.1). Fixture with three candidates whose deps
  are verified: `thm:x(pass_count=2)`, `def:y(pass_count=0)`,
  `lem:z(pass_count=1)`. With `verifier_workers = 1`, coordinator
  dispatches in order `def:y` → `lem:z` → `thm:x` (count ASC, label
  ASC tiebreak within a count). Confirms `def` is a valid verifier
  target (not restricted to proof-requiring kinds).
- `integration`: **coordinator is the AppliedEvent poller**
  (ARCHITECTURE §6.7.1 step 3). After a wrapper exits with job
  file `status = "publishing"`, coordinator on its next tick polls
  `kb.applied_event_record(event_id)` for the event wrapper
  published, writes final `status` (`applied` / `apply_failed`
  with `reason` + `detail`) into the job file, then deletes it.
  Fixture covers both branches:
  - Happy path: librarian applies → coordinator writes `applied`
  - Fail path: librarian records `apply_failed` with reason
    `label_conflict` → coordinator writes `apply_failed` + reason +
    detail
- `integration`: **coordinator owns the `events/` watchdog**
  (ARCHITECTURE §6.5). A new event file dropped via atomic rename
  into `events/{date}/` is detected by coordinator's watchdog,
  which sends an `APPLY(event_id, path)` command to librarian over
  the command channel; librarian replies `APPLIED` /
  `APPLY_FAILED`; coordinator mirrors the outcome into its own
  dashboard-facing state (SSE `applied_event` envelope fires).
  Librarian **never** autonomously picks up events from `events/`
  on its own.
- `system`: `rethlas supervise` can run a tiny workspace to steady state

**Exit**

- Coordinator is a correct singleton parent, pool-based dispatcher,
  pre-dispatch gate, AppliedEvent poller, and `events/` watchdog
  owner

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
- coordinator child-process integration for dashboard
  - spawn dashboard subprocess
  - monitor dashboard heartbeat
  - restart-three-times-then-degrade policy

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
- `integration`: **SSE envelope schema + type coverage** — every
  SSE message is a JSON object `{type, ts, payload}` where `ts` is
  UTC ISO 8601 with `Z` suffix. The test harness triggers one
  event of each Phase I type within a single test run and asserts
  envelope structure and delivery:
  - `truth_event` (new file under `events/`)
  - `applied_event` (new row in `AppliedEvent`)
  - `job_change` (creation / update / deletion of a
    `runtime/jobs/*.json`)
  - `coordinator_tick` (`coordinator.json` updated)
  - `librarian_tick` (`librarian.json` updated)
  - `alert` (new line appended to `rejected_writes.jsonl` or
    `drift_alerts.jsonl`)
  Per §6.7.1.
- `integration`: Kuzu-dependent endpoints return 503 + `Retry-After: 5` during rebuild
- `integration`: while `librarian.json.rebuild_in_progress = true`,
  Kuzu-dependent endpoints (`/api/overview`, `/api/theorems`,
  `/api/node/{label}`, `/api/rejected`) return HTTP 503 with
  `Retry-After: 5`; non-Kuzu endpoints (`/api/coordinator`,
  `/api/active`, raw `/events/stream`) continue to serve
- `integration`: coordinator child-management for dashboard
  - dashboard restart three times then degrade
  - dashboard startup grace period honored before first heartbeat
  - dashboard startup-timeout path: after the 30 s grace window
    without a first heartbeat, coordinator restarts up to 3× with
    30 s backoff; after the 3rd failure, marks
    `children.dashboard.status = "degraded"` and keeps coordinator +
    librarian running
  - `children.dashboard.status` reflected in `coordinator.json`
- `integration`: graceful shutdown order with dashboard present is
  dashboard → in-flight workers → librarian
- `integration`: **staleness thresholds** — fixture sets
  `coordinator.json.updated_at` to various ages; dashboard returns
  liveness label `healthy` (≤ 60 s), `degraded` (> 60 s, ≤ 5 min),
  `down` (> 5 min OR file missing). Same thresholds for
  `librarian.json` (ARCHITECTURE §6.7.1).
- `integration`: **`/api/events?limit=N` clamp** — `N > 500` is
  silently capped at 500; `N < 1` returns HTTP 400; `N` in `[1, 500]`
  returns exactly `N` results when the workspace has enough events.
- `integration`: **malformed runtime JSON robustness (H2)** —
  fixture writes garbage bytes into `coordinator.json`; dashboard
  does not crash, logs the parse error + path to
  `runtime/logs/dashboard.log`, treats the component as `down` for
  display purposes, and continues serving other endpoints normally.
  Same check with `librarian.json`.
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

- `integration`: **category A** (event stream integrity) — fixture
  where an event file's filename `event_id` disagrees with the body
  JSON `event_id`; linter reports the mismatch and exits non-zero.
  Additional A-fixtures:
  - two event files with the same `event_id` but different filenames /
    bytes → duplicate event-id violation
  - event with `parent_event_id` (or other event reference field)
    pointing at a missing prior event → missing-reference violation
- `integration`: **category B** (KB structural) — fixture with a
  cycle hand-inserted into Kuzu `DependsOn`; linter reports the
  cycle path. Additional B-fixtures:
  - two Kuzu `Node` rows that violate label uniqueness assumptions
    (or a duplicate-label import fixture, depending on backend test
    setup) → uniqueness violation
  - a node whose label prefix does not match its `kind` (§3.5.2)
  - a node whose kind-appropriate fields are invalid (for example
    `kind=external_theorem` with empty `source_note`)
- `integration`: **category C** (`pass_count` audit) — fixture
  where librarian has been forced to store a `Node.pass_count` that
  disagrees with the event-stream-replayed `audit_count`; linter
  reports drift per §5.5.1.
- `integration`: **category D** (`repair_count` audit) — fixture
  where stored `Node.repair_count` disagrees with the event-stream
  replay per §5.5.1; linter reports drift.
- `integration`: **category E** (`nodes/` ↔ Kuzu rendering) —
  fixture where one `nodes/*.md` has been hand-edited to differ
  from Kuzu's rendered output; another label at `pass_count >= 1`
  has no `.md` file; an orphan `.md` file exists for a label not
  in Kuzu. Linter reports all three; `--repair-nodes` fixes all
  three idempotently; second linter run after repair is clean.
- `integration`: **category F** (`events/` ↔ `AppliedEvent`
  inventory) — fixture where an applied event file's bytes are
  mutated after apply (changes the file's SHA-256 but keeps the
  filename / `event_id` intact); linter detects
  `event_sha256` mismatch and reports the target event_id.
  Second F-fixture: event file has been **deleted** outside
  Rethlas while `AppliedEvent` row still exists — linter detects
  the missing file.
- `integration`: `--repair-nodes` is idempotent and only touches
  category E artefacts (no category A/B/C/D/F side effects)
- `integration`: **all six categories run to completion and
  aggregate** — fixture that simultaneously violates categories B
  (a cycle in Kuzu) and D (stored `repair_count` disagrees with
  event-stream replay). `rethlas linter` runs every category
  (A→F order), reports violations for both B and D in a single
  `linter_report.json`, exits with code 5. No "fail-fast" short
  circuit; user sees every problem at once rather than whack-a-mole
  fixing them one category at a time.
- `integration`: linter refuses with live `supervise.lock` unless `--allow-concurrent`
- `system`: JSON report is written to
  `runtime/state/linter_report.json` and exit code 5 on violations
  (§2.3 exit-code table)
- `system`: **clean-workspace success path** — a workspace that
  just went through `rethlas init` + a handful of valid user events
  applied by a brief supervise run, with no drift anywhere, passes
  `rethlas linter` with exit code **0**. `linter_report.json` has
  every category's `violations` array empty and a `summary` that
  reads "0 violations". Without this test, an implementation that
  always reports "violations found" would still pass the failure-
  path tests and only fail in production.

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

Minimum CI stages (register both `fault` and `golden` markers in
`pyproject.toml`; select them with `-m`, not `-k`):

1. `pytest tests/unit`
2. `pytest tests/integration -m "not fault"`
3. `pytest tests/system -m "not fault"`
4. `pytest tests -m "fault"` (fault-injection stage, both
   integration and system fault-marked tests together)
5. `pytest tests/integration -m "golden"` (golden-snapshot stage)
6. `rethlas linter` against a golden clean fixture

**Platform matrix.** CI runs the full gate on both **Linux**
(`ubuntu-latest` or equivalent) and **macOS** (`macos-latest`).
Windows is out of scope (ARCHITECTURE §1 — flock / POSIX O_APPEND /
process groups / POSIX signals are assumed). A test that passes on
only one of Linux/macOS is treated as a regression.

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
