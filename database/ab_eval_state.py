"""Durable per-question model-ab results; no embedding or database writes."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid

from database import embedding as em, embedding_resume as er


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    from scripts.runpod_embed import atomic as write
    write(path, value)


def loaded_models():
    import requests
    try:
        response = requests.get(em.resolve_ollama_base_url().rstrip("/") + "/api/ps", timeout=10)
        response.raise_for_status()
        return [{k: m.get(k) for k in ("name", "digest", "size", "size_vram", "expires_at")}
                for m in response.json()["models"]]
    except Exception as error:
        return {"status": "NOT_VERIFIED", "error_type": type(error).__name__}


def collect_context(client, plans, timeouts, environment):
    import requests
    from database.qdrant import store
    from scripts.runpod_embed import MODEL_DIGEST
    # Uses completed collections directly; never calls resume() or reads the 297 checkpoint.
    context = er.evaluation_context(client)
    url, _ = store.resolve_connection_settings()
    er.require_target(url)
    endpoint = em.resolve_ollama_base_url().rstrip("/")
    response = requests.get(endpoint + "/api/version", timeout=10)
    response.raise_for_status()
    models = {name: row["digest"] for name, row in context["models"].items()}
    expected = {"qwen3-embedding:0.6b": "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
                "qwen3-embedding:4b": MODEL_DIGEST}
    if models != expected:
        raise ValueError("Evaluation model digest mismatch")
    environment = environment or {"gpu": "NOT_VERIFIED", "ssh_tunnel": "NOT_VERIFIED"}
    allowed = {"gpu", "gpu_vram_gib", "ssh_tunnel", "host_label", "driver_version", "remote_ram_gib", "cpu_label"}
    if (set(environment) - allowed or "gpu" not in environment or "ssh_tunnel" not in environment
            or any(isinstance(v, (dict, list)) for v in environment.values())):
        raise ValueError("Invalid evaluation environment fields; do not supply credentials")
    code_files = ["retrieval_v140_eval.py", "ab_eval_state.py", "embedding.py", "embedding_resume.py",
                  "retrieval_eval.py", "full_index.py", "handoff_data.py", "qdrant/store.py"]
    identity = {"schema": 1, "models": models, "collections": context["collections"],
        "manifest": context["manifest_sha256"], "freeze": context["freeze_sha256"],
        "code": {n: hashlib.sha256((er.ROOT / "database" / n).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                 for n in code_files},
        "python": context["python"], "platform": context["platform"], "head": context["git"]["head"],
        "environment": environment, "ollama_url": endpoint, "qdrant_url": url,
        "ollama_version": response.json()["version"], "timeouts": timeouts, "plans": plans,
        "order": list(plans), "top_k": 5, "filters": None, "query_instruction": None,
        "warmup": False, "batch_size": 1}
    identity["code"]["scripts/runpod_embed.py"] = hashlib.sha256(
        (er.ROOT / "scripts/runpod_embed.py").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return identity, {"collection_context": context, "loaded_models": loaded_models(),
                      "remote_gpu_evidence": "Operator supplied; verify with remote nvidia-smi and ollama ps"}


class Journal:
    def __init__(self, output, identity, runtime, *, resume=False):
        self.output, self.identity, self.runtime = Path(output), identity, runtime
        self.resume = resume
        self.run_id = uuid.uuid4().hex
        self.phase, self.config, self.question = "preflight", None, None
        self.reused, self.saved = 0, 0

    def __enter__(self):
        if not self.resume:
            self.output.mkdir(parents=True, exist_ok=False)
        elif not self.output.is_dir():
            raise ValueError("Resume output directory is missing")
        self.lock = er.writer_lock(self.output / "session.lock")
        self.lock.__enter__()
        try:
            path = self.output / "identity.json"
            if self.resume:
                if digest(json.loads(path.read_text(encoding="utf-8"))) != digest(self.identity):
                    raise ValueError("Resume identity mismatch: model/data/code/settings/environment changed")
            else:
                atomic(path, self.identity)
            (self.output / "questions").mkdir(exist_ok=True)
            (self.output / "runs").mkdir(exist_ok=True)
            self.run_path = self.output / "runs" / (self.run_id + ".json")
            self.record("RUNNING")
            return self
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise

    def record(self, status, **extra):
        atomic(self.run_path, {"status": status, "run_id": self.run_id,
            "utc": datetime.now(timezone.utc).isoformat(), "runtime": self.runtime,
            "phase": self.phase, "config": self.config, "question": self.question,
            "reused": self.reused, "newly_saved": self.saved, **extra})

    def __exit__(self, kind, error, traceback):
        try:
            self.record("PASS" if error is None else "INTERRUPTED_OR_FAILED",
                        error_type=kind.__name__ if kind else None,
                        cause_type=type(error.__cause__).__name__ if error and error.__cause__ else None)
        finally:
            self.lock.__exit__(kind, error, traceback)

    def path(self, config, question):
        return self.output / "questions" / (digest([config, question]) + ".json")

    def load(self, config, question):
        path = self.path(config, question)
        if not path.exists(): return None
        value = json.loads(path.read_text(encoding="utf-8"))
        row = value["row"]
        if (value["identity"] != digest(self.identity) or value["config"] != config
                or row["question_id"] != question or value["row_digest"] != digest(row)
                or any(not isinstance(row[k], (int, float)) or not math.isfinite(row[k]) or row[k] < 0
                       for k in ("embedding_seconds", "search_seconds"))):
            raise ValueError("Saved question identity/checksum/timing mismatch")
        self.reused += 1
        return row

    def save(self, config, row):
        path = self.path(config, row["question_id"])
        if path.exists(): raise ValueError("Successful question will not be overwritten")
        atomic(path, {"identity": digest(self.identity), "config": config,
                      "row": row, "row_digest": digest(row), "run_id": self.run_id,
                      "saved_at": datetime.now(timezone.utc).isoformat()})
        self.saved += 1

    def loading(self, label):
        self.runtime.setdefault("loading_snapshots", {})[label] = loaded_models()
        self.record("RUNNING")
