# Phase 3: Learner and Referee Source Ingestion

Date: 2026-05-03

Phase 3 turns finished mathematical sources into Rethlas knowledge and review
records. Sources include papers, books, lecture notes, preprints, and internal
manuscripts. A source may be polished and still contain gaps, notation drift,
implicit prerequisites, citation errors, or wrong statements.

Phase 3 has two top-level Codex-call roles:

```text
learner = learn from a source and build or extend the knowledge base
referee = review a source or node set and report correctness gaps
```

Both roles may call generator and verifier through the Rethlas harness. Neither
role directly mutates truth state. They emit proposed node batches, review
reports, citation records, and repair tasks that the librarian validates.

## Phase Boundary

Phase 2 gives Rethlas a node-first theorem library and branch search.

Phase 3 adds source ingestion and review:

- read source documents;
- segment definitions, statements, proofs, examples, and references;
- create candidate node documents with source provenance;
- fill omitted proof steps where possible;
- verify extracted logical chains;
- retrieve and check external citations;
- mark unresolved gaps explicitly.

Phase 3 does not require Lean and does not decide final formal truth. Formal
kernel checking remains Phase 4.

## Agent Model

Phase 3 should use two separate top-level agents, not one generic
source-understanding agent with different prompts.

```text
source pipeline -> source spans -> learner/referee -> harness requests
                                             |
                                             v
                         librarian admission / review record storage
```

Both agents are Codex-call orchestration agents. They receive bounded context
packets from the harness and may request inner services:

- source-span retrieval;
- page-image inspection for low-confidence spans;
- external reference retrieval;
- generator calls for local bridge lemmas or repairs;
- verifier calls for extracted claims, generated bridges, and repaired proof
  segments.

Neither agent writes durable truth directly. Durable changes go through the
librarian. The distinction between the agents is the artifact they are allowed
to produce.

### Runtime Independence

`learner` should be implemented as an independent role in the same operational
sense as `generator` and `verifier`: it should have its own prompt contract,
decoder, job type, output schema, logs, budgets, and scheduler lane.

It is not the same logical layer as generator/verifier:

```text
generator   primitive proof-production worker for a target node/branch
verifier    primitive proof-checking worker for a node/proof segment
learner     source-to-KB orchestration worker over source spans
referee     review orchestration worker over claims, proofs, and citations
```

This means `learner` is independently scheduled, but it uses generator and
verifier as inner services when it needs proof completion or checking. It
should not be implemented as a mode inside generator, because source ingestion
needs its own provenance handling, OCR confidence gates, citation awareness,
node batching, and stopping conditions.

Recommended runtime shape:

```text
sources pipeline
  -> learner job(source_id, span range, budgets)
      -> learner.batch_proposed
      -> learner.bridge_requested
      -> learner.verification_requested
      -> learner.issue_reported
  -> librarian admission / scheduler dispatch
```

The same applies to `referee`: it should be independently scheduled and have
its own review schema, but it may request generator/verifier/retrieval work
through the harness.

## System Design

The Phase 3 design should extend the existing Rethlas worker pattern instead
of replacing it.

Current Phase I workers follow this shape:

```text
CLI/coordinator -> runtime/jobs/{job_id}.json
               -> role wrapper
               -> Codex run
               -> decoder
               -> event file
               -> librarian/projector
               -> Kuzu + node docs
```

Learner and referee should use the same shape, with different job inputs and
event payloads.

### Worker Directories

Add two materialized agent directories:

```text
agents/
  generation/
  verification/
  learner/
  referee/
```

Each directory should own its own `AGENTS.md`, Codex config, skills, prompt
contract, and decoder assumptions. `learner` should not reuse the generation
agent directory, because source ingestion needs different instructions,
schemas, tools, and stop conditions.

### Runtime Job Types

Add independent job kinds:

```text
generator
verifier
learner
referee
source_ingest
reference_retrieval
```

The existing `JobRecord` is node-target oriented. Phase 3 should not keep
adding role-specific top-level fields forever. Introduce a v2 job shape with a
common envelope and role-specific context:

