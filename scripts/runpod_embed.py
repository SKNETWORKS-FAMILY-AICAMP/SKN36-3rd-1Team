"""Portable standard-library-only Ollama worker. No DB credentials or DB calls."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import struct
import subprocess
import time
import urllib.request
import uuid

MODEL = "qwen3-embedding:4b"
MODEL_DIGEST = "df5bd2e3c74cd8d069d21dc038f1b359fcdc9458fce1c99bd43c9eb1518ff907"
DIM, COUNT, RULE = 2560, 841, "policy_section_body_v2"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_sums(folder):
    names = set()
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        sha, name = line.split(None, 1)
        if Path(name).name != name or name in names or file_sha(folder / name) != sha:
            raise ValueError("Package checksum/path mismatch")
        names.add(name)
    if not {"manifest.json", "inputs.jsonl", "runpod_embed.py", "README.md", "requirements.txt"} <= names:
        raise ValueError("Required checksums missing")


def load_package(folder):
    verify_sums(folder)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    core = {k: v for k, v in m.items() if k != "package_id"}
    if (m.get("schema") != 1 or m.get("model") != MODEL or m.get("model_digest") != MODEL_DIGEST
            or m.get("dimension") != DIM or m.get("input_rule") != RULE
            or m.get("count") != COUNT or m.get("dataset") != "1.4.0-candidate"
            or m.get("package_id") != digest(core) or m.get("inputs_sha256") != file_sha(folder / "inputs.jsonl")):
        raise ValueError("Package schema/model/rule/identity mismatch")
    rows = [json.loads(line) for line in (folder / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(rows) != COUNT or len({r["point_id"] for r in rows}) != COUNT:
        raise ValueError("Input count/duplicate ID mismatch")
    for row in rows:
        uuid.UUID(row["point_id"])
        if (not all(isinstance(row.get(k), str) and row[k] for k in
                    ("chunk_id", "document_id", "policy_id", "input_text"))
                or row["input_sha256"] != hashlib.sha256(row["input_text"].encode()).hexdigest()
                or (row["model"], row["model_digest"], row["dimension"], row["input_rule"])
                != (MODEL, MODEL_DIGEST, DIM, RULE)
                or not isinstance(row["payload"], dict)
                or any(row["payload"].get(k) != row[k] for k in ("chunk_id", "document_id", "policy_id"))):
            raise ValueError("Invalid input row/schema/hash")
    return m, rows


def validate_vector(vector):
    if (not isinstance(vector, list) or len(vector) != DIM
            or not all(type(v) in (float, int) and math.isfinite(v) for v in vector)):
        raise ValueError("Invalid embedding dimension/nonfinite vector")
    # Qdrant uses float32; reject values that overflow when serialized there.
    for value in vector:
        struct.pack("f", value)
    if not any(vector):
        raise ValueError("Zero vector is invalid for Cosine")


def validate_result(result, row, manifest):
    if (result.get("package_id") != manifest["package_id"]
            or any(result.get(k) != row[k] for k in
                   ("point_id", "input_sha256", "model", "model_digest", "dimension", "input_rule"))
            or result.get("record_sha256") != digest({k: v for k, v in result.items() if k != "record_sha256"})):
        raise ValueError("Result identity/checksum mismatch")
    validate_vector(result["vector"])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Ollama redirects are forbidden")


def api(path, body=None):
    if path not in ("tags", "embed", "version", "ps"):
        raise ValueError("Unexpected Ollama API")
    request = urllib.request.Request("http://127.0.0.1:11434/api/" + path,
        data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    # Ignore proxy variables: never send these requests outside the pod loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=180) as response:
        return json.load(response)


@contextmanager
def worker_lock(folder):
    import fcntl  # Linux only; no dependency installation.
    with (folder / "worker.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def generate(folder, output, request=api):
    manifest, rows = load_package(folder)
    installed = [m for m in request("tags")["models"] if m["name"] == MODEL]
    if len(installed) != 1 or installed[0]["digest"] != manifest["model_digest"]:
        raise ValueError("Installed Ollama model digest mismatch")
    output.mkdir(parents=True, exist_ok=True)
    existing = output / "package_id.txt"
    if existing.exists() and existing.read_text() != manifest["package_id"]:
        raise ValueError("Output belongs to another input package")
    if not existing.exists():
        with existing.open("x") as stream: stream.write(manifest["package_id"])
    records = output / "records"
    records.mkdir(exist_ok=True)
    expected = {r["point_id"] for r in rows}
    if {p.stem for p in records.glob("*.json")} - expected:
        raise ValueError("Unexpected result ID")
    # Validate every existing record before making any embedding request.
    for row in rows:
        path = records / (row["point_id"] + ".json")
        if path.exists(): validate_result(json.loads(path.read_text()), row, manifest)
    version = request("version")
    try:
        gpu = subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                                       "--format=csv,noheader"], text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        gpu = "NOT_VERIFIED"
    started, new, skipped, status = time.perf_counter(), 0, 0, "interrupted_or_failed"
    run_id = uuid.uuid4().hex
    try:
        for row in rows:
            path = records / (row["point_id"] + ".json")
            if path.exists():
                skipped += 1
                continue
            tick = time.perf_counter()
            body = request("embed", {"model": MODEL, "input": [row["input_text"]], "truncate": False})
            vectors = body["embeddings"]
            if len(vectors) != 1: raise ValueError("Embedding batch count mismatch")
            validate_vector(vectors[0])
            result = {k: row[k] for k in
                      ("point_id", "input_sha256", "model", "model_digest", "dimension", "input_rule")}
            result.update(package_id=manifest["package_id"], vector=vectors[0],
                          seconds=time.perf_counter() - tick, generation_run=run_id)
            result["record_sha256"] = digest(result)
            atomic(path, result)  # Commit per Point; incomplete .tmp is ignored on restart.
            new += 1
            print(json.dumps({"new": new, "already_saved": skipped, "target": len(rows)}), flush=True)
        status = "PASS"
    finally:
        atomic(output / ("run-" + run_id + ".json"), {"status": status, "new": new, "skipped": skipped,
            "wall_seconds": time.perf_counter() - started, "python": platform.python_version(),
            "platform": platform.platform(), "utc": datetime.now(timezone.utc).isoformat(),
            "package_id": manifest["package_id"], "ollama_version": version, "gpu": gpu})
    # Final immutable record set is validated before creating the transferable result.
    results = []
    for row in rows:
        result = json.loads((records / (row["point_id"] + ".json")).read_text())
        validate_result(result, row, manifest)
        results.append(result)
    atomic(output / "results.json", {"package_id": manifest["package_id"], "records": results,
        "runs": [json.loads(p.read_text()) for p in sorted(output.glob("run-*.json"))]})
    (output / "SHA256SUMS").write_text(file_sha(output / "results.json") + "  results.json\n", encoding="utf-8")
    return {"status": status, "new": new, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        manifest, rows = load_package(args.package)
        print(json.dumps({"status": "PACKAGE_PASS", "count": len(rows), "package_id": manifest["package_id"]}))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    with worker_lock(args.output):
        print(json.dumps(generate(args.package, args.output)))


if __name__ == "__main__":
    main()
