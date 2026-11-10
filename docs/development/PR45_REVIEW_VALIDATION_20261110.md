# PR #45 review validation (2026-11-10)

Base HEAD: `3b9da1bfef528e85d3a5f29907c2a1b478b38444`. Python 3.13.15.
Changes are uncommitted. Approved inputs and the Issue #41 worktree are unchanged.

## Review changes

1. `require_cross_db_pass()` requires exact `PASS` and an empty failures list. Partial-read `MISMATCH` with no failures is rejected. Errors report a safe status label and checked/total/failure counts.
2. Apply reuses `load_graph(..., verify=True).verification`, requiring both loader and nested verification success. Verify-only directly calls `verify_loaded_graph()`. Full node/relationship/property/schema verification remains unchanged. JSON records the verification source.
3. The initial full ID/payload/vector scan always runs. Zero updates reuse it; actual updates trigger a second scan. Both paths retain full payload/protected-field/vector checks and the final cross-DB verification.
4. Document hashes are mapped once. Missing references, duplicate IDs (even identical hashes), invalid IDs and malformed lowercase SHA-256 hashes fail before DB writes. Payload values are unchanged.
5. Fixed snapshot dimensions/points are centralized as `SNAPSHOT_DIMENSIONS=1024` and `SNAPSHOT_POINTS=1138`. Bundle metadata must match these constants. Other release/graph criteria remain fixed.

## Regression tests

- Restore safety: 24 passed (8 existing + 16 new methods, with additional subcases).
- Release-related suite: 65 passed, including the 24 restore tests.
- Legacy suite: 319 passed with `LIFEPOLICY_DATASET_DIR=data/handoff` and `PYTHONPATH=tests/unit`.
- Candidate suite: 25 executed, 23 passed, 1 failure and 1 error from pre-existing missing original files.
- Live suites: 18 executed successfully; the legacy profile class was skipped once because it requires v1.3.2. Final v1.4.0 and Neo4j smoke passed, including POL_005, POL_GOV_006, POL_GOV_011 and POL_009.
- The initial broad legacy run without its required dataset/import configuration failed; a second run with handoff but without its unit-test import path still had an import error. Explicit configuration passed all 319 tests. These unsuccessful runs are retained in `.cache/pr45-*.json`.
- `git diff --check`: passed (Git emitted LF-to-CRLF advisory warnings).

New tests cover exact/missing/unknown cross-DB statuses, contradictory failures, actual partial-read return semantics, both restore modes, zero/N-update scan counts, final cross-check calls, failed graph verification, failed post-update metadata/protected fields/vectors, document mapping validation and fixed bundle criteria.

## Actual isolated restoration

New compose project: `issue43-pr45-review-20261110`; loopback ports Qdrant 56338, Neo4j HTTP 57476 / Bolt 57689. Existing pinned images were used without downloads. Only new project volumes were written:

- `issue43-pr45-review-20261110_qdrant_storage`
- `issue43-pr45-review-20261110_neo4j_data`
- `issue43-pr45-review-20261110_neo4j_logs`

Current modified script was executed against the existing checksum-verified bundle via `--repo` pointing at this worktree and `--bundle .cache/issue43-handoff-bundle-20261110`. Preflight, `--apply`, and `--verify-only` all passed. Apply used `load_graph.verification`; verify-only used `verify_loaded_graph`.

Actual counts: 246 policies, 261 documents, 599 original chunks, 1,138 B2 points, 1,004 graph nodes, 2,431 relationships, 136 REQUIRES_CONDITION relationships. Cross-DB status PASS, 1,138/1,138 checked, all seven checks PASS for every point, no failures. Full graph properties verified. Metadata updates: zero (snapshot already current). Embedding calls: zero.

Full vector SHA-256 remained `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df`. Source manifest LF SHA-256: `49246d1bda0624f6a10d19b252713f6ffb69375e07321fa615f594a44c698389`.

Real nonzero metadata updates were not injected into the DB; that path is covered by regression tests. After verification only the new test containers were stopped (60-second timeout); all new volumes remain. Neo4j exit 0; Qdrant exit 143 on termination; OOM false for both. Existing DBs/volumes and 4B points were not modified.

Local evidence: `.cache/pr45-preflight.json`, `pr45-apply.json`, `pr45-verify-only.json`, `pr45-tests.json`, `pr45-legacy-handoff-path-tests.json`, `pr45-live-tests.json`, `pr45-docker-assets.json`, `pr45-original-preservation.json`. These are ignored local artifacts, not commit candidates.

## Remaining limitations and compatibility

`tests.test_candidate_release.CandidateIntegrityTests.test_chunk_texts_and_document_hashes_match_saved_bodies` fails because original raw evidence paths are missing. `test_frozen_manifests_match_actual_files` errors on missing `data/releases/v1/gov24_public_services.jsonl` (the companion merged file is also absent). No evidence files, expectations or approved data were modified; these are separate from this review.

Existing valid bundle checksums/manifests still pass; success report fields remain and verification-source/cross-DB detail fields are additive. Missing bundle point criteria now fail deliberately. The previously distributed ZIP and its embedded old restore script were preserved, not rebuilt or relabeled. Actual validation used the modified worktree script; distributing these fixes requires a later rebuilt/checksummed/revalidated package. No commit/push was performed.


## PR45 review package r1 ? latest distribution (2026-11-10)

Supersedes the earlier distribution for new restores; historical results below/above remain historical.

- ZIP: `rag-final-v140-pr45-review-worktree-20261110-r1.zip` (local artifact: `.cache/rag-final-v140-pr45-review-worktree-20261110-r1.zip`)
- ZIP SHA-256: `5b053525a13443b4afc0bc7650bf65c9c943429ebdf9ee96437ab248f260e5e8`
- Restore script SHA-256: `5433a24e5fb679e714f42dfe652c10e7a97d9c1643bbf8cb82acad2183f30de1`
- Source: uncommitted Issue #43 worktree based on `3b9da1bfef528e85d3a5f29907c2a1b478b38444`; this is not a new commit SHA.
- Extract `source-pr45-review-worktree.zip` from the bundle. Use this extracted source and its `scripts/restore_final_v140.py`, not the earlier source archive or another checkout.
- Independent ZIP extraction: six internal checksums matched; extracted restore safety tests 24/24 passed on Python 3.13.15. Both `--apply` and `--verify-only` passed against new empty DB volumes.
- Actual counts: Neo4j 1,004 nodes / 2,431 relationships / 136 REQUIRES_CONDITION; Qdrant 1,138 points / 1,024 dimensions / Cosine. Cross-DB PASS, 1,138/1,138; full graph properties verified; vector/protected payload unchanged; embedding calls zero.
- Vector digest: `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df`. Metadata updates zero because the snapshot is already aligned.
- Evidence: [PR45_PACKAGE_VALIDATION_20261110.json](PR45_PACKAGE_VALIDATION_20261110.json). This distribution-side evidence was recorded after packaging; it does not change the sealed ZIP or its checksums.
- Review items 1?5 are included in both script copies. The old ZIP and `.sha256` remain unchanged. The earlier statement that the review ZIP had not yet been rebuilt is historical and superseded by this entry.
- No secrets, `.env`, models, 4B vectors, local caches or test DB volumes are included. `.env.example` contains configuration names only. Dependencies must already be installed; another PC/OS and fresh dependency installation remain unverified.
