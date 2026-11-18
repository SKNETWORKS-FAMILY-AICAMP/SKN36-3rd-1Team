"""Issue #91: 최신 v1.4.0-candidate 그래프를 #91 전용 Neo4j에 적재·검증 — 기본은 읽기 전용 dry-run.

대상: docs/development/issue91_r2_neo4j.compose.yaml (Neo4j Community 5.26.32, bolt://127.0.0.1:56487)
적재·검증은 기존 database.neo4j.load_graph를 그대로 쓴다 (로더를 복제하지 않는다).
  - 입력 검증 validate_graph_input, 빈 DB 확인 preflight_database, 배치 적재 load_graph(verify=True),
    적재 후 종합 검증 verify_loaded_graph (건수·스키마·노드 ID·관계 양끝·속성값)
교차 검증은 기존 hybrid_search.verify_cross_db와 full_index.verify_full_index를 읽기 전용으로 쓴다.

실행 대상 보호
  - Neo4j 주소는 --neo4j-url 인자만 쓴다(bolt://127.0.0.1:56487 하나만 허용). NEO4J_URI·.env는 읽지 않는다.
  - 비밀번호는 이 프로세스의 환경 변수 ISSUE91_NEO4J_PASSWORD에서만 읽는다(공유 DB의 NEO4J_PASSWORD와 분리, 출력 안 함).
  - 서버 버전 5.26.32 community, 틀린 비밀번호가 거부되는지(인증 활성) 확인한다.
  - dry-run이 출력한 fingerprint(서버 버전·전체 건수·레이블/관계별 건수·적재 상태)를 --expect-fingerprint로
    넘겨야 쓴다. 쓰기 직전 같은 드라이버로 다시 계산해 다르면 쓰지 않는다. 적재는 검증한 같은 실행기로 한다.
  - 기본 적재는 빈 DB에만 한다(load_graph 기본값). 중간 실패 뒤 이어서 완성은 --resume(부분 적재 상태에서만)으로만 한다.
  - 기존 데이터를 지우거나 초기화하는 기능은 없다.
  - 교차 검증(--cross-db)은 #91 Qdrant(56446)만 읽는다. Qdrant 쓰기는 없다.

실행 (프로젝트 루트, .venv):
    python scripts/load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487                         # dry-run
    python scripts/load_neo4j_r2_isolated.py --neo4j-url ... --load --confirm-write --expect-fingerprint <값>
    python scripts/load_neo4j_r2_isolated.py --neo4j-url ... --verify                                    # 읽기 전용
    python scripts/load_neo4j_r2_isolated.py --neo4j-url ... --cross-db --qdrant-url http://127.0.0.1:56446
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import full_index as fi, handoff_data as hd  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402
from scripts import update_b2_from_snapshot as qdrant_r2  # noqa: E402

ISOLATED_BOLT_PORT = 56487
# 이 PC에서 쓰였거나 기본인 Neo4j Bolt 포트 (공유·기존 DB). 거부 목록이며, 허용은 56487 하나뿐이다.
SHARED_BOLT_PORTS = frozenset({7687, 17687, 18687, 47687, 57687, 57690})
NEO4J_USER = "neo4j"
NEO4J_DATABASE = "neo4j"
PASSWORD_ENV = "ISSUE91_NEO4J_PASSWORD"
EXPECTED_NEO4J_VERSION = "5.26.32"
EXPECTED_NEO4J_EDITION = "community"
# restore_final_v140와 같은 기준: (정책, 문서, 릴리스 청크, B2 청크, 노드, 관계, REQUIRES_CONDITION)
EXPECTED_COUNTS = restore.EXPECTED
LOAD_BATCH_SIZE = 128                 # restore_final_v140 적재와 같은 값
# #91 Qdrant 최종(1,139 Point) 전체 벡터 digest — 10단계 기록(10-dry-complete.json)
QDRANT_R2_POINTS = 1139
QDRANT_R2_VECTOR_DIGEST = "8577c3c8698fcb891fb35544eac01ec02066fb5c2ef0313d15b6a26e576da3d3"
QUERY_COMPONENTS = "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition"


class Neo4jR2Error(ValueError):
    """실행할 수 없음. 메시지에 비밀번호·쿼리 값은 넣지 않는다."""


# ---------------------------------------------------------------------------
# 1. 대상 보호
# ---------------------------------------------------------------------------
def require_isolated_bolt_url(url: str) -> str:
    """bolt:// + loopback + 자격 증명 없음 + 포트 56487만 허용한다 (neo4j:// 라우팅·다른 포트 거부)."""
    parts = urlsplit(url)
    if parts.scheme != "bolt":
        raise Neo4jR2Error("bolt:// 주소만 허용합니다")
    restore.require_local_url(url)
    if parts.port is None or parts.port in SHARED_BOLT_PORTS or parts.port != ISOLATED_BOLT_PORT:
        raise Neo4jR2Error(f"#91 전용 Bolt 포트 {ISOLATED_BOLT_PORT}만 허용합니다 (7687 등 기존 포트 거부)")
    if parts.path not in ("", "/") or parts.query:
        raise Neo4jR2Error("주소에 경로·쿼리를 넣지 않습니다")
    return url


