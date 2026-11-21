"""Issue #105: r3 번들을 r3 verify 프로젝트(issue105-r3-verify)에만 복원하는 보호 래퍼 — 기본 읽기 전용 preflight.

복원·검증 본체는 scripts/restore_final_v140_r3.py의 restore()를 같은 프로세스에서 호출한다.
r2 래퍼(restore_r2_isolated)와 달리 환경 변수를 덮어쓰지 않고, 고정 주소·전용 인증을 settings로 직접 넘긴다.

보호 장치 (r2 래퍼와 같은 기준, Docker·서버 확인 함수는 r2 래퍼 것을 프로젝트 값만 바꿔 재사용)
  - 주소 고정: Qdrant http://127.0.0.1:<verify qdrant_port>, Neo4j bolt://127.0.0.1:<verify bolt_port>.
    공유·r1·r2·r3 build 포트는 거부한다.
  - 인증: ISSUE105_R3_VERIFY_QDRANT_API_KEY / ISSUE105_R3_VERIFY_NEO4J_PASSWORD만. 원본·공유·r2·r3 build 값과 같으면 거부
  - Docker 소유 확인: 두 포트의 컨테이너가 정확히 이 프로젝트의 qdrant·neo4j 서비스, 실행 중, 고정 이미지 digest,
    127.0.0.1 바인딩만, 이 프로젝트 이름의 볼륨만. 프로젝트에 다른 컨테이너가 있어도 거부
  - 서버: Qdrant 1.12.4·키 없는 요청 401·컬렉션 0·별칭 0, Neo4j 5.26.32 community·틀린 비밀번호 거부·노드/관계 0
  - fingerprint: Docker 식별 정보 + 서버 상태. --apply는 직전 preflight fingerprint와 같을 때만, 복원 후 식별 정보 재확인
  - 소스·번들: --repo는 이 래퍼가 들어 있는 소스 트리, 번들의 restore_final_v140_r3.py가 그 트리 것과 바이트 동일
  - R3_PINS가 확정되지 않으면 check_bundle 단계에서 거부된다
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import restore_final_v140 as restore  # noqa: E402  (표준 라이브러리만)
from scripts import restore_final_v140_r3 as restore_r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402  (Docker·서버 확인 함수 재사용)

ENV = profiles.environment("verify")
BUILD = profiles.environment("build")
PROJECT = ENV["project"]
QDRANT_URL = f"http://127.0.0.1:{ENV['qdrant_port']}"
NEO4J_URI = f"bolt://127.0.0.1:{ENV['bolt_port']}"
NEO4J_USER = "neo4j"
NEO4J_DATABASE = "neo4j"
QDRANT_KEY_ENV = ENV["qdrant_key_env"]
NEO4J_PASSWORD_ENV = ENV["neo4j_password_env"]
FOREIGN_SECRET_ENVS = (*profiles.FOREIGN_SECRET_ENVS, BUILD["qdrant_key_env"], BUILD["neo4j_password_env"])
DENIED_PORTS = profiles.FOREIGN_PORTS | {BUILD["qdrant_port"], BUILD["bolt_port"]}
SERVICES = profiles.services_for("verify")
EXPECTED_COLLECTIONS = sorted(spec["collection"] for spec in profiles.R3_COLLECTIONS.values())
IsolationError = r2w.IsolationError


def require_target_urls(qdrant_url: str = QDRANT_URL, neo4j_uri: str = NEO4J_URI) -> None:
    from urllib.parse import urlsplit
    for url, scheme, wanted in ((qdrant_url, "http", QDRANT_URL), (neo4j_uri, "bolt", NEO4J_URI)):
        parts = urlsplit(url)
        restore.require_local_url(url)
        if parts.scheme != scheme or parts.port is None or parts.port in DENIED_PORTS or url != wanted:
            raise IsolationError(f"r3 검증 대상 주소가 아닙니다 (허용: {wanted})")


def read_secrets(environ=None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    values = {"qdrant": environ.get(QDRANT_KEY_ENV, ""), "neo4j": environ.get(NEO4J_PASSWORD_ENV, "")}
    missing = [name for name, key in ((QDRANT_KEY_ENV, "qdrant"), (NEO4J_PASSWORD_ENV, "neo4j")) if not values[key].strip()]
    if missing:
        raise IsolationError(f"r3 검증용 인증 환경 변수가 없습니다: {missing}")
    if values["qdrant"] == values["neo4j"]:
        raise IsolationError("Qdrant 키와 Neo4j 비밀번호는 서로 달라야 합니다")
    reused = [name for name in FOREIGN_SECRET_ENVS if environ.get(name) and environ.get(name) in values.values()]
    if reused:
        raise IsolationError(f"원본·공유·r2·r3 build 인증값을 재사용했습니다 (변수 이름: {reused})")
    return values


def settings_for(values: dict[str, str]) -> dict[str, str]:
    return {"QDRANT_URL": QDRANT_URL, "QDRANT_API_KEY": values["qdrant"], "NEO4J_URI": NEO4J_URI,
            "NEO4J_USER": NEO4J_USER, "NEO4J_PASSWORD": values["neo4j"]}


def make_qdrant_client(values: dict[str, str]):
    from qdrant_client import QdrantClient
    return QdrantClient(url=QDRANT_URL, api_key=values["qdrant"], timeout=30)


def make_neo4j_driver(password: str):
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, password), connection_timeout=5)
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        raise
    return driver


def fingerprint(identity: dict[str, Any], servers: dict[str, Any]) -> str:
    return restore.digest({"project": PROJECT, "qdrant_url": QDRANT_URL, "neo4j_uri": NEO4J_URI,
                           "docker": identity, "servers": servers})


def check_source_and_bundle(repo: Path, bundle: Path, pins: dict[str, Any] | None = None) -> dict[str, Any]:
    if Path(repo).resolve() != ROOT:
        raise IsolationError("--repo는 이 래퍼가 들어 있는 소스 트리여야 합니다 (다른 소스와 섞지 않음)")
    package, snapshots = restore_r3.check_bundle(bundle, pins)
    tool = Path(bundle) / Path(profiles.RESTORE_SCRIPT).name
    if not tool.is_file() or not profiles.same_text_ignoring_eol(tool.read_bytes(),
                                                                 (ROOT / profiles.RESTORE_SCRIPT).read_bytes()):
        raise IsolationError("번들의 restore_final_v140_r3.py가 소스 트리의 것과 다릅니다")
    restore_r3.check_repo_matches_source(repo, bundle, package)
    return {"bundle": package.get("bundle"), "manifest_lf_sha256": package.get("manifest_lf_sha256"),
            "source_commit": package.get("source_commit"),
            "collections": {k: {f: v.get(f) for f in ("collection", "points", "vector_digest", "snapshot_sha256")}
                            for k, v in package["collections"].items()},
            "snapshots": {k: p.name for k, p in snapshots.items()}}


class Context:
    """Docker 확인 → 클라이언트 생성 → 서버 확인. 테스트는 factory를 바꿔 끼운다."""

    def __init__(self, values, *, docker=r2w.run_docker, qdrant_factory=make_qdrant_client,
                 neo4j_factory=make_neo4j_driver, runner_factory=None, http_get=None, graph=None):
        self.values, self.docker, self.http_get = values, docker, http_get
        self.qdrant_factory, self.neo4j_factory = qdrant_factory, neo4j_factory
        self.runner_factory, self.graph = runner_factory, graph

    def identity(self) -> dict[str, Any]:
        return r2w.verify_docker_ownership(self.docker, project=PROJECT, services=SERVICES)

    def snapshot(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        from database.neo4j import load_graph as lg
        identity = self.identity()
        r2w.check_qdrant_auth(self.http_get, url=QDRANT_URL)
        r2w.check_neo4j_auth(self.neo4j_factory)
        client = self.qdrant_factory(self.values)
        driver = self.neo4j_factory(self.values["neo4j"])
        try:
            runner = (self.runner_factory or (lambda d: lg.Neo4jDriverRunner(d, database=NEO4J_DATABASE)))(driver)
            servers = r2w.inspect_servers(client, runner, self.graph or lg.read_graph_input(),
                                          expected_collections=EXPECTED_COLLECTIONS)
        finally:
            client.close()
            driver.close()
        return identity, servers, fingerprint(identity, servers)


def run(mode: str, repo: Path, bundle: Path, ctx: Context, *, confirm_write: bool = False,
        expected_fingerprint: str | None = None, restore_fn=None, pins: dict[str, Any] | None = None) -> dict:
    restore_fn = restore_fn or restore_r3.restore
    require_target_urls()
    source = check_source_and_bundle(repo, bundle, pins)
    identity, servers, current = ctx.snapshot()
    settings = settings_for(ctx.values)
    base = {"project": PROJECT, "qdrant_url": QDRANT_URL, "neo4j_uri": NEO4J_URI, "bundle": source,
            "docker": identity, "servers": servers, "fingerprint": current}

    def call(apply: bool, verify_only: bool) -> dict[str, Any]:
        return restore_fn(Namespace(repo=Path(repo), bundle=Path(bundle), apply=apply, verify_only=verify_only),
                          settings=settings, pins=pins)

    if mode == "preflight":
        if servers["state"] != "empty":
            raise IsolationError(f"검증 대상 DB가 비어 있지 않습니다: {servers['state']}")
        result = call(False, False)
        if result.get("status") != "PREFLIGHT_PASS_NO_WRITES":
            raise IsolationError(f"restore preflight 결과가 {result.get('status')}입니다")
        return {"mode": mode, "db_writes": 0, **base, "restore_preflight": result}
    if mode == "apply":
        if not confirm_write or not expected_fingerprint:
            raise IsolationError("--apply 에는 --confirm-write 와 --expect-fingerprint 가 필요합니다")
        if current != expected_fingerprint:
            raise IsolationError("fingerprint가 preflight와 다릅니다 (대상 컨테이너·서버가 바뀜) → 쓰지 않음")
        if servers["state"] != "empty":
            raise IsolationError(f"검증 대상 DB가 비어 있지 않습니다: {servers['state']}")
        result = call(True, False)
        if ctx.identity() != identity:
            raise IsolationError("복원 중 대상 컨테이너가 바뀌었습니다")
        if result.get("status") != "PASS":
            raise IsolationError(f"restore 결과가 {result.get('status')}입니다 (부분 자산 보존, 재실행 금지)")
        verification = call(False, True)
        if verification.get("status") != "PASS" or ctx.identity() != identity:
            raise IsolationError("복원 후 verify-only 검증 실패 (부분 자산 보존)")
        return {"mode": mode, **base, "restore_apply": result, "post_verify": verification}
    if mode == "verify-only":
        if servers["state"] != "complete":
            raise IsolationError(f"검증 대상 상태가 {servers['state']}입니다 (complete여야 함)")
        verification = call(False, True)
        if verification.get("status") != "PASS":
            raise IsolationError("verify-only 검증 실패")
        return {"mode": mode, "db_writes": 0, **base, "post_verify": verification}
    raise IsolationError(f"알 수 없는 모드: {mode}")


def main(argv: list[str] | None = None, ctx_factory=Context) -> int:
    parser = argparse.ArgumentParser(description="r3 번들 독립 복원 보호 래퍼 (기본 읽기 전용 preflight)")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expect-fingerprint")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.apply and not (args.confirm_write and args.expect_fingerprint):
        parser.error("--apply 에는 --confirm-write 와 --expect-fingerprint 가 필요합니다")
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    name = "apply" if args.apply else "verify-only" if args.verify_only else "preflight"
    try:
        os.environ["LIFEPOLICY_DATASET_DIR"] = str(ROOT / profiles.RELEASE_DIR)   # database 모듈 import 전에 고정
        output = run(name, args.repo, args.bundle, ctx_factory(read_secrets()), confirm_write=args.confirm_write,
                     expected_fingerprint=args.expect_fingerprint)
        result = {"status": "PASS", **{k: v for k, v in output.items() if k != "status"}}
    except (IsolationError, ValueError) as error:
        result = {"status": "FAILED", "mode": name, "error_type": type(error).__name__, "reason": str(error)[:800],
                  "action": "자동 재실행하지 않는다. 대상 DB는 보존한다. 원인 확인 후 새 프로젝트·새 볼륨에서 다시 시작한다."}
    except Exception as error:
        result = {"status": "FAILED", "mode": name, "error_type": type(error).__name__,
                  "action": "자동 재실행하지 않는다. 컨테이너 상태와 검증용 인증 변수를 확인한다."}
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
