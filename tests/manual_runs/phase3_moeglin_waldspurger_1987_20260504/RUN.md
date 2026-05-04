# Phase 3 Learner Manual Run: Moeglin-Waldspurger 1987

This workspace preserves real learner runs for:

`C. Moeglin and J.-L. Waldspurger, Modeles de Whittaker degeneres pour des groupes p-adiques, Math. Z. 196 (1987), 427-452.`

The PDF is referenced by symlink at `sources/moeglin_waldspurger_whittaker/paper.pdf`.
Original PDF SHA256:

```text
5faa6797d9d76af54f2ddb50409bde87e498d58bd3d51b760bac0bbedac6fbc8
```

## Saved Results

- Extracted full text: `sources/moeglin_waldspurger_whittaker/full_text.txt`
- Initial introduction context: `sources/moeglin_waldspurger_whittaker/learner_context.json`
- Body-results context: `sources/moeglin_waldspurger_whittaker/learner_context_body_results.json`
- Body-gaps context: `sources/moeglin_waldspurger_whittaker/learner_context_body_gaps.json`
- II.1.3 corollary context: `sources/moeglin_waldspurger_whittaker/learner_context_ii_1_3_corollary.json`
- Learner artifacts: `knowledge_base/phase3/learner_batches/*.json`
- Codex logs: `runtime/logs/*.codex.log`
- Linter report: `runtime/state/linter_report.json`

Saved learner batches:

- `20260504T054552.177-0001-0b6b3aaf5ca36d7f`: 6 candidates, 6 verification requests, 6 issues.
- `20260504T063645.797-0001-62c81532048d8dfc`: 11 candidates, 0 bridge requests, 8 issues.
- `20260504T064715.061-0001-ac1c2dc49c1eb9cc`: 2 candidates, 0 bridge requests, 2 issues.
- `20260504T065136.081-0001-c5be066c0024dcf4`: 1 candidate, 0 bridge requests, 1 issue.
- `20260504T080624.794-0001-befc0efe3d96975c`: 2 proof-enrichment candidates, 1 bridge request, 2 issues.

Total preserved output: 22 candidate records, 20 unique labels, 6 verification requests, 1 bridge request, 19 issues.
The manual workspace linter reported 0 violations after all five batches.

## Candidate Labels

- `def:degenerate_whittaker_setup`
- `def:degenerate_whittaker_forms`
- `def:whittaker_orbit_sets`
- `ext:harish_chandra_local_character_expansion`
- `ext:rodier_whittaker_model_criterion`
- `ext:moeglin_waldspurger_main_theorem`
- `lem:mw_i_3_compact_subgroup_bch`
- `lem:mw_i_6_character_stabilizer`
- `lem:mw_i_10_nonzero_invariants`
- `prop:mw_i_11_orbit_from_whittaker`
- `lem:mw_i_12_dimension_coefficient`
- `lem:mw_i_13_trivial_action`
- `prop:mw_i_14_jacquet_injection`
- `lem:mw_i_15_transition_injective`
- `prop:mw_ii_1_3_regular_principal_series_orbits`
- `prop:mw_ii_1_3_whittaker_dimension_formula`
- `prop:mw_ii_2_gl_n_unique_nilpotent_orbit`
- `prop:mw_ii_3_1_small_rank_orbits`
- `lem:mw_ii_3_2_concentration_criterion`
- `lem:mw_ii_3_3_rank_projection`

## Notes

The first run learned only 6 nodes because its context intentionally covered the introduction and first pages, and `max_nodes=6` capped output. The later runs used focused spans from the body of the paper and increased the preserved result set to 20 candidates.

Large OCR spans are expensive and slow. The second run used 14 body-result spans and eventually succeeded, but small targeted batches were more reliable for recovering missed statements. The text contains PDF page-break control characters, so displayed `rg` or `nl` line numbers may differ from Python `splitlines()` line numbers; regenerate spans from explicit matched text when accuracy matters.

The runs are conservative. OCR-damaged formulas, matrix displays, bibliography-dependent claims, and proof jumps are recorded as issues rather than silently promoted as verified facts. These artifacts are learner proposals, not referee-approved theorem nodes.

Most proof-requiring candidates in the first four preserved batches were extracted
before the learner prompt required proof reconstruction, so many have empty
`proof` fields and should be treated as statement-level study nodes. The fifth
batch demonstrates the proof-enrichment mode on I.11 and I.12: it reuses the
existing labels, adds `proof_status`, `proof_steps`, and `depends_on`, and emits
a bridge request for the unreconstructed measure-cancellation step in I.12.

## Reuse

Inspect the preserved artifacts without spending model tokens:

```bash
uv run --python /opt/local/bin/python3.11 python -m cli.main \
  --workspace tests/manual_runs/phase3_moeglin_waldspurger_1987_20260504 \
  linter
```

Run a new learner attempt only when intentionally spending new model tokens. Materialize agents first because the preserved fixture ignores generated agent templates:

```bash
uv run --python /opt/local/bin/python3.11 python - <<'PY'
from pathlib import Path
from common.runtime.agents_install import materialize_agents
materialize_agents(
    workspace_root=Path("tests/manual_runs/phase3_moeglin_waldspurger_1987_20260504"),
    overwrite=True,
)
PY

uv run --python /opt/local/bin/python3.11 python -m cli.main \
  --workspace tests/manual_runs/phase3_moeglin_waldspurger_1987_20260504 \
  learner \
  --source src:moeglin_waldspurger_whittaker_1987 \
  --context-json tests/manual_runs/phase3_moeglin_waldspurger_1987_20260504/sources/moeglin_waldspurger_whittaker/learner_context_body_gaps.json \
  --max-nodes 5 \
  --silent-timeout-s 600 \
  --actor learner:mw-body-gaps
```
