"""Issue #105: r3 격리 Neo4j(build 프로젝트) 적재·검증과 B2·B3 각각의 교차 검증 — 기본 dry-run.

적재·검증 본체는 r2 도구(load_neo4j_r2_isolated)의 inspect_state·dry_run·load·verify를 그대로 호출한다
(내부에서 기존 preflight_database·load_graph(verify=True)·verify_loaded_graph를 쓴다).
r3에서 달라지는 것은 기준값(최신 릴리스, R3_PINS)과 대상(build 프로젝트 포트·인증 변수), 교차 검증 대상(두 컬렉션)이다.

보호 장치
  - 주소: bolt://127.0.0.1:<build bolt_port>만 (공유·r2 포트 거부, neo4j:// 라우팅 거부). NEO4J_URI·.env 미사용
  - 인증: ISSUE105_R3_BUILD_NEO4J_PASSWORD만 (원본·공유·r2 값 재사용 거부). 틀린 비밀번호가 거부되는지 먼저 확인
  - Docker 소유: dry-run과 쓰기 전후에 build 프로필 프로젝트(현재 issue105-r3d-build) 소유를 확인한다(update_r3_collections.require_build_ownership).
    fingerprint = Neo4j 서버 상태 지문 + Docker 식별 정보
  - 쓰기(--load/--resume): R3_PINS 확정 + --confirm-write + dry-run fingerprint 일치. 빈 DB(load) 또는 부분 적재(resume)에서만
  - 교차 검증(--cross-db, 읽기 전용): 컬렉션마다 Point 수·전체 벡터 digest(R3_PINS) → verify_cross_db → verify_full_index,
    검증 전후 벡터·payload 불변
"""
from __future__ import annotations

import argparse
import json
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
from scripts import load_neo4j_r2_isolated as lnr  # noqa: E402  (r2 적재·검증 함수 재사용)
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import update_r3_collections as qdrant_r3  # noqa: E402

ENV = profiles.environment("build")
ISOLATED_BOLT_PORT = ENV["bolt_port"]
PASSWORD_ENV = ENV["neo4j_password_env"]
NEO4J_USER = lnr.NEO4J_USER
NEO4J_DATABASE = lnr.NEO4J_DATABASE


class Neo4jR3Error(ValueError):
    """실행할 수 없음. 메시지에 비밀번호·쿼리 값은 넣지 않는다."""


def require_isolated_bolt_url(url: str, port: int = ISOLATED_BOLT_PORT) -> str:
    parts = urlsplit(url)
    if parts.scheme != "bolt":
        raise Neo4jR3Error("bolt:// 주소만 허용합니다")
    restore.require_local_url(url)
    if parts.port is None or parts.port in profiles.FOREIGN_PORTS or parts.port != port:
        raise Neo4jR3Error(f"r3 build 프로젝트 Bolt 포트 {port}만 허용합니다 (공유·r2 포트 거부)")
    if parts.path not in ("", "/") or parts.query:
        raise Neo4jR3Error("주소에 경로·쿼리를 넣지 않습니다")
    return url


def make_isolated_driver(url: str, environ=None):
    require_isolated_bolt_url(url)
    password = qdrant_r3.read_secret(PASSWORD_ENV, environ)
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(url, auth=(NEO4J_USER, password), connection_timeout=5)
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        raise
    return driver


def check_auth_enforced(url: str) -> bool:
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
    raise Neo4jR3Error("틀린 비밀번호로 접속됨 (인증이 꺼져 있음) → 중단")


# ---------------------------------------------------------------------------
# 기준값
# ---------------------------------------------------------------------------
def load_reference(pins: dict[str, Any] | None = None, release: dict[str, Any] | None = None,
                   *, require_pins: bool = False) -> dict[str, Any]:
    """
    최신 릴리스(planner.load_release) + 문서 해시·청크 연결 확인. r2 함수가 쓰는 ref 모양으로 만든다:
    counts = (정책, 문서, 릴리스 청크, B2 Point, 노드, 관계, REQUIRES_CONDITION) — load_neo4j_r2_isolated와 같은 순서.
    """
    pins = profiles.R3_PINS if pins is None else pins
    if require_pins:
        profiles.require_finalized(pins)
    release = release or planner.load_release(pins)
    payload_chunks = hd.load_payload_chunks()
    links = {"payload_chunks": lg.check_vector_payload_links(release["graph"], payload_chunks, release["documents"])}
    for key, entry in release["collections"].items():
        links[f"{key}_chunks"] = lg.check_vector_payload_links(release["graph"], entry["chunks"], release["documents"])
    broken = {f"{name}:{k}": len(v) for name, result in links.items() for k, v in result.items()
              if k != "payload_count" and v}
    if broken:
        raise Neo4jR3Error(f"문서 해시·청크 연결 불일치: {broken}")
    c = release["counts"]
    counts = (c["policies"], c["documents"], c["release_chunks"], len(release["collections"]["b2"]["chunks"]),
              c["neo4j_nodes"], c["neo4j_relationships"], c["requires_condition"])
    return {"graph": release["graph"], "counts": counts, "manifest_lf_sha256": release["manifest_lf_sha256"],
            "validation": release["validation"], "release": release,
            "chunks": release["collections"]["b2"]["chunks"], "documents": release["documents"]}


