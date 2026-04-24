# Phase I Task List

**Status.** Draft. Concrete tasks to implement the Phase I architecture
defined in `ARCHITECTURE.md`.

Each task is independently testable. Tasks are grouped into milestones.
Within a group, order follows dependencies.

---

## Goal

A minimum viable Rethlas that can:
- Accept user-authored events (definitions, external theorems, open problems)
- Run Codex generator (fresh + repair modes)
- Run Codex verifier (single-call, Codex internally uses 3 skills)
- Project events into `dag.kz/` and `nodes/*.md` via librarian
- Schedule dispatches via coordinator (count-based)
- Show a linear HTML dashboard
- Recover full state from events (`rethlas rebuild`)

**Explicitly out of scope for Phase I:**
- Claude / consensus verification
- Semantic embedding search
- Cytoscape / interactive DAG
- Blueprint LaTeX export
- Importer (external library reader)
- Inducedorbit data migration
- Clock skew detection / multi-machine concurrency

---

## M0 — Repo restructure (no code change)

**M0.1** `git mv agents/generation generator`  
**M0.2** `git mv agents/verification verifier`  
**M0.3** `git mv agents/common common`  
**M0.4** `rm -rf agents/` (empty after moves)  
**M0.5** Delete obsolete scripts that will be rewritten under event model:
- `generator/scripts/run_with_recovery.py`
- `generator/scripts/verify_sections.py`
- `generator/scripts/verification_aggregation.py`
- `generator/scripts/build_blueprint_from_theorem_library.py`
- `generator/scripts/lint_theorem_library.py`
- `generator/scripts/materialize_theorem_library_proofs.py`
- `generator/scripts/validate_theorem_completeness.py`

`generator/scripts/show_run_status.py` — salvageable; decide at M2 when
CLI is built.

**M0.6** Relocate problem-statement markdowns out of tool repo:
- `generator/data/*.md` → ideally moved into the `inducedorbit` workspace
  repo; Phase I can simply delete them from Rethlas if migration is
  deferred.

**M0.7** Add top-level `docs/` (already exists with this file).

**M0.8** Initial `pyproject.toml` declaring the `rethlas` package with
`rethlas` CLI entry point.

**M0.9** Create empty new top-level dirs: `coordinator/`, `librarian/`,
`linter/`, `dashboard/`, `cli/`.

**M0.10** `producers.toml` at repo root with Phase I truth-producer
registry: `user`, `generator`, `verifier` (coordinator, librarian, linter
are non-truth runtime components and need not register as truth
producers).

Milestone exit: repo builds (`pip install -e .`), `rethlas --help` shows
stub subcommands.

---

## M1 — Common infrastructure

**M1.1** `common/events/` — event read/write/parse
- Filename composition and parsing
- YAML frontmatter parsing (for `.md`)
- JSON parsing (for `.json`)
- `.tmp` + atomic rename write helper
- Schema validation (required fields, types, filename ↔ body consistency)

**M1.2** `common/kb/types.py` — dataclasses for `Node`, `Edge`, `Event`,
`Verdict`

**M1.3** `common/kb/interface.py` — `KnowledgeBase` Protocol

**M1.4** `common/kb/kuzu_backend.py` — Kuzu implementation
- Schema init
- All Protocol methods
- Single-writer enforcement via Kuzu's built-in locking

**M1.5** `common/kb/factory.py` — `open_kb()` returning the one backend

**M1.6** `common/kb/hashing.py` — `statement_hash()` and
`verification_hash()` Merkle computation

**M1.7** `common/runtime/codex_runner.py` — Popen + log mtime monitoring +
process-group kill on 30-min stale
- Based on existing `run_with_recovery.py` subprocess pattern
- Uses `os.setsid` + `os.killpg` for process group management

**M1.8** `common/config/` — minimal `rethlas.toml` loader (fields emerge
as needed)

**M1.9** Unit tests for event round-trip, KB Protocol contract, hash
computation, codex runner.

Milestone exit: `common/` packages importable; tests green.

---

## M2 — Rethlas CLI skeleton

**M2.1** `cli/main.py` — `rethlas` entry point with subcommands:
- `rethlas init` (M3)
- `rethlas supervise` (M6)
- `rethlas dashboard` (M7)
- `rethlas linter` (M8)
- `rethlas rebuild` (M4)
- `rethlas generator` (M4)
- `rethlas verifier` (M5)
- `rethlas status` (Phase II candidate; stub)

