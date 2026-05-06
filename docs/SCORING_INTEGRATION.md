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

## 3. Turning it on (Phase B — landed in this branch)

**Wiring is now in place.** Set the toggle in `rethlas.toml`:

```toml
[scheduling]
use_voi_scoring = true   # default false
```

What the wiring does — see [coordinator/main.py](../coordinator/main.py)
`_build_priority_fn` (immediately after `_snapshot_kb`) and the dispatch
site below it:

- Builds a `ProofGraph` from the current `_KBSnapshot.candidates` each
  tick. Each `ScoredNode` carries the candidate's `statement` as
  `claim_text` and the `dep_statement_hashes` keys as `depends_on`.
- Calls `make_priority_fn(graph, roc=perfect_verifier_roc(), cluster=...)`
  and passes the result as `priority_fn=` into `select_verifier_targets`.
- Returns `None` (→ legacy order) when the toggle is off, the snapshot
  is empty, or anything inside the scoring layer raises. The dispatcher
  itself also has a `try/except` guard, so two layers of defence keep
  the scoring path from starving dispatch.
- **Tier-strict BFS by ``pass_count``** (S1, landed): the dispatcher
  groups candidates by ``pass_count`` and walks tiers low → high,
  invoking ``priority_fn`` once per tier. This guarantees no node enters
  pass ``k+1`` while another node still has pass ``k`` outstanding —
  the user's "all nodes get pass 1 before anyone starts pass 2"
  constraint. See ``docs/SCORING_SCHEDULING.md §3``.

Phase B intentionally uses neutral placeholders — `posterior_p=0.5`,
`embedding=()`, `perfect_verifier_roc()`. Cluster propagation is
inactive without embeddings; calibration is short-circuited until
verifier confidence + ground-truth feedback exist. The VOI signal is
still informative because it pulls each node's full
`relevance_cone` (the dependency closure) into Z. Phase C lifts those
placeholders.

Tests covering the wiring:

- [tests/scoring/test_config.py](../tests/scoring/test_config.py) —
  parser accepts `use_voi_scoring`, defaults to `false`, rejects
  non-bool.
- [tests/scoring/test_main_wiring.py](../tests/scoring/test_main_wiring.py) —
  `_build_priority_fn` returns `None` when toggle off / snapshot empty /
  scoring layer crashes; returns a callable otherwise.
- [tests/scoring/test_dispatcher_integration.py](../tests/scoring/test_dispatcher_integration.py) —
  dispatcher honours, omits, and recovers from `priority_fn`.

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

> **Design note (2026-05-06).** The original draft proposed a 5%
> "audit sampling" loop to estimate the LLM verifier's `(p_tpr, p_fpr)`.
> That is **rejected**: a proof is binary truth, not a probability,
> and any LLM-vs-LLM audit just kicks the ground-truth question up a
> level. Calibration learning (`VerifierROC.update`,
> `IsotonicCalibrator.add`) is reserved for the cases where real
> ground truth is available — in this project that means **human
> spot-check only** (Lean is out of scope per the user's "纯自然语言"
> directive). In pure-LLM mode the scheduler does not estimate noise —
> it **escalates verification depth** instead.

These items are **out of scope** for this branch:

| Need | File to touch | Why |
|------|---------------|-----|
| `verdict ∈ {ok, fail, abstain}` (or richer) on every verdict | [verifier/decoder.py](../verifier/decoder.py), [verifier/role.py](../verifier/role.py) | lets the policy decide whether to escalate before declaring `verified` |
| Ensemble k=3 dispatch | [coordinator/main.py](../coordinator/main.py), [common/runtime/jobs.py](../common/runtime/jobs.py) | independent passes feed the policy's "do they agree?" check |
| **Adaptive verifier policy** (new) | new `rethlas_scoring/policy.py` + hook in [coordinator/main.py](../coordinator/main.py) | scheduler decides per-node what verification to run next: another LLM pass, refute task, escalate to stronger model, or mark `user_blocked`. Replaces the rejected audit-sampling loop. |
| `claim_text` + `embedding` in KB snapshot | [common/kb/types.py](../common/kb/types.py), [librarian/projector.py](../librarian/projector.py) | required for cluster propagation + bridge audit |
| Refute task worker | new `refute/role.py` mirroring `verifier/role.py` | DESIGN §8; consumed by the policy as an escalation step |
| Bridge frontier detection | future M14+ in [librarian/projector.py](../librarian/projector.py) | DESIGN §6 |

Each of these is independently shippable; the new scoring layer
degrades gracefully (cluster disabled when embeddings are empty,
`BridgeAudit` simply isn't called, policy falls back to fixed
`desired_pass_count`) until they exist.

### 5.1 Adaptive verifier policy

详见 [`SCORING_SCHEDULING.md §7`](SCORING_SCHEDULING.md) —— 那里有完
整的 deterministic state machine 设计(`Evidence` 累积 + `classify` /
`next_action` 纯函数,无任何概率阈值)。本节(以前的草稿)已合并过去。

集成关系:VOI(L4)决定**哪个**节点先动,policy(L5)决定那个节点
**下一步做什么动作**(default verify / refute / strong / user_blocked)。

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