# ---------------------------------------------------------------------------
# 상태·적재 (r2 함수 재사용)
# ---------------------------------------------------------------------------
def combined_fingerprint(neo4j_fingerprint: str, identity: dict[str, Any]) -> str:
    """Neo4j 서버 상태 지문(r2 함수) + build Docker 식별 정보."""
    return restore.digest({"neo4j": neo4j_fingerprint,
                           "docker": {"project": qdrant_r3.BUILD_PROJECT, "services": identity}})


def ownership(docker=None) -> dict[str, Any]:
    try:
        return qdrant_r3.require_build_ownership(docker)
    except qdrant_r3.UpdateError as error:
        raise Neo4jR3Error(str(error)) from error


def dry_run(runner, ref: dict[str, Any], pins: dict[str, Any] | None = None, docker=None) -> dict[str, Any]:
    """읽기 전용. build Docker 소유 확인(실패하면 중단) + 서버 상태. fingerprint는 둘을 합친 값이다."""
    identity = ownership(docker)
    output = lnr.dry_run(runner, ref)
    output.update({"neo4j_fingerprint": output["fingerprint"], "docker_project": qdrant_r3.BUILD_PROJECT,
                   "docker": identity, "fingerprint": combined_fingerprint(output["fingerprint"], identity),
                   "r3_pins_finalized": profiles.is_finalized(pins)})
    return output


