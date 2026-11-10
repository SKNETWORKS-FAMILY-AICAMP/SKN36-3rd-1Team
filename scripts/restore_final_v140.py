"""Issue #43: checksum-verified B2 snapshot + latest graph restore into NEW local DBs only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sys
from urllib.parse import urlsplit

COLLECTION = "exp_b2_qwen3emb06b_v140_v1"
MODEL = "qwen3-embedding:0.6b"
SNAPSHOT_DIMENSIONS = 1024
SNAPSHOT_POINTS = 1138
EXPECTED = (246, 261, 599, SNAPSHOT_POINTS, 1004, 2431, 136)


def require_local_url(value):
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise ValueError("Only credential-free loopback URLs are permitted")
    return value


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def check_bundle(folder):
    folder = Path(folder).resolve()
    verified = set()
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, name = line.split(None, 1)
        path = (folder / name.strip().lstrip("*")).resolve()
        if not path.is_relative_to(folder) or file_sha(path) != expected:
            raise ValueError("Bundle path/checksum mismatch")
        verified.add(path)
    if (folder / "package.json") not in verified:
        raise ValueError("Package manifest must be checksummed")
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    if (manifest.get("collection") != COLLECTION or manifest.get("dimensions") != SNAPSHOT_DIMENSIONS
            or manifest.get("points") != SNAPSHOT_POINTS):
        raise ValueError("Unexpected bundle collection/dimension/count")
    snapshot = (folder / manifest["snapshot"]).resolve()
    if snapshot not in verified or not snapshot.is_relative_to(folder) or file_sha(snapshot) != manifest["snapshot_sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    return manifest, snapshot


def scan(client):
    records, offset = {}, None
    while True:
        points, offset = client.scroll(COLLECTION, offset=offset, limit=128,
                                       with_payload=True, with_vectors=True)
        records.update({str(p.id): {"payload": p.payload or {}, "vector": p.vector} for p in points})
        if offset is None:
            return records


def vector_digest(records):
    return digest([(key, records[key]["vector"]) for key in sorted(records)])


def protected_digest(records):
    return digest([(key, records[key]["vector"],
                    {k: v for k, v in records[key]["payload"].items()
                     if k not in {"category", "life_stages"}}) for key in sorted(records)])


def alignment_plan(records, expected):
    if set(records) != set(expected):
        raise ValueError("Full Point ID set mismatch")
    changes, field_counts = [], {}
    for point_id, record in records.items():
        vector = record["vector"]
        if not isinstance(vector, list) or len(vector) != SNAPSHOT_DIMENSIONS or not all(math.isfinite(v) for v in vector):
            raise ValueError("Invalid snapshot vector")
        changed = {key: value for key, value in expected[point_id].items()
                   if record["payload"].get(key) != value}
        if set(changed) - {"category", "life_stages"}:
            raise ValueError("Unexpected protected payload difference")
        if changed:
            changes.append((point_id, changed))
            for key in changed:
                field_counts[key] = field_counts.get(key, 0) + 1
    return changes, field_counts



def build_document_hashes(documents):
    hashes = {}
    for document in documents:
        document_id = document.get("document_id")
        content_hash = document.get("content_hash")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Invalid document_id in document hash mapping")
        if document_id in hashes:
            raise ValueError(f"Duplicate document_id in hash mapping: {document_id}")
        if not isinstance(content_hash, str) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise ValueError(f"Invalid SHA-256 content_hash for document_id: {document_id}")
        hashes[document_id] = content_hash
    return hashes


def require_cross_db_pass(links):
    status = links.get("status")
    if status == "PASS" and links.get("failures") == []:
        return
    # Only known status labels/counts are exposed; never echo arbitrary backend values.
    label = status if status in ("PASS", "MISMATCH", "NOT_TESTED") else "MISSING" if status is None else "UNKNOWN"
    def count(key):
        value = links.get(key)
        return value if type(value) is int and value >= 0 else "MISSING_OR_INVALID"
    failures = links.get("failures")
    failed = len(failures) if isinstance(failures, list) else "MISSING_OR_INVALID"
    raise ValueError(f"Cross-DB validation failed: status={label}, "
                     f"checked={count('qdrant_points')}, total={count('qdrant_count')}, failures={failed}")


def require_empty(collection_names, counts):
    if collection_names or counts != {"nodes": 0, "relationships": 0}:
        raise ValueError("Restore requires empty Qdrant and Neo4j; existing data is never deleted")


def restore(args):
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo))
    os.environ["LIFEPOLICY_DATASET_DIR"] = str(repo / "data/releases/v1.4.0-candidate")
    from database import full_index as fi, handoff_data as hd, hybrid_search as hs
    from database.qdrant import store
    from database.neo4j import load_graph as lg
    from neo4j import GraphDatabase
    from qdrant_client import QdrantClient
    import requests

    package, snapshot = check_bundle(args.bundle)
    if fi.manifest_sha256() != package["manifest_lf_sha256"]:
        raise ValueError("Source release manifest differs from bundle")
    if any(not row["matched"] for row in hd.verify_manifest_checksums()):
        raise ValueError("Source release checksum mismatch")
    documents = hd.load_documents(verify_hash=True)
    document_hashes = build_document_hashes(documents)
    metadata = hd.load_payload_metadata()
    chunks, _ = fi.build_config_chunks("A_B2_V140", documents)
    missing_ids = sorted({chunk["document_id"] for chunk in chunks} - document_hashes.keys())
    if missing_ids:
        raise ValueError(f"Missing content_hash for chunk document_id(s): {missing_ids}")
    graph = lg.read_graph_input()
    validation = lg.validate_graph_input(graph)
    actual = (len({d["policy_id"] for d in documents}), len(documents), len(hd.load_payload_chunks()), len(chunks),
              sum(validation.node_counts.values()), sum(validation.relationship_counts.values()),
              validation.relationship_counts["REQUIRES_CONDITION"])
    if not validation.ok or actual != EXPECTED:
        raise ValueError("Latest source counts/integrity mismatch")
    settings = {key: os.environ.get(key, "") for key in
                ("QDRANT_URL", "QDRANT_API_KEY", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")}
    if not all(settings.values()):
        raise ValueError("Required DB environment variable is missing")
    require_local_url(settings["QDRANT_URL"])
    require_local_url(settings["NEO4J_URI"])
    client = QdrantClient(url=settings["QDRANT_URL"], api_key=settings["QDRANT_API_KEY"], timeout=30)
    driver = GraphDatabase.driver(settings["NEO4J_URI"],
                                 auth=(settings["NEO4J_USER"], settings["NEO4J_PASSWORD"]),
                                 connection_timeout=5)
    try:
        driver.verify_connectivity()
        runner = lg.Neo4jDriverRunner(driver)
        if not args.verify_only:
            require_empty([c.name for c in client.get_collections().collections],
                          runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
            if not args.apply:
                return {"status": "PREFLIGHT_PASS_NO_WRITES", "counts": actual,
                        "snapshot_sha256": package["snapshot_sha256"]}
            # Both DBs were checked empty before the first write. Failure leaves only NEW assets.
            with snapshot.open("rb") as stream:
                response = requests.post(settings["QDRANT_URL"].rstrip("/") +
                    f"/collections/{COLLECTION}/snapshots/upload", params={"priority": "snapshot"},
                    headers={"api-key": settings["QDRANT_API_KEY"]}, files={"snapshot": stream}, timeout=120)
                response.raise_for_status()
        info = client.get_collection(COLLECTION)
        params = info.config.params.vectors
        if (params.size != SNAPSHOT_DIMENSIONS or str(params.distance).split(".")[-1].lower() != "cosine"
                or info.points_count != SNAPSHOT_POINTS):
            raise ValueError("Collection size/distance/count mismatch")
        records = scan(client)
        expected = {}
        for chunk in chunks:
            old = records.get(chunk["point_id"], {}).get("payload", {})
            run = {"embedding_model": MODEL, "embedding_dim": SNAPSHOT_DIMENSIONS,
                   "embedding_input_rule": fi.EMBEDDING_INPUT_RULE,
                   "index_run_id": old.get("index_run_id")}
            expected[chunk["point_id"]] = store.build_point_payload(
                chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
                content_hash=document_hashes[chunk["document_id"]],
                run_info=run)
        changes, counts = alignment_plan(records, expected)
        before = protected_digest(records)
        vectors = vector_digest(records)
        if vectors != package["vector_digest"]:
            raise ValueError("Full vector digest differs from verified source")
        if args.verify_only and changes:
            raise ValueError("Current payload needs alignment; verify-only refuses writes")
        for point_id, payload in changes:
            client.set_payload(COLLECTION, payload=payload, points=[point_id], wait=True)
        after = scan(client) if changes else records
        remaining, _ = alignment_plan(after, expected)
        if remaining or protected_digest(after) != before:
            raise ValueError("Payload alignment/invariance verification failed")
        if args.verify_only:
            verification = lg.verify_loaded_graph(graph, runner)
            verification_source = "verify_loaded_graph"
        else:
            load_result = lg.load_graph(graph, runner, batch_size=128, verify=True)
            if load_result.get("ok") is not True:
                raise ValueError("Neo4j restore verification failed")
            verification = load_result.get("verification")
            verification_source = "load_graph.verification"
        if not isinstance(verification, dict) or verification.get("ok") is not True:
            raise ValueError("Neo4j full verification failed")
        links = hs.verify_cross_db(client=client, runner=runner, collection=COLLECTION)
        require_cross_db_pass(links)
        return {"status": "PASS", "counts": actual, "collection": COLLECTION,
                "metadata_points_updated": len(changes), "metadata_field_updates": counts,
                "all_vectors_and_protected_payload_unchanged": True, "vector_digest": vectors,
                "graph_totals": runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0],
                "full_graph_properties_verified": verification["ok"],
                "graph_verification_source": verification_source,
                "cross_db": {k: links[k] for k in ("status", "qdrant_points", "qdrant_count",
                                                  "all_points_read", "summary", "failures")},
                "manifest_lf_sha256": fi.manifest_sha256(), "embedding_calls": 0,
                "verify_only": args.verify_only}
    finally:
        client.close()
        driver.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bundle", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error("Existing report will not be overwritten")
    try:
        result = restore(args)
    except Exception as error:
        # Do not print raw network errors or configuration values.
        result = {"status": "FAILED", "error_type": type(error).__name__,
                  "action": "Stop. Preserve partial NEW DB assets; never delete or overwrite existing data."}
        if isinstance(error, ValueError):
            result["reason"] = str(error)
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "PREFLIGHT_PASS_NO_WRITES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
