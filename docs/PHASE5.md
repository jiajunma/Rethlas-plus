# Phase 5: Blockchain, Formalization, and Decentralized Verification

Date: 2026-05-02

This phase records the blockchain-related Rethlas-plus ideas. It assumes the
Phase 2 node-first theorem library exists. The chain should not replace the
library, Kuzu, or verifier; it should provide provenance, rewards, and
attestations around them.

## Core Position

The chain is not the knowledge base and should not decide mathematical truth.

Recommended layers:

```text
nodes/**/*.md       theorem/lemma/definition library
Kuzu                graph/index representation
events              local journal and audit log
blockchain          timestamp/provenance/reward/attestation layer
```

For informal proofs, on-chain records should say:

```text
Verifier X attested verification_hash H as accepted.
```

They should not claim absolute truth. Higher-confidence rewards should be tied
to formal proof artifacts checked by Lean/Coq/Isabelle kernels.

## Snapshot Anchoring

The first useful chain integration is snapshot notarization.

For a library snapshot:

1. Normalize `nodes/**/*.md`.
2. Compute file hashes.
3. Build a Merkle tree over the topic directory.
4. Compute dependency-closure roots for important theorems.
5. Store full manifests off-chain.
6. Anchor roots on-chain.

Recommended snapshot record:

```yaml
workspace_id: ...
snapshot_id: 2026-05-02T...
filesystem_root: sha256:...
dependency_index_root: sha256:...
kuzu_projection_root: sha256:...
formal_release_root: sha256:...
manifest_cid: ipfs://...
```

The chain stores roots and content IDs, not full proofs.

## Merkle Library

Use multiple hash layers:

```text
statement_hash       mathematical statement + dep statement hashes
verification_hash    statement_hash + proof
node_content_hash    stable node content
node_metadata_hash   topic/tags/search/formalization metadata
node_full_hash       content + metadata
filesystem_root      nodes/ directory tree root
dependency_root      theorem dependency closure root
```

Topic moves should not invalidate proof verification, but they may change
metadata/full snapshot roots.

## Token / Reward Layer

If a token is introduced, it should reward useful verification and
formalization work, not pretend to mine mathematical truth.

Token uses:

- verifier staking;
- bounty escrow;
- challenge deposits;
- reward emissions;
- governance over reward weights;
- snapshot/registry fees.

Avoid public token sale assumptions in early design. Depending on jurisdiction,
token issuance may raise securities/regulatory issues. Start with off-chain
points/reputation or private testnet rewards before a public token.

## Verifier Miners / Attesters

The "miners" are better modeled as verifier attesters.

Flow:

1. Prover/formalizer submits a node or formal artifact.
2. Verifier stakes and takes the task.
3. Verifier runs the required check/build process.
4. Verifier submits a commit to verdict/report.
5. Verifier reveals verdict and report hash.
6. Challenge window opens.
7. If not successfully challenged, rewards are released.
8. False attestations can be slashed.

Commit-reveal helps prevent copying other verifiers' answers.

## Reward Sources

Prefer bounty-funded rewards over pure inflation.

Reward targets:

- proving a new theorem;
- verifying a proof;
- finding a real gap or counterexample;
- formalizing an informal node;
- building reusable definitions and lemmas;
- maintaining proofs across Lean/mathlib upgrades.

Reward weights should favor:

- formal kernel-checked artifacts;
- reusable dependencies;
- high-downstream-impact lemmas;
- successful maintenance after ecosystem upgrades.

## Verification Levels

Recommended levels:

```text
L0 generated / unverified
L1 LLM verifier accepted
L2 multiple independent LLM/human attestations
L3 formal statement aligned
L4 Lean/Coq/Isabelle proof kernel checked
L5 upstreamed / maintained across versions
```

Token rewards should be small for L1/L2 and much larger for L4/L5.

## Lean Formalization

Lean should not be required for the core Rethlas exploration loop. It should
serve as a high-confidence settlement layer for valuable nodes.

Node frontmatter can link to formal artifacts:

```yaml
formalization:
  status: proof_checked
  system: lean4
  package: rethlas_orbits
  module: Rethlas.Orbits.InducedToy
  lean_name: induced_orbit_toy_problem
  lean_version: ...
  mathlib_commit: ...
  lake_manifest_hash: sha256:...
  dependency_closure_hash: sha256:...
  artifact_cid: ipfs://...
  proof_hash: sha256:...
```

Formal proof rewards should be tied to pinned toolchain and dependency
versions.

## Lean / Mathlib Evolution

Lean and Mathlib evolve. On-chain records should not mutate in place.

Correct model:

```text
old checked artifact remains historically checked
new Lean/mathlib version produces a new artifact/version
registry current pointer moves to the newest checked version
```

Each formal artifact must pin:

- Lean version;
- mathlib commit;
- Lake manifest hash;
- source root;
- dependency closure hash;
- build options;
- artifact hashes.

States:

```text
checked_current
historically_checked
needs_port
port_in_progress
ported
deprecated
```

If a proof breaks after a Mathlib upgrade, that should create a maintenance
bounty. It should not slash the old prover/verifier, because the old
attestation was true for the old pinned environment.

## Formal Library Market

