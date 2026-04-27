# Phase II Implementation Plan

**Status.** Draft, to be tightened before M12 implementation begins.

This file is the execution plan for Phase II. It picks up after the
Phase I exit gate (M0..M11 in `PHASE1.md`) and extends the dashboard
into a dynamic, drill-down view of proof state. Like `PHASE1.md` it is
implementation-oriented:

- milestones are ordered by dependency;
- every milestone has explicit tests;
- milestone exits are hard gates, not vague progress markers.

If this file and `ARCHITECTURE.md` diverge, fix one of them
immediately.

---

## Goal

A Rethlas dashboard that surfaces *proof state* in a structure
operators can read at a glance and stays correct in real time:

- show every theorem's proof tree as a foldable outline rooted at the
  theorem, with each `\ref{}`-induced dependency expanding to its own
  nested children;
- visually encode kind (definition / proposition / lemma / theorem /
  external_theorem) and current status (done / verified /
  needs_verification / blocked_on_dependency / needs_generation /
  generation_blocked_on_dependency / user_blocked / in_flight) using
  the §M9 status vocabulary;
- update **live** — when a verifier verdict, generator batch, or
  librarian apply lands, the affected node's badge changes within one
  SSE round-trip without a page reload, without losing the operator's
  current scroll/expansion state;
- let the operator drill from any tree node into the existing
  per-node detail panel (`/api/node/{label}`).

**Explicitly out of scope for Phase II (initial scope, M12 only):**

- Lean / Mathlib formalization bridge
- multi-backend consensus (Claude alongside Codex)
- semantic embedding search across nodes
- multi-workspace / multi-project orchestration
- writable user actions in the dashboard (still read-only)
- standalone Cytoscape-style force-directed graph view (deferred to
  **Phase II.5** — see milestone stub at the bottom of this file)
- exporting to static blueprint HTML
- multi-root cross-tree linking beyond the lightweight "↗ also under"
  hint described in M12.E

The Lean bridge, multi-backend consensus, embedding search, and
multi-project orchestration remain candidates for Phase III.

---

## Delivery Principle

Phase II is dashboard-only. **No new truth-event types**, no new
producers, no projector changes. The dashboard reads existing channels
(`/events/stream`, `/api/overview`, `/api/node/{label}`, the new
`/api/tree`) and renders them. Anything that needs a new event or
projector behavior is out of Phase II scope.

Test layers used throughout follow PHASE1.md conventions:

- `unit`: pure functions, JSON shape, small state transitions
- `integration`: dashboard core + Kuzu + a real `nodes/` view
- `system`: full HTTP server + browser-style fetches via the test
  harness (Playwright optional, see M12.D)
- `static`: docstring / template / CSS-class consistency checks

The 80 % unit-test coverage gate from Phase I applies. New JavaScript
ships with a lightweight unit-test runner (`vitest` or stdlib `node
--test`; see M12.B).

---

## Milestones

## M12 — Dynamic Proof-Tree Visualization

The whole of Phase II's initial scope. Decomposed into five
sub-milestones (A..E) so the test gate at each step keeps the system
green.

### M12.A — `/api/tree` endpoint

**Deliverables**