def read_password(environ=None) -> str:
    """비밀번호는 이 프로세스의 ISSUE91_NEO4J_PASSWORD에서만 읽는다 (.env·NEO4J_PASSWORD 미사용)."""
    value = (environ if environ is not None else os.environ).get(PASSWORD_ENV, "")
    if not value.strip():
        raise Neo4jR2Error(f"{PASSWORD_ENV}를 이 프로세스의 환경 변수로 설정하세요 (.env는 읽지 않음)")
    return value


def make_isolated_driver(url: str, environ=None):
    """--neo4j-url 그대로 접속하는 드라이버 (연결 확인 포함)."""
    require_isolated_bolt_url(url)
    password = read_password(environ)
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(url, auth=(NEO4J_USER, password), connection_timeout=5)
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        raise
    return driver


def check_auth_enforced(url: str) -> bool:
    """무작위 틀린 비밀번호가 AuthError로 거부되는지 확인한다 (인증이 꺼져 있으면 중단)."""
    require_isolated_bolt_url(url)
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError
    driver = GraphDatabase.driver(url, auth=(NEO4J_USER, secrets.token_hex(16)), connection_timeout=5)
    try:
        driver.verify_connectivity()
    except AuthError:
        return True
    finally:
        driver.close()
    raise Neo4jR2Error("틀린 비밀번호로 접속됨 (인증이 꺼져 있음) → 중단")


def server_info(runner) -> dict[str, Any]:
    """Neo4j 버전·에디션이 기대값인지 확인한다 (읽기 전용)."""
    rows = [row for row in runner.read_rows(QUERY_COMPONENTS) if row.get("name") == "Neo4j Kernel"]
    if len(rows) != 1:
        raise Neo4jR2Error("dbms.components에서 Neo4j Kernel을 찾지 못했습니다")
    version, edition = (rows[0].get("versions") or [None])[0], str(rows[0].get("edition", "")).lower()
    if version != EXPECTED_NEO4J_VERSION or edition != EXPECTED_NEO4J_EDITION:
        raise Neo4jR2Error(f"Neo4j {version} {edition} ≠ {EXPECTED_NEO4J_VERSION} {EXPECTED_NEO4J_EDITION}")
    return {"version": version, "edition": edition}