```json
{
  "schema": "rethlas-job-v2",
  "job_id": "learn-...",
  "kind": "learner",
  "mode": "learn_source_spans",
  "target": "src:...#span-range",
  "context_hash": "sha256:...",
  "dispatch_hash": "sha256:...",
  "status": "running",
  "log_path": "runtime/logs/learn-....codex.log",
  "input": {
    "source_id": "src:...",
    "span_ids": [],
    "page_image_refs": [],
    "tex_refs": [],
    "layout_refs": [],
    "existing_kb_matches": [],
    "budgets": {}
  }
}
```

Generator/verifier can stay on the current v1 shape until the migration is
worth doing. Learner/referee should start with v2 so source-specific context
does not pollute the node-worker schema.

### Events

Add event types with separate producer registration:

```text
source.artifact_registered
source.spans_extracted
learner.batch_proposed
learner.issue_reported
referee.review_completed
referee.citation_checked
```

Suggested ownership:

- `source.*` events are produced by deterministic ingestion code, not Codex.
- `learner.batch_proposed` contains candidate nodes, source-backed dependency
  edges, bridge requests, verification requests, and extraction issues.
- `learner.issue_reported` records OCR ambiguity, notation ambiguity,
  suspicious source claims, missing proof steps, and manual-transcription
  requests when no node batch is ready.
- `referee.review_completed` stores the review report and verdict.
- `referee.citation_checked` stores a durable citation-evidence record.

Referee should not silently mutate theorem nodes. If it finds a useful repair
lemma, the report may recommend a node update, but the actual node admission
must be a separate librarian-validated node event.

### Scheduler Shape

Use three queues:

```text
source queue       PDF/OCR/layout extraction and source-span creation
learning queue     learner jobs over source/span chunks
review queue       referee jobs over flagged claims, papers, or node sets
```

Learner/referee should not run unbounded nested proof search themselves. They
emit harness requests:

```text
bridge_requested      -> scheduler dispatches generator
verification_requested -> scheduler dispatches verifier
citation_requested    -> scheduler dispatches reference retrieval
manual_check_needed   -> dashboard/user queue
```

When those results return, the scheduler either resumes the original
learner/referee run or opens a follow-up job with the new evidence. This keeps
budgeting, logs, retries, and dashboard state visible.

### Source Storage

Original source files are immutable. Derived files are allowed:

```text
sources/
  algebraic_geometry/
    hartshorne_ch2.pdf
    hartshorne_ch2.tex
    hartshorne_ch2.source.md
    hartshorne_ch2.layout.json
    hartshorne_ch2.tex_ast.json
    hartshorne_ch2.pdf_tex_alignment.json
    pages/
      0001.png
    ocr/
      0001.txt
      0001.hocr
    corrections/
      0001.manual.md
```

The source metadata should be human-facing Markdown with YAML frontmatter when
practical. Large mechanical artifacts such as layout JSON, OCR hOCR, and page
images stay as separate derived files with hashes referenced from the source
record.

Learner source input should support three modes:

```text
pdf_only       PDF is the only primary artifact; use embedded text/OCR/layout.
tex_only       TeX is the primary artifact; use TeX structure and macros.
pdf_plus_tex   PDF and corresponding TeX are both available; align them.
```

`pdf_plus_tex` is the preferred mode when available. TeX usually contains
cleaner mathematical structure, labels, theorem environments, macros, and
bibliography keys. PDF preserves the actual published rendering, page
numbers, visual formula layout, and citation locators. Learner should use TeX
for structure and formula source, but keep PDF spans as page-level provenance
and visual evidence.

### Kuzu Read Model

Kuzu should index Phase 3 objects as a read model:

```text
(:Source)
(:SourceSpan)
(:Node)
(:Review)
(:CitationCheck)
(:Issue)
(:BridgeRequest)

(Node)-[:EXTRACTED_FROM]->(SourceSpan)
(Node)-[:DEPENDS_ON]->(Node)
(Review)-[:REVIEWS]->(Node or SourceSpan)
(CitationCheck)-[:CHECKS]->(SourceSpan)
(Issue)-[:BLOCKS]->(Node or SourceSpan)
(BridgeRequest)-[:FILLS_GAP_BETWEEN]->(SourceSpan)
```

The Markdown/YAML documents remain the durable human-editable surface. Kuzu is
rebuilt or synchronized by the librarian.

### Admission Rules

The librarian should reject learner/referee outputs when:

- referenced source spans do not exist;
- source span hashes do not match the dispatch context;
- OCR/layout confidence is below the configured threshold and no manual
  correction is cited;
