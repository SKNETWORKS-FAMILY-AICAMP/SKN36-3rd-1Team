"""Issue #91: r1 B2 벡터 재사용 계획 — 읽기 전용 dry-run.

기존 B2 컬렉션 덤프(JSON, 1,138 Point)의 벡터를 최신 v1.4.0-candidate B2 청크(1,139개)에
다시 쓸 수 있는지 판정한다. DB에 접속하지 않고, 임베딩을 호출하지 않고, 어떤 파일도 쓰지 않는다
(--report를 준 경우에만 새 보고서 파일 하나를 만든다. 이미 있으면 덮어쓰지 않는다).

판정 순서
  1. 덤프 SHA-256 → 2. Point ID 형식·중복·건수 → 3. 벡터 1,024차원·유한값
  4. vector_digest를 r1 검증 기록(PR45_PACKAGE_VALIDATION_20261110.json)과 대조
  5. 덤프 payload의 임베딩 모델·차원·입력 규칙·실행 ID가 고정값과 같은지, 현재 A_B2_V140 구성과 같은지
  6. 최신 릴리스(데이터셋 버전·manifest 고정)에서 B2 청크 생성
  7. Point마다 "덤프 payload로 다시 만든 임베딩 입력" == "최신 청크의 임베딩 입력"이면 벡터 재사용
  8~12. 재사용 Point에 최신 build_point_payload를 적용하고 분류·건수·대상 ID를 고정값과 대조한다.

덤프는 r1 snapshot 자체가 아니다. 같은 것은 vector_digest(벡터 전체)뿐이며 payload·파일은 r1과 다를 수 있다.

DB 쓰기(시드)는 이 스크립트에 없다. Cosine 컬렉션에 벡터를 다시 upsert하면 Qdrant가 정규화를 다시 해
float32 값이 바뀔 수 있음을 확인했기 때문이다(인메모리 실측). 쓰기 방식은 팀 결정 후 별도로 구현한다.

실행 (프로젝트 루트):
    python scripts/seed_b2_from_r1_vectors.py
    python scripts/seed_b2_from_r1_vectors.py --report <새 파일 경로.json>
"""
from __future__ import annotations

import argparse
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
from database.qdrant import store  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402  (file_sha·vector_digest를 같은 정의로 재사용)

CONFIG = "A_B2_V140"
DATASET_VERSION = "1.4.0-candidate"
# PR #89 병합본(e764518)의 manifest.json LF 기준 SHA-256. 다른 릴리스에는 이 계획을 적용하지 않는다.
MANIFEST_LF_SHA256 = "d48ef11b599466b394bef15e275be26b24be9dfde1e74771c86b8e3d384acf50"
EXPECTED_CHUNKS = 1139

# 덤프: Issue #31 작업(2026-11-09)의 exp_b2_qwen3emb06b_v140_v1 전체 scroll 결과. Git에 넣지 않는다(venv/는 ignore).
DEFAULT_DUMP = ROOT / "venv" / "issue31_20261109" / "exp_b2_qwen3emb06b_v140_v1.after.json"
DUMP_SHA256 = "1b22b75f0f8099a69ca3903c7393cb7d18dc705e56838a7069a67a2c05830c1d"
DUMP_POINTS = 1138
DUMP_DIMENSIONS = 1024
# r1 검증 기록의 vector_digest (apply·verify_only 두 곳 모두 같은 값이어야 한다)
R1_RECORD = ROOT / "docs" / "development" / "PR45_PACKAGE_VALIDATION_20261110.json"
R1_VECTOR_DIGEST = "f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df"
# 덤프 1,138 Point가 모두 가진 임베딩 실행 정보 (2026-11-07 A_B2_V140 전체 색인)
DUMP_RUN = {"embedding_model": "qwen3-embedding:0.6b", "embedding_dim": 1024,
            "embedding_input_rule": "policy_section_body_v2",
            "index_run_id": "RUN_FULL_A_B2_V140_20261107_173008"}

# 2026-11-19 오프라인 분석에서 확정한 기대 분류. 하나라도 다르면 멈춘다.
EXPECTED_REUSE = 1135
EXPECTED_EMBED_CHUNK_IDS = frozenset({
    "CHK_B2_DOC_BOK_031_MOE_2026_C001",   # 신규 문서
    "CHK_B2_DOC_BOK_054_C002", "CHK_B2_DOC_BOK_054_C003", "CHK_B2_DOC_BOK_054_C004",  # 본문 변경
})
EXPECTED_CORE_REFRESH_CHUNK_IDS = frozenset({"CHK_B2_DOC_BOK_054_C001", "CHK_B2_DOC_BOK_054_C005"})
PINNED_EXPECTATIONS = {"chunks": EXPECTED_CHUNKS, "reuse": EXPECTED_REUSE, "embed": EXPECTED_EMBED_CHUNK_IDS,
                       "core_refresh": EXPECTED_CORE_REFRESH_CHUNK_IDS}