# ---------------------------------------------------------------------------
# 2. 기준값 (최신 릴리스, DB 없음)
# ---------------------------------------------------------------------------
def load_reference(expected: tuple = EXPECTED_COUNTS) -> dict[str, Any]:
    """
    seed.load_release(데이터셋 버전·manifest 고정·릴리스 체크섬·문서 해시·B2 청크) + 그래프 입력 검증
    (참조 무결성·중복 ID·중복 관계·속성) + 문서 해시·청크 연결 확인 + 건수 고정값 대조.
    """
    release = seed.load_release()
    graph = lg.read_graph_input()
    validation = lg.validate_graph_input(graph)
    if not validation.ok:
        raise Neo4jR2Error(f"그래프 입력 검증 실패: {validation.summary()['error_counts']}")
    documents = release["documents"]
    payload_chunks = hd.load_payload_chunks()
    links = {"payload_chunks": lg.check_vector_payload_links(graph, payload_chunks, documents),
             "b2_chunks": lg.check_vector_payload_links(graph, release["chunks"], documents)}
    broken = {f"{name}:{key}": len(value) for name, result in links.items()
              for key, value in result.items() if key != "payload_count" and value}
    if broken:
        raise Neo4jR2Error(f"문서 해시·청크 연결 불일치: {broken}")
    actual = (len({d["policy_id"] for d in documents}), len(documents), len(payload_chunks), len(release["chunks"]),
              sum(validation.node_counts.values()), sum(validation.relationship_counts.values()),
              validation.relationship_counts.get("REQUIRES_CONDITION", 0))
    if actual != tuple(expected):
        raise Neo4jR2Error(f"최신 릴리스 건수 {actual} ≠ 고정값 {tuple(expected)}")
    return {"graph": graph, "counts": actual, "validation": validation.summary(),
            "expected_counts": lg.expected_counts(graph), "manifest_lf_sha256": release["manifest_lf_sha256"],
            "chunks": release["chunks"], "documents": documents}


