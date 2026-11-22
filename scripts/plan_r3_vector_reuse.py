"""Issue #105: r3 B2·B3 벡터 재사용 계획 — 읽기 전용 (DB 접속·임베딩·DB 쓰기 없음).

r2(seed_b2_from_r1_vectors)의 안전 기준을 그대로 쓰되 B2·B3를 컬렉션별로 따로 판정한다.

판정 기준 (컬렉션마다, 원본 Point ↔ 최신 릴리스 청크)
  1. Point ID = uuid5(chunk_id), 중복 없음, 같은 Point의 chunk_id 동일
  2. 원본 Point의 임베딩 모델·차원·입력 규칙이 구성(full_index)과 같아야 한다 (다르면 재사용하지 않고 중단)
  3. "원본 payload로 다시 만든 임베딩 입력" == "최신 청크의 임베딩 입력"이면 벡터 재사용 (seed.dump_embedding_input)
     → 재사용 Point는 최신 build_point_payload로 payload를 갱신하되 원본의 실행 정보(index_run_id 등)는 보존한다
  4. 입력이 달라진 Point(본문·섹션명·정책명 변경) = 삭제 후 재임베딩, 원본에 없는 Point = 신규 임베딩
  5. 최신 청크에 없는 원본 Point = remove. 기본 고정값은 0개이며, 다르면 쓰기 단계가 거부한다

계획(plan)은 벡터 값을 담지 않는다. Point별 벡터 SHA-256(restore.digest([(id, vector)]))과 재사용 부분 digest만 담아
갱신 도구(update_r3_collections)가 단계마다 서버 상태를 같은 계획과 대조할 수 있게 한다.

이 모듈은 최신 릴리스 값을 고정하지 않는다. 계산한 값은 proposed_pins로만 출력하고,
검토·승인 후 scripts/restore_profiles_v140.py의 R3_PINS에 사람이 기록한다.

계획 기준(basis)
  - dump_preview  : 이 CLI가 #31 덤프 등 파일로 만드는 계획. 사전 검토용이며 계획 파일로 저장하지 않는다.
                    proposed_pins의 plan_sha256도 비워 둔다(None).
  - server_snapshot: update_r3_collections가 build 격리 서버에 실제 복원한 snapshot을 scan해 만드는 계획(--write-plan).
                    최종 고정값(plan_sha256)과 모든 쓰기 단계는 이 기준만 받는다.
  같은 벡터라도 payload가 다르면(예: #31 before snapshot과 after 덤프의 region 16건) plan_sha256이 달라지므로 혼용하지 않는다.

실행 (프로젝트 루트, 읽기 전용):
    python scripts/plan_r3_vector_reuse.py --b3-dump venv/issue31_20261109/exp_b3_qwen3emb06b_v140_v1.after.json
    python scripts/plan_r3_vector_reuse.py --b3-dump <덤프> --report <새 보고서.json>
B2의 원본은 r2 snapshot이고 JSON 덤프가 없다. B2·B3 최종 계획은 build 서버 dry-run(--write-plan)으로 만든다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import chunking, embedding as em, full_index as fi, handoff_data as hd  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402  (dump_embedding_input·CORE_PAYLOAD_FIELDS 재사용)

PLAN_SCHEMA = "r3-reuse-plan/1"
RUN_FIELDS = ("embedding_model", "embedding_dim", "embedding_input_rule", "index_run_id")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class PlanError(ValueError):
    """계획을 만들 수 없음. 메시지에 벡터 값·비밀값을 넣지 않는다."""


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def point_vector_sha(point_id: str, vector: list[float]) -> str:
    """단일 Point 벡터 digest (r2 DELETE_TARGETS와 같은 정의)."""
    return restore.digest([(point_id, vector)])


# ---------------------------------------------------------------------------
# 1. 원본 Point (덤프 JSON 또는 서버 scan 결과)
# ---------------------------------------------------------------------------
def check_records(records: dict[str, dict[str, Any]], dimensions: int = profiles.EMBEDDING_DIM) -> None:
    """Point ID 형식·벡터 차원·유한값을 확인한다 (값은 출력하지 않는다)."""
    for point_id, record in records.items():
        if not UUID_PATTERN.match(point_id):
            raise PlanError(f"Point ID 형식 오류: {point_id[:40]}")
        vector = record.get("vector")
        if (not isinstance(vector, list) or len(vector) != dimensions
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)):
            raise PlanError(f"벡터가 {dimensions}차원 유한값이 아닙니다: {point_id}")
        if not isinstance(record.get("payload"), dict):
            raise PlanError(f"payload가 없습니다: {point_id}")


def load_dump(path: Path | str, dimensions: int = profiles.EMBEDDING_DIM) -> dict[str, dict[str, Any]]:
    """Qdrant scroll 덤프(JSON 목록: id·payload·vector)를 읽는다. 파일은 읽기만 한다."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise PlanError("덤프 형식이 목록이 아닙니다")
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        point_id = str(row.get("id"))
        if point_id in records:
            raise PlanError(f"중복 Point ID: {point_id}")
        records[point_id] = {"payload": row.get("payload"), "vector": row.get("vector")}
    check_records(records, dimensions)
    return records