- candidate node labels conflict without an explicit revision path;
- generated bridge lemmas lack provenance;
- dependency edges introduce a graph cycle;
- a referee report claims a citation is checked without evidence hash or
  retrieved statement summary;
- verifier-dependent fields are marked verified without a verifier event.

### Dashboard

Phase 3 dashboard views should include:

- source ingestion status: imported, OCRed, segmented, learned, reviewed;
- learner batches: proposed nodes, span coverage, unresolved issues;
- referee reports: verdicts, critical gaps, citation mismatches;
- manual review queue: low-confidence formulas, bad OCR, missing references;
- provenance view: click a node and see source page, bbox, extracted text, and
  review history.

This should reuse the Phase 2 lazy graph expansion pattern: a source expands to
spans, spans expand to learned nodes/reviews, nodes expand to proof/search
state.

### Implementation Order

Recommended order:

1. Build source artifact pipeline and source-span records.
2. Add job-v2 envelope while keeping generator/verifier on v1.
3. Add `agents/learner` and `learner.batch_proposed` decoder/admission.
4. Add Kuzu source/span/node provenance edges.
5. Add learner scheduler lane over span chunks.
6. Add `agents/referee` and review/citation events.
7. Add bridge/verification request queues instead of nested direct calls.
8. Add dashboard source/provenance/review views.

### `learner`

Primary question:

```text
What reliable KB nodes can be learned from this source?
```

Owns:

- candidate node batches;
- extracted definitions/statements/proof skeletons;
- source-backed dependency edges;
- bridge-lemma requests;
- unresolved extraction issues.

Stops when:

- the assigned source span budget is exhausted;
- all assigned spans have candidate nodes, explicit non-node classifications,
  or issues;
- OCR/layout/source ambiguity blocks reliable extraction;
- generator/verifier budget for local bridge filling is exhausted.

It should hand off to `referee` when a source claim looks suspicious, a proof
gap remains after bounded repair, or a citation must be checked before the node
can be trusted.

### `referee`

Primary question:

```text
Is this source/node/proof correct enough, and where exactly does it fail?
```

Owns:

- review reports;
- verdicts;
- checked-claim records;
- external citation checks;
- verified repair records;
- unresolved gap reports.

Stops when:

- every assigned claim has a verdict;
- a critical gap or counterexample invalidates the target;
- needed external references are unavailable;
- OCR/layout quality is too low to judge a claim;
- repair budget is exhausted.

It may recommend KB updates, but does not perform a full source import. If a
review discovers useful new lemmas, they are emitted as candidate nodes or
repair lemmas for librarian admission, not silently merged into the reviewed
proof.

### Shared Invariants

- Use `learner`, not `leaner`, as the role name.
- Preserve original source notation and normalized notation separately.
- Every extracted or generated claim must cite source spans or generated
  provenance.
- A generated bridge lemma must be explicit; hidden proof completion is not
  allowed.
- A verifier result is required before any extracted/generated proof segment is
  treated as verified.
- A low-confidence OCR/formula span can block both agents.
- Failures are productive artifacts: unresolved gaps, missing citations, and
  manual-transcription requests are stored instead of retried indefinitely.

## Source Model

A source is evidence and provenance for nodes. It is not itself a theorem node.

Recommended source record:

```yaml
source_id: src:hartshorne_ch2
kind: book | article | lecture_notes | manuscript | webpage
bibliography:
  title: ...
  authors: [...]
  year: ...
  venue: ...
  doi: ...
  arxiv: ...
  url: ...
local_artifact:
  path: sources/algebraic_geometry/hartshorne_ch2.pdf
  content_hash: sha256:...
  ocr_hash: sha256:...
  extraction_mode: embedded_text | ocr | hybrid | manual_transcription
  page_image_root: sha256:...
  layout_json: sources/algebraic_geometry/hartshorne_ch2.layout.json
tex_artifact:
  path: sources/algebraic_geometry/hartshorne_ch2.tex
  content_hash: sha256:...
  root_file: hartshorne_ch2.tex
  included_files: []
  macro_digest: sha256:...
  bibliography_files: []
  tex_ast_json: sources/algebraic_geometry/hartshorne_ch2.tex_ast.json
alignment:
  mode: pdf_only | tex_only | pdf_plus_tex
  alignment_json: sources/algebraic_geometry/hartshorne_ch2.pdf_tex_alignment.json
  confidence: 0.88
status: imported | segmented | learned | reviewed | needs_manual_cleanup
```

