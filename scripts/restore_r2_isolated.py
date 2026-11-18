"""Issue #91: r2 번들을 두 번째 독립 환경(issue91-r2-verify-20261119)에만 복원하는 보호 래퍼 — 기본 읽기 전용.

복원·검증 자체는 기존 scripts/restore_final_v140.py의 restore(args)를 같은 프로세스에서 그대로 호출한다.
restore()는 호출 시점의 환경 변수(QDRANT_URL·QDRANT_API_KEY·NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD)로 접속하고
loopback 여부만 확인하므로, 이 래퍼가 그 앞뒤를 막는다.

보호 장치
  - 주소 고정: Qdrant http://127.0.0.1:56556, Neo4j bolt://127.0.0.1:56587 (사용자의 QDRANT_URL·NEO4J_URI 등은 무시하고
    restore 호출 동안만 이 값으로 덮어쓴 뒤 원래 값으로 되돌린다). 원본·공유 DB 포트는 목록으로도 거부한다.
  - 인증: ISSUE91_VERIFY_QDRANT_API_KEY / ISSUE91_VERIFY_NEO4J_PASSWORD 전용 변수만 쓴다. #91 원본·공유 DB 값
    (QDRANT_API_KEY·ISSUE91_NEO4J_PASSWORD·NEO4J_PASSWORD)과 같으면 거부한다. 값은 출력하지 않는다.
  - Docker 소유 확인: 두 포트에 게시된 컨테이너가 정확히 이 Compose 프로젝트의 qdrant·neo4j 서비스 하나씩이고,
    실행 중, 고정 이미지 digest, 127.0.0.1 바인딩만, 이 프로젝트 이름의 볼륨만 마운트해야 한다.
    프로젝트에 다른 컨테이너가 있어도 거부한다. "loopback 포트가 맞다"만으로 통과시키지 않는다.
  - 서버 확인: Qdrant 1.12.4·키 없는 요청 401·컬렉션 0·별칭 0, Neo4j 5.26.32 community·틀린 비밀번호 거부·
    DB 이름 neo4j·노드/관계 0·스키마 충돌 없음(preflight_database).
  - fingerprint: Docker 식별 정보(컨테이너 ID·이미지·시작 시각·볼륨)와 서버 상태의 digest. --apply는 직전 preflight의
    fingerprint가 있어야 하고, 쓰기 직전 다시 계산해 다르면 쓰지 않는다. restore 후 Docker 식별 정보가 바뀌어도 실패.
  - 소스·번들: --repo는 이 스크립트가 들어 있는 소스 트리여야 하고, 번들의 restore_final_v140.py가 그 트리의 것과
    바이트 동일해야 한다. check_bundle(SHA256SUMS·package·snapshot checksum)은 restore가 다시 확인한다.
  - 재실행: 자동 재시도는 없다. 실패하면 새 DB에 부분 자산이 남고, 다음 --apply는 빈 서버가 아니어서 거부된다.

실행 (압축을 푼 r2 소스 트리에서, .venv):
    python scripts/restore_r2_isolated.py --repo <소스> --bundle <번들 폴더>                       # 읽기 전용 preflight
    python scripts/restore_r2_isolated.py --repo <소스> --bundle <번들 폴더> --apply --confirm-write --expect-fingerprint <값>
    python scripts/restore_r2_isolated.py --repo <소스> --bundle <번들 폴더> --verify-only           # 읽기 전용
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import restore_final_v140 as restore  # noqa: E402  (표준 라이브러리만 import)

PROJECT = "issue91-r2-verify-20261119"
QDRANT_URL = "http://127.0.0.1:56556"
NEO4J_URI = "bolt://127.0.0.1:56587"
NEO4J_USER = "neo4j"
NEO4J_DATABASE = "neo4j"
QDRANT_KEY_ENV = "ISSUE91_VERIFY_QDRANT_API_KEY"
NEO4J_PASSWORD_ENV = "ISSUE91_VERIFY_NEO4J_PASSWORD"
# 이 값과 같은 검증용 비밀값은 거부한다 (#91 원본·공유 DB 인증값 재사용 금지)
FOREIGN_SECRET_ENVS = ("QDRANT_API_KEY", "ISSUE91_NEO4J_PASSWORD", "NEO4J_PASSWORD")
# 원본·공유·이전 작업 DB 포트 (거부 목록). 허용은 56556·56587 둘뿐이다.
DENIED_PORTS = frozenset({6333, 6334, 16333, 36333, 46335, 46336, 56336, 56339, 56446,
                          7474, 7687, 17474, 17687, 18474, 18687, 47474, 47687,
                          57474, 57477, 57687, 57690, 56487})
EXPECTED_QDRANT_VERSION = "1.12.4"
EXPECTED_NEO4J_VERSION = "5.26.32"
SERVICES = {
    "qdrant": {"port": 56556, "container_port": "6333/tcp",
               "image": "sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636",
               "volumes": {f"{PROJECT}_qdrant_storage"}},
    "neo4j": {"port": 56587, "container_port": "7687/tcp",
              "image": "sha256:42ef3ebfd671ebd01fdebb6dcbe80cf7b45eef2aa3d346ef4132f7ef31e67d2f",
              "volumes": {f"{PROJECT}_neo4j_data", f"{PROJECT}_neo4j_logs"}},
}
MANAGED_ENV = ("QDRANT_URL", "QDRANT_API_KEY", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "LIFEPOLICY_DATASET_DIR")
RELEASE_DIR = "data/releases/v1.4.0-candidate"


class IsolationError(ValueError):
    """독립 복원 대상이 확인되지 않음. 메시지에 비밀값을 넣지 않는다."""


# ---------------------------------------------------------------------------
# 1. 주소·인증 정보
# ---------------------------------------------------------------------------
def require_target_urls(qdrant_url: str = QDRANT_URL, neo4j_uri: str = NEO4J_URI) -> None:
    """고정 주소만 허용한다 (인자로 다른 값을 넘겨도 거부)."""
    for url, scheme, wanted in ((qdrant_url, "http", QDRANT_URL), (neo4j_uri, "bolt", NEO4J_URI)):
        parts = urlsplit(url)
        restore.require_local_url(url)
        if parts.scheme != scheme or parts.port is None or parts.port in DENIED_PORTS or url != wanted:
            raise IsolationError(f"독립 복원 대상 주소가 아닙니다 (허용: {wanted})")


def read_secrets(environ=None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    values = {"qdrant": environ.get(QDRANT_KEY_ENV, ""), "neo4j": environ.get(NEO4J_PASSWORD_ENV, "")}
    missing = [name for name, key in ((QDRANT_KEY_ENV, "qdrant"), (NEO4J_PASSWORD_ENV, "neo4j")) if not values[key].strip()]
    if missing:
        raise IsolationError(f"검증용 인증 환경 변수가 없습니다: {missing}")
    if values["qdrant"] == values["neo4j"]:
        raise IsolationError("Qdrant 키와 Neo4j 비밀번호는 서로 달라야 합니다")
    reused = [name for name in FOREIGN_SECRET_ENVS if environ.get(name) and environ.get(name) in values.values()]
    if reused:
        raise IsolationError(f"원본·공유 DB 인증값을 재사용했습니다 (변수 이름: {reused})")
    return values


@contextmanager
def isolated_environment(repo: Path, values: dict[str, str]):
    """restore() 호출 동안만 접속 환경 변수를 고정값으로 덮어쓰고, 끝나면 원래 값으로 되돌린다."""
    saved = {name: os.environ.get(name) for name in MANAGED_ENV}
    wanted = {"QDRANT_URL": QDRANT_URL, "QDRANT_API_KEY": values["qdrant"], "NEO4J_URI": NEO4J_URI,
              "NEO4J_USER": NEO4J_USER, "NEO4J_PASSWORD": values["neo4j"],
              "LIFEPOLICY_DATASET_DIR": str(Path(repo) / RELEASE_DIR)}
    os.environ.update(wanted)
    try:
        yield wanted
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def require_environment_unchanged(wanted: dict[str, str]) -> None:
    changed = sorted(name for name, value in wanted.items() if os.environ.get(name) != value)
    if changed:
        raise IsolationError(f"복원 중 접속 환경 변수가 바뀌었습니다: {changed}")


# ---------------------------------------------------------------------------
# 2. Docker 소유 확인 (읽기 전용 docker ps / docker inspect)
# ---------------------------------------------------------------------------
def run_docker(args: list[str]) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise IsolationError(f"docker {args[0]} 실패 (exit {result.returncode})")
    return result.stdout


def inspect_containers(ids: list[str], docker: Callable[[list[str]], str]) -> list[dict[str, Any]]:
    return json.loads(docker(["inspect", *ids])) if ids else []


def verify_docker_ownership(docker: Callable[[list[str]], str] = run_docker) -> dict[str, Any]:
    """두 포트에 게시된 컨테이너와 프로젝트 컨테이너가 정확히 기대한 두 개인지 확인하고 식별 정보를 돌려준다."""
    project_ids = sorted(docker(["ps", "-a", "-q", "--no-trunc", "--filter",
                                 f"label=com.docker.compose.project={PROJECT}"]).split())
    identity: dict[str, Any] = {}
    seen: set[str] = set()
    for service, spec in SERVICES.items():
        ids = sorted(docker(["ps", "-a", "-q", "--no-trunc", "--filter", f"publish={spec['port']}"]).split())
        containers = inspect_containers(ids, docker)
        if len(containers) != 1:
            raise IsolationError(f"{spec['port']} 포트에 게시된 컨테이너가 {len(containers)}개입니다 (정확히 1개여야 함)")
        item = containers[0]
        labels = (item.get("Config") or {}).get("Labels") or {}
        if (labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") != service):
            raise IsolationError(f"{spec['port']} 포트의 컨테이너가 {PROJECT}의 {service} 서비스가 아닙니다")
        state = item.get("State") or {}
        if not state.get("Running"):
            raise IsolationError(f"{service} 컨테이너가 실행 중이 아닙니다")
        if item.get("Image") != spec["image"]:
            raise IsolationError(f"{service} 컨테이너 이미지가 고정 digest와 다릅니다")
        ports = (item.get("NetworkSettings") or {}).get("Ports") or {}
        published = {key: value for key, value in ports.items() if value}
        if published != {spec["container_port"]: [{"HostIp": "127.0.0.1", "HostPort": str(spec["port"])}]}:
            raise IsolationError(f"{service} 컨테이너의 포트 게시가 127.0.0.1:{spec['port']} 하나가 아닙니다")
        mounts = item.get("Mounts") or []
        names = {mount.get("Name") for mount in mounts}
        if any(mount.get("Type") != "volume" for mount in mounts) or names != spec["volumes"]:
            raise IsolationError(f"{service} 컨테이너 마운트가 이 프로젝트 전용 볼륨이 아닙니다")
        seen.add(item.get("Id"))
        identity[service] = {"id": item.get("Id"), "image": item.get("Image"), "started_at": state.get("StartedAt"),
                             "volumes": sorted(names)}
    if set(project_ids) != seen:
        raise IsolationError(f"{PROJECT} 프로젝트 컨테이너가 기대한 2개와 다릅니다 ({len(project_ids)}개)")
    return identity


# ---------------------------------------------------------------------------
# 3. 서버 확인 (읽기 전용)
# ---------------------------------------------------------------------------
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


def check_qdrant_auth(http_get=None) -> int:
    if http_get is None:
        import requests
        http_get = requests.get
    status = getattr(http_get(f"{QDRANT_URL}/collections", timeout=5), "status_code", None)
    if status != 401:
        raise IsolationError(f"키 없는 Qdrant 요청이 401이 아닙니다 (HTTP {status})")
    return status


def check_neo4j_auth(driver_factory=None) -> bool:
    from neo4j.exceptions import AuthError
    factory = driver_factory or (lambda password: make_neo4j_driver(password))
    try:
        driver = factory(secrets.token_hex(16))
    except AuthError:
        return True
    driver.close()
    raise IsolationError("틀린 비밀번호로 Neo4j에 접속됨 (인증 꺼짐)")


def inspect_servers(qdrant_client, neo4j_runner, graph) -> dict[str, Any]:
    """Qdrant·Neo4j 버전, 이름, 데이터 상태 (읽기 전용)."""
    from database.neo4j import load_graph as lg
    version = getattr(qdrant_client.info(), "version", None)
    if version != EXPECTED_QDRANT_VERSION:
        raise IsolationError(f"Qdrant {version} ≠ {EXPECTED_QDRANT_VERSION}")
    collections = sorted(c.name for c in qdrant_client.get_collections().collections)
    aliases = sorted(a.alias_name for a in qdrant_client.get_aliases().aliases)
    rows = [row for row in neo4j_runner.read_rows("CALL dbms.components() YIELD name, versions, edition "
                                                  "RETURN name, versions, edition") if row.get("name") == "Neo4j Kernel"]
    neo4j = {"version": (rows[0].get("versions") or [None])[0] if rows else None,
             "edition": str(rows[0].get("edition", "")).lower() if rows else None}
    if neo4j != {"version": EXPECTED_NEO4J_VERSION, "edition": "community"}:
        raise IsolationError(f"Neo4j {neo4j} ≠ {EXPECTED_NEO4J_VERSION} community")
    database = neo4j_runner.read_rows("CALL db.info() YIELD name RETURN name")[0]["name"]
    if database != NEO4J_DATABASE:
        raise IsolationError(f"Neo4j 기본 DB가 {database}입니다")
    totals = dict(neo4j_runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
    graph_state = neo4j_graph_state(graph, neo4j_runner)
    if not collections and not aliases:
        qdrant_state = "empty"
    elif collections == [restore.COLLECTION] and not aliases:
        qdrant_state = "restored_collection"
    else:
        qdrant_state = "foreign_collections"
    if (graph_state, qdrant_state) == ("empty", "empty"):
        state = "empty"
    elif (graph_state, qdrant_state) == ("complete", "restored_collection"):
        state = "complete"
    else:
        state = f"neo4j_{graph_state}+qdrant_{qdrant_state}"
    return {"qdrant": {"version": version, "collections": collections, "aliases": aliases, "state": qdrant_state},
            "neo4j": {**neo4j, "database": database, "totals": totals, "state": graph_state}, "state": state}


def neo4j_graph_state(graph, runner) -> str:
    """기존 preflight_database로 판정 (읽기 전용): empty / partial / complete / foreign_data / schema_conflict."""
    from database.neo4j import load_graph as lg
    try:
        return lg.preflight_database(graph, runner)["state"]
    except lg.DatabaseNotEmptyError:
        try:
            return lg.preflight_database(graph, runner, allow_existing=True)["state"]
        except lg.PreflightError as error:
            return (error.args[1] if len(error.args) > 1 else {}).get("state", "foreign_data")
    except lg.PreflightError as error:
        return (error.args[1] if len(error.args) > 1 else {}).get("state", "schema_conflict")


def fingerprint(identity: dict[str, Any], servers: dict[str, Any]) -> str:
    return restore.digest({"project": PROJECT, "qdrant_url": QDRANT_URL, "neo4j_uri": NEO4J_URI,
                           "docker": identity, "servers": servers})


# ---------------------------------------------------------------------------
# 4. 소스·번들
# ---------------------------------------------------------------------------
def check_source_and_bundle(repo: Path, bundle: Path) -> dict[str, Any]:
    if Path(repo).resolve() != ROOT:
        raise IsolationError("--repo는 이 래퍼가 들어 있는 소스 트리여야 합니다 (다른 소스와 섞지 않음)")
    package, snapshot = restore.check_bundle(bundle)
    tool = Path(bundle) / "restore_final_v140.py"
    if not tool.is_file() or tool.read_bytes() != (ROOT / "scripts" / "restore_final_v140.py").read_bytes():
        raise IsolationError("번들의 restore_final_v140.py가 소스 트리의 것과 다릅니다")
    return {"package": {k: package.get(k) for k in ("collection", "points", "dimensions", "snapshot_sha256",
                                                      "vector_digest", "manifest_lf_sha256", "source_commit")},
            "snapshot": snapshot.name}


# ---------------------------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------------------------
class Context:
    """Docker 확인 → 클라이언트 생성 → 서버 확인을 한곳에 모은다. 테스트는 factory를 바꿔 끼운다."""

    def __init__(self, values, *, docker=run_docker, qdrant_factory=make_qdrant_client,
                 neo4j_factory=make_neo4j_driver, runner_factory=None, http_get=None, graph=None):
        self.values, self.docker, self.http_get = values, docker, http_get
        self.qdrant_factory, self.neo4j_factory = qdrant_factory, neo4j_factory
        self.runner_factory, self.graph = runner_factory, graph

    def snapshot(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        from database.neo4j import load_graph as lg
        identity = verify_docker_ownership(self.docker)
        check_qdrant_auth(self.http_get)
        check_neo4j_auth(self.neo4j_factory)
        client = self.qdrant_factory(self.values)
        driver = self.neo4j_factory(self.values["neo4j"])
        try:
            runner = (self.runner_factory or (lambda d: lg.Neo4jDriverRunner(d, database=NEO4J_DATABASE)))(driver)
            servers = inspect_servers(client, runner, self.graph or lg.read_graph_input())
        finally:
            client.close()
            driver.close()
        return identity, servers, fingerprint(identity, servers)


def post_verify(repo: Path, bundle: Path, ctx: Context, restore_fn) -> dict[str, Any]:
    """읽기 전용: restore(verify_only) + verify_full_index + Docker 식별 재확인."""
    from database import full_index as fi, handoff_data as hd
    from argparse import Namespace
    verified = restore_fn(Namespace(repo=Path(repo), bundle=Path(bundle), apply=False, verify_only=True))
    client = ctx.qdrant_factory(ctx.values)
    try:
        documents = hd.load_documents(verify_hash=True)
        chunks, _ = fi.build_config_chunks("A_B2_V140", documents)
        slices = fi.verify_full_index("A_B2_V140", client=client, chunks=chunks, documents=documents)
    finally:
        client.close()
    ok = (verified.get("status") == "PASS" and slices.get("passed") is True
          and verified.get("cross_db", {}).get("qdrant_points") == restore.SNAPSHOT_POINTS
          and verified.get("graph_totals") == {"nodes": restore.EXPECTED[4], "relationships": restore.EXPECTED[5]})
    return {"ok": ok, "restore_verify_only": verified, "verify_full_index": slices}


def run(mode: str, repo: Path, bundle: Path, ctx: Context, *, confirm_write: bool = False,
        expected_fingerprint: str | None = None, restore_fn=None) -> dict[str, Any]:
    from argparse import Namespace
    restore_fn = restore_fn or restore.restore
    require_target_urls()
    source = check_source_and_bundle(repo, bundle)
    identity, servers, current = ctx.snapshot()
    base = {"project": PROJECT, "qdrant_url": QDRANT_URL, "neo4j_uri": NEO4J_URI, "bundle": source,
            "docker": identity, "servers": servers, "fingerprint": current}
    if mode == "preflight":
        if servers["state"] != "empty":
            raise IsolationError(f"검증 대상 DB가 비어 있지 않습니다: {servers['state']}")
        with isolated_environment(repo, ctx.values) as wanted:
            result = restore_fn(Namespace(repo=Path(repo), bundle=Path(bundle), apply=False, verify_only=False))
            require_environment_unchanged(wanted)
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
        with isolated_environment(repo, ctx.values) as wanted:
            result = restore_fn(Namespace(repo=Path(repo), bundle=Path(bundle), apply=True, verify_only=False))
            require_environment_unchanged(wanted)
            if verify_docker_ownership(ctx.docker) != identity:
                raise IsolationError("복원 중 대상 컨테이너가 바뀌었습니다")
            if result.get("status") != "PASS":
                raise IsolationError(f"restore 결과가 {result.get('status')}입니다 (부분 자산 보존, 재실행 금지)")
            verification = post_verify(repo, bundle, ctx, restore_fn)
        if not verification["ok"]:
            raise IsolationError("복원 후 검증 실패 (부분 자산 보존)")
        return {"mode": mode, **base, "restore_apply": result, "post_verify": verification}
    if mode == "verify-only":
        if servers["state"] != "complete":
            raise IsolationError(f"검증 대상 Neo4j 상태가 {servers['state']}입니다 (complete여야 함)")
        with isolated_environment(repo, ctx.values) as wanted:
            verification = post_verify(repo, bundle, ctx, restore_fn)
            require_environment_unchanged(wanted)
        if not verification["ok"]:
            raise IsolationError("verify-only 검증 실패")
        return {"mode": mode, "db_writes": 0, **base, "post_verify": verification}
    raise IsolationError(f"알 수 없는 모드: {mode}")


def main(argv: list[str] | None = None, ctx_factory=Context) -> int:
    parser = argparse.ArgumentParser(description="r2 번들 독립 복원 보호 래퍼 (기본 읽기 전용 preflight)")
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
        os.environ["LIFEPOLICY_DATASET_DIR"] = str(ROOT / RELEASE_DIR)   # database 모듈 import 전에 고정
        output = run(name, args.repo, args.bundle, ctx_factory(read_secrets()), confirm_write=args.confirm_write,
                     expected_fingerprint=args.expect_fingerprint)
        result = {"status": "PASS", **{k: v for k, v in output.items() if k != "status"}}
    except (IsolationError, ValueError) as error:
        result = {"status": "FAILED", "mode": name, "error_type": type(error).__name__, "reason": str(error)[:800],
                  "action": "자동 재실행하지 않는다. 대상 DB는 보존한다. 원인 확인 후 새 프로젝트·새 볼륨에서 다시 시작한다."}
    except Exception as error:   # 접속·인증 오류: 설정값이 섞일 수 있어 종류만 남긴다
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