def check_source(key: str, records: dict[str, dict[str, Any]], sources: dict[str, Any] | None = None) -> str:
    """원본 Point 수·전체 벡터 digest가 r3 원본 자산 기록(R3_SOURCES)과 같은지 확인한다."""
    source = (sources or profiles.R3_SOURCES)[key]
    digest = restore.vector_digest(records)
    if len(records) != source["points"] or digest != source["vector_digest"]:
        raise PlanError(f"{key} 원본이 기록된 자산({source['label']})과 다릅니다: Point {len(records)}개, "
                        f"digest 일치={digest == source['vector_digest']}")
    return digest


# ---------------------------------------------------------------------------
# 2. 최신 릴리스 (DB 없음)
# ---------------------------------------------------------------------------
def load_release(pins: dict[str, Any] | None = None, keys: tuple[str, ...] = profiles.COLLECTION_KEYS) -> dict[str, Any]:
    """
    데이터셋 버전·릴리스 체크섬·문서 해시를 확인하고 컬렉션별 청크와 그래프 건수를 만든다.
    pins에 확정된 값(manifest·건수)이 있으면 대조하고, 없으면(None) 계산값을 proposed로만 돌려준다.
    """
    pins = profiles.R3_PINS if pins is None else pins
    if hd.dataset_version() != profiles.DATASET_VERSION:
        raise PlanError(f"데이터셋 버전 {hd.dataset_version()} ≠ {profiles.DATASET_VERSION}")
    bad = [row["file"] for row in hd.verify_manifest_checksums() if not row["matched"]]
    if bad:
        raise PlanError(f"릴리스 파일 체크섬 불일치: {bad}")
    manifest = fi.manifest_sha256()
    if pins.get("manifest_lf_sha256") not in (None, manifest):
        raise PlanError("현재 릴리스 manifest가 r3 고정값과 다릅니다")
    documents = hd.load_documents(verify_hash=True)
    metadata = hd.load_payload_metadata()
    content_hashes = {d["document_id"]: d["content_hash"] for d in documents}
    collections: dict[str, Any] = {}
    for key in keys:
        spec = profiles.collection_spec(key)
        config = fi.get_config(spec["config"])
        if config["collection"] != spec["collection"] or fi.config_model(config) != (spec["model"], spec["dimensions"]):
            raise PlanError(f"{key} 구성이 full_index와 다릅니다")
        chunks, _info = fi.build_config_chunks(spec["config"], documents)
        missing = sorted({c["document_id"] for c in chunks} - content_hashes.keys())
        if missing:
            raise PlanError(f"{key} 청크의 문서 content_hash 없음: {missing[:5]}")
        if len(chunks) != config["expected_chunks"]:
            raise PlanError(f"{key} 청크 수 {len(chunks)} ≠ full_index expected_chunks {config['expected_chunks']}")
        pinned = (pins.get("collections") or {}).get(key, {}).get("points")
        if pinned not in (None, len(chunks)):
            raise PlanError(f"{key} 청크 수 {len(chunks)} ≠ r3 고정값 {pinned}")
        collections[key] = {"spec": spec, "chunks": chunks, "chunk_fingerprint": fi.chunk_fingerprint(chunks)}
    graph = lg.read_graph_input()
    validation = lg.validate_graph_input(graph)
    if not validation.ok:
        raise PlanError(f"그래프 입력 검증 실패: {validation.summary()['error_counts']}")
    counts = {"policies": len({d["policy_id"] for d in documents}), "documents": len(documents),
              "release_chunks": len(hd.load_payload_chunks()),
              "neo4j_nodes": sum(validation.node_counts.values()),
              "neo4j_relationships": sum(validation.relationship_counts.values()),
              "requires_condition": validation.relationship_counts.get("REQUIRES_CONDITION", 0)}
    pinned_counts = pins.get("release_counts") or {}
    differing = [k for k, v in pinned_counts.items() if v is not None and counts.get(k) != v]
    if differing:
        raise PlanError(f"릴리스 건수가 r3 고정값과 다릅니다: {differing}")
    return {"manifest_lf_sha256": manifest, "documents": documents, "metadata": metadata,
            "content_hashes": content_hashes, "collections": collections, "graph": graph,
            "validation": validation.summary(), "counts": counts}


