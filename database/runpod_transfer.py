"""Issue #41 portable input export and explicit, missing-only local result import."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import zipfile

from database import embedding_resume as er
from database import embedding as em, full_index as fi, handoff_data as hd
from database.qdrant import store
from scripts import runpod_embed as worker

RUN_ID = "RUN_ISSUE41_RUNPOD"


def source_data():
    if hd.dataset_version() != "1.4.0-candidate" or any(not r["matched"] for r in hd.verify_manifest_checksums()):
        raise ValueError("Local release checksum/version mismatch")
    docs = hd.load_documents(verify_hash=True)
    chunks = fi.build_config_chunks(er.CONFIG, docs)[0]
    if chunks != fi.build_config_chunks("A_B2_V140", docs)[0] or len(chunks) != er.POINTS:
        raise ValueError("Local B2 chunk/ID contract mismatch")
    return chunks, hd.load_payload_metadata(), {d["document_id"]: d["content_hash"] for d in docs}


def input_row(chunk, metadata, hashes):
    text = em.build_embedding_text(chunk, store.metadata_for_chunk(metadata, chunk), fi.EMBEDDING_INPUT_RULE)
    payload = store.build_point_payload(chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
        content_hash=hashes[chunk["document_id"]], run_info=fi.make_run_info(er.CONFIG, RUN_ID))
    return {**{k: chunk[k] for k in ("point_id", "chunk_id", "document_id", "policy_id")},
            "input_text": text, "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "model": worker.MODEL, "model_digest": worker.MODEL_DIGEST,
            "dimension": worker.DIM, "input_rule": worker.RULE, "payload": payload}


def checked_scan(client, chunks, metadata, hashes):
    params = client.get_collection(er.COLLECTION).config.params.vectors
    if params.size != er.DIM or str(params.distance).lower().split(".")[-1] != "cosine":
        raise ValueError("4B collection config mismatch")
    records = er.scan(client)
    if client.count(er.COLLECTION, exact=True).count != len(records):
        raise ValueError("Incomplete Point scan")
    er.validate_records(records, chunks, metadata, hashes)
    return records


def export_package(client, folder):
    chunks, metadata, hashes = source_data()
    records = checked_scan(client, chunks, metadata, hashes)
    anchor = json.loads(er.ANCHOR.read_text(encoding="utf-8"))
    if len(records) != 297 or er.digest(records) != anchor["partial_records_sha256"]:
        raise ValueError("Export must match the verified original 297 Points")
    rows = [input_row(c, metadata, hashes) for c in chunks if c["point_id"] not in records]
    if len(rows) != worker.COUNT:
        raise ValueError("Expected exactly 841 missing input records")
    installed = [m for m in worker.api("tags")["models"] if m["name"] == worker.MODEL]
    if len(installed) != 1 or installed[0]["digest"] != worker.MODEL_DIGEST:
        raise ValueError("Local model digest mismatch")
    archive = folder.with_suffix(".zip")
    if folder.exists() or archive.exists() or Path(str(archive) + ".sha256").exists():
        raise ValueError("Export path exists; choose a new path")
    folder.mkdir(parents=True)
    with (folder / "inputs.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows: stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {"schema": 1, "dataset": "1.4.0-candidate", "model": worker.MODEL,
        "model_digest": worker.MODEL_DIGEST, "dimension": worker.DIM, "input_rule": worker.RULE,
        "count": worker.COUNT, "total_points": er.POINTS, "existing_ids": sorted(records),
        "existing_records_digest": er.digest(records), "manifest_sha256": fi.manifest_sha256(),
        "chunk_fingerprint": fi.chunk_fingerprint(chunks), "inputs_sha256": worker.file_sha(folder / "inputs.jsonl"),
        "source_git": fi.git_state(), "source_basis": "uncommitted Issue41 working tree",
        "worker_sha256": worker.file_sha(Path(worker.__file__))}
    manifest["package_id"] = worker.digest(manifest)
    worker.atomic(folder / "manifest.json", manifest)
    shutil.copyfile(worker.__file__, folder / "runpod_embed.py")
    shutil.copyfile(er.ROOT / "docs/development/ISSUE41_RUNPOD_GUIDE.md", folder / "README.md")
    (folder / "requirements.txt").write_text("# Python >=3.10 standard library only; no pip packages required.\n", encoding="utf-8")
    (folder / "SHA256SUMS").write_text("".join(worker.file_sha(p) + "  " + p.name + "\n"
        for p in sorted(folder.iterdir()) if p.is_file()), encoding="utf-8")
    worker.load_package(folder)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zip_file:
        for p in sorted(folder.iterdir()): zip_file.write(p, p.name)
    Path(str(archive) + ".sha256").write_text(worker.file_sha(archive) + "  " + archive.name + "\n", encoding="utf-8")
    return {"zip": str(archive), "bytes": archive.stat().st_size, "sha256": worker.file_sha(archive),
            "count": len(rows), "model_digest": worker.MODEL_DIGEST, "package_id": manifest["package_id"]}


def f32(vector):
    # Canonical unit float32 vectors: Qdrant Cosine uses normalized vectors.
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid Cosine vector norm")
    return [struct.unpack("f", struct.pack("f", value / norm))[0] for value in vector]


def float32_bytes(vector):
    """Compare storage values, without normalizing the REST response again."""
    worker.validate_vector(vector)
    return b"".join(struct.pack("<f", value) for value in vector)


def same_payload(actual, expected):
    """Python equality alone considers True == 1 == 1.0; payload types matter."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same_payload(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(same_payload(a, b) for a, b in zip(actual, expected))
    return actual == expected