Source spans are first-class provenance:

```yaml
span_id: span:hartshorne_ch2:section_2_4:p17_l12_l28
source_id: src:hartshorne_ch2
locator: "Chapter II, Proposition 4.1, proof paragraph 2"
text_hash: sha256:...
text_excerpt: ...
page: 17
bbox: [72.0, 120.0, 510.0, 210.0]
extraction:
  mode: embedded_text | ocr | hybrid | manual_transcription | tex | pdf_tex_aligned
  confidence: 0.93
  engine: pymupdf | tesseract | mathpix | manual | ...
  needs_visual_check: false
tex:
  file: hartshorne_ch2.tex
  environment: proposition
  label: prop:...
  line_range: [120, 148]
  macro_context_hash: sha256:...
alignment:
  pdf_span_id: span:hartshorne_ch2:pdf:p17_b04
  tex_span_id: span:hartshorne_ch2:tex:prop_4_1
  confidence: 0.88
```

Node frontmatter can cite source spans:

```yaml
provenance:
  source_spans:
    - span:hartshorne_ch2:section_2_4:p17_l12_l28
  extraction_agent: learner
  extraction_run: learn_2026_05_03_hartshorne_ch2
  confidence: medium
```

## PDF, TeX, and OCR Pipeline

Learner and referee should not consume raw PDFs or raw TeX projects directly.
They should receive source spans produced by a source-artifact pipeline.

Required pipeline stages:

```text
1. Store original PDF/TeX artifacts immutably and compute content hashes.
2. Detect whether PDF pages have embedded text, images, or both.
3. Extract embedded PDF text and layout when available.
4. Render PDF page images for provenance and visual fallback.
5. For scanned/image-only PDF pages, run OCR.
6. Parse TeX projects when available: root file, includes, theorem
   environments, labels, refs, citations, macros, and bibliography keys.
7. Align TeX spans to PDF spans when both artifacts are available.
8. Detect math regions, theorem-like blocks, references, captions, and footnotes.
9. Produce source spans with page/bbox or TeX file/line locator, text hash,
   extraction mode, macro context, and confidence.
10. Flag low-confidence spans for visual/manual review before learner/referee
    trust them.
```

Extraction modes:

```text
embedded_text          born-digital PDF text extraction
ocr                    scanned page OCR
hybrid                 embedded text plus OCR/visual correction
manual_transcription   human-supplied correction
tex                    TeX source extraction
pdf_tex_aligned        TeX source aligned to rendered PDF evidence
```

Scanning/OCR risks must be explicit:

- OCR may confuse mathematical symbols, subscripts, superscripts, and Greek
  letters.
- PDF text order may not match visual reading order.
- Formula extraction may require image-backed review.
- Page headers, footers, equation numbers, and reference labels may be
  incorrectly attached to proof text.

TeX risks must also be explicit:

- TeX macros may hide mathematical meaning; spans need a macro-context hash.
- The submitted TeX may not match the published PDF.
- Multi-file projects may have stale included files or conditional compilation.
- Theorem labels and citation keys may be missing, duplicated, or unrelated to
  the rendered wording.
- Comments and draft notes in TeX should not be treated as published claims
  unless explicitly requested.

Low-confidence source spans should not be silently learned as theorem nodes.
They should produce `needs_visual_check` or `needs_manual_transcription`
issues.

Recommended artifact layout:

```text
sources/
  algebraic_geometry/
    hartshorne_ch2.pdf
    hartshorne_ch2.tex
    hartshorne_ch2.source.yaml
    hartshorne_ch2.layout.json
    hartshorne_ch2.tex_ast.json
    hartshorne_ch2.pdf_tex_alignment.json
    pages/
      0001.png
      0002.png
    ocr/
      0001.hocr
      0001.txt
```

The source pipeline is deterministic infrastructure. Codex agents may inspect
rendered page snippets when needed, but the default learner/referee context
should be structured spans rather than whole-page images or raw TeX trees.

## Skill Implementation Boundary

The PDF/TeX/OCR source-reading workflow can live inside the learner/referee
skills, but not as free-form prompt behavior. The skill should own the
Codex-facing procedure and call deterministic scripts or harness services for
fragile parsing.