**M2.2** `cli/init.py` — `rethlas init`
- Creates `events/`, `knowledge_base/`, `runtime/` (empty)
- Writes minimal `rethlas.toml`
- Writes workspace `.gitignore`

**M2.3** `cli/rebuild.py` — `rethlas rebuild`
- Delete `knowledge_base/` and `runtime/`
- Invoke librarian in one-shot mode to replay events

Milestone exit: `rethlas init` creates a fresh workspace; `rethlas rebuild`
runs (no-op with empty events).

---

## M3 — Librarian (read events → write KB + nodes)

**M3.1** `librarian/projector.py` — event projection logic
- For each event type, update Kuzu atomic fields per §5.4 table
- Hash recomputation + BFS propagation (Merkle; statement changes only)
- `pass_count` update rules:
  - Definitions / external_theorems created at 0
  - Proof-requiring kinds (lemma/theorem/proposition) created at -1
  - verifier accepted + hash match → +1
  - verifier gap/critical + hash match → -1; store `verification_report` and `repair_hint`
  - hash change: `count = 0` if proof non-empty, `-1` if empty
- No -1 propagation rule (strict-monotone dispatch already handles it)
- No separate counter-example event — handled as statement revision
  inside a normal `generator.batch_committed` (batch contains the
  revised node whose new statement is the negation, which flips the
  `statement_hash` and triggers the usual Merkle cascade)
- Set `repair_hint` when user emits `user.hint_attached`; clear on the
  next `generator.batch_committed` that touches that target label

**M3.2** `librarian/validator.py` — business rule validation
- Referenced event_ids exist and precede current
- Referenced labels exist
- New labels are unique
- No cycle introduced
- Producer + type match `producers.toml`

**M3.3** `librarian/renderer.py` — node markdown file generation
- Given a node label, query Kuzu and format markdown
- Output location: `knowledge_base/nodes/{kind_prefix}_{sanitized_label}.md`

**M3.4** `librarian/main.py` — long-running daemon
- File watcher on `events/` (via `watchdog`)
- Ordered event processing (stable sort by iso_ms, seq, uid per
  ARCHITECTURE §3.7.1)
- Full node re-render on startup
- Rebuild is a CLI action (`rethlas rebuild`), not an event — librarian
  exposes it via `librarian/cli.py`, never via a truth event (Phase I
  truth producers are user/generator/verifier only)

**M3.5** `librarian/cli.py` — `rethlas librarian [--rebuild | --daemon]`