The long-term chain product is a market for constructing formal Lean libraries,
not merely verifying isolated theorems.

Task types:

- definition bounty;
- statement alignment bounty;
- lemma bounty;
- proof bounty;
- dependency bounty;
- maintenance bounty.

Rethlas discovers useful informal theorem/lemma dependencies. The formal market
turns selected nodes into Lean definitions, statements, and proofs. Kuzu tracks
the formal dependency graph and can decompose major theorem bounties into
sub-bounties.

## Lean Build Cost

The expensive part is Lean/mathlib build/checking, not Merkle hashing or Kuzu.

Cost order:

```text
1. Cold source build of all Mathlib         very high
2. Porting after Lean/mathlib upgrades      very high
3. Building a large module                  medium/high
4. Checking a leaf theorem with cache       lower
5. Kuzu projection / Merkle hashing         low
```

Verifier miners should not be required to cold-build Mathlib from source.
They should use pinned caches and verify hashes.

## Decentralized Lean Artifact Cache

DHT/IPFS-style storage can accelerate artifact distribution.

Recommended artifact tiers:

```text
Tier 1: .olean     core import/typecheck cache
Tier 2: .ilean     editor/docs/navigation cache
Tier 3: .c         backend cache
Tier 4: .o/.a/.so  native/platform-specific cache
```

If only one tier is implemented first, prioritize `.olean`.

`.olean` is most useful for avoiding re-elaboration/typechecking of imports.
`.c` and native objects help full package/executable builds but are less central
to proof checking.

## Artifact Manifest

Every artifact must be content-addressed and version-pinned:

```yaml
package: mathlib
module: Mathlib.Algebra.Group.Basic
lean_version: 4.x.y
mathlib_commit: abc123
lake_manifest_hash: sha256:...
build_options_hash: sha256:...
artifacts:
  olean:
    hash: sha256:...
    cid: ...
  ilean:
    hash: sha256:...
    cid: ...
  c:
    hash: sha256:...
    cid: ...
  native:
    target_triple: aarch64-apple-darwin
    c_compiler: clang-...
    flags_hash: sha256:...
    object_hash: sha256:...
    cid: ...
```

Fetch policy:

```text
fetch by CID/hash
verify content hash
match Lean/mathlib/Lake/build options
use as cache
run target build/check
submit attestation
```

Do not assume artifacts from DHT are trusted.

## Reproducibility Notes

Do not assume different machines produce byte-identical `.olean` or `.c`
artifacts unless benchmarked under a pinned environment.

`.olean` is more relevant to proof checking than `.c`, but even `.olean` should
be treated as a cache/artifact with a hash and build attestation, not as an
unqualified consensus object.

`.c` is even more backend/platform/options-sensitive and should be treated as a
performance cache, not proof truth.

Chain consensus should anchor:

```text
source hash
pinned environment
artifact hashes
checker/build attestations
```

not just native build output.

## Implementation Roadmap

### P5.A: Snapshot roots

- Compute normalized node file hashes.
- Build filesystem Merkle roots.
- Build theorem dependency closure roots.
- Produce snapshot manifest.

### P5.B: Off-chain registry

- Store manifests in Git/IPFS/Arweave.
- Track formal artifact metadata.
- Track historical/current formal versions.

### P5.C: On-chain anchoring

- Anchor snapshot roots and manifest CIDs.
- Add attestations over library snapshots.

### P5.D: Formalization metadata

- Add formalization fields to node frontmatter.
- Link informal nodes to Lean modules/names/artifacts.

### P5.E: Bounty and attestation protocol

- Add verifier staking.
- Add commit-reveal verdict submissions.
- Add challenge windows.
- Add slashing for false attestations.

### P5.F: Lean artifact DHT

- Build content-addressed artifact manifests.
- Store `.olean`, `.ilean`, `.c`, and native artifacts by tier.
- Integrate with build workers as a cache layer.

### P5.G: Formal library market

- Add definition/statement/proof/dependency/maintenance bounties.
- Weight rewards by dependency impact and formal verification level.

## Locked Decisions

- The registry and bounty contracts should target an EVM-compatible chain
  interface, but the first deployment should be on a permissioned testnet or
  private rollup. If public deployment is needed later, prefer a low-fee
  EVM-compatible L2. Keep the contract interface chain-abstracted so the
  deployment target can move.
- Staking/slashing should be permissioned at first. Slash only for provably
  false attestations inside a pinned toolchain/environment; do not slash old
  honest attestations because Lean/Mathlib later changed.
- Statement alignment before formal proof should be judged by canonical
  statement normalization plus human/LLM attestation, not by trying to encode
  truth on-chain.
- The first Lean bounty domain should be a topic with stable informal nodes,
  pinned dependencies, and manageable proof sizes. Do not start with a fresh
  frontier research topic.
- DHT artifact roots should be certified by CI-generated manifests plus
  content-addressed hashes. The chain records the manifest root and attestation
  hash, not raw build outputs.
- Reproducibility should be measured at the artifact level (`.olean`/manifest
  hash equality under a pinned toolchain), not by requiring byte-identical
  native outputs across every machine.
- Reward splits should favor formal proof and maintenance most, then
  verification, then informal discovery. Exact weights should be a governance
  parameter, not a protocol constant.
