# Issue #41 durable model A/B evaluation

Current status: run02 and run03 completed. See [results and limitations](ISSUE41_AB_RUN03_ANALYSIS.md).
The implementation-preparation statement below is historical. The command uses a new
run04 path as an example only; no new evaluation was executed for PR preparation.

This change prepares evaluation only. No real query embedding or 55-question run was
executed during implementation. Existing Recall@5/MRR@5 definitions are unchanged.

## Preconditions and metadata

Keep the notebook Qdrant on `127.0.0.1:46335`. Keep RunPod Ollama on its own loopback
and connect through authenticated SSH TCP forwarding to notebook `127.0.0.1:11435`.
Do not expose Qdrant or an unauthenticated Ollama listener publicly.
Both model tags/digests must match the verified 0.6B/4B models. Record remote GPU/driver
with `nvidia-smi` and actual offload with `ollama ps`; GPU fields below are operator
evidence, not automatic hardware attestation. Verify the Pod rather than guessing values.

Create a credential-free environment JSON, for example `.cache/issue41-runpod-environment.json`:

```json
{"gpu":"RTX 4090","gpu_vram_gib":24,"ssh_tunnel":true,"host_label":"runpod-eval"}
```

Optional fields: `driver_version`, `remote_ram_gib`, `cpu_label`. Do not put passwords,
keys, SSH commands, private key paths or tokens in this file. Use the same verified
environment file throughout a resumed run. Unverified fields should not be presented
as observed facts. If the actual GPU/driver/host configuration changes, use a new run.

## Future execution (not run during preparation)

From the repository root, after establishing the authenticated tunnel:

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
$env:QDRANT_URL='http://127.0.0.1:46335'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11435'
.\.venv\Scripts\python.exe -B -m database.retrieval_v140_eval --mode model-ab --output docs/development/experiments/issue41_ab_run04 --timeout-06b 300 --timeout-4b 600 --environment .cache/issue41-runpod-environment.json
```

The two timeout values apply to every request for that model, including its first load.
Defaults are 180/300 seconds; the command gives a larger 300/600-second budget for remote
cold loading. These are limits, not guarantees. No warmup is performed, and a first load
is included in embedding latency only when the model is not already resident. Record
loading state rather than claiming every run is cold. Control remote model residency
before a fresh run (for example one loaded model at a time); the evaluator does not stop
models/services or change the server environment. Do not preload one model selectively
when comparing latency. Network round-trip overhead is part of measured embedding time.

After Ctrl+C, timeout or another failure, wait for the process to exit, fix the connection
or transient resource issue, then repeat the exact command with `--resume`. Do not delete
successful question files. Missing/failed questions will be attempted again; previously
saved successes are reused without embedding/search/scoring calls. The same failure is
not automatically retried inside a single invocation. Changing timeout or metadata is a
new experimental condition and requires a new output path, not relaxing resume checks.

If a process dies before a successful result is durably committed, that question may
need re-execution. The DB is only read, so no index rollback/import is required.

## Output and contracts

- `identity.json`: exact resume identity: both model digests, full completed collection
  digests, release manifest, freeze and question content hashes, evaluator/dependency code
  hashes, model order, Top-5, filters/instruction, timeout, endpoint, Python/OS/HEAD,
  actual Ollama version and declared remote environment.
- `questions/*.json`: one atomic/fsynced successful result per model/question with identity
  and row checksums. Point/query failures are not converted to misses or zero scores.
- `runs/*.json`: run status, reused/new counts, failure type/cause and phase/question/model,
  context/RAM snapshots and model loading snapshots before/after each model and after the
  first newly successful question. A hard kill can leave RUNNING; question files are authoritative.
- `session.lock`: OS-managed lock released on process exit; preserve the file.
- `summary.json`: original result structure and metric/timing keys, plus additive
  `reproducibility` metadata. Metrics are emitted only when all required calls succeed.

`evaluation_context()` is called directly for completed collection digests. The initial
297-Point checkpoint and `embedding_resume --context` CLI are not used. Actual model
loading/RAM/time are volatile observations and may change on resume; they are recorded,
not compared as identity fields. Declared GPU/environment, endpoints, versions/settings,
code/data hashes and model/collection digests must match exactly.

Timing remains one measured embedding call followed by one measured Qdrant search per
question. Model order is 0.6B then 4B. Mean/p95/total use 55 successful samples per model;
quality uses 52 answerable questions. Combined time sums embedding+search and excludes
preflight, scoring, journaling and metadata capture. Original timings of reused rows
are retained. A resumed run mixes sessions/cold starts: label it as resumed, and use a
fresh uninterrupted run for a clean latency benchmark. No arbitrary performance values
are supplied by tests.

Recall@5 requires all required evidence spans; MRR@5 uses the rank where the last
required span is found. Split-only/partial/doc-only are not full hits. These are not
policy-level Recall/MRR, and the gold remains pending final human review. The existing
297 CPU / 841 GPU index mix and remote inference/network path remain evaluation limitations.
