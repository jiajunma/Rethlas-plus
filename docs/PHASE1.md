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
- Run Codex verifier (3-stage pipeline)
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

**M0.10** `producers.toml` at repo root with initial registry (user,
generator, verifier, coordinator, librarian, linter).

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
- For each event type, define the update to Kuzu atomic fields
- Hash recomputation + BFS propagation
- `verification_count` update rules (axioms get 1; proof nodes get 0 then
  driven by verdicts; -1 on wrong; reset on hash change)
- Counter-example cascade on `theorem_disproven`: replace dep references
  to disproven theorem with counter-example; set dependents' count to -1

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
- Ordered event processing (by event_id)
- Full node re-render on startup
- Handle `coordinator.full_rebuild_requested` event by calling rebuild

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

**M4.4** Update `generator/mcp/server.py`:
- Keep: `search_arxiv_theorems`, `verify_proof_service`, `memory_*`
- Add: `get_event(event_id)`, `closure(label, direction, depth)`
- Remove nothing in Phase I

**M4.5** `generator/role.py` — new thin wrapper
- Read dispatch event (target + mode + optional repair context refs)
- Assemble minimal Codex prompt
- Invoke `codex exec` via `common/runtime/codex_runner`
  - cwd = `<workspace>/knowledge_base/nodes/`
  - `--sandbox read-only`
- Parse stdout for `<node>` blocks
- Emit fine-grained events per node
- Emit session markers (`attempt_started` with pid+log_path;
  `attempt_produced` / `attempt_failed`)

**M4.6** `cli/generator.py` — `rethlas generator --target <label>
--mode {fresh|repair}` invokes `generator/role.py`

Milestone exit: `rethlas generator --target lem:test --mode fresh` runs
Codex, writes events. Librarian projects them into KB + nodes/.

---

## M5 — Verifier (Codex, 3-stage pipeline)

**M5.1** Keep `verifier/.agents/skills/` intact; update skill AGENTS.md for
structured output per stage.

**M5.2** Update `verifier/.codex/config.toml` similarly to generator.

**M5.3** Update `verifier/AGENTS.md`: document cwd = nodes/, read-only,
and per-stage output format.

**M5.4** Update `verifier/mcp/server.py`:
- Existing tools preserved
- Add `get_event(event_id)` and `closure(label, direction, depth)`

**M5.5** `verifier/role.py` — 3-stage pipeline orchestrator
- Read dispatch event
- Stage 1: `check-referenced-statements`
- Stage 2: `verify-sequential-statements`
- Stage 3: `synthesize-verification-report`
- Each stage is an independent `codex exec` call
- Emit `run_stage_completed` after each stage
- Compute `verification_hash` against current KB state before dispatching
- Emit `run_completed` with verdict + verification_hash

**M5.6** `cli/verifier.py` — `rethlas verifier --target <label>`

Milestone exit: `rethlas verifier --target lem:test` runs 3 stages, emits
verdict event. Librarian increments count on accepted; sets count=-1 on
wrong.

---

## M6 — Coordinator

**M6.1** `coordinator/policy.py` — pure decision function
- Given KB state, return list of dispatches to emit
- Count-based filtering and priority per §10 of ARCHITECTURE
- Respect `DESIRED_COUNT` and `MAX_REPAIRS` thresholds

**M6.2** `coordinator/loop.py` — main loop
- Read KB
- Compute dispatches via policy
- For each dispatch: acquire budget slot, launch wrapper subprocess, emit
  dispatch event
- Monitor in-flight dispatches (via codex log mtime)
- On timeout: kill process group, emit `attempt_timed_out`
- Check global stop condition: all proof-requiring nodes count >= DESIRED

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

**M8.1** `linter/checks.py` — category A + B checks only
- A: event filename ↔ frontmatter consistency; unique event_ids;
  referenced event_ids exist
- B: no cycles in Kuzu; label uniqueness; kind-field consistency

**M8.2** `linter/main.py` — one-shot run
- Load events, rebuild KB in memory (or query existing), run checks
- Emit `linter.run_completed` with counts
- Emit `linter.invariant_violated` per violation

**M8.3** `cli/linter.py` — `rethlas linter [--mode fast]`

Milestone exit: `rethlas linter` reports zero violations on a clean
workspace; flags injected errors in test cases.

---

## M9 — End-to-end smoke test

**M9.1** Fixture: a tiny workspace with
- One `user.definition_added` event
- One `user.open_problem_created` event (goal with 1 dep on the definition)

**M9.2** `rethlas supervise` in this fixture:
- Coordinator dispatches generator for the goal (fresh)
- Generator produces `<node>` blocks → events
- Librarian projects → KB + nodes/
- Coordinator dispatches verifier
- Verifier returns accepted
- Count increments; eventually reaches DESIRED_COUNT = 3
- Supervise loop idles (global stop condition met)

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
- [ ] User can drop a definition / external_theorem / open_problem as an
      event file
- [ ] Librarian projects it; nodes/*.md and dag.kz populated
- [ ] `rethlas supervise` drives generator + verifier loop
- [ ] Goal reaches verification_count >= DESIRED_COUNT = 3
- [ ] Dashboard at port 8765 shows current state
- [ ] Linter reports consistency
- [ ] `rm -rf knowledge_base/; rethlas rebuild` reconstructs full state

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