Milestone exit: manually drop a user event file in `events/`, run librarian,
see dag.kz populated and nodes/*.md generated.

---

## M4 — Generator (Codex, fresh + repair modes)

**M4.1** Keep `generator/.agents/skills/` intact; update each skill's
AGENTS.md instructions to require `<node>` block output format and
`\ref{label}` conventions.

**M4.2** Update `generator/.codex/config.toml`:
- Remove `--dangerously-bypass-approvals-and-sandbox` (sandbox mode set at
  invocation time)
- MCP server path stays as `./mcp/server.py`

**M4.3** Update `generator/AGENTS.md`:
- Document Codex workspace: cwd = nodes/, read-only
- Document output format: `<node>` blocks
- Document `\ref{label}` convention
- Document repair mode prompt format

**M4.4** Update `generator/mcp/server.py` to match ARCHITECTURE §8.1:
- Keep: `search_arxiv_theorems(query)`, `memory_init` / `memory_append`
  / `memory_search`
- Remove any stale tools that do not appear in §8.1 (e.g.,
  `verify_proof_service`, `get_event`, `closure`) — those are not in
  the Phase I generator toolset

**M4.5** `generator/role.py` — new thin wrapper
- Read dispatch parameters from coordinator (runtime job file / CLI
  args): target label + mode + optional repair context refs. Dispatch
  is **not** a truth event in Phase I (coordinator is not a truth
  producer per ARCHITECTURE §3.5).
- Assemble minimal Codex prompt
- Invoke `codex exec` via `common/runtime/codex_runner`
  - cwd = `<workspace>/knowledge_base/nodes/`
  - `--sandbox read-only`
- Parse stdout for `<node>` blocks
- Stage the batch outside `events/` until fully validated, then atomically
  publish exactly **one** `generator.batch_committed` truth event (per
  ARCHITECTURE §3.7.2). No per-node truth events, no `attempt_started` /
  `attempt_failed` truth events.
- Runtime-only artifacts (pid, codex log path, crash info) go to
  `runtime/jobs/{job_id}.json` and `runtime/logs/{job_id}.codex.log`.
  Decoder rejections are appended to `runtime/state/rejected_batches.jsonl`.

**M4.6** `cli/generator.py` — `rethlas generator --target <label>
--mode {fresh|repair}` invokes `generator/role.py`

Milestone exit: `rethlas generator --target lem:test --mode fresh` runs
Codex, writes events. Librarian projects them into KB + nodes/.

---

## M5 — Verifier (Codex, single-call, original Rethlas pattern)

**M5.1** Keep `verifier/.agents/skills/` intact (3 skills:
check-referenced-statements, verify-sequential-statements,
synthesize-verification-report). Codex uses them internally via
multi-agent feature.

**M5.2** Keep `verifier/.codex/config.toml` multi-agent setup.

**M5.3** Update `verifier/AGENTS.md`:
- Document cwd = nodes/, read-only sandbox
- Document output format: final JSON with verdict ∈ {accepted, gap, critical}
- Document `resolve-reference` skill: label `:` → `_` for cat-ing nodes/*.md

**M5.4** Verifier has no Phase I MCP tools (per ARCHITECTURE §8). If
existing `verifier/.codex/config.toml` registers an MCP server, remove
the registration. Keep `verifier/mcp/` directory for future Phase II use
but don't launch an MCP server.

**M5.5** `verifier/role.py` — single `codex exec` invocation
- Read dispatch parameters from coordinator (runtime job file / CLI
  args): target label. Dispatch is not a truth event in Phase I.
- Compute current `verification_hash` (pre-dispatch revalidation per
  ARCHITECTURE §5.5.2)
- Build minimal prompt: target label + statement + proof
- `codex exec` once; parse verdict JSON
- Emit `verifier.run_completed` with verdict, verification_hash,
  verification_report, repair_hint

**M5.6** `cli/verifier.py` — `rethlas verifier --target <label>`

Milestone exit: `rethlas verifier --target lem:test` emits verdict event.
Librarian increments `pass_count` on accepted; sets to -1 on gap/critical.

---

## M6 — Coordinator

**M6.1** `coordinator/policy.py` — pure decision function
- Given KB state + runtime job state, return list of dispatches
- Count-based filtering and priority per §10 of ARCHITECTURE
- Respects `DESIRED_COUNT`; no hard repair cap in Phase I (repair rounds
  are advisory only — generator keeps being dispatched on count=-1 until
  hash escapes)
- Skip nodes with in-flight runtime jobs (no concurrent dispatch)

**M6.2** `coordinator/loop.py` — main loop
- Read KB + runtime jobs
- Compute dispatches via policy
- For each dispatch: acquire budget slot, launch wrapper subprocess,
  record runtime job file under `runtime/jobs/`
- Monitor in-flight dispatches (via codex log mtime)
- On timeout: kill process group; mark runtime job timed out
- Check global stop condition: all nodes (including definitions and
  external_theorems) have `pass_count >= DESIRED_COUNT` — matches
  ARCHITECTURE §10.1 / §6.4

**M6.3** `coordinator/supervise.py` — launch and monitor long-running
children (librarian, coordinator loop, dashboard)

**M6.4** `cli/supervise.py` — `rethlas supervise`

Milestone exit: `rethlas supervise` in a workspace with some user events
drives the generator-verifier loop autonomously.

---

## M7 — Dashboard (Phase I linear)

**M7.1** `dashboard/server.py` — FastAPI app
- `GET /` — HTML overview (goals + health + recent activity)
- `GET /api/goals` — JSON
- `GET /api/active` — JSON
- `GET /api/events?limit=N&actor=&type=` — JSON
- `GET /api/node/{label}` — JSON
- `GET /events/stream` — SSE

**M7.2** `dashboard/templates/` — minimal HTML (vanilla, no React)
- Uses fetch + SSE from JS
- Renders linear lists (goals table, active work table, event timeline,
  node detail side panel)

**M7.3** `cli/dashboard.py` — `rethlas dashboard --port 8765`

Milestone exit: browser at `localhost:8765` shows workspace state.

---

## M8 — Linter (minimal)

**M8.1** `linter/checks.py` — category A + B + C (pass_count audit) checks
- A: event filename ↔ frontmatter consistency; unique event_ids;
  referenced event_ids exist
- B: no cycles in Kuzu; label uniqueness; kind-field consistency

**M8.2** `linter/main.py` — one-shot run
- Load events, rebuild KB in memory (or query existing), run checks
- Report results via stdout / JSON to `runtime/state/linter_report.json`
  (linter is NOT a truth producer per ARCHITECTURE §6). Exit non-zero on
  any violation so CI can gate on it.

**M8.3** `cli/linter.py` — `rethlas linter [--mode fast]`

Milestone exit: `rethlas linter` reports zero violations on a clean
workspace; flags injected errors in test cases.

---

## M9 — End-to-end smoke test

**M9.1** Fixture: a tiny workspace with
- One `user.node_added` event (`kind: definition`, starts at count=0)
- One `user.node_added` event (`kind: theorem`, empty proof, starts at
  count=-1; statement references the definition via `\ref{def:...}`)

**M9.2** `rethlas supervise` in this fixture:
- Coordinator parallel dispatches:
  - Verifier on the definition (count=0, no deps, strict monotone vacuous)
  - Generator on the theorem (count=-1)
- Definition verified, count increments; eventually reaches DESIRED=3
- Generator produces theorem's proof (+ maybe sub-lemmas)
- Librarian projects → dag.kz + nodes/
- Theorem now has proof, count=0
- Coordinator can dispatch verifier on theorem only when
  definition.count > theorem.count (strict monotone)
  - theorem.count=0 needs def.count ≥ 1 → OK once def verified once
  - theorem.count=1 needs def.count ≥ 2 → OK once def verified twice
  - ... until both hit DESIRED=3
- Supervise loop idles (global stop condition: all nodes count ≥ DESIRED)

**M9.3** Kill supervise, `rm -rf knowledge_base/`, restart.
- Librarian re-projects from events → same KB state
- Coordinator continues

Milestone exit: smoke test script green.

---

## Task numbering summary

```
M0 — Repo restructure (10 tasks)
M1 — Common infrastructure (9 tasks)
M2 — CLI skeleton (3 tasks)
M3 — Librarian (5 tasks)
M4 — Generator (6 tasks)
M5 — Verifier (6 tasks)
M6 — Coordinator (4 tasks)
M7 — Dashboard (3 tasks)
M8 — Linter (3 tasks)
M9 — End-to-end (3 tasks)
```

Total: ~52 concrete tasks.

---

## Dependencies

```
M0 ──▶ M1 ──▶ M2 ──▶ M3 ──▶ M4 ──▶ M5 ──▶ M6 ──┬──▶ M7
                                                └──▶ M8 ──▶ M9
```

- M1 is the foundation; everything depends on it
- M3 (librarian) must precede anything that writes KB
- M4 and M5 (agents) can be parallelized once M3 is done
- M7 and M8 can be parallelized once M6 is done

---

## Phase I "done" criteria

- [ ] Workspace can be created (`rethlas init`)
- [ ] User can drop a definition / external_theorem / theorem as an
      event file
- [ ] Librarian projects it; nodes/*.md and dag.kz populated
- [ ] `rethlas supervise` drives generator + verifier loop
- [ ] All nodes reach pass_count >= DESIRED_COUNT = 3
- [ ] Dashboard at port 8765 shows current state
- [ ] Linter reports consistency (event-stream integrity + KB invariants)
- [ ] `rm -rf knowledge_base/; rethlas rebuild` reconstructs full state
- [ ] Linter audits `pass_count` against event-stream replay (§5.5.1)

---

## Not in Phase I (parking lot)

- Claude adapter
- Audit mode / consensus verification
- `search_relevant` semantic search MCP tool
- Cytoscape.js interactive DAG in dashboard
- Blueprint LaTeX export (`rethlas export --blueprint`)
- Importer for external libraries
- Inducedorbit data migration
- Linter category C (full projection drift)
- Clock skew / multi-machine
- `common/mcp/` shared module (each agent keeps its own)
- `adapters/codex/` nesting (flat structure for Phase I)