# 같은 Point인지 판단하는 핵심 payload: full_index가 conflict 판정에 쓰는 필드 + 본문
CORE_PAYLOAD_FIELDS = tuple(fi.PAYLOAD_IDENTITY_FIELDS) + ("text",)
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SeedPlanError(ValueError):
    """재사용 계획을 만들 수 없음. 메시지에 벡터 값·비밀값을 넣지 않는다."""


# ---------------------------------------------------------------------------
# 1~4. 덤프 검증
# ---------------------------------------------------------------------------
def load_dump(path: Path | str, expected_sha256: str = DUMP_SHA256, expected_points: int = DUMP_POINTS,
              dimensions: int = DUMP_DIMENSIONS) -> dict[str, dict[str, Any]]:
    """덤프를 SHA-256 확인 후 읽어 {point_id: {"payload", "vector"}}로 돌려준다. 파일은 읽기만 한다."""
    path = Path(path)
    if not path.is_file():
        raise SeedPlanError(f"덤프 파일이 없습니다: {path}")
    actual = restore.file_sha(path)
    if actual != expected_sha256:
        raise SeedPlanError(f"덤프 SHA-256 불일치: {actual}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SeedPlanError("덤프 최상위가 Point 목록이 아닙니다")
    records: dict[str, dict[str, Any]] = {}
    duplicated = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            raise SeedPlanError("덤프 Point 형식 오류 (id·payload·vector 필요)")
        point_id = str(row.get("id"))
        if not UUID_PATTERN.match(point_id):
            raise SeedPlanError(f"Point ID 형식 오류: {point_id[:40]}")
        if point_id in records:
            duplicated.append(point_id)
            continue
        if row["payload"].get("point_id") != point_id:
            raise SeedPlanError(f"payload.point_id와 Point ID가 다릅니다: {point_id}")
        vector = row.get("vector")
        if (not isinstance(vector, list) or len(vector) != dimensions
                or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)):
            raise SeedPlanError(f"벡터 차원·유한값 오류: {point_id}")
        records[point_id] = {"payload": row["payload"], "vector": vector}
    if duplicated:
        raise SeedPlanError(f"중복 Point ID {len(duplicated)}개: {sorted(duplicated)[:3]}")
    if len(records) != expected_points:
        raise SeedPlanError(f"덤프 Point 수 {len(records)} ≠ {expected_points}")
    return records


def check_r1_record(path: Path | str = R1_RECORD, expected_digest: str = R1_VECTOR_DIGEST,
                    expected_points: int = DUMP_POINTS) -> None:
    """고정한 vector_digest가 저장소의 r1 검증 기록과 같은지 확인한다 (상수만 믿지 않는다)."""
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    for mode in ("apply", "verify_only"):
        section = record.get(mode) or {}
        if section.get("vector_digest") != expected_digest or section.get("status") != "PASS":
            raise SeedPlanError(f"r1 검증 기록({mode})의 vector_digest·status가 고정값과 다릅니다")
        if (section.get("cross_db") or {}).get("qdrant_count") != expected_points:
            raise SeedPlanError(f"r1 검증 기록({mode})의 Point 수가 {expected_points}가 아닙니다")


def check_vector_digest(records: dict[str, dict[str, Any]], expected: str = R1_VECTOR_DIGEST) -> str:
    actual = restore.vector_digest(records)
    if actual != expected:
        raise SeedPlanError(f"vector_digest 불일치: {actual}")
    return actual


def check_dump_run(records: dict[str, dict[str, Any]], run: dict[str, Any] = DUMP_RUN) -> None:
    """덤프 Point의 임베딩 실행 정보가 모두 고정값이고, 고정값이 현재 A_B2_V140 구성과 같은지 확인한다."""
    model, dim = fi.config_model(fi.get_config(CONFIG))
    if (run["embedding_model"], run["embedding_dim"], run["embedding_input_rule"]) != (
            model, dim, fi.EMBEDDING_INPUT_RULE):
        raise SeedPlanError("덤프 임베딩 모델·차원·입력 규칙이 현재 A_B2_V140 구성과 다릅니다")
    differing = sorted(point_id for point_id, record in records.items()
                       if any(record["payload"].get(field) != value for field, value in run.items()))
    if differing:
        raise SeedPlanError(f"임베딩 실행 정보가 다른 Point {len(differing)}개: {differing[:3]}")