Good skill responsibilities:

- decide whether the task is `pdf_only`, `tex_only`, or `pdf_plus_tex`;
- call or request source-artifact scripts;
- inspect source spans, page snippets, TeX snippets, and alignment records;
- extract/normalize mathematical claims from those spans;
- decide when confidence is too low and emit a manual-check issue;
- produce strict learner/referee JSON outputs;
- request generator/verifier/retrieval work through the harness.

Responsibilities that should be scripts or harness services, not prose-only
skill instructions:

- PDF rendering and embedded-text extraction;
- OCR and hOCR/ALTO parsing;
- TeX project parsing, include resolution, macro collection, theorem
  environment extraction, and bibliography-key extraction;
- PDF span to TeX span alignment;
- content hashing and macro-context hashing;
- source-span persistence and librarian admission.

Recommended skill folders:

```text
agents/learner/.agents/skills/rethlas-learner/
  SKILL.md
  references/
    learner_output_schema.md
    source_span_schema.md
    pdf_tex_modes.md
  scripts/
    prepare_source_context.py
    validate_learner_batch.py

agents/referee/.agents/skills/rethlas-referee/
  SKILL.md
  references/
    referee_report_schema.md
    citation_check_schema.md
    gap_taxonomy.md
  scripts/
    prepare_review_context.py
    validate_referee_report.py
```

Shared deterministic source tools should live in the Rethlas harness, not be
duplicated in both skills:

```text
sources/
  pdf.py
  ocr.py
  tex.py
  alignment.py
  spans.py
  citations.py
```

The skill may call these tools, but the durable source artifacts and hashes
belong to the harness/librarian layer.

## Learner Skill

The learner learns from already written sources and builds or extends the KB.
Its primary job is to make the knowledge base larger, better organized, and
more useful for later proof search. It should assume that the source is
valuable, but not infallible.

### Responsibilities

- parse the source into sections and source spans;
- use TeX structure when available while preserving PDF locator evidence;
- extract definitions, notation conventions, assumptions, theorem statements,
  lemmas, examples, remarks, and proof skeletons;
- respect span extraction confidence and request visual/manual review for
  low-confidence OCR or formula regions;
- normalize notation into Rethlas node style while preserving the original
  source locator;
- identify implicit dependencies and prerequisites;
- create candidate node documents with `source_backed` provenance;
- request verification for extracted definitions, external theorem records,
  and theorem/lemma/proposition proof nodes;
- detect skipped proof steps and formulate missing intermediate lemmas;
- call generator to fill missing steps when a local bridge lemma is plausible;
- call verifier on extracted and generated nodes;
- record source inconsistencies, notation ambiguity, or likely errors.

### Non-goals

- Do not silently rewrite the source theorem into a stronger or different
  theorem.
- Do not mark a node closed verified merely because it appeared in a book.
- Do not flatten a whole chapter into one giant node.
- Do not hide generated bridge lemmas; every bridge becomes its own node or
  explicit proof step.
- Do not make generator depend on raw source documents. Generator should use
  admitted KB nodes and retrieval summaries, not raw learner scratch output.

### Learner-to-Generator Data Flow

Learner expands the library; generator consumes the library.

```text
paper/book/PDF/TeX
  -> source pipeline
  -> learner batch
  -> librarian admission
  -> knowledge_base/nodes/<topic_path>/...
  -> Kuzu/BM25/vector retrieval indexes
  -> generator context packet
  -> proof/helper generation
```

Generator should call the KB through retrieval services, not by reading the
source corpus directly. The generator context should include:

- relevant node labels, statements, and verification/closure status;
- dependency summaries and aliases;
- source-backed lemmas and generated bridge lemmas when admitted;
- proof variants when useful;
- source provenance summaries only when needed for mathematical context.

Generator should not treat learner-created nodes as verified merely because
they came from a source. It must respect `verification.status` and
`closure_status`. Unverified source-backed nodes may be used as hypotheses,
ideas, or candidates only when the search policy allows that risk.

### Workflow