# ---------------------------------------------------------------------------
# 3. 분류 (컬렉션 하나)
# ---------------------------------------------------------------------------
def expected_run(spec: dict[str, Any]) -> dict[str, Any]:
    return {"embedding_model": spec["model"], "embedding_dim": spec["dimensions"],
            "embedding_input_rule": fi.EMBEDDING_INPUT_RULE}


def reuse_payload(chunk: dict[str, Any], metadata: dict[str, Any], content_hashes: dict[str, str],
                  run: dict[str, Any]) -> dict[str, Any]:
    """재사용 Point의 최신 payload. 실행 정보는 원본 Point 것을 그대로 쓴다 (벡터 출처 추적)."""
    return store.build_point_payload(chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
                                     content_hash=content_hashes[chunk["document_id"]], run_info=run)


def classify(records: dict[str, dict[str, Any]], chunks: list[dict[str, Any]], metadata: dict[str, Any],
             content_hashes: dict[str, str], spec: dict[str, Any]) -> dict[str, Any]:
    """최신 청크마다 재사용/삭제 후 재임베딩/신규를 판정한다. 입력 dict는 바꾸지 않는다."""
    wanted_run = expected_run(spec)
    reuse, delete, new, seen = {}, {}, {}, set()
    for chunk in chunks:
        point_id = chunk["point_id"]
        if point_id != chunking.make_point_id(chunk["chunk_id"]) or point_id in seen:
            raise PlanError(f"청크 Point ID 규칙 위반·중복: {chunk['chunk_id']}")
        if not chunk["chunk_id"].startswith(spec["chunk_prefix"]):
            raise PlanError(f"{spec['key']} 청크 ID 접두어가 아닙니다: {chunk['chunk_id']}")
        seen.add(point_id)
        record = records.get(point_id)
        if record is None:
            new[point_id] = {"chunk_id": chunk["chunk_id"]}
            continue
        old = record["payload"]
        if old.get("chunk_id") != chunk["chunk_id"]:
            raise PlanError(f"같은 Point ID의 chunk_id가 다릅니다: {point_id}")
        if {k: old.get(k) for k in wanted_run} != wanted_run:
            raise PlanError(f"{spec['key']} 원본 Point의 임베딩 모델·차원·입력 규칙이 구성과 다릅니다: {point_id}")
        meta = store.metadata_for_chunk(metadata, chunk)
        if seed.dump_embedding_input(old) != em.build_embedding_text(chunk, meta, fi.EMBEDDING_INPUT_RULE):
            parts = [name for name, before, after in (
                ("text", old.get("text"), chunk["text"]),
                ("section_title", old.get("section_title"), chunk.get("section_title")),
                ("policy_name", old.get("policy_name"), meta.get("policy_name"))) if before != after]
            delete[point_id] = {"chunk_id": chunk["chunk_id"], "text_sha256": text_sha(old.get("text", "")),
                                "vector_sha": point_vector_sha(point_id, record["vector"]),
                                "changed_parts": parts or ["rule_output"]}
            continue
        run = {k: old.get(k) for k in RUN_FIELDS}
        payload = reuse_payload(chunk, metadata, content_hashes, run)
        changed = sorted(f for f in set(old) | set(payload) if old.get(f) != payload.get(f))
        core = [f for f in changed if f in seed.CORE_PAYLOAD_FIELDS]
        reuse[point_id] = {"chunk_id": chunk["chunk_id"], "run": run,
                           "kind": "core_refresh" if core else ("metadata_refresh" if changed else "identical"),
                           "changed_fields": changed, "vector_sha": point_vector_sha(point_id, record["vector"])}
    remove = {p: {"chunk_id": records[p]["payload"].get("chunk_id"),
                  "text_sha256": text_sha(records[p]["payload"].get("text", "")),
                  "vector_sha": point_vector_sha(p, records[p]["vector"])} for p in sorted(set(records) - seen)}
    return {"reuse": reuse, "delete": delete, "new": new, "remove": remove}