def load_results(folder, manifest, rows):
    if (folder / "SHA256SUMS").read_text(encoding="utf-8").strip() != worker.file_sha(folder / "results.json") + "  results.json":
        raise ValueError("Result file checksum mismatch")
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    mapped = {r["point_id"]: r for r in result["records"]}
    if (result["package_id"] != manifest["package_id"] or len(mapped) != len(result["records"])
            or set(mapped) != {r["point_id"] for r in rows}):
        raise ValueError("Result package/ID set mismatch")
    for row in rows: worker.validate_result(mapped[row["point_id"]], row, manifest)
    return mapped


def import_results(client, package, results, *, execute=False):
    manifest, rows = worker.load_package(package)
    anchor = json.loads(er.ANCHOR.read_text(encoding="utf-8"))
    if manifest["existing_records_digest"] != anchor["partial_records_sha256"]:
        raise ValueError("Package does not match the local original checkpoint")
    generated = load_results(results, manifest, rows)
    chunks, metadata, hashes = source_data()
    if manifest["manifest_sha256"] != fi.manifest_sha256() or manifest["chunk_fingerprint"] != fi.chunk_fingerprint(chunks):
        raise ValueError("Local source differs from exported source")
    expected = {c["point_id"]: c for c in chunks}
    for row in rows:
        if row != input_row(expected[row["point_id"]], metadata, hashes):
            raise ValueError("Exported text/hash/payload differs from current source")
    original_ids = set(manifest["existing_ids"])
    if (len(original_ids) != 297 or original_ids & set(generated)
            or original_ids | set(generated) != set(expected)):
        raise ValueError("Original/exported ID partition mismatch")
    before = checked_scan(client, chunks, metadata, hashes)
    original = {k: v for k, v in before.items() if k in original_ids}
    if set(original) != original_ids or er.digest(original) != manifest["existing_records_digest"]:
        raise ValueError("Original 297 Points changed; refuse import")
    payloads = {row["point_id"]: row["payload"] for row in rows}

    def verify_imported(records):
        for key in set(records) & set(generated):
            if (not same_payload(records[key]["payload"], payloads[key])
                    or float32_bytes(records[key]["vector"]) != float32_bytes(f32(generated[key]["vector"]))):
                raise ValueError("Existing imported Point differs; never overwrite")

    verify_imported(before)
    todo = sorted(set(generated) - set(before))
    if not execute:
        return {"mode": "read-only", "existing": len(before), "to_add": len(todo),
                "original_digest_unchanged": True, "package_id": manifest["package_id"]}
    for index, key in enumerate(todo):
        if client.count(er.COLLECTION, exact=True).count != len(before) + index:
            raise ValueError("Concurrent DB change; stop import")
        if client.retrieve(er.COLLECTION, ids=[key], with_payload=False, with_vectors=False):
            raise ValueError("Target Point already exists; refuse overwrite")
        store.upsert_points(client, er.COLLECTION, [{"id": key, "vector": f32(generated[key]["vector"]),
                                                   "payload": payloads[key]}])
    after = checked_scan(client, chunks, metadata, hashes)
    if set(after) != set(expected) or er.digest({k: after[k] for k in original_ids}) != manifest["existing_records_digest"]:
        raise ValueError("Final ID set/original digest mismatch")
    verify_imported(after)
    return {"status": "PASS", "added": len(todo), "points": len(after),
            "original_digest_unchanged": True, "records_digest": er.digest(after)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "import"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    er.require_target(args.url)
    er.verify_container()
    if args.mode == "export" and args.execute: parser.error("Export never writes DB")
    if args.mode == "import" and args.results is None: parser.error("--results is required")
    from qdrant_client import QdrantClient
    client = QdrantClient(url=args.url, timeout=30)
    try:
        if args.mode == "export": result = export_package(client, args.package)
        elif args.execute:
            with er.writer_lock(er.STATE.with_suffix(".lock")):
                result = import_results(client, args.package, args.results, execute=True)
        else: result = import_results(client, args.package, args.results)
        print(json.dumps(result, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