# ---------------------------------------------------------------------------
# 3. 상태 판정 (읽기 전용)
# ---------------------------------------------------------------------------
def inspect_state(runner, ref: dict[str, Any]) -> dict[str, Any]:
    """
    preflight_database로 판정한다 (읽기 전용): empty / partial / complete / not_empty(foreign_data) / schema_conflict.
    비어 있지 않으면 allow_existing=True로 한 번 더 읽어 "입력의 부분집합인지"를 가린다 (쓰기 없음).
    """
    try:
        preflight = lg.preflight_database(ref["graph"], runner)
    except lg.DatabaseNotEmptyError:
        try:
            preflight = lg.preflight_database(ref["graph"], runner, allow_existing=True)
        except lg.PreflightError as error:
            preflight = error.args[1] if len(error.args) > 1 else {"state": "foreign_data"}
    except lg.PreflightError as error:
        preflight = error.args[1] if len(error.args) > 1 else {"state": "schema_conflict"}
    totals = dict(preflight.get("totals") or runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
    return {"state": preflight.get("state"), "totals": totals, "counts": lg.snapshot_counts(runner)}


def server_fingerprint(info: dict[str, Any], state: dict[str, Any]) -> str:
    return restore.digest({"neo4j": info, "state": state["state"], "totals": state["totals"],
                           "counts": state["counts"]})


def require_fingerprint(runner, ref: dict[str, Any], expected: str | None) -> tuple[dict, dict]:
    """같은 실행기로 서버·상태를 다시 읽어 dry-run fingerprint와 같을 때만 통과한다."""
    if not expected:
        raise Neo4jR2Error("dry-run이 출력한 fingerprint를 expected_fingerprint로 넘겨야 씁니다")
    info = server_info(runner)
    state = inspect_state(runner, ref)
    if server_fingerprint(info, state) != expected:
        raise Neo4jR2Error("서버 fingerprint가 dry-run과 다릅니다 (대상 서버·데이터가 바뀜) → 쓰지 않음")
    return info, state


def dry_run(runner, ref: dict[str, Any]) -> dict[str, Any]:
    info = server_info(runner)
    state = inspect_state(runner, ref)
    return {"mode": "dry-run", "db_writes": 0, "neo4j": info, "state": state["state"], "totals": state["totals"],
            "expected_totals": {"nodes": ref["counts"][4], "relationships": ref["counts"][5]},
            "expected_requires_condition": ref["counts"][6], "release_counts": ref["counts"],
            "manifest_lf_sha256": ref["manifest_lf_sha256"], "fingerprint": server_fingerprint(info, state)}


# ---------------------------------------------------------------------------
# 4. 쓰기 (--load / --resume, --confirm-write + --expect-fingerprint)
# ---------------------------------------------------------------------------
def load(runner, ref: dict[str, Any], confirm_write: bool = False, expected_fingerprint: str | None = None,
         resume: bool = False) -> dict[str, Any]:
    """
    검증한 같은 실행기로 기존 load_graph(verify=True)를 실행한다.
    - load: 빈 DB(state=empty)에서만. load_graph 기본값(allow_existing=False)이 쓰기 직전에 한 번 더 막는다.
    - resume: 부분 적재(state=partial)에서만. load_graph(allow_existing=True)가 기존 데이터가 입력의 부분집합인지 다시 확인한다.
    """
    if not confirm_write:
        raise Neo4jR2Error("confirm_write=True 없이 쓰지 않습니다")
    _info, state = require_fingerprint(runner, ref, expected_fingerprint)
    wanted = "partial" if resume else "empty"
    if state["state"] != wanted:
        raise Neo4jR2Error(f"쓰기 전 상태가 {state['state']}입니다 ({wanted}에서만 {'resume' if resume else 'load'})")
    result = lg.load_graph(ref["graph"], runner, batch_size=LOAD_BATCH_SIZE, allow_existing=resume, verify=True)
    verification = result.get("verification") or {}
    totals = verification.get("totals") or {}
    ok = (result.get("ok") is True and verification.get("ok") is True
          and (totals.get("nodes"), totals.get("relationships")) == (ref["counts"][4], ref["counts"][5])
          and (verification.get("after_counts") or {}).get("rel:REQUIRES_CONDITION") == ref["counts"][6])
    summary = {"status": "LOADED" if ok else "VERIFY_FAILED", "steps": result.get("steps"), "totals": totals,
               "count_mismatches": result.get("count_mismatches"), "verification_ok": verification.get("ok"),
               "notifications": lg.classify_notifications(getattr(runner, "notifications", []))}
    if not ok:
        raise Neo4jR2Error(f"적재 후 검증 실패 (데이터는 보존): {json.dumps(summary, ensure_ascii=False)[:500]}")
    return summary


def verify(runner, ref: dict[str, Any]) -> dict[str, Any]:
    """읽기 전용 종합 검증 (verify_loaded_graph)."""
    server_info(runner)
    result = lg.verify_loaded_graph(ref["graph"], runner)
    return {"mode": "verify", "db_writes": 0, "ok": result["ok"], "totals": result["totals"],
            "count_mismatches": result["count_mismatches"],
            "checks": {k: result[k]["ok"] for k in ("schema", "nodes", "relationships", "node_properties",
                                                    "relationship_properties")}}


# ---------------------------------------------------------------------------
# 5. 교차 검증 (#91 Qdrant 56446 + #91 Neo4j, 둘 다 읽기 전용)
# ---------------------------------------------------------------------------
def cross_check(qdrant_client, runner, ref: dict[str, Any], vector_digest: str = QDRANT_R2_VECTOR_DIGEST,
                points: int = QDRANT_R2_POINTS) -> dict[str, Any]:
    """
    Qdrant: 격리 서버(1.12.4·대상 컬렉션 하나)·Point 수·전체 벡터 digest를 확인하고, 검증 전후 벡터·payload가 같은지 본다.
    Neo4j: 버전 확인 후 verify_loaded_graph가 통과한 상태여야 한다.
    연결: hybrid_search.verify_cross_db(정책·문서·SUPPORTED_BY·content_hash) + full_index.verify_full_index(원문 슬라이스).
    """
    from database import hybrid_search as hs
    qdrant_r2.require_isolated_server(qdrant_client)
    before = restore.scan(qdrant_client)
    before_vectors, before_payloads = restore.vector_digest(before), restore.digest(
        [(p, before[p]["payload"]) for p in sorted(before)])
    if len(before) != points or before_vectors != vector_digest:
        raise Neo4jR2Error(f"Qdrant Point {len(before)}개·벡터 digest가 r2 기록과 다릅니다")
    server_info(runner)
    graph = lg.verify_loaded_graph(ref["graph"], runner)
    if not graph["ok"]:
        raise Neo4jR2Error("Neo4j가 최신 그래프와 일치하지 않아 교차 검증을 하지 않습니다")
    links = hs.verify_cross_db(client=qdrant_client, runner=runner, collection=qdrant_r2.COLLECTION)
    slices = fi.verify_full_index(seed.CONFIG, client=qdrant_client, chunks=ref["chunks"], documents=ref["documents"])
    after = restore.scan(qdrant_client)
    unchanged = (restore.vector_digest(after) == before_vectors
                 and restore.digest([(p, after[p]["payload"]) for p in sorted(after)]) == before_payloads)
    restore.require_cross_db_pass(links)
    if not slices["passed"] or not unchanged:
        raise Neo4jR2Error(f"교차 검증 실패: full_index={slices['passed']}, qdrant_unchanged={unchanged}")
    return {"mode": "cross-db", "db_writes": 0, "status": "PASS", "qdrant_points": len(after),
            "qdrant_vector_digest": before_vectors, "qdrant_unchanged": unchanged,
            "neo4j_totals": graph["totals"],
            "cross_db": {k: links[k] for k in ("status", "qdrant_points", "qdrant_count", "all_points_read",
                                               "summary", "failures")},
            "full_index_verification": slices}


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="#91 전용 Neo4j 그래프 적재·검증 (기본 dry-run)")
    parser.add_argument("--neo4j-url", required=True, help="bolt://127.0.0.1:56487 (그 밖의 주소 거부)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--load", action="store_true", help="빈 DB에 전체 적재 (load_graph verify=True)")
    mode.add_argument("--resume", action="store_true", help="중간 실패로 남은 부분 적재를 이어서 완성")
    mode.add_argument("--verify", action="store_true", help="읽기 전용 종합 검증")
    mode.add_argument("--cross-db", action="store_true", help="#91 Qdrant와 읽기 전용 교차 검증")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expect-fingerprint", help="직전 dry-run이 출력한 fingerprint")
    parser.add_argument("--qdrant-url", help="--cross-db 전용, http://127.0.0.1:56446만 허용")
    parser.add_argument("--report", type=Path, help="결과를 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    writing = args.load or args.resume
    if writing and not (args.confirm_write and args.expect_fingerprint):
        parser.error("--load·--resume 에는 --confirm-write 와 --expect-fingerprint 가 함께 필요합니다")
    if args.cross_db and not args.qdrant_url:
        parser.error("--cross-db 에는 --qdrant-url 이 필요합니다")
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    driver = qdrant_client = None
    try:
        url = require_isolated_bolt_url(args.neo4j_url)
        if args.cross_db:
            qdrant_r2.require_isolated_url(args.qdrant_url)
        ref = load_reference()
        auth_enforced = check_auth_enforced(url)
        driver = make_isolated_driver(url)
        runner = lg.Neo4jDriverRunner(driver, database=NEO4J_DATABASE)
        if args.load or args.resume:
            output = {"mode": "resume" if args.resume else "load",
                      "result": load(runner, ref, args.confirm_write, args.expect_fingerprint, resume=args.resume)}
            output.update({k: v for k, v in dry_run(runner, ref).items() if k not in ("mode", "db_writes")})
        elif args.verify:
            output = verify(runner, ref)
        elif args.cross_db:
            from database import hybrid_search as hs
            qdrant_client = qdrant_r2.make_isolated_client(args.qdrant_url)
            output = cross_check(qdrant_client, hs.ReadOnlyTimedRunner(driver, database=NEO4J_DATABASE), ref)
        else:
            output = dry_run(runner, ref)
        result = {"status": "PASS", "auth_enforced": auth_enforced, **output}
    except (Neo4jR2Error, seed.SeedPlanError, qdrant_r2.UpdateError, lg.GraphLoadError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:1000],
                  "action": "쓰지 않고 중단. 부분 적재 여부는 dry-run의 state로 확인한다(partial이면 승인 후 --resume)."}
    except Exception as error:   # 접속·인증·드라이버 오류: 메시지에 설정값이 섞일 수 있어 종류만 남긴다
        result = {"status": "FAILED", "error_type": type(error).__name__,
                  "action": "접속 대상·컨테이너 상태·비밀번호 환경 변수를 확인한다. 데이터는 바꾸지 않았다."}
    finally:
        for resource in (qdrant_client, driver):
            if resource is not None:
                resource.close()
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
