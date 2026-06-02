---
name: rethlas-referee
description: Review mathematical correctness, citation applicability, and source-backed node quality for Rethlas referee_report_v1 jobs. Use when a Rethlas referee job asks to check statements, proofs, dependencies, source extraction, theorem graphs, typo findings, or recommended KB updates.
---

# Rethlas Referee

Use this skill when reviewing mathematical correctness, citation applicability,
or source-backed node quality.

## Workflow

1. Bind the review to target hashes and cited source spans.
2. Do not edit files. Referee jobs are read-only and must end with one JSON
   object on stdout.
3. Stabilize the statement, hypotheses, notation, and dependency snapshot.
4. Check proof steps in order and separate source omissions from verified
   reconstructed jumps.
5. For an already published source, accept cited literature provisionally as
   published-source premises unless the local context is internally suspect.
   Do not require external bibliography access merely to admit a source-backed
   statement. Record loose citation details, but focus on whether the statement,
   hypotheses, and notation are correctly extracted from the reviewed source.
6. Use surrounding context and proof usage to stabilize the statement. If the
   theorem line is ambiguous, infer only what the local source context supports.
7. Emit requested-detail issues when the source statement or notation is unclear.
8. For source-level reviews, extract a theorem graph:
   - `theorem_nodes` for the main definitions, assumptions, lemmas,
     propositions, theorems, conjectures, corollaries, remarks, and bridge requests.
     Choose labels that are stable across reruns by combining source id, kind,
     and locator/title information.
     Use status from `proved`, `conditional`, `conjectural`, `assumed`, `gap`,
     `wrong`, `context`, `source_claim`, and `review_only`.
     Include implicit prose paragraphs when they function as durable
     definitions, notation conventions, assumptions, unnamed lemmas,
     criteria, equivalences, reductions, constructions, or theorem-like claims.
     Mark explicit environments with `extraction_kind: "explicit_environment"`.
     For explicit theorem/proposition/lemma/conjecture/assumption/definition
     nodes, preserve the original source statement in `source_excerpt`,
     including hypotheses and displayed formulas. Use `statement` only for a
     compact review summary when useful. If the source statement has displayed
     formulas, include the relevant formula block(s) in `formula_excerpt`.
     Keep these fields as source evidence. For hard-to-read PDF/OCR output,
     use `$rethlas-node-typesetting` and add `display_source_excerpt`,
     `display_formula_excerpt`, and `typesetting_notes` for dashboard display
     without overwriting the raw evidence.
     Mark paragraph-derived nodes with `extraction_kind: "implicit_paragraph"`,
     `source_locator`, and `source_note`; these two fields are required for
     paragraph-derived nodes. Include `promotion_confidence` and
     `overpromotion_risk` when the promotion is judgment-sensitive.
     External theorem nodes are review-only by default; use
     `scope: "review_only"` unless proposing a later KB candidate.
   - `theorem_dependency_edges` for logical use: each edge points from
     `dependency` to `dependent`. Use relation from `uses`, `assumes`,
     `depends_on`, `proves`, `reduces_to`, `needs_bridge`,
     `supports_verdict`, `cites`, `proves_injectivity`, and
     `proves_exhaustivity`.
   - `node_location_notes` for paper/thesis positions: page, section, theorem
     number, line/span ids, and a short note.
   - `typo_findings` for typos, notation drift, wrong cross-references,
     suspicious signs/exponents, and OCR-uncertain formulas.
9. Check paragraph-derived nodes for over-promotion: a remark should not become
   a theorem unless later proof usage or source wording supports that role.
10. Put recommended KB changes in `recommended_kb_updates`; do not write node
   events directly.

## Verdict Discipline

Allowed verdicts:

- `accepted`
- `accepted_with_minor_gaps`
- `needs_revision`
- `major_gap`
- `wrong`
- `needs_external_reference_access`

Unresolved blocker or major issues about the extracted statement are
incompatible with acceptance. Missing external bibliography access is not a
blocker for published-source study admission unless it prevents determining the
statement or hypotheses.

## Required Output

Return one raw JSON object with `output_schema: "referee_report_v1"`. Do not
wrap it in markdown.