# ---------------------------------------------------------------------------
# 6. 최신 릴리스
# ---------------------------------------------------------------------------
def load_release(expected_version: str = DATASET_VERSION, expected_manifest: str = MANIFEST_LF_SHA256,
                 expected_chunks: int = EXPECTED_CHUNKS) -> dict[str, Any]:
    """고정한 데이터셋·manifest인지 확인하고 B2 청크·메타데이터·문서 해시를 만든다 (DB 없음)."""
    if hd.dataset_version() != expected_version:
        raise SeedPlanError(f"데이터셋 버전 {hd.dataset_version()} ≠ {expected_version}")
    manifest = fi.manifest_sha256()
    if manifest != expected_manifest:
        raise SeedPlanError(f"manifest SHA-256 {manifest} ≠ 고정값")
    bad = [row["file"] for row in hd.verify_manifest_checksums() if not row["matched"]]
    if bad:
        raise SeedPlanError(f"릴리스 파일 체크섬 불일치: {bad}")
    documents = hd.load_documents(verify_hash=True)
    chunks, _info = fi.build_config_chunks(CONFIG, documents)
    if len(chunks) != expected_chunks or fi.get_config(CONFIG)["expected_chunks"] != expected_chunks:
        raise SeedPlanError(f"B2 청크 수 {len(chunks)} ≠ {expected_chunks}")
    return {"chunks": chunks, "documents": documents, "metadata": hd.load_payload_metadata(),
            "content_hashes": {d["document_id"]: d["content_hash"] for d in documents},
            "manifest_lf_sha256": manifest, "chunk_fingerprint": fi.chunk_fingerprint(chunks)}


# ---------------------------------------------------------------------------
# 7~11. 분류
# ---------------------------------------------------------------------------
def dump_embedding_input(payload: dict[str, Any]) -> str:
    """덤프 payload(본문·섹션명·정책명·규칙)로 당시 임베딩 입력을 다시 만든다. 현재와 같은 함수·규칙을 쓴다."""
    chunk = {"chunk_id": payload.get("chunk_id"), "text": payload["text"], "section_title": payload.get("section_title")}
    return em.build_embedding_text(chunk, {"policy_name": payload.get("policy_name")}, payload["embedding_input_rule"])


def classify(records: dict[str, dict[str, Any]], chunks: list[dict[str, Any]], metadata: dict[str, Any],
             content_hashes: dict[str, str], run: dict[str, Any] = DUMP_RUN) -> dict[str, Any]:
    """
    최신 청크마다 재사용/새 임베딩을 판정한다. 재사용 Point는 최신 build_point_payload 결과와 덤프 벡터를 짝짓는다.
    run_info는 덤프 실행 정보 그대로(index_run_id 보존 → 벡터 출처 추적). 입력 dict는 바꾸지 않는다.
    """
    reuse, embed, seen = [], [], set()
    for chunk in chunks:
        point_id = chunk["point_id"]
        if point_id != chunking.make_point_id(chunk["chunk_id"]) or point_id in seen:
            raise SeedPlanError(f"청크 Point ID 규칙 위반·중복: {chunk['chunk_id']}")
        seen.add(point_id)
        meta = store.metadata_for_chunk(metadata, chunk)
        if chunk["document_id"] not in content_hashes:
            raise SeedPlanError(f"문서 content_hash 없음: {chunk['document_id']}")
        record = records.get(point_id)
        if record is None:
            embed.append({"point_id": point_id, "chunk_id": chunk["chunk_id"], "reason": "new_point"})
            continue
        old = record["payload"]
        if old.get("chunk_id") != chunk["chunk_id"]:
            raise SeedPlanError(f"같은 Point ID의 chunk_id가 다릅니다: {point_id}")
        new_input = em.build_embedding_text(chunk, meta, fi.EMBEDDING_INPUT_RULE)
        if dump_embedding_input(old) != new_input:
            parts = [name for name, before, after in (
                ("text", old.get("text"), chunk["text"]),
                ("section_title", old.get("section_title"), chunk.get("section_title")),
                ("policy_name", old.get("policy_name"), meta.get("policy_name"))) if before != after]
            embed.append({"point_id": point_id, "chunk_id": chunk["chunk_id"],
                          "reason": "embedding_input_changed", "changed_parts": parts or ["rule_output"]})
            continue
        payload = store.build_point_payload(chunk, policy_meta=meta, content_hash=content_hashes[chunk["document_id"]],
                                            run_info=run)
        changed = sorted(field for field in set(old) | set(payload) if old.get(field) != payload.get(field))
        core = [field for field in changed if field in CORE_PAYLOAD_FIELDS]
        kind = "core_refresh" if core else ("metadata_refresh" if changed else "identical")
        reuse.append({"point_id": point_id, "chunk_id": chunk["chunk_id"], "kind": kind,
                      "changed_fields": changed, "payload": payload, "vector": record["vector"]})
    remove = sorted(set(records) - seen)
    return {"reuse": reuse, "embed": embed, "remove": [
        {"point_id": point_id, "chunk_id": records[point_id]["payload"].get("chunk_id")} for point_id in remove]}