```text
1. Ingest source artifact and metadata.
2. Run PDF/text/OCR/TeX/layout/alignment pipeline and produce source spans.
3. Segment source into logical spans.
4. Extract candidate claims and notation contexts.
5. Build a dependency skeleton.
6. Map each claim to existing KB labels or propose new labels.
7. For each proof:
     a. preserve source proof skeleton;
     b. identify jump steps;
     c. propose bridge lemmas for jumps;
     d. call generator for bridge lemmas only when local context is enough;
     e. call verifier on extracted definitions, external theorem records, and
        extracted/filled proof chains.
8. Emit a learner batch for librarian admission.
9. After admission, schedule indexing so generator retrieval can see the new
   nodes.
10. Emit unresolved issues for referee/manual review.
```

### Output Schema

Learner output should be strict JSON plus optional Markdown rationale:

```json
{
  "source_id": "src:...",
  "run_id": "learn_...",
  "source_spans": [],
  "notation_contexts": [],
  "candidate_nodes": [],
  "dependency_edges": [],
  "generated_bridge_requests": [],
  "verification_requests": [],
  "issues": [],
  "summary": "..."
}
```

Issue types:

```text
notation_ambiguous
implicit_dependency
missing_proof_step
external_reference_needed
source_claim_suspect
extraction_low_confidence
ocr_low_confidence
needs_visual_check
needs_manual_transcription
```

## Referee Skill

The referee reviews a source, node set, or proposed article. It is stricter
than learner. It should try to repair local jump steps by asking generator and
verifier, but if repair fails it must report the gap clearly.

### Responsibilities

- check statement consistency and notation;
- verify proof steps in order;
- identify missing lemmas, circular dependencies, false uses of references, and
  unstated hypotheses;
- retrieve external references when a proof cites another paper/book/theorem;
- compare cited result with the actual needed statement;
- ask generator for a bridge lemma or repair only when the intended fix is
  local and well-scoped;
- ask verifier to check repaired chains;
- produce a review verdict with gap severity and evidence.

### Non-goals

- Do not convert every review into a full KB extraction job.
- Do not accept a proof merely because a plausible repair exists; the repair
  must be generated and verified or listed as conjectural.
- Do not suppress unresolved citation failures.
- Do not write referee scratch work or unresolved repairs into
  `knowledge_base/nodes/`.

### Referee Workspace

Referee should have a separate workspace so review work does not pollute the
knowledge base.

Recommended layout:

```text
reviews/
  review_2026_05_03_paper_x/
    review.yaml
    report.md
    issues/
      issue_001.yaml
      issue_002.yaml
    evidence/
      source_spans.json
      citation_checks.json
      extraction_quality.json
    repairs/
      bridge_attempts.json
      verified_repairs.json
    requests/
      author_requested_details.md
      generator_requests.json
      verifier_requests.json
      citation_requests.json
```

The review workspace may contain:

- source lookups and citation evidence;
- generated repair attempts;
- verifier results for local repairs;
- counterexample attempts;
- requested missing details;
- recommended KB updates.

It must not be treated as the theorem library. The default referee output is a
review report plus issue list. A recommended KB update becomes a node only if a
separate librarian-admitted update accepts it.

Referee report lifecycle:

```text
draft
awaiting_source
awaiting_author_details
repair_attempted
ready_for_decision
closed
```

If the original source is unclear, referee should create a requested-detail
issue instead of guessing:

```yaml
issue_type: requested_detail
severity: major
source_span: span:...
problem: "The proof invokes a standard reduction but does not state the required lemma."
requested_detail: "State and prove the reduction lemma, including hypotheses."
blocks_verdict: true
```

### Review Concerns

Referee must check more than local proof gaps. A useful review should separate
mathematical correctness, source reliability, citation applicability, and
presentation quality.

Statement and hypothesis checks:

- exact theorem statement, quantifier scope, and conclusion;
- hidden assumptions such as characteristic, algebraic closure, compactness,
  connectedness, Noetherian hypotheses, finiteness, smoothness, separability,
  completeness, topology, measure, or base field;
- notation drift between sections;
- overloaded symbols with different ambient objects;
- whether the statement used in the proof is stronger or weaker than the
  displayed theorem;
- whether examples and edge cases match the claimed generality.

Proof-dependency checks:

- circular dependency between lemmas;
- appeal to a lemma before its hypotheses have been established;
- use of a result under missing side conditions;
- "clear", "standard", or "well-known" steps that are actually nonlocal;
- changes of category, topology, or equivalence relation inside the proof;
- proof by reduction where the reduction does not preserve the target
  property;
- proof variants that prove only a special case.

Citation checks:

- cited theorem statement exactly matches the needed use;
- cited source version is identified: arXiv version, published version,
  erratum, book edition, theorem numbering;
- cited result has its own prerequisites and conventions satisfied;
- citation is not merely thematically related;
- reference chain does not hide an unavailable or circular dependency;
- missing access is reported as `needs_external_reference_access`, not guessed.

Source-artifact checks:

- PDF text, OCR text, TeX source, and rendered PDF agree on the relevant claim;
- TeX comments or draft notes are not treated as published claims;
- macros are expanded or recorded with a macro-context hash;
- formulas with low OCR or alignment confidence are visually checked;
- page/bbox or TeX line evidence is attached to every serious finding.

Counterexample and sanity checks:

- test degenerate cases and smallest examples;
- check compatibility with known examples, toy models, or boundary cases;
- try to construct a counterexample before accepting a surprising
  strengthening;
- distinguish "no counterexample found" from positive verification.

Repair boundary:

- A referee may try a local repair, but the report must say whether the
  original proof is valid as written.
- If a repair adds a hypothesis, weakens a conclusion, or changes a definition,
  it is a revision recommendation, not acceptance.
- Generated bridge lemmas are evidence only after verifier success.
- Failed repairs should create explicit gaps, not another retry loop.

### Issue Severity

Review issues should be classified so the verdict is machine-actionable:

```text
blocker       statement false, proof invalid, circular dependency, or critical citation mismatch
major         substantial missing lemma, missing hypothesis, nonlocal repair needed
minor         local gap repairable without changing statement or dependencies
citation      external reference unavailable, mismatched, or under-specified
extraction    OCR/TeX/PDF evidence unreliable
expository    unclear notation, missing definition, confusing organization
editorial     typo or wording issue with no mathematical effect
```

Only `minor`, `expository`, and `editorial` issues are compatible with
`accepted_with_minor_gaps`. Any unresolved `blocker` or `major` issue should
force `needs_revision`, `major_gap`, or `wrong`.

### Workflow

```text
1. Bind the review to target hashes: source artifact hash, statement hash,
   proof hash, and dependency snapshot.
2. Stabilize the statement and hypotheses.
3. Run extraction-quality checks on PDF/OCR/TeX/alignment spans.
4. Extract proof steps in order.
5. Resolve local notation and cited labels.
6. For each step:
     a. check whether it follows from previous steps and dependencies;
     b. if a jump is local, request a generated bridge proof;
     c. verify the bridge or repaired step;
     d. if repair fails, record an explicit gap.
7. For external citations:
     a. retrieve source metadata and quoted theorem statement;
     b. compare cited theorem to the needed use;
     c. record applicability, mismatch, or missing access.
8. If the source is unclear or details are missing, create requested-detail
   issues with exact source spans and blocked claims.
9. Run counterexample and edge-case sanity checks for high-risk claims.
10. Emit review report with severity, evidence spans, repair attempts, and
   unresolved gaps.
```

### Verdicts

```text
accepted
accepted_with_minor_gaps
needs_revision
major_gap
wrong
needs_external_reference_access
```

Review reports should include:

```json
{
  "review_id": "review_...",
  "target": "source or node labels",
  "workspace_path": "reviews/review_...",
  "target_hashes": {},
  "verdict": "needs_revision",
  "checked_claims": [],
  "generated_repairs": [],
  "verified_repairs": [],
  "unresolved_gaps": [],
  "requested_details": [],
  "issues": [],
  "counterexample_attempts": [],
  "external_reference_checks": [],
  "extraction_quality_checks": [],
  "recommended_kb_updates": [],
  "summary": "..."
}
```

## Generator and Verifier Use

Learner and referee are orchestration agents. Generator and verifier remain
inner tools.

Use generator for:

- bridge lemmas between two extracted proof steps;
- expansion of a source proof sketch into explicit intermediate claims;
- candidate proof repair when the intended route is already specified;
- alternate formulation when notation ambiguity is resolved.

Use verifier for:

- each extracted theorem/proof node;
- each generated bridge lemma;
- each repaired local proof segment;
- citation applicability claims when the cited statement is available.

Do not call generator endlessly. If a bridge fails after budget, record the gap
and move on.

## External Reference Retrieval

External references are a harness service used especially by referee.

Reference states:

```text
resolved_exact
resolved_partial
resolved_metadata_only
missing_access
statement_mismatch
not_applicable
```