def build_plan(key: str, records: dict[str, dict[str, Any]], release: dict[str, Any],
               sources: dict[str, Any] | None = None, *, basis: str) -> dict[str, Any]:
    """
    원본 확인 → 분류 → 직렬화 가능한 계획 (벡터 값 없음).
    basis: PLAN_BASIS_SERVER(build 서버에 실제 복원한 snapshot scan, 최종 고정값·쓰기용)
           PLAN_BASIS_PREVIEW(#31 덤프 등 파일, 사전 검토용 — 같은 벡터라도 payload가 달라 plan_sha256이 다를 수 있다)
    """
    if basis not in (profiles.PLAN_BASIS_SERVER, profiles.PLAN_BASIS_PREVIEW):
        raise PlanError(f"알 수 없는 계획 기준: {basis}")
    check_records(records)
    source_digest = check_source(key, records, sources)
    entry = release["collections"][key]
    spec = entry["spec"]
    classified = classify(records, entry["chunks"], release["metadata"], release["content_hashes"], spec)
    reuse_subset = {p: {"vector": records[p]["vector"]} for p in classified["reuse"]}
    kinds: dict[str, int] = {}
    fields: dict[str, int] = {}
    for item in classified["reuse"].values():
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
        for field in item["changed_fields"]:
            fields[field] = fields.get(field, 0) + 1
    runs: dict[str, int] = {}
    for item in classified["reuse"].values():
        runs[str(item["run"].get("index_run_id"))] = runs.get(str(item["run"].get("index_run_id")), 0) + 1
    plan = {
        "schema": PLAN_SCHEMA, "basis": basis,
        "collection_key": key, "collection": spec["collection"], "config": spec["config"],
        "manifest_lf_sha256": release["manifest_lf_sha256"], "chunk_fingerprint": entry["chunk_fingerprint"],
        "source": {"label": (sources or profiles.R3_SOURCES)[key]["label"], "points": len(records),
                   "vector_digest": source_digest},
        "target_points": len(entry["chunks"]),
        "counts": {"reuse": len(classified["reuse"]), "delete": len(classified["delete"]),
                   "new": len(classified["new"]), "embed": len(classified["delete"]) + len(classified["new"]),
                   "remove": len(classified["remove"]), "reuse_by_kind": dict(sorted(kinds.items())),
                   "reuse_payload_field_changes": dict(sorted(fields.items())),
                   "reuse_by_index_run_id": dict(sorted(runs.items()))},
        "reuse_vector_digest": restore.vector_digest(reuse_subset),
        **classified,
    }
    if plan["counts"]["reuse"] + plan["counts"]["embed"] != plan["target_points"]:
        raise PlanError("재사용 + 임베딩 수가 목표 Point 수와 다릅니다")
    return plan


