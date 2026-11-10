# Issue #41 RunPod vector-only transfer

Current status: the 841 generated vectors were imported and the 4B collection contains
1,138 verified Points. These r1 export/generation/import commands document the completed
transfer, not an instruction to repeat it. See [A/B analysis](ISSUE41_AB_RUN03_ANALYSIS.md).

This package contains public policy inputs for the 841 missing B2 Points only.
It contains no DB connection details, secrets, models, or the original 297 vectors.
The source basis is the uncommitted Issue #41 worktree; inspect manifest.json.
Keep the original ZIP and its SHA-256 as the trusted package identity.

## RunPod (Linux, RTX 4090)

Use a private pod and persistent storage. Provision Python >=3.10 and Ollama separately;
the worker requires only the Python standard library. Do not expose Ollama publicly:
bind it to 127.0.0.1:11434. No Qdrant/Neo4j server or API password is needed.
Provisioning, model download and paid pod creation are not performed by this repository.
Check `nvidia-smi`, available disk, Ollama version and actual GPU offload before the run.
Use the exact model tag/digest from manifest.json. Do not substitute an HF embedding
implementation, another quantization, context option or query instruction.
The local source was Ollama 0.40.15; prefer the same version and record any difference.
CPU-generated 297 + GPU-generated 841 is a mixed-backend experiment; identical weights
do not guarantee bit-identical results across hardware. Record this limitation in A/B results.

```bash
sha256sum -c issue41-runpod-841-20261110-r1.zip.sha256
mkdir transfer && cd transfer
unzip ../issue41-runpod-841-20261110-r1.zip
sha256sum -c SHA256SUMS
python3 runpod_embed.py --package . --output ../vectors --verify-only
# Starts real embedding; run only after explicit execution approval and GPU checks:
python3 runpod_embed.py --package . --output ../vectors
```

The worker refuses model digest/schema/rule/input-hash mismatches, redirects and proxies.
Requests use localhost `/api/embed`, one exact input, `truncate=false`, timeout 180 seconds,
without instructions or extra options. It validates 2,560 finite, nonzero float32-compatible
values. A Linux process lock prevents duplicate workers using the same output directory.
Per-ID records are fsynced then atomically renamed before continuing. A complete record
is never embedded again on restart. An interrupted in-flight call before a record commit
may need recomputation; this cannot be eliminated without a server-side transaction.
Corrupted saved results cause refusal rather than replacement. `.tmp` remnants are ignored.
Use Ctrl+C, wait for process exit, then repeat the identical command and output directory.
Preserve persistent output storage when stopping/replacing a pod.

`run-*.json` records actual elapsed time, completed/skipped counts, failures, Python/OS,
Ollama version and GPU inventory (NOT_VERIFIED if nvidia-smi is unavailable). Check
`ollama ps` during the run for actual offload; inventory alone does not prove GPU execution.
On full completion `results.json` contains all 841 records and run metadata and SHA256SUMS
checksums the final file. Transfer both files to a new local results directory. Retain the
per-Point records and run logs for recovery. No real embeddings/performance results are
included in the upload ZIP.

## Local export and import (repository root)

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
.\.venv\Scripts\python.exe -B -m database.runpod_transfer export --url http://127.0.0.1:46335 --package .cache/issue41-runpod-841-20261110-r1
# Default read-only validation of downloaded results:
.\.venv\Scripts\python.exe -B -m database.runpod_transfer import --url http://127.0.0.1:46335 --package .cache/issue41-runpod-841-20261110-r1 --results .cache/runpod-return-r1
# Only after explicit user approval; writes to the A/B DB:
.\.venv\Scripts\python.exe -B -m database.runpod_transfer import --url http://127.0.0.1:46335 --package .cache/issue41-runpod-841-20261110-r1 --results .cache/runpod-return-r1 --execute
```

Export refuses existing output paths. The fixed revision name above must be changed for
any new export. Existing package r1 can be reused without re-exporting. The local import
requires the full repository and its already installed Qdrant dependencies; these are not
needed on RunPod. The original paused checkpoint remains local.

Import validates file and per-record hashes, the full expected result ID set, local source
manifest/chunks/input strings/payload, the original 297-ID partition and its entire record
digest. The endpoint, existing container/volume and collection are pinned to the A/B DB;
final DB port 46336 is rejected. Original Points are never selected for writes. Already
imported target IDs must match the result vector/payload or import refuses to overwrite.
New vectors are canonically normalized to unit float32 for Qdrant Cosine; original 297
vectors are untouched. After writing, all 1,138 IDs/payloads, all imported vectors and the
original 297 digest are checked. A lost upsert response can be reconciled by re-running
read-only validation; the next import adds only still-missing IDs. Logs are not authoritative.
All local import/resume writers share the same lock. External writers must be stopped;
Qdrant upsert has no atomic insert-if-absent guarantee against unrelated writers.

Do not delete/modify original markers, checkpoints, approved data or DB volumes to recover
an error. Do not run import/resume simultaneously. Before actual import, back up the A/B
volume separately. A mismatch requires investigation, not relaxing hash/ID checks.

## Evaluation after successful full import

Keep `retrieval_v140_eval --mode model-ab`: 55 queries, 52 scored, Top-5, no instructions,
same filters. Store model/collection digests and environment via embedding_resume --context
before/after evaluation. Use a new output directory and record the CPU/GPU mix and Ollama
versions. Recall@5/MRR@5 use all required evidence spans rather than policy-level matching;
gold is not finally human-reviewed, so results remain preliminary. No evaluation is run
as part of package preparation.