def load(runner, ref: dict[str, Any], confirm_write: bool = False, expected_fingerprint: str | None = None,
         resume: bool = False, pins: dict[str, Any] | None = None, docker=None) -> dict[str, Any]:
    """R3_PINS 확정 → build Docker 소유 확인 → (서버 상태 + Docker) fingerprint 대조 → r2 load(같은 실행기) → 소유 재확인."""
    if not confirm_write:
        raise Neo4jR3Error("confirm_write=True 없이 쓰지 않습니다")
    # 그래프 적재는 Qdrant 최종 vector_digest와 무관하므로 갱신 단계와 같이 그 두 칸만 미확정을 허용한다
    pins = profiles.require_finalized(pins, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
    release_counts = profiles.release_counts_tuple(pins["release_counts"])
    actual = (ref["counts"][0], ref["counts"][1], ref["counts"][2], *ref["counts"][4:])
    if ref["manifest_lf_sha256"] != pins["manifest_lf_sha256"] or actual != release_counts:
        raise Neo4jR3Error("그래프 기준이 r3 고정값(manifest·건수)과 다릅니다")
    identity = ownership(docker)
    if not expected_fingerprint:
        raise Neo4jR3Error("dry-run이 출력한 fingerprint를 expected_fingerprint로 넘겨야 씁니다")
    current = lnr.dry_run(runner, ref)["fingerprint"]
    if combined_fingerprint(current, identity) != expected_fingerprint:
        raise Neo4jR3Error("fingerprint가 dry-run과 다릅니다 (대상 서버·데이터·컨테이너가 바뀜) → 쓰지 않음")
    try:
        result = lnr.load(runner, ref, confirm_write=True, expected_fingerprint=current, resume=resume)
    except lnr.Neo4jR2Error as error:
        raise Neo4jR3Error(str(error)) from error
    if ownership(docker) != identity:
        raise Neo4jR3Error("적재 중 build 컨테이너 식별 정보가 바뀌었습니다 (부분 상태 보존)")
    return result


def verify(runner, ref: dict[str, Any]) -> dict[str, Any]:
    return lnr.verify(runner, ref)


# ---------------------------------------------------------------------------
# 교차 검증 (B2·B3 각각, 읽기 전용)
# ---------------------------------------------------------------------------
def cross_check(qdrant_client, runner, ref: dict[str, Any], pins: dict[str, Any] | None = None) -> dict[str, Any]:
    from database import hybrid_search as hs
    pins = profiles.require_finalized(pins)
    names = qdrant_r3.require_isolated_server(qdrant_client)
    wanted = sorted(spec["collection"] for spec in profiles.R3_COLLECTIONS.values())
    if names != wanted:
        raise Neo4jR3Error(f"격리 Qdrant에 r3 두 컬렉션이 모두 있어야 합니다: {names}")
    lnr.server_info(runner)
    graph = lg.verify_loaded_graph(ref["graph"], runner)
    if not graph["ok"]:
        raise Neo4jR3Error("Neo4j가 최신 그래프와 일치하지 않아 교차 검증을 하지 않습니다")
    release = ref["release"]
    results: dict[str, Any] = {}
    for key in profiles.COLLECTION_KEYS:
        spec, pinned, entry = profiles.collection_spec(key), pins["collections"][key], release["collections"][key]
        before = qdrant_r3.scan_collection(qdrant_client, spec["collection"])
        before_vectors = restore.vector_digest(before)
        before_payloads = restore.digest([(p, before[p]["payload"]) for p in sorted(before)])
        if len(before) != pinned["points"] or before_vectors != pinned["vector_digest"]:
            raise Neo4jR3Error(f"{key} Point {len(before)}개·벡터 digest가 r3 고정값과 다릅니다")
        links = hs.verify_cross_db(client=qdrant_client, runner=runner, collection=spec["collection"])
        slices = fi.verify_full_index(spec["config"], client=qdrant_client, chunks=entry["chunks"],
                                      documents=release["documents"])
        after = qdrant_r3.scan_collection(qdrant_client, spec["collection"])
        unchanged = (restore.vector_digest(after) == before_vectors
                     and restore.digest([(p, after[p]["payload"]) for p in sorted(after)]) == before_payloads)
        restore.require_cross_db_pass(links)
        if not slices["passed"] or not unchanged:
            raise Neo4jR3Error(f"{key} 교차 검증 실패: full_index={slices['passed']}, qdrant_unchanged={unchanged}")
        results[key] = {"collection": spec["collection"], "points": len(after), "vector_digest": before_vectors,
                        "qdrant_unchanged": unchanged,
                        "cross_db": {k: links[k] for k in ("status", "qdrant_points", "qdrant_count",
                                                           "all_points_read", "summary", "failures")},
                        "full_index_verification": slices}
    return {"mode": "cross-db", "db_writes": 0, "status": "PASS", "neo4j_totals": graph["totals"],
            "collections": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r3 격리 Neo4j 적재·검증 + B2·B3 교차 검증 (기본 dry-run)")
    parser.add_argument("--neo4j-url", required=True, help=f"bolt://127.0.0.1:{ISOLATED_BOLT_PORT} (그 밖의 주소 거부)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--load", action="store_true", help="빈 DB에 전체 적재 (load_graph verify=True)")
    mode.add_argument("--resume", action="store_true", help="중간 실패로 남은 부분 적재를 이어서 완성")
    mode.add_argument("--verify", action="store_true", help="읽기 전용 종합 검증")
    mode.add_argument("--cross-db", action="store_true", help="r3 Qdrant 두 컬렉션과 읽기 전용 교차 검증")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expect-fingerprint")
    parser.add_argument("--qdrant-url", help=f"--cross-db 전용, http://127.0.0.1:{qdrant_r3.ISOLATED_QDRANT_PORT}만 허용")
    parser.add_argument("--report", type=Path, help="결과를 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    if (args.load or args.resume) and not (args.confirm_write and args.expect_fingerprint):
        parser.error("--load·--resume 에는 --confirm-write 와 --expect-fingerprint 가 함께 필요합니다")
    if args.cross_db and not args.qdrant_url:
        parser.error("--cross-db 에는 --qdrant-url 이 필요합니다")
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    if args.report and profiles.is_preserved_output(args.report):
        parser.error("보존 자산 폴더(#104 r3, .cache/issue105-r3) 안에는 새 파일도 만들지 않습니다")
    driver = qdrant_client = None
    try:
        url = require_isolated_bolt_url(args.neo4j_url)
        if args.cross_db:
            qdrant_r3.require_isolated_url(args.qdrant_url)
        if args.load or args.resume:
            profiles.require_finalized(deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
        ref = load_reference(require_pins=bool(args.cross_db))
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
            qdrant_client = qdrant_r3.make_isolated_client(args.qdrant_url)
            output = cross_check(qdrant_client, hs.ReadOnlyTimedRunner(driver, database=NEO4J_DATABASE), ref)
        else:
            output = dry_run(runner, ref)
        result = {"status": "PASS", "auth_enforced": auth_enforced, **output}
    except (Neo4jR3Error, lnr.Neo4jR2Error, planner.PlanError, profiles.ProfileError, qdrant_r3.UpdateError,
            lg.GraphLoadError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:1000],
                  "action": "쓰지 않고 중단. 부분 적재 여부는 dry-run의 state로 확인한다(partial이면 승인 후 --resume)."}
    except Exception as error:
        result = {"status": "FAILED", "error_type": type(error).__name__,
                  "action": "접속 대상·컨테이너 상태·비밀번호 환경 변수를 확인한다. 데이터는 바꾸지 않았다."}
    finally:
        for resource in (qdrant_client, driver):
            if resource is not None:
                resource.close()
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        planner.write_new_json(args.report, result)
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