def plan_sha256(plan: dict[str, Any]) -> str:
    return restore.digest(plan)


def validate_plan(plan: dict[str, Any], release: dict[str, Any], pins: dict[str, Any] | None = None,
                  sources: dict[str, Any] | None = None, *, require_pins: bool = False,
                  deferred: tuple[str, ...] = ()) -> dict[str, Any]:
    """
    저장된 계획이 지금 릴리스·원본 자산·(확정된) r3 고정값과 맞는지 확인한다.
    deferred: 갱신 단계에서만 profiles.UPDATE_STAGE_DEFERRED_PINS(최종 vector_digest)의 미확정을 허용한다.
    """
    pins = profiles.R3_PINS if pins is None else pins
    if require_pins:
        profiles.require_finalized(pins, deferred=deferred)
    if plan.get("schema") != PLAN_SCHEMA:
        raise PlanError("계획 형식이 다릅니다")
    if plan.get("basis") not in (profiles.PLAN_BASIS_SERVER, profiles.PLAN_BASIS_PREVIEW):
        raise PlanError("계획 기준(basis)이 없거나 알 수 없습니다")
    if require_pins and plan["basis"] != profiles.PLAN_BASIS_SERVER:
        raise PlanError("최종 고정값·쓰기에는 build 서버 snapshot 기준 계획(basis=server_snapshot)만 씁니다 "
                        "(덤프 계획은 사전 검토용)")
    key = plan.get("collection_key")
    if key not in release["collections"]:
        raise PlanError(f"알 수 없는 컬렉션 키: {key}")
    entry = release["collections"][key]
    source = (sources or profiles.R3_SOURCES)[key]
    if (plan["manifest_lf_sha256"] != release["manifest_lf_sha256"]
            or plan["chunk_fingerprint"] != entry["chunk_fingerprint"]
            or plan["collection"] != entry["spec"]["collection"] or plan["config"] != entry["spec"]["config"]):
        raise PlanError(f"{key} 계획이 현재 릴리스(manifest·청크)와 다릅니다 → 계획을 다시 만든다")
    if plan["source"]["points"] != source["points"] or plan["source"]["vector_digest"] != source["vector_digest"]:
        raise PlanError(f"{key} 계획의 원본이 기록된 자산과 다릅니다")
    ids = [set(plan[name]) for name in ("reuse", "delete", "new")]
    if ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2]:
        raise PlanError(f"{key} 계획의 재사용·삭제·신규 대상이 겹칩니다")
    if set().union(*ids) != {c["point_id"] for c in entry["chunks"]}:
        raise PlanError(f"{key} 계획의 Point 집합이 최신 청크와 다릅니다")
    pinned = (pins.get("collections") or {}).get(key, {})
    checks = {"points": plan["target_points"], "reuse_count": plan["counts"]["reuse"],
              "reuse_vector_digest": plan["reuse_vector_digest"], "embed_count": plan["counts"]["embed"],
              "delete_count": plan["counts"]["delete"], "remove_count": plan["counts"]["remove"],
              "plan_sha256": plan_sha256(plan)}
    differing = sorted(k for k, v in checks.items() if pinned.get(k) is not None and pinned[k] != v)
    if differing:
        raise PlanError(f"{key} 계획이 r3 고정값과 다릅니다: {differing}")
    return checks


