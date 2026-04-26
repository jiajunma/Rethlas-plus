---
name: search-math-results
description: Find relevant math results, constructions, examples, counterexamples, and background references for a statement. Use when you need context for a new problem, supporting references for constructing examples or counterexamples, or external results while proving subgoals.
---

# Search Math Results

Use this skill as the default retrieval workflow for mathematical background and related results.

## Input Contract

Read:

- the current target statement, subgoal, lemma, or claim
- the search intent:
  - `theorem`
  - `construction`
  - `example`
  - `counterexample`
  - `background`
- relevant branch/subgoal context from memory

## Procedure

1. Start with `search_arxiv_theorems`.
2. When using `search_arxiv_theorems`, phrase the query as a complete mathematical statement whenever possible.
3. Inspect the returned items and decide whether they are useful for the current need.
4. If a useful theorem/example/counterexample is found and it comes from a paper, use only information returned by the available search tool and any already-available verified nodes. Do not download PDFs or write files.
5. If a useful theorem is found, do not stop at the statement alone. Extract any available proof ideas, constructions, reductions, or proof patterns that may help with the current target statement.
6. Expand the definitions and concepts appearing in that theorem using the surrounding context of the paper, and check carefully whether the theorem is actually applicable to the current situation. Be explicit about terminology that may shift across contexts.
7. Record not only what the theorem says, but also what its proof suggests for the current problem when that information is available.
8. If the theorem search returns no useful information, report that clearly and continue with non-search reasoning skills.
9. Summarize the most useful findings and explain why they matter for the current proof state.
10. If a result may later be used in a proof, preserve its full statement and source identifiers so downstream proof steps can cite it explicitly.

## Usefulness Test

Treat theorem-search results as useful only if they do at least one of the following:

- provide a theorem/lemma/definition close to the target statement
- provide a construction/example/counterexample that can be adapted
- suggest a standard technique or reformulation relevant to the current branch

If the results are vague, off-topic, or too weak to guide the next step, stop retrieval for now and switch back to examples, counterexamples, decomposition, or direct proof search.

## Output Contract

Append a summary record to `scratch_events`:

```json
{
  "event_type": "search_math_results",
  "query": "...",
  "search_intent": "theorem|construction|example|counterexample|background",
  "primary_tool": "search_arxiv_theorems",
  "fallback_used": false,
  "results_summary": ["..."],
  "useful_references": [
    {
      "title": "...",
      "complete_statement": "...",
      "url_or_id": "...",
      "paper_id": "...",
      "arxiv_id": "...",
      "theorem_id": "...",
      "expanded_definitions": ["paper-context expansions of terms/concepts used in the statement"],
      "applicability_check": ["why the statement does or does not apply in the current setting"],
      "proof_insights": ["optional extracted techniques or ideas from the proof"],
      "why_useful": "..."
    }
  ],
  "branch_id": "optional",
  "subgoal_id": "optional"
}
```

## MCP Tools

- `search_arxiv_theorems`
- `memory_append`
- `memory_search`

## Failure Logging

If theorem search yields no useful information, append a `scratch_events` record with:

- `event_type="search_math_results_stalled"`
- the attempted queries
- the reason the results were not useful

## Next Skill

- A close theorem found whose proof technique might transfer →
  return to whichever skill triggered this search; the technique
  feeds back into `$direct-proving` (during plan screening) or
  `$propose-subgoal-decomposition-plans` (during planning).
- A counter-example pattern surfaced → `$construct-counterexamples`
  with the surfaced pattern as the candidate.
- A construction or example surfaced → `$construct-toy-examples` to
  adapt it locally.
- Search stalled → return to `$construct-toy-examples` /
  `$construct-counterexamples` for non-search reasoning.