- `dashboard/server.py`
  - new `DashboardCore.tree(root: str | None) -> dict` method
  - new HTTP route `GET /api/tree` (no args → all theorems as roots;
    `?root=<label>` → only that root's subtree)
  - new entry in the §6.7.1 endpoint inventory in the module
    docstring
- `dashboard/server.py` `/api/overview` body unchanged
- `docs/ARCHITECTURE.md` §6.7.1 — append the new endpoint

**Response shape** (stable contract; freeze before M12.B)

```jsonc
{
  "ts": "2026-04-27T00:00:00.000Z",
  "trees": [
    {
      "label": "thm:goal",
      "kind": "theorem",
      "status": "needs_verification",
      "pass_count": 0,
      "repair_count": 0,
      "desired_pass_count": 3,
      "in_flight": false,
      "shared_parents": [],          // labels of OTHER theorems that
                                     // also depend on this node; empty
                                     // for roots
      "children": [
        {
          "label": "prop:helper",
          "kind": "proposition",
          "status": "blocked_on_dependency",
          "pass_count": 0,
          "repair_count": 0,
          "desired_pass_count": 3,
          "in_flight": false,
          "shared_parents": ["thm:other_root"],
          "children": [ /* ...recurse... */ ]
        }
      ]
    }
  ],
  "node_count": 6,                   // total distinct nodes across
                                     // all trees (de-duplicated)
  "edge_count": 5
}
```

Cycle handling: the recursion carries a `visited` set per root path
so a corrupt KB (post-projector — should not happen, but the dashboard
must not hang) cannot loop forever. A node already in `visited` is
emitted with `children: []` and a sibling `cycle_detected: true` field.

**Tests**

- `unit`: `core.tree()` on an empty KB returns `{trees: [], node_count: 0, edge_count: 0}`
- `unit`: `core.tree()` on a one-theorem-no-deps KB returns one
  tree with empty `children`
- `integration`: `core.tree()` on the M9 fixture (def → lem → thm
  chain) returns three nested levels with correct `kind` and `status`
- `integration`: `core.tree("thm:t")` returns only that theorem's
  subtree; theorems not on the path are absent from `trees`
- `integration`: `core.tree(root)` with an unknown label returns
  `{trees: [], …}` (no exception, no 404 — the dashboard caller can
  fall back to all-roots mode)
- `integration`: shared dep — KB has two theorems both depending on
  `lem:shared`; `lem:shared` shows up under each theorem's children
  with `shared_parents` listing the other theorem
- `integration`: dangling `\ref{}` — admitted node references a label
  that's not in KB; the unresolved label appears in `children` with
  `status: "missing_from_nodes"` and `kind: null`. The dashboard's
  H29 boundary intent (admit + flag) flows through to this view.
- `integration`: corrupt-DAG defense — synthetically inject a 2-node
  cycle into Kuzu (bypass projector), call `core.tree()`, assert it
  returns within 1 s and emits `cycle_detected: true` on the second
  occurrence
- `integration`: `/api/tree` HTTP route returns 200 + the same JSON
  shape as `core.tree()`; `/api/tree?root=<label>` filters; `?root=`
  empty string is treated as "no root" (all theorems)
- `integration`: `/api/tree` returns 503 + `Retry-After: 5` during
  rebuild (matches `/api/theorems` behavior)
- `static`: `dashboard/server.py` module docstring lists `/api/tree`
  in the endpoint inventory

**Exit**

`/api/tree` is the single source of truth for proof-tree structure.
Frontend never reads `/api/theorems` or `/api/nodes` to build the
tree; it reads `/api/tree` once on load.

---

### M12.B — Foldable outline frontend

**Deliverables**

- `dashboard/templates/index.html` — new `<section id="proof_tree">`
  inserted between the existing "Knowledge base" stat row and the
  "Active jobs" table
- inline `<style>` (or new file referenced by template) defining the
  proof-tree CSS class system (see M12.C for the visual spec)
- inline `<script>` providing:
  - `loadProofTree()` — calls `/api/tree`, builds the DOM
  - `renderTreeNode(node, parentEl)` — recursive renderer using
    nested `<details>/<summary>` elements
  - `expandToDepth(n)` — utility to auto-expand the first `n` levels
    (default 2) on first load
  - integration with existing `showNode(label)` so clicking a node's
    label opens the per-node detail panel
- `dashboard/templates/__init__.py` — re-export the updated
  `INDEX_HTML` constant; existing template-loader plumbing stays the
  same

**Behavior**

- First load: fetch `/api/tree`, render every root theorem as a
  top-level `<details open>`. Auto-expand depth 2 (root + immediate
  deps), leave deeper subtrees collapsed by default to keep large
  trees readable.
- A node card shows: kind icon (kind-specific `<svg>` or unicode
  glyph), label as a clickable link, status pill, `pass/desired`
  badge, `repair_count` badge if `> 0`, `in_flight` spinner if true,
  shared-parents `↗` chip listing the other roots.
- MathJax v3 (chosen over KaTeX for wider LaTeX coverage and a clean
  ``typesetPromise([root])`` story under SSE-patched subtrees) renders
  any `$...$` / `\(...\)` / `$$...$$` / `\[...\]` inside the optional
  statement preview shown on hover. The existing `renderMath(root)`
  helper is the single typeset entry point; M12.D reuses it for
  badge-driven re-typesets.
- Operator's expansion state is preserved across live updates
  (M12.D): we re-render only the changed node's badges, not the
  surrounding tree, so the user's open `<details>` chevrons don't
  collapse.

**Tests**

- `unit` (Python, exercises `INDEX_HTML` content): the rendered
  template contains `id="proof_tree"`, references
  `loadProofTree()`, and is inserted at the documented DOM location
- `unit` (JavaScript via `node --test`): `renderTreeNode()` called on
  a single-node fixture produces a `<details>` with the right
  data-attributes (`data-node-label`, `data-node-kind`,
  `data-node-status`)
- `unit` (JS): `renderTreeNode()` called on a depth-3 fixture
  produces three nested `<details>` and the depth-3 child is
  collapsed by default (no `open` attribute)
- `unit` (JS): clicking the label of a tree node calls
  `showNode("the:label")` (verified via a stub that records the
  argument)
- `integration` (HTTP server up): hitting `/` returns HTML containing
  the `proof_tree` section; the section's `<script>` fires `loadProofTree()`
  on `DOMContentLoaded`
- `static`: every `kind` value the §3.5.1 enum admits has a
  corresponding `[data-node-kind="..."]` CSS rule in the template
  (catch new kinds added later that forget to update the visual)
- `static`: every status value in `dashboard/state.py:STATUS_*` has a
  corresponding `[data-node-status="..."]` CSS rule

**Exit**

A live workspace's proof tree renders correctly on first page load.
No live updates yet — that's M12.D.

---

### M12.C — Visual system (kind / status / badges)

**Deliverables**

- CSS (in `dashboard/templates/index.html` `<style>` or
  `dashboard/templates/static/proof_tree.css` if we extract):
  - one selector per kind: `[data-node-kind="theorem"] > summary`
    etc., setting border, background, and the kind icon via
    `::before` content
  - one selector per status: `[data-node-status="done"]` etc.,
    setting the status pill background + foreground color
  - badge styling: `.pass-badge`, `.repair-badge`, `.in-flight-badge`,
    `.shared-parents-chip`
  - hover / focus / active states for the `<summary>` row
  - mobile/narrow layout: `<details>` collapses left padding below
    700 px viewport so deeply-nested trees stay readable on a laptop

**Visual reference** (the canonical mapping; tests pin this)

| kind             | shape (border + icon) | base color  |
| ---------------- | --------------------- | ----------- |
| definition       | square + `def`        | gray        |
| proposition      | rounded + `prop`      | indigo      |
| lemma            | rounded + `lem`       | teal        |
| theorem          | hexagon + `thm`       | orange      |
| external_theorem | dashed hex + `ext`    | orange-dim  |

| status                          | pill bg     | pill text |
| ------------------------------- | ----------- | --------- |
| done                            | green-600   | white     |
| verified                        | green-300   | green-900 |
| needs_verification              | yellow-300  | gray-900  |
| blocked_on_dependency           | orange-400  | gray-900  |
| needs_generation                | gray-300    | gray-900  |
| generation_blocked_on_dependency| gray-500    | white     |
| user_blocked                    | red-400     | white     |
| in_flight                       | blue-400    | white (pulse animation) |

OkLCH values pinned in CSS variables under `:root` so a future theme
override replaces one block instead of rewriting selectors.

**Tests**

- `unit` (CSS-as-text): every required class exists in the rendered
  template
- `unit` (CSS): `:root` defines exactly the documented OkLCH custom
  properties (matches the table above)
- `unit` (JS): the `renderTreeNode()` output for a known fixture has
  the expected `data-node-kind` + `data-node-status` attributes that
  the CSS targets (catches a refactor that renames an attribute on
  one side)
- `unit` (JS): `repair_count > 0` produces a `.repair-badge` element
  with the number; `repair_count == 0` does not
- `unit` (JS): `in_flight: true` adds the pulse animation class
- `static`: a Python contract test in
  `tests/unit/test_proof_tree_contract.py` reads the template and
  asserts every status/kind in the §M9 vocabulary is reachable in
  CSS — i.e. adding a new status without a CSS rule fails CI

**Exit**

The static rendering is visually correct and accessibility-clean
(WCAG AA contrast at minimum on all status pills).

---

### M12.D — Live updates via SSE

**Deliverables**

- frontend `dashboard/templates/index.html` `<script>`:
  - new `subscribeProofTree()` function that opens an SSE connection
    to `/events/stream`
  - dispatch on the existing envelope `{type, ts, payload}`:
    - `applied_event` → re-fetch the affected node via
      `/api/node/{label}` and patch only its badges in the DOM
    - `truth_event.generator.batch_committed` → for any newly
      committed label not in the tree, fetch its subtree via
      `/api/tree?root=<root>` and splice in (no full reload)
    - `librarian_tick` → no DOM mutation; used as a heartbeat
      indicator only
  - debounce: collapse multiple events for the same label within a
    300 ms window into a single re-fetch
  - reconnect: on SSE disconnect, exponential backoff (1 s, 2 s, 5 s,
    capped at 10 s); show a "live updates paused — reconnecting…"
    chip in the section header during the gap
- backend: no changes (M9 SSE schema already covers this)

**State preservation**

The DOM patch path NEVER recreates the surrounding `<details>`
elements. It only mutates:

- the inner `.status-pill` text + class
- the `.pass-badge` text
- the `.repair-badge` (insertion / removal)
- the `.in-flight-badge` (insertion / removal)

User's open/closed `<details>` state is preserved by virtue of not
being touched.

**Tests**

- `unit` (JS, with a stubbed EventSource): one
  `applied_event` event for `lem:foo` triggers exactly one
  `/api/node/lem:foo` fetch, and its result patches the badges of
  the matching `<details>` element
- `unit` (JS): two events for the same label within 300 ms result in
  exactly one fetch (debounce)
- `unit` (JS): one event for a label NOT currently in the tree
  triggers a `/api/tree?root=<owning_root>` fetch and a splice; the
  rest of the tree DOM is unchanged
- `unit` (JS): SSE disconnect is followed by retries on the
  documented backoff schedule; the "reconnecting" chip is shown then
  hidden
- `integration` (HTTP server + real SSE): publish a fake
  `verifier.run_completed(verdict=accepted)` truth event in a
  fixture workspace; assert the dashboard's `/events/stream`
  delivers an `applied_event` envelope; the test's headless fetch
  client confirms the next `/api/node/<label>` call shows
  `pass_count` advanced. No browser; the test only validates the
  server-side data flow.
- `system` (optional, `e2e-runner` agent): Playwright opens the
  dashboard, asserts the tree renders, publishes a
  `verifier.run_completed` event from the test, and within 2 s the
  affected node's pill changes color in the DOM. Quarantined behind
  `RETHLAS_E2E=1` env flag so CI without browsers stays green.

**Exit**

Operator can leave the dashboard open while supervise runs and watch
the tree change colors in real time without page reloads.

---

### M12.E — Polish

**Deliverables** (each independently shippable; pick whichever the
operator hits friction on first)

1. **Search** — input above the tree filters labels (substring,
   case-insensitive). Matched nodes get a `.search-match` highlight;
   non-matched nodes' parents auto-expand so matches are visible.
2. **Status filter chips** — toggle chips for each status above the
   tree; clicking `done` hides done nodes, etc. Filters are
   client-side; URL hash records active filters
   (`#filters=needs_verification,in_flight`) so a refresh restores
   them.
3. **Auto-expand on attention** — `in_flight` and any non-`done`
   non-`verified` status auto-expands its ancestor chain on first
   render and on transition into that status (so a new failure pops
   open without the operator hunting).
4. **"Also under" navigation** — clicking the `↗` chip on a
   shared-deps node scrolls smoothly to the same node under its
   other parent and briefly flashes it.
5. **Per-node mini sparkline** — last 10 verifier verdicts as 10 tiny
   colored cells next to the status pill, so a flapping node is
   visible without opening the detail panel.

**Tests**

For each shipped sub-feature, at minimum:

- `unit` (JS): pure rendering + state-mutation test
- `integration`: end-to-end through `/api/tree` + SSE
- `static`: CSS classes referenced by JS exist in the template

**Exit**

Phase II is "done enough" once at least items 1, 2, and 3 from above
are shipped + tested. Items 4 and 5 are nice-to-have and may roll into
Phase III.

---

## Test Gate Summary

| Milestone | Required green tests                              |
| --------- | ------------------------------------------------- |
| M12.A     | All `unit` + `integration` listed under M12.A     |
| M12.B     | All M12.A + M12.B tests; `static` template gate    |
| M12.C     | Add CSS contract test                             |
| M12.D     | Add SSE-driven live-update tests                  |
| M12.E     | Per-feature tests as listed                       |

Phase II is shippable when M12.A through M12.D are all green and at
least three of the M12.E sub-features are landed with their tests.

The Phase I 471-test baseline plus M12 additions must all pass with
`pytest tests/ -q` returning 0 failures.

---

## Phase II Done Criteria

1. `/api/tree` is documented in ARCH §6.7.1 and serves the contract
   shape from M12.A on every workspace.
2. Dashboard loads the proof tree on first paint within 1 s on a
   100-node KB.
3. Live SSE updates reflect a new verifier verdict in the DOM within
   2 s of the event landing on disk (locally — networked hosts add
   their own latency).
4. Operator's expansion state survives at least 100 consecutive live
   updates without a single forced collapse.
5. CSS contract test enforces that every kind + status in the §M9
   vocabulary has a visual rule.
6. The dashboard remains read-only — no Phase II milestone introduces
   a writable HTTP method (POST / PUT / DELETE) or a new truth-event
   producer.

---

## M13 — DAG Proof Graph (Phase II.5)

Phase II.5 ships *after* M12 lands and is observed in production for
at least one full toy-problem run. The goal is a complementary
view: where M12 reads the proof state as a hierarchical outline
rooted at theorems, M13 shows the same KB as a true DAG so shared
lemmas, multi-parent dependencies, and graph-shape regressions are
visible at a glance.

The two views share **all** of M12's infrastructure: the OkLCH design
tokens, the SSE listener + debounce, the `showNode(label)` detail
plumbing, and the kind/status vocabulary contract test. M13 adds a
graph endpoint and a Cytoscape-driven canvas; nothing in M12 is
duplicated.

### Shared infrastructure produced by M12 (reused by M13)

| Asset                                  | Source           | M13 use                             |
| -------------------------------------- | ---------------- | ----------------------------------- |
| OkLCH `:root` design tokens            | M12.C            | identical kind/status colors on graph nodes |
| `[data-node-kind]` / `[data-node-status]` enum coverage test | M12.C `test_proof_tree_contract` | extend to assert graph nodes carry the same data attributes |
| `showNode(label)` detail-panel helper  | existing pre-M12 | click handler on graph nodes        |
| SSE envelope dispatch + 300 ms debounce | M12.D `subscribeProofTree()` | factor into a shared module both views import |
| `/api/node/{label}` patch endpoint     | existing M9      | identical re-fetch on `applied_event` |

Therefore M12 must be implemented in a way that exposes its SSE
debounce + the design tokens as reusable units — not deeply hard-coded
into the outline render path. Concrete deliverable: M12.D's
`subscribeProofTree()` lives in a function that takes a "patch one
node" callback, so M13 can pass its own graph-aware patch function
without duplicating the dispatch logic.

### M13.A — `/api/graph` endpoint

**Deliverables**

- `dashboard/server.py`
  - new `DashboardCore.graph(root: str | None) -> dict` returning a
    flat `{nodes:[…], edges:[…]}` shape ready for Cytoscape ingest
  - new HTTP route `GET /api/graph` (no args → entire KB; `?root=<label>`
    → connected component reachable downward from that label)
  - `docs/ARCHITECTURE.md` §6.7.1 endpoint inventory updated

**Response shape**

```jsonc
{
  "ts": "2026-04-27T00:00:00.000Z",
  "nodes": [
    {
      "id": "thm:goal",
      "label": "thm:goal",
      "kind": "theorem",
      "status": "needs_verification",
      "pass_count": 0,
      "repair_count": 0,
      "desired_pass_count": 3,
      "in_flight": false
    }
  ],
  "edges": [
    {
      "id": "thm:goal__depends_on__lem:helper",
      "source": "thm:goal",
      "target": "lem:helper",
      "resolved": true        // false for dangling \ref{} (H29 admitted but no Kuzu edge)
    }
  ],
  "node_count": 6,
  "edge_count": 5
}
```

**Tests** (mirror M12.A's structure)

- `unit`: empty KB → empty arrays
- `unit`: single-node KB → 1 node, 0 edges
- `integration`: M9 fixture (def → lem → thm) → 3 nodes, 2 edges,
  edge `target` always equals an existing node `id`
- `integration`: shared dep — `lem:shared` depended on by two
  theorems → 1 node, 2 edges; no duplicate node entries
- `integration`: dangling `\ref{}` → 1 edge with `resolved: false`,
  the dangling label appears as a `node` with `kind: null` and
  `status: "missing_from_nodes"`
- `integration`: corrupt-DAG cycle defense — same fixture as M12.A,
  endpoint completes within 1 s without infinite loop
- `integration`: HTTP route + 503 on rebuild + filter by root
- `static`: docstring lists `/api/graph` in §6.7.1 inventory

### M13.B — View toggle in dashboard

**Deliverables**

- `dashboard/templates/index.html`
  - the existing `<section id="proof_tree">` gains a tab strip:
    `[ Outline ] [ Graph ]` with the active view persisted to
    `localStorage` (`rethlas-proof-view: outline|graph`) so a refresh
    keeps the operator's preference
  - hidden-by-default `<div id="proof_graph_canvas">` mounted lazily
    when the operator first picks the Graph tab (avoids loading
    Cytoscape on operators who never click)

**Tests**

- `unit` (JS): clicking the Graph tab calls `loadProofGraph()` once
- `unit` (JS): refresh after picking Graph restores the Graph tab
  (localStorage round-trip with a stub)
- `static`: template references `proof_graph_canvas` and the tab
  strip; tab labels match the visual spec

### M13.C — Cytoscape + dagre rendering

**Deliverables**

- vendored Cytoscape.js + cytoscape-dagre + dagre **and** the M12-era
  MathJax v3 bundle (currently CDN-loaded) as static assets under
  `dashboard/templates/static/` (no CDN dependency at runtime —
  supervise must work air-gapped). Vendoring all three together
  amortizes the static/-directory plumbing cost across one milestone.
- `dashboard/templates/static/proof_graph.js` exporting
  `renderProofGraph(canvasEl, graphJson, callbacks)` and
  `patchGraphNode(label, nodeJson)`
- inline `<script>` boot path in `index.html` that calls these
- node styling shares the M12.C OkLCH tokens (no separate palette);
  shape mapping by kind:
  - definition → square
  - proposition → ellipse
  - lemma → round-rectangle
  - theorem → hexagon
  - external_theorem → dashed hexagon
- edge styling: solid for `resolved: true`, dotted for `resolved: false`
- layout: `dagre` top-down by default, preset to `LR` (left-to-right)
  if the graph is wide enough to make TB cramped; controls let the
  operator switch between `dagre`, `breadthfirst`, and `cose`
- click on a node → existing `showNode(label)`

**Tests**

- `unit` (JS): `renderProofGraph()` on a 1-node fixture renders one
  Cytoscape element with the right `data` payload
- `unit` (JS): `patchGraphNode()` mutates only the targeted node's
  `data.status` + `data.pass_count`; unrelated nodes untouched
- `unit` (JS): a `resolved: false` edge gets the `dotted` line style
  class
- `integration`: HTTP fetch of `/api/graph` followed by render
  produces a Cytoscape instance with `nodes().length === 6` on the
  toy fixture
- `static`: shape-mapping test — every `kind` enum value has a
  Cytoscape style rule
- `static`: vendored Cytoscape file SHA + version pinned in a
  `dashboard/templates/static/VERSIONS.txt` so a silent CDN swap
  can't drift the bundle

### M13.D — Live updates on graph

**Deliverables**

- the M12.D `subscribeProofTree()` refactor produces a generic
  `subscribeProofState(patchOneNode, addOrSpliceTree)` wrapper; M13
  passes graph-aware callbacks (`patchGraphNode`,
  `addGraphSubgraph`)
- adding a new node via `truth_event.generator.batch_committed`:
  M13 fetches `/api/graph?root=<owning_root>` and merges new nodes
  + edges into the existing Cytoscape instance without re-running
  layout from scratch (incremental layout via dagre's "preset"
  positions)

**Tests**

- `unit` (JS): one `applied_event` for `lem:foo` calls
  `patchGraphNode("lem:foo", …)` exactly once
- `unit` (JS): `truth_event.generator.batch_committed` adding two
  new helpers triggers `addGraphSubgraph` with both labels
- `integration`: SSE-driven verdict change is visible in the graph
  within 2 s (mirrors M12.D)
- `system` (optional): Playwright opens the Graph tab, publishes a
  fake verdict, asserts the node's color class changes

### M13.E — Polish

1. **Layout switcher** chips above the canvas
2. **Zoom-to-fit** + **center on selection** buttons
3. **Hover highlight** — on hover, the node's full upstream
   (ancestors) and downstream (dependents) get a `.path-highlight`
   class; everything else dims
4. **Search** — the M12.E search input becomes a shared input that
   also drives the graph (matched node centered + flashed)
5. **Cycle / drift warning banner** — if `/api/graph` returns
   `cycle_detected: true` or any `resolved: false` edges, a banner at
   the top of the section flags it, since both indicate corruption /
   pending repair

Acceptance:

- M13 is shippable when M13.A through M13.D are green and at least
  items 1, 2, 3 from M13.E are landed.
- Phase II + II.5 together must keep
  `pytest tests/ -q` returning 0 failures.

### M13 Done Criteria

1. `/api/graph` endpoint documented in ARCH §6.7.1.
2. Graph view loads and lays out a 100-node KB within 2 s on a
   modern laptop.
3. Live SSE updates reflect a new verifier verdict in the graph DOM
   within 2 s.
4. Outline ↔ Graph tab swap preserves operator state (scroll
   position on outline, viewport + selection on graph) across the
   swap.
5. Vendored Cytoscape bundle is checksum-pinned and works
   offline (no CDN required during supervise).
6. Read-only contract still holds — no Phase II.5 code adds a
   writable HTTP method.

---

## Notes

- **Why not Cytoscape (in M12)** — discussed in conversation prior
  to this plan: a force-directed graph is impressive but harder to
  read at a glance than a foldable outline rooted at theorems. M12
  ships the outline. Cytoscape arrives in M13/Phase II.5 as the
  complementary view, not the replacement.
- **Why not server-rendered HTML** — Phase II requires liveness, and
  a full SSR cycle on every event would either tear the operator's
  expansion state or require server-side session storage. JS-driven
  tree + targeted DOM patches is simpler and matches the §6.7.1
  read-only contract.
- **Why no new event types** — the existing M9 SSE envelope already
  covers everything the proof-tree view needs to react to. Adding
  events would invalidate the Phase I "no projector changes for
  Phase II" delivery principle.
- **Cycle defense** — the projector never admits a cycle (post-H29
  REASON_CYCLE rejection covers self-loops + multi-node), but the
  dashboard MUST still defend against a corrupt KB. M12.A's
  cycle-detected test pins this.
- If Phase II uncovers an architectural issue (e.g. the SSE schema
  needs a new envelope type for partial node patches to be
  efficient), open an SKILL_AUDIT entry and update ARCH §6.7.1
  before changing the schema.