# ---------------------------------------------------------------------------
# 4. 고정값 제안 (자동 기록하지 않음)
# ---------------------------------------------------------------------------
def proposed_pins(release: dict[str, Any], plans: dict[str, dict[str, Any]],
                  final_digests: dict[str, str] | None = None) -> dict[str, Any]:
    """계산값을 R3_PINS와 같은 모양으로 만든다. 최종 vector_digest는 임베딩 완료 후에만 알 수 있다."""
    final_digests = final_digests or {}
    collections = {}
    for key in profiles.COLLECTION_KEYS:
        plan = plans.get(key)
        entry = release["collections"].get(key)
        server_plan = bool(plan) and plan.get("basis") == profiles.PLAN_BASIS_SERVER
        collections[key] = {
            "points": len(entry["chunks"]) if entry else None,
            "vector_digest": final_digests.get(key),
            "reuse_count": plan["counts"]["reuse"] if plan else None,
            "reuse_vector_digest": plan["reuse_vector_digest"] if plan else None,
            "embed_count": plan["counts"]["embed"] if plan else None,
            "delete_count": plan["counts"]["delete"] if plan else None,
            "remove_count": plan["counts"]["remove"] if plan else None,
            # plan_sha256은 build 서버 계획에서만 제안한다 (덤프 계획 SHA와 혼용 금지)
            "plan_sha256": plan_sha256(plan) if server_plan else None}
    return {"manifest_lf_sha256": release["manifest_lf_sha256"], "release_counts": dict(release["counts"]),
            "collections": collections,
            "plan_basis": {k: v.get("basis") for k, v in plans.items()},
            "note": "제안값이다. 검토·승인 후에만 scripts/restore_profiles_v140.py R3_PINS에 옮겨 적는다. "
                    "plan_sha256은 build 서버 snapshot 기준 계획에서만 나온다"}


def summarize(plan: dict[str, Any]) -> dict[str, Any]:
    """출력용. 본문·벡터 없이 건수와 대상 chunk_id만."""
    return {"collection_key": plan["collection_key"], "basis": plan.get("basis"), "collection": plan["collection"],
            "source": plan["source"],
            "target_points": plan["target_points"], "counts": plan["counts"],
            "reuse_vector_digest": plan["reuse_vector_digest"], "plan_sha256": plan_sha256(plan),
            "delete": sorted((v["chunk_id"], v["changed_parts"]) for v in plan["delete"].values()),
            "new_chunk_ids": sorted(v["chunk_id"] for v in plan["new"].values()),
            "remove_chunk_ids": sorted(str(v["chunk_id"]) for v in plan["remove"].values()),
            "core_refresh": sorted(v["chunk_id"] for v in plan["reuse"].values() if v["kind"] == "core_refresh")}


def write_new_json(path: Path, value: Any) -> None:
    """새 파일만 만든다 (이미 있으면 FileExistsError)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r3 B2·B3 벡터 재사용 계획 (읽기 전용)")
    parser.add_argument("--b2-dump", type=Path, help="B2 원본 덤프 JSON (r2 snapshot과 같은 digest여야 함)")
    parser.add_argument("--b3-dump", type=Path, help="B3 원본 덤프 JSON (#31 snapshot과 같은 digest여야 함)")
    parser.add_argument("--report", type=Path, help="결과를 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    if args.report and profiles.is_preserved_output(args.report):
        parser.error("보존 자산 폴더(#104 r3, .cache/issue105-r3) 안에는 새 파일도 만들지 않습니다")
    try:
        release = load_release()
        plans = {}
        for key, path in (("b2", args.b2_dump), ("b3", args.b3_dump)):
            if path:
                # 덤프 계획은 사전 검토용이다. 계획 파일로 저장하지 않으며 쓰기 도구는 이 기준을 거부한다.
                plans[key] = build_plan(key, load_dump(path), release, basis=profiles.PLAN_BASIS_PREVIEW)
        result = {"status": "DRY_RUN_PASS", "db_writes": 0, "embedding_calls": 0, "plan_basis": "dump_preview",
                  "release": {"manifest_lf_sha256": release["manifest_lf_sha256"], "counts": release["counts"],
                              "points": {k: len(v["chunks"]) for k, v in release["collections"].items()}},
                  "r3_pins_finalized": profiles.is_finalized(),
                  "plans": {k: summarize(v) for k, v in plans.items()},
                  "not_planned": [k for k in profiles.COLLECTION_KEYS if k not in plans],
                  "proposed_pins": proposed_pins(release, plans)}
    except (PlanError, profiles.ProfileError, FileExistsError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:800]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        write_new_json(args.report, result)
    print(text)
    return 0 if result["status"] == "DRY_RUN_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
