# SCORING_INTEGRATION — wiring `rethlas_scoring/` into the coordinator

> Configures the rollback-safe `priority_fn` hook described in
> `SCORING_AUDIT.md §4.2`. Companion to `SCORING_DESIGN.md`,
> `SCORING_HANDOFF.md`, and `SCORING_AUDIT.md`.

---

## 1. What landed in this change

| Layer | File | Action |
|-------|------|--------|
| New scoring package | [rethlas_scoring/](../rethlas_scoring/) | 9 modules, pure stdlib |
| Tests | [tests/scoring/](../tests/scoring/) | 49 tests, runs in <0.1 s |
| Dispatcher hook | [coordinator/dispatcher.py](../coordinator/dispatcher.py) | new optional `priority_fn` parameter (lines 24–35, 65–115) |
| Docs | `docs/SCORING_{DESIGN,HANDOFF,AUDIT,INTEGRATION}.md` | this set |

Existing dispatcher behaviour is **byte-identical** when `priority_fn=None`
(default) — the legacy `(pass_count, label)` ordering still applies.
[tests/unit/test_m8_dispatcher.py](../tests/unit/test_m8_dispatcher.py)
(7 tests, unchanged) passes alongside the new suite.

---

## 2. The hook

```python
# coordinator/dispatcher.py
PriorityFn = Callable[[Sequence[str], int], list[str]]

def select_verifier_targets(
    candidates,
    *,
    capacity,
    in_flight_targets,
    priority_fn: PriorityFn | None = None,   # ← new
) -> list[str]: ...
```

- Same dedup (`label → min pass_count`) and `in_flight_targets` skip
  rules in both branches.
- If `priority_fn` raises or returns nothing → automatic fallback to the
  legacy ordering. The dispatcher contract cannot regress.
- Labels the priority_fn omits are appended in legacy order so a
  buggy scorer **cannot starve** a node.

---

## 3. Turning it on (Phase B — cold)

In `coordinator/main.py` around the existing
[`select_verifier_targets`](../coordinator/main.py) call site (line 866):

```python
from rethlas_scoring.scheduler import make_priority_fn
from rethlas_scoring.calibration import perfect_verifier_roc
from rethlas_scoring.cluster import ClusterIndex
from rethlas_scoring.data import ProofGraph, ScoredNode

# ... existing code ...

if state.config.scheduling.use_voi_scoring:
    sg_nodes = {
        c.target: ScoredNode(
            id=c.target,
            claim_text=c.target,            # placeholder
            embedding=(),                   # cluster disabled until embeddings land
            posterior_p=0.5,                # neutral prior
        )
        for c in snapshot.candidates
    }
    sgraph = ProofGraph.build(sg_nodes)
    pfn = make_priority_fn(
        graph=sgraph,
        roc=perfect_verifier_roc(),         # placeholder until calibration data exists
        cluster=ClusterIndex().build(sgraph),
        n_voi_samples=200,
    )
else:
    pfn = None

ver_targets = select_verifier_targets(
    ver_pool,
    capacity=ver_capacity,
    in_flight_targets=in_flight_targets,
    priority_fn=pfn,
)
```

Add `use_voi_scoring: bool = False` to the `[scheduling]` section of
`rethlas.toml` (existing `state.config.scheduling` dataclass — extend in
[common/config/](../common/config/)).

This wiring is intentionally **not** committed in this branch — it
crosses module boundaries and warrants its own PR with a config-loader
test. Treat the pseudocode above as a recipe.

---

## 4. Rollback

Three rollback levels, smallest blast first:

1. **Per-process**: set `use_voi_scoring = false` in `rethlas.toml` and
   restart `rethlas supervise`. No code change.
2. **Per-deploy**: revert just the `coordinator/main.py` wiring patch;
   the dispatcher hook is dormant when nothing supplies `priority_fn`.
3. **Total**: revert `coordinator/dispatcher.py` to its pre-change form.
   The `rethlas_scoring/` package keeps working as a standalone library
   for offline analysis; deletion is also safe (no other module imports
   it).

---

## 5. Phase C — what still needs verifier-side changes

These items are **out of scope** for this branch but unlock the full
VOI signal:

| Need | File to touch | Why |
|------|---------------|-----|
| `confidence: float` on every verdict | [verifier/decoder.py](../verifier/decoder.py), [verifier/role.py](../verifier/role.py) | feeds `IsotonicCalibrator` + `EnsembleVerifier` |
| Ensemble k=3 dispatch | [coordinator/main.py](../coordinator/main.py), [common/runtime/jobs.py](../common/runtime/jobs.py) | DESIGN §7 — Dawid-Skene needs ≥3 votes |
| Ground-truth feedback loop (~5% sample → strong verifier) | new `verifier/audit_role.py` | trains `VerifierROC` per bucket |
| `claim_text` + `embedding` in KB snapshot | [common/kb/types.py](../common/kb/types.py), [librarian/projector.py](../librarian/projector.py) | required for cluster propagation + bridge audit |
| Refute task worker | new `refute/role.py` mirroring `verifier/role.py` | DESIGN §8 |
| Bridge frontier detection | future M14+ in [librarian/projector.py](../librarian/projector.py) | DESIGN §6 |

Each of these is independently shippable; the new scoring layer
degrades gracefully (cluster disabled when embeddings are empty, VOI
falls back to neutral prior, `BridgeAudit` simply isn't called) until
they exist.

---

## 6. Performance

Smoke profile from `tests/scoring/`:

- 49 tests, including 4 VOI MC runs at N=2000–4000 samples, complete in
  **< 0.1 s** total on a fresh Python 3.14 interpreter.
- Per-call `voi_node` at N=200 on a 3-node graph is sub-millisecond;
  scaling is `O(N · |relevance_cone|)`.

For a real workspace at ~200 nodes with `n_voi_samples=200`, expect
~10–40 ms added to each dispatch tick when `priority_fn` is on. The
legacy path stays at < 1 ms. Tune via `DEFAULTS.voi_mc_samples` or pass
`n_voi_samples=` directly through `make_priority_fn`.

---

## 7. Testing

```bash
# Scoring suite (no rethlas-CLI dependencies)
~/myenv/bin/python -m pytest tests/scoring/ -v

# Plus dispatcher regression
~/myenv/bin/python -m pytest tests/scoring/ tests/unit/test_m8_dispatcher.py
```

Last green run on this branch: **56/56 in 0.08 s**.
