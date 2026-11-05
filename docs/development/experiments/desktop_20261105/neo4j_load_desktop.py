"""
⚠⚠ 주의: Neo4j DB 쓰기 스크립트 — 실행하면 DB 내용이 바뀐다 ⚠⚠

사용 조건 (모두 만족할 때만, 사용자 승인 후 실행)
1. 대상 Neo4j가 "빈 DB"이거나, 이미 같은 인계 데이터(노드 1,034 / 관계 3,615)만 들어 있는 DB일 것
   - 1차 적재는 빈 DB에서만 진행된다 (preflight_database → DatabaseNotEmptyError로 멈춤).
   - 2차 적재는 allow_existing=True 경로로, 기존 데이터가 입력의 부분집합일 때만 MERGE한다.
2. 실행하면 제약조건 9개·인덱스 4개를 만들고(IF NOT EXISTS), 노드·관계를 MERGE하며 속성을 SET한다.
   → 다른 데이터가 든 DB, 다른 버전의 인계 데이터에는 실행하지 않는다.
3. 삭제·초기화 동작은 없다. 실패해도 이미 커밋된 배치는 남는다(되돌리지 않음).
4. .env의 NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD 필요. 비밀번호는 출력하지 않는다.
5. 결과 JSON은 Git에 올리지 않을 경로에 저장한다.

보존 정보: 2026-11-05 데스크톱(Neo4j 5.26.32)에서 1회 실행한 스크립트. 결과는 같은 폴더의 neo4j_load_desktop.json.
실행 위치: 프로젝트 루트에서
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261105/neo4j_load_desktop.py <결과 JSON 경로>

---
Issue #5 데스크톱 Neo4j 실제 적재 + 재적재 검증 (프로젝트 함수만 호출).

DB 쓰기 있음 (사용자 승인: 2026-11-05) — 새 로더(database/neo4j/load_graph.py)만 사용. 인계 load_neo4j.py는 실행하지 않는다.
삭제·초기화 동작 없음. 비밀번호는 출력하지 않는다.
"""
import hashlib
import json
import logging
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
logging.getLogger("neo4j").setLevel(logging.ERROR)   # 알림은 끄지 않고 runner.notifications로 모은다
from neo4j import GraphDatabase  # noqa: E402

from database import embedding as em, handoff_data as hd  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402

OUT = sys.argv[1]
report: dict = {"loader": "database/neo4j/load_graph.py", "legacy_loader_executed": False}


def fingerprint(rows):
    """행 목록을 정렬해 sha256 (노드 ID 집합·관계 양끝 집합 비교용)."""
    text = "\n".join(sorted(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows))
    return {"rows": len(rows), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def db_fingerprints(runner):
    return {"nodes": fingerprint(runner.read_rows(lg.build_node_identity_query())),
            "relationships": fingerprint(runner.read_rows(lg.build_relationship_endpoint_query())),
            "totals": runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0]}


def compact(verification):
    """보고용 요약 (전체 목록 대신 문제 개수)."""
    return {"ok": verification["ok"], "totals": verification["totals"], "totals_ok": verification["totals_ok"],
            "count_mismatches": verification["count_mismatches"],
            "schema": {k: verification["schema"][k] for k in ("ok", "problems", "constraints", "indexes",
                                                                "constraint_owned_indexes", "default_lookup_indexes",
                                                                "actual_constraint_types")},
            "nodes": {k: verification["nodes"][k] for k in ("ok", "problems", "expected_by_label", "actual_by_label",
                                                             "expected_total", "actual_total")},
            "relationships": {k: verification["relationships"][k] for k in ("ok", "problems", "expected_by_type",
                                                                             "actual_by_type", "expected_total",
                                                                             "actual_total")}}


# 1) 입력 파일·manifest 검증 (DB 접속 전)
manifest = [item for item in hd.verify_manifest_checksums() if item["file"].startswith("neo4j/")]
graph = lg.read_graph_input()
validation = lg.validate_graph_input(graph)
report["input"] = {"manifest_neo4j_files": len(manifest), "manifest_all_matched": all(i["matched"] for i in manifest),
                   "validation": validation.summary()}
if not (report["input"]["manifest_all_matched"] and validation.ok):
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print("STOP: input invalid")
    sys.exit(1)

em.load_env_file()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
driver.verify_connectivity()
report["server"] = {"agent": driver.get_server_info().agent, "protocol": list(driver.get_server_info().protocol_version)}
runner = lg.Neo4jDriverRunner(driver)

# 2) 1차 적재 (기본: 빈 DB에만)
t0 = time.perf_counter()
first = lg.load_graph(graph, runner)
report["first_load"] = {"seconds": round(time.perf_counter() - t0, 3), "steps": first["steps"],
                        "merged": first["merged"], "preflight_state": first["preflight"]["state"],
                        "preflight_totals": first["preflight"]["totals"],
                        "preflight_schema_ok": first["preflight"]["schema"]["ok"],
                        "after_counts": first["after_counts"], "verification": compact(first["verification"]),
                        "ok": first["ok"]}
report["notifications_first_load"] = lg.classify_notifications(runner.notifications)
runner.notifications.clear()
fp_first = db_fingerprints(runner)
report["fingerprint_after_first"] = fp_first
print("first load ok:", first["ok"])
if not first["ok"]:
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print("STOP: first load verification failed (데이터는 그대로 둠)")
    sys.exit(1)

# 3) 기본 보호 확인: allow_existing 없이 다시 적재 → 거부, 쓰기 없음
writes = []
original_write, original_schema = runner.write_batch, runner.run_schema
runner.write_batch = lambda *a, **k: writes.append("write") or original_write(*a, **k)
runner.run_schema = lambda *a, **k: writes.append("schema") or original_schema(*a, **k)
try:
    lg.load_graph(graph, runner)
    report["guard_default_reload"] = {"refused": False}
except lg.DatabaseNotEmptyError as error:
    report["guard_default_reload"] = {"refused": True, "error": type(error).__name__,
                                      "state": error.report["state"], "writes_attempted": len(writes)}
runner.write_batch, runner.run_schema = original_write, original_schema

# 4) 승인된 재실행 경로: allow_existing=True (기존 데이터가 입력과 같은지 먼저 검증)
t0 = time.perf_counter()
second = lg.load_graph(graph, runner, allow_existing=True)
report["second_load"] = {"seconds": round(time.perf_counter() - t0, 3), "merged": second["merged"],
                         "preflight_state": second["preflight"]["state"],
                         "preflight_nodes_ok": second["preflight"]["nodes"]["ok"],
                         "preflight_relationships_ok": second["preflight"]["relationships"]["ok"],
                         "verification": compact(second["verification"]), "ok": second["ok"]}
report["notifications_second_load"] = lg.classify_notifications(runner.notifications)
fp_second = db_fingerprints(runner)
report["fingerprint_after_second"] = fp_second
report["reload_unchanged"] = {
    "node_id_set_same": fp_first["nodes"] == fp_second["nodes"],
    "relationship_endpoint_set_same": fp_first["relationships"] == fp_second["relationships"],
    "totals_same": fp_first["totals"] == fp_second["totals"],
}
driver.close()
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print("second load ok:", second["ok"], "| unchanged:", report["reload_unchanged"])
print("done")