A citation check should store:

```yaml
citation_key: ...
source_span: span:...
claimed_use: ...
retrieved_reference: ...
quoted_or_paraphrased_statement: ...
applicability: resolved_exact | statement_mismatch | ...
evidence_hash: sha256:...
```

When the external source cannot be accessed, referee should say so. It should
not invent the cited theorem.

## KB Integration

Learner-created nodes should default to:

```yaml
library_role: source_backed
verification:
  local_status: unverified
  closure_status: open
provenance:
  extraction_agent: learner
```

Generated bridge lemmas should default to:

```yaml
library_role: generated_bridge
provenance:
  source_spans: [...]
  generated_to_fill_gap: true
```

Referee reports are not ordinary theorem nodes. They are review records linked
to source spans and node labels. If a referee produces a verified repair lemma,
that repair lemma is admitted as an ordinary node with provenance.

Referee scratch artifacts stay under `reviews/`, not `knowledge_base/nodes/`.
`recommended_kb_updates` are proposals only. They become KB nodes, revisions,
aliases, or proof variants only through a separate librarian admission step.

## Skill Contracts

### `rethlas-learner`

Trigger when asked to learn from a finished mathematical paper/book or ingest a
source into the KB.

Required context packet:

```json
{
  "task": "learn_source",
  "source_id": "src:...",
  "source_spans": [],
  "page_image_refs": [],
  "tex_refs": [],
  "layout_refs": [],
  "alignment_refs": [],
  "existing_kb_matches": [],
  "notation_context": {},
  "budgets": {"max_nodes": 30, "max_bridge_requests": 8},
  "output_schema": "learner_batch_v1"
}
```

Prompt discipline:

- Work span-by-span; do not summarize an entire book chapter into one output.
- Keep original notation and normalized notation side by side when ambiguity is
  possible.
- Distinguish `source_claim`, `normalized_claim`, and `generated_bridge`.
- Never mark source claims as verified without a verifier result.
- When a proof jump is found, first state the missing intermediate claim, then
  request generator/verifier budget if the claim is local.
- If OCR/layout confidence is low, emit `needs_visual_check` instead of
  normalizing the claim as if the text were reliable.

### `rethlas-referee`

Trigger when asked to review a proof/source/node set for correctness.

Required context packet:

```json
{
  "task": "review_source_or_nodes",
  "target": {},
  "source_spans": [],
  "page_image_refs": [],
  "tex_refs": [],
  "layout_refs": [],
  "alignment_refs": [],
  "available_dependencies": [],
  "external_reference_index": [],
  "budgets": {"max_bridge_repairs": 5, "max_external_refs": 20},
  "output_schema": "referee_report_v1"
}
```

Prompt discipline:

- Bind the review to the supplied source/proof/dependency hashes. If the target
  changes, request a new review context.
- Review in proof order.
- State whether the original proof is valid as written before discussing
  possible repairs.
- Separate source-local gaps from missing external-reference access.
- Classify every issue by severity.
- When citing an external result, record the exact retrieved statement or a
  bounded paraphrase plus evidence hash.
- Attempt local bridge repair only under budget and only when the required
  statement is clear.
- For scanned PDFs or low-confidence formulas, inspect page images or request
  manual transcription before judging a mathematical claim.
- For surprising strengthenings, run toy-example or counterexample sanity
  checks when the context allows.
- If repair fails, report the gap; do not keep retrying.
- The final verdict must be supported by listed checked claims, verified
  repairs, unresolved gaps, and citation checks.

## Locked Decisions

- The extraction role is named `learner`.
- The review role is named `referee`.
- Learner and referee are top-level Codex-call skills; generator and verifier
  are inner tools they may invoke through the harness.
- Learner treats published sources as valuable but not automatically correct.
- Referee may attempt local repairs, but unresolved failures must be reported.
- External citation retrieval is mandatory for referee when a proof relies on a
  citation not already in the KB.
- Source provenance is required for every learned node.
- PDF/OCR extraction is a deterministic source-artifact pipeline before
  learner/referee. Codex agents consume spans and page-image references, not
  raw PDFs as unstructured context.
- OCR/layout confidence is part of provenance and can block learning or review.
- Generated bridge lemmas must be explicit KB nodes or explicit proof segments,
  never hidden in prose.
