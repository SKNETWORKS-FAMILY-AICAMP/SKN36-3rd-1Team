"""Issue #41: explicit, checkpointed missing-only resume on the dedicated A/B DB.

Default is read-only. Existing local execute.py modes/markers are never used or edited.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import subprocess

from database import embedding as em, full_index as fi, handoff_data as hd
from database.qdrant import store

URL = "http://127.0.0.1:46335"
COLLECTION = "exp_b2_qwen3emb4b_v140_v1"
CONFIG = "A_B2_4B_V140"
MODEL, DIM, POINTS = "qwen3-embedding:4b", 2560, 1138
ROOT = Path(__file__).resolve().parents[1]
ANCHOR = ROOT / ".cache/issue41-ab-execution-20261110-01/paused-checkpoint.json"
STATE = ROOT / ".cache/issue41-safe-resume/checkpoint.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def require_target(url, collection=COLLECTION):
    if url != URL or collection != COLLECTION:
        raise ValueError("Only the pinned Issue41 A/B endpoint and 4B collection are allowed")


def verify_container():
    docker = "C:/Users/Playdata/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
    info = json.loads(subprocess.check_output([docker, "inspect", "issue41-ab-run01"], text=True))[0]
    if (not info["State"]["Running"]
            or info["Config"]["Labels"].get("purpose") != "issue41-ab-execution"
            or [(m.get("Name"), m["Destination"]) for m in info["Mounts"]]
            != [("issue41_ab_run01_storage", "/qdrant/storage")]
            or info["HostConfig"]["PortBindings"].get("6333/tcp")
            != [{"HostIp": "127.0.0.1", "HostPort": "46335"}]):
        raise ValueError("A/B container identity/mount/port mismatch")


def available_ram():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in
            ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise RuntimeError("RAM inspection failed")
    return memory.available


def scan(client, collection=COLLECTION):
    records, offset = {}, None
    while True:
        rows, offset = client.scroll(collection, offset=offset, limit=128,
                                     with_payload=True, with_vectors=True)
        for row in rows:
            key = str(row.id)
            if key in records:
                raise ValueError("Duplicate Point ID while scanning")
            records[key] = {"payload": row.payload, "vector": row.vector}
        if offset is None:
            return records


def validate_records(records, chunks, metadata, hashes):
    expected = {c["point_id"]: c for c in chunks}
    if len(expected) != len(chunks) or not set(records) <= set(expected):
        raise ValueError("Unexpected/duplicate Point ID")
    for key, record in records.items():
        vector, payload = record["vector"], record["payload"]
        if (not isinstance(vector, list) or len(vector) != DIM
                or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in vector)):
            raise ValueError("Invalid 4B vector dimension/value")
        if not isinstance(payload, dict) or not payload.get("index_run_id"):
            raise ValueError("Missing payload/run identity")
        chunk = expected[key]
        desired = store.build_point_payload(chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
            content_hash=hashes[chunk["document_id"]],
            run_info=fi.make_run_info(CONFIG, payload["index_run_id"]))
        if payload != desired:
            raise ValueError("Full payload/content_hash mismatch")


def reconcile(records, state, anchor, fingerprint):
    """DB is authoritative; only one journaled uncertain write may be adopted."""
    if state is None:
        if len(records) != anchor["partial_4b"]["existing_points"] or digest(records) != anchor["partial_records_sha256"]:
            raise ValueError("Initial DB differs from paused checkpoint")
        return {"fingerprint": fingerprint, "acknowledged": {k: digest(v) for k, v in records.items()},
                "pending": None}
    if state["fingerprint"] != fingerprint:
        raise ValueError("Checkpoint dataset/input/model mismatch")
    known = state["acknowledged"]
    if any(k not in records or digest(records[k]) != value for k, value in known.items()):
        raise ValueError("Acknowledged Point changed/missing")
    extras = set(records) - set(known)
    if extras - {state["pending"]}:
        raise ValueError("Unjournaled Point found; possible concurrent writer")
    return {**state, "acknowledged": {k: digest(v) for k, v in records.items()}, "pending": None}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def writer_lock(path):
    """OS lock releases on crash; the lock file is retained, not a one-shot marker."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def resume(client, chunks, metadata, hashes, anchor, path, *, execute=False, embedder=None,
           ram=available_ram):
    config = fi.get_config(CONFIG)
    if config["collection"] != COLLECTION or fi.config_model(config) != (MODEL, DIM):
        raise ValueError("Pinned 4B configuration changed")
    if len(chunks) != POINTS:
        raise ValueError("Expected 1138 B2 chunks")
    params = client.get_collection(COLLECTION).config.params.vectors
    if params.size != DIM or str(params.distance).lower().split(".")[-1] != "cosine":
        raise ValueError("Collection dimension/distance mismatch")
    records = scan(client)
    if client.count(COLLECTION, exact=True).count != len(records):
        raise ValueError("Incomplete collection scan")
    validate_records(records, chunks, metadata, hashes)
    fingerprint = digest({"model": MODEL, "dim": DIM, "rule": fi.EMBEDDING_INPUT_RULE,
        "manifest": fi.manifest_sha256(), "chunks": chunks,
        "inputs": [em.build_embedding_text(c, store.metadata_for_chunk(metadata, c), fi.EMBEDDING_INPUT_RULE)
                   for c in chunks], "hashes": hashes})
    state = reconcile(records, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None,
                      anchor, fingerprint)
    todo = [c for c in chunks if c["point_id"] not in records]
    if not execute:
        return {"mode": "read-only", "existing": len(records), "missing": len(todo),
                "records_digest": digest(records), "fingerprint": fingerprint}
    if ram() < 6 * 1024 ** 3:
        raise ValueError("Less than 6 GiB available; this threshold does not guarantee safe model loading")
    save_state(path, state)
    # Lazy construction; dry-run and preflight never invoke/load an embedding model.
    embedder = embedder or em.make_ollama_embedder(model=MODEL, expected_dim=DIM, batch_size=1, timeout=180)
    for chunk in todo:
        if ram() < 2 * 1024 ** 3:
            raise ValueError("Less than 2 GiB available before next request; stop without retry")
        key = chunk["point_id"]
        # Single-writer lock + count check prevent this tool's parallel resumes.
        if client.count(COLLECTION, exact=True).count != len(records):
            raise ValueError("Collection changed during resume")
        state["pending"] = key
        save_state(path, state)  # Intent is durable before embedding/upsert.
        text = em.build_embedding_text(chunk, store.metadata_for_chunk(metadata, chunk), fi.EMBEDDING_INPUT_RULE)
        vectors = em.validate_vectors(embedder([text]), 1, DIM)
        points = store.build_points([chunk], vectors, policy_metadata=metadata, content_hashes=hashes,
                                    run_info=fi.make_run_info(CONFIG, "RUN_ISSUE41_SAFE_RESUME"))
        store.upsert_points(client, COLLECTION, points)
        rows = client.retrieve(COLLECTION, ids=[key], with_payload=True, with_vectors=True)
        new = {str(p.id): {"payload": p.payload, "vector": p.vector} for p in rows}
        if set(new) != {key}:
            raise ValueError("Point save not confirmed; rerun preflight to reconcile DB")
        validate_records(new, chunks, metadata, hashes)
        records.update(new)
        state["acknowledged"][key] = digest(new[key])
        state["pending"] = None
        save_state(path, state)
        with path.with_suffix(".jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"confirmed_point_id": key, "confirmed_points": len(records)}) + "\n")
        print(json.dumps({"confirmed_points": len(records), "total": len(chunks)}), flush=True)
    after = scan(client)
    validate_records(after, chunks, metadata, hashes)
    reconcile(after, state, anchor, fingerprint)
    if set(after) != {c["point_id"] for c in chunks}:
        raise ValueError("Final ID set incomplete")
    return {"status": "PASS", "points": len(after), "records_digest": digest(after)}


def evaluation_context(client):
    """Read-only sidecar evidence; capture again immediately before/after evaluation."""
    import requests
    response = requests.get(em.resolve_ollama_base_url().rstrip("/") + "/api/tags", timeout=10)
    response.raise_for_status()
    models = {m["name"]: {k: m.get(k) for k in ("digest", "size", "details")}
              for m in response.json()["models"] if m["name"] in (MODEL, "qwen3-embedding:0.6b")}
    return {"captured_at": datetime.now(timezone.utc).isoformat(),
            "models": models, "collections": {name: digest(scan(client, name)) for name in
            ("exp_b2_qwen3emb06b_v140_v1", COLLECTION)},
            "model_order": ["qwen3-embedding:0.6b", MODEL], "python": platform.python_version(),
            "platform": platform.platform(), "available_ram_bytes": available_ram(), "endpoint": URL,
            "git": fi.git_state(), "manifest_sha256": fi.manifest_sha256(),
            "evaluator_sha256": hashlib.sha256((ROOT / "database/retrieval_v140_eval.py").read_bytes()).hexdigest(),
            "freeze_sha256": hashlib.sha256((ROOT / "docs/development/retrieval_eval/retrieval_eval_v0.freeze.json").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--context", action="store_true", help="Read-only model/collection digest sidecar")
    args = parser.parse_args()
    require_target(args.url)
    verify_container()
    if hd.dataset_version() != "1.4.0-candidate" or any(not r["matched"] for r in hd.verify_manifest_checksums()):
        raise ValueError("Release dataset/checksum mismatch")
    docs = hd.load_documents(verify_hash=True)
    chunks = fi.build_config_chunks(CONFIG, docs)[0]
    if chunks != fi.build_config_chunks("A_B2_V140", docs)[0]:
        raise ValueError("B2 chunks/IDs differ")
    from qdrant_client import QdrantClient
    client = QdrantClient(url=args.url, timeout=30)  # Dedicated AB DB has no auth; never inherit final DB config.
    try:
        kwargs = dict(execute=args.execute)
        inputs = (client, chunks, hd.load_payload_metadata(), {d["document_id"]: d["content_hash"] for d in docs},
                  json.loads(ANCHOR.read_text(encoding="utf-8")), STATE)
        if args.execute:
            with writer_lock(STATE.with_suffix(".lock")):
                result = resume(*inputs, **kwargs)
        else:
            result = resume(*inputs, **kwargs)
        if args.context:
            result["evaluation_context"] = evaluation_context(client)
        print(json.dumps(result, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