def validate_plan(plan: dict[str, Any], records: dict[str, dict[str, Any]],
                  expected: dict[str, Any] = PINNED_EXPECTATIONS) -> None:
    """분류가 고정 기대값과 정확히 같은지, 재사용 벡터가 덤프와 같은 값인지 확인한다."""
    reuse, embed = plan["reuse"], plan["embed"]
    if plan["remove"]:
        raise SeedPlanError(f"최신 청크에 없는 덤프 Point {len(plan['remove'])}개 (제거 필요)")
    if len(reuse) + len(embed) != expected["chunks"] or len(reuse) != expected["reuse"]:
        raise SeedPlanError(f"분류 건수 불일치: 재사용 {len(reuse)}, 새 임베딩 {len(embed)}")
    if {item["chunk_id"] for item in embed} != set(expected["embed"]):
        raise SeedPlanError(f"새 임베딩 대상이 기대와 다릅니다: {sorted(item['chunk_id'] for item in embed)}")
    core = {item["chunk_id"] for item in reuse if item["kind"] == "core_refresh"}
    if core != set(expected["core_refresh"]):
        raise SeedPlanError(f"핵심 payload 갱신 대상이 기대와 다릅니다: {sorted(core)}")
    for item in reuse:
        missing = [field for field in store.REQUIRED_PAYLOAD_FIELDS if field not in item["payload"]]
        if missing:
            raise SeedPlanError(f"payload 필수 필드 누락 {item['chunk_id']}: {missing}")
        if item["vector"] != records[item["point_id"]]["vector"]:
            raise SeedPlanError(f"재사용 벡터가 덤프와 다릅니다: {item['point_id']}")
    subset = {item["point_id"]: {"vector": item["vector"]} for item in reuse}
    expected_subset = {item["point_id"]: {"vector": records[item["point_id"]]["vector"]} for item in reuse}
    if restore.vector_digest(subset) != restore.vector_digest(expected_subset):
        raise SeedPlanError("재사용 벡터 부분 digest가 덤프와 다릅니다")


def summarize(plan: dict[str, Any], release: dict[str, Any], dump_path: Path, vector_digest: str) -> dict[str, Any]:
    """출력용 요약. 벡터 값과 본문은 넣지 않는다."""
    kinds: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    for item in plan["reuse"]:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
        for field in item["changed_fields"]:
            field_counts[field] = field_counts.get(field, 0) + 1
    reuse_subset = {item["point_id"]: {"vector": item["vector"]} for item in plan["reuse"]}
    return {
        "status": "DRY_RUN_PASS", "db_writes": 0, "embedding_calls": 0,
        "dump": {"path": str(dump_path), "sha256": DUMP_SHA256, "points": DUMP_POINTS,
                 "vector_digest": vector_digest, "matches_r1_record": vector_digest == R1_VECTOR_DIGEST,
                 "note": "r1 snapshot 자체가 아니라 같은 vector_digest를 가진 컬렉션 덤프다"},
        "release": {"dataset_version": DATASET_VERSION, "manifest_lf_sha256": release["manifest_lf_sha256"],
                    "chunks": len(release["chunks"]), "chunk_fingerprint": release["chunk_fingerprint"]},
        "embedding_run": dict(DUMP_RUN),
        "counts": {"reuse": len(plan["reuse"]), "embed": len(plan["embed"]), "remove": len(plan["remove"]),
                   "reuse_by_kind": dict(sorted(kinds.items())),
                   "reuse_payload_field_changes": dict(sorted(field_counts.items()))},
        "reuse_vector_subset_digest": restore.vector_digest(reuse_subset),
        "core_refresh": [{"point_id": i["point_id"], "chunk_id": i["chunk_id"], "fields": i["changed_fields"]}
                         for i in plan["reuse"] if i["kind"] == "core_refresh"],
        "embed": plan["embed"], "remove": plan["remove"],
    }


def build_plan(dump_path: Path | str = DEFAULT_DUMP) -> dict[str, Any]:
    dump_path = Path(dump_path)
    check_r1_record()
    records = load_dump(dump_path)
    digest = check_vector_digest(records)
    check_dump_run(records)
    release = load_release()
    plan = classify(records, release["chunks"], release["metadata"], release["content_hashes"])
    validate_plan(plan, records)
    return summarize(plan, release, dump_path, digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r1 B2 벡터 재사용 계획 (읽기 전용 dry-run, DB 접속 없음)")
    parser.add_argument("--dump", type=Path, default=DEFAULT_DUMP, help="기존 B2 컬렉션 덤프 JSON (읽기만 함)")
    parser.add_argument("--report", type=Path, help="요약을 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    try:
        result = build_plan(args.dump)
    except SeedPlanError as error:
        result = {"status": "FAILED", "reason": str(error), "db_writes": 0, "embedding_calls": 0}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] == "DRY_RUN_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
