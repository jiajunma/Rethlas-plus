# Rethlas Referee

Use this skill when reviewing mathematical correctness, citation applicability,
or source-backed node quality.

## Workflow

1. Bind the review to target hashes and cited source spans.
2. Stabilize the statement, hypotheses, notation, and dependency snapshot.
3. Check proof steps in order and separate source omissions from verified
   reconstructed jumps.
4. For an already published source, accept cited literature provisionally as
   published-source premises unless the local context is internally suspect.
   Do not require external bibliography access merely to admit a source-backed
   statement. Record loose citation details, but focus on whether the statement,
   hypotheses, and notation are correctly extracted from the reviewed source.
5. Use surrounding context and proof usage to stabilize the statement. If the
   theorem line is ambiguous, infer only what the local source context supports.
6. Emit requested-detail issues when the source statement or notation is unclear.
7. Put recommended KB changes in `recommended_kb_updates`; do not write node
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
