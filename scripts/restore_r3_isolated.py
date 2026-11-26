"""Issue #105: r3 번들을 r3 verify 프로젝트(현재 프로필 issue105-r3d-verify)에만 복원하는 보호 래퍼 — 기본 읽기 전용 preflight.
대상 프로젝트·포트·인증 변수는 restore_profiles_v140.R3_ENVIRONMENTS["verify"]에서 읽는다. 이전 verify(issue105-r3-verify)는
PRESERVED_R3_ENVIRONMENTS["verify-6d0450f"]로 보존되며 그 포트·인증 변수·프로젝트는 거부된다.

복원·검증 본체는 scripts/restore_final_v140_r3.py의 restore()를 같은 프로세스에서 호출한다.
r2 래퍼(restore_r2_isolated)와 달리 환경 변수를 덮어쓰지 않고, 고정 주소·전용 인증을 settings로 직접 넘긴다.

보호 장치 (r2 래퍼와 같은 기준, Docker·서버 확인 함수는 r2 래퍼 것을 프로젝트 값만 바꿔 재사용)
  - 주소 고정: Qdrant http://127.0.0.1:<verify qdrant_port>, Neo4j bolt://127.0.0.1:<verify bolt_port>.
    공유·r1·r2·r3 build 포트는 거부한다.
  - 인증: ISSUE105_R3D_VERIFY_QDRANT_API_KEY / ISSUE105_R3D_VERIFY_NEO4J_PASSWORD만(프로필 값). 원본·공유·r2·r3 build·
    이전 verify 값과 같으면 거부
  - Docker 소유 확인: 두 포트의 컨테이너가 정확히 이 프로젝트의 qdrant·neo4j 서비스, 실행 중, 고정 이미지 digest,
    127.0.0.1 바인딩만, 이 프로젝트 이름의 볼륨만. 프로젝트에 다른 컨테이너가 있어도 거부
  - 서버: Qdrant 1.12.4·키 없는 요청 401·컬렉션 0·별칭 0, Neo4j 5.26.32 community·틀린 비밀번호 거부·노드/관계 0
  - 복원 결과(#111 P1-3): 실제 컬렉션은 임시 이름 2개(<정식>__tmp_<32 hex>), 정식 이름은 그 임시 컬렉션을
    가리키는 alias 2개다. 이 짝이 정확할 때만 Qdrant 상태가 "published"이고, Neo4j complete와 함께일 때만
    verify-only를 허용한다. 정식 이름 컬렉션·부분 alias·다른 컬렉션/alias가 있으면 거부한다.
  - 이 보호 장치들은 다른 쓰기 주체가 접근할 가능성을 낮출 뿐 잠금이 아니다 (restore_final_v140_r3 "덮어쓰기 안전 경계")
  - 실행 잠금(#111 P1-C, RunLock): --apply는 DB 연결 전에 같은 프로젝트·Qdrant 포트의 OS 파일 잠금을 얻고 끝까지
    보유한다. 이 래퍼끼리의 동시 apply만 막는다 (잠금을 따르지 않는 프로그램·직접 API 호출은 막지 못함)
  - fingerprint: Docker 식별 정보 + 서버 상태. --apply는 직전 preflight fingerprint와 같을 때만, 복원 후 식별 정보 재확인
  - 소스·번들: --repo는 이 래퍼가 들어 있는 소스 트리, 번들의 restore_final_v140_r3.py가 그 트리 것과 바이트 동일
  - R3_PINS가 확정되지 않으면 check_bundle 단계에서 거부된다
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
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
CANONICAL = {key: spec["collection"] for key, spec in profiles.R3_COLLECTIONS.items()}
IsolationError = r2w.IsolationError


def inspect_servers(qdrant_client, neo4j_runner, graph) -> dict[str, Any]:
    """r2 래퍼의 inspect_servers(버전·Neo4j 상태)를 그대로 쓰고, Qdrant 상태만 r3 C안 기준으로 다시 판정한다 (읽기 전용).

    Qdrant: empty(컬렉션·alias 0) / published(임시 2개 + 정식 alias 2개 정확히 짝) / not_published(그 밖의 모든 상태)
    전체: empty+empty → empty, Neo4j complete + published → complete, 그 밖 → neo4j_<상태>+qdrant_<상태>
    """
    servers = r2w.inspect_servers(qdrant_client, neo4j_runner, graph, expected_collections=EXPECTED_COLLECTIONS)
    collections = servers["qdrant"]["collections"]
    aliases = restore_r3.alias_mapping(qdrant_client)
    if not collections and not aliases:
        qdrant_state = "empty"
    elif restore_r3.published_state(collections, aliases, CANONICAL) is not None:
        qdrant_state = "published"
    else:
        qdrant_state = "not_published"
    graph_state = servers["neo4j"]["state"]
    if (graph_state, qdrant_state) == ("empty", "empty"):
        state = "empty"
    elif (graph_state, qdrant_state) == ("complete", "published"):
        state = "complete"
    else:
        state = f"neo4j_{graph_state}+qdrant_{qdrant_state}"
    servers["qdrant"] = {**servers["qdrant"], "aliases": dict(sorted(aliases.items())), "state": qdrant_state}
    servers["state"] = state
    return servers


GUARD_FIELDS = ("collections", "aliases", "points", "vector_digests", "payload_digests", "protected_digests",
                "projected_payload_digests", "neo4j_totals")      # 서버 관측값과 비교하는 항목
GUARD_INPUTS = ("alignment_changes",)                              # 비교하지 않고 관측 계산에만 쓰는 항목 (필수)


def projected_payload_digest(records: dict[str, dict[str, Any]], changes, name: str) -> str:
    """정렬 변경을 지금 서버 payload에 적용했을 때의 전체 payload digest (#111 P1-B 보완, 읽기 전용 계산)."""
    try:
        projected = restore_r3.project_alignment(records, changes)
    except ValueError as error:
        raise IsolationError(f"정렬 변경이 허용 범위를 벗어납니다: {name} ({error})") from error
    return restore_r3.payload_digest(restore, projected)


def observe_stage_state(ctx: "Context", expected: dict[str, Any]) -> dict[str, Any]:
    """guard 검사용 현재 서버 상태를 새 연결로 읽는다 (읽기 전용). expected에 적힌 컬렉션만 Point 수·digest를 계산한다.
    연결은 호출마다 새로 만들고, 예외가 나도 finally에서 닫는다."""
    from database.neo4j import load_graph as lg
    changes = expected["alignment_changes"]
    if set(changes) != set(expected["projected_payload_digests"]):
        raise IsolationError("write guard 기대 상태가 불완전합니다 (정렬 변경 목록과 기대 digest의 컬렉션이 다름)")
    client = ctx.qdrant_factory(ctx.values)
    try:
        driver = ctx.neo4j_factory(ctx.values["neo4j"])
        try:
            runner = (ctx.runner_factory or (lambda d: lg.Neo4jDriverRunner(d, database=NEO4J_DATABASE)))(driver)
            totals = dict(runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
        finally:
            driver.close()
        names = (set(expected["vector_digests"]) | set(expected["payload_digests"]) | set(expected["protected_digests"])
                 | set(expected["projected_payload_digests"]))
        records = {name: restore_r3.scan(client, name) for name in sorted(names)}
        return {"collections": sorted(c.name for c in client.get_collections().collections),
                "aliases": restore_r3.alias_mapping(client),
                "points": {name: client.count(name, exact=True).count for name in expected["points"]},
                "vector_digests": {n: restore.vector_digest(records[n]) for n in expected["vector_digests"]},
                "payload_digests": {n: restore_r3.payload_digest(restore, records[n]) for n in expected["payload_digests"]},
                "protected_digests": {n: restore.protected_digest(records[n]) for n in expected["protected_digests"]},
                "projected_payload_digests": {n: projected_payload_digest(records[n], changes[n], n)
                                              for n in expected["projected_payload_digests"]},
                "neo4j_totals": {"nodes": totals.get("nodes"), "relationships": totals.get("relationships")}}
    finally:
        client.close()


class IsolatedWriteGuard(restore_r3.WriteGuard):
    """격리 래퍼가 apply 검사(고정 주소·전용 인증·Docker 소유·fingerprint·빈 서버)를 통과한 뒤 만드는 쓰기 허가.

    #111 P1-A: restore()의 진입 검증(require_write_guard)을 통과시킨다.
    #111 P1-B: restore()가 쓰기 직전마다 check(stage, expected)를 부른다. 검사 순서 (하나라도 어긋나면 IsolationError·
    ValueError로 멈춘다 → 그 쓰기와 이후 쓰기 0회):
      1. armed·run_id 형식 재검증(require_write_guard) + 발급 때 run_id와 같은지
      2. lock_checker: 없거나, 예외를 던지거나, True가 아니면 거부 (run()의 apply는 실제 RunLock.is_held를 넘긴다, P1-C)
      3. Docker 컨테이너 ID·시작 시각 등 식별 정보 = 발급 때 값 (ctx·identity가 없으면 거부)
      4. 첫 쓰기 직전(require_preflight_fingerprint)에는 fingerprint = preflight 값
      5. Qdrant·Neo4j 현재 상태(새 연결로 읽기) = expected의 GUARD_FIELDS 전부. 정렬 직전에는 이번 정렬 변경을
         지금 서버 payload에 적용한 결과의 전체 digest = 번들 payload_digest (정렬 허용 필드 밖 차이는 전부 거부)
         조회 실패는 IsolationError("서버 상태 조회 실패")로 바꿔 멈춘다
    check와 실제 쓰기 사이의 경쟁(TOCTOU)은 막지 못한다. 보안 경계가 아니다(WriteGuard 참고).
    """

    def __init__(self, run_id: str, *, ctx: "Context | None" = None, identity: dict[str, Any] | None = None,
                 preflight_fingerprint: str | None = None, lock_checker=None):
        self.run_id = run_id
        self.armed = True
        self._issued_run_id = run_id
        self._ctx, self._identity = ctx, identity
        self._preflight_fingerprint = preflight_fingerprint
        self._lock_checker = lock_checker

    def check(self, stage: str, expected: dict[str, Any]) -> None:
        restore_r3.require_write_guard(self)
        if self.run_id != self._issued_run_id:
            raise IsolationError(f"write guard run_id가 발급 때와 다릅니다 → 쓰지 않음 ({stage})")
        if not callable(self._lock_checker):
            raise IsolationError(f"실행 잠금 확인 함수(lock_checker)가 없습니다 → 쓰지 않음 ({stage})")
        try:
            locked = self._lock_checker()
        except Exception as error:
            raise IsolationError(f"실행 잠금 확인 중 오류({type(error).__name__}) → 쓰지 않음 ({stage})") from error
        if locked is not True:
            raise IsolationError(f"실행 잠금을 보유하고 있지 않습니다 → 쓰지 않음 ({stage})")
        if self._ctx is None or self._identity is None:
            raise IsolationError(f"write guard에 대상 컨텍스트·식별 정보가 없습니다 → 쓰지 않음 ({stage})")
        if not isinstance(expected, dict) or any(field not in expected for field in GUARD_FIELDS + GUARD_INPUTS):
            raise IsolationError(f"write guard 기대 상태가 불완전합니다 → 쓰지 않음 ({stage})")
        if self._ctx.identity() != self._identity:
            raise IsolationError(f"대상 컨테이너 식별 정보(ID·시작 시각)가 바뀌었습니다 → 쓰지 않음 ({stage})")
        if expected.get("require_preflight_fingerprint") is not False:
            if not self._preflight_fingerprint or self._ctx.snapshot()[2] != self._preflight_fingerprint:
                raise IsolationError(f"fingerprint가 preflight와 다릅니다 → 쓰지 않음 ({stage})")
        try:
            observed = observe_stage_state(self._ctx, expected)
        except IsolationError as error:
            raise IsolationError(f"{error} → 쓰지 않음 ({stage})") from error
        except Exception as error:                     # 연결·조회 실패(컬렉션 없음 등)도 명확한 거부로 남긴다
            raise IsolationError(f"쓰기 직전 서버 상태 조회 실패({type(error).__name__}) → 쓰지 않음 ({stage})") from error
        mismatched = [field for field in GUARD_FIELDS if observed[field] != expected[field]]
        if mismatched:
            raise IsolationError(f"쓰기 직전 서버 상태가 기대와 다릅니다 {mismatched} → 쓰지 않음 ({stage})")


class RunLockError(IsolationError):
    """실행 잠금을 얻지 못함 (다른 실행이 보유 중이거나 이 프로세스가 이미 보유). 대기·재시도·삭제·강제 해제 없음."""


def default_lock_path(project: str = PROJECT, qdrant_port: int = ENV["qdrant_port"], environ=None) -> Path:
    """같은 검증 프로젝트·Qdrant 포트는 항상 같은 경로 (#111 P1-C).
    Windows: %LOCALAPPDATA%\\issue105-r3\\locks\\<project>-<port>.lock / 그 밖: $XDG_STATE_HOME(기본 ~/.local/state)/issue105-r3/locks/...
    """
    environ = os.environ if environ is None else environ
    if os.name == "nt":
        base = environ.get("LOCALAPPDATA")
        if not base:
            raise IsolationError("LOCALAPPDATA가 없어 실행 잠금 경로를 정할 수 없습니다")
    else:
        base = environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "issue105-r3" / "locks" / f"{project}-{qdrant_port}.lock"


def _os_lock(fd: int) -> None:
    """0번 바이트 비차단 배타 잠금. 이미 잠겨 있으면 즉시 OSError."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)          # LK_NBLCK: 재시도 없이 실패 (LK_LOCK은 10회 재시도)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def read_lock_owner(path: Path) -> dict[str, Any] | None:
    """잠금 파일 1번 바이트 이후의 소유자 JSON (참고 정보). 없거나 깨졌으면 None.
    소유자 정보가 있다고 잠금이 보유된 것은 아니다 — 보유 여부는 OS 잠금으로만 판단한다."""
    try:
        with open(path, "rb") as stream:
            stream.seek(1)
            data = stream.read(4096)
        owner = json.loads(data.decode("utf-8")) if data else None
    except (OSError, ValueError):
        return None
    return owner if isinstance(owner, dict) else None


class RunLock:
    """이 복원 래퍼의 apply 동시 실행을 막는 OS 파일 잠금 (#111 P1-C). 보안 경계가 아니다.

    - 잠금: 파일을 os.open(O_RDWR|O_CREAT)로 열고 0번 바이트를 비차단 배타 잠금
      (Windows msvcrt.locking LK_NBLCK = LockFile 바이트 범위 잠금, POSIX fcntl.flock LOCK_EX|LOCK_NB)
    - 소유자 정보: 잠금을 얻은 뒤 1번 바이트 이후에 JSON(run_id·PID·시작 시각·프로젝트·포트·컨테이너 ID)을 쓴다.
      실패한 쪽은 이 정보를 읽어 보고만 한다. 잠금 파일이 있는 것과 OS 잠금을 보유한 것은 다르다.
    - 같은 프로세스 재진입: 프로세스 안 등록부로 먼저 거부한다 (OS 잠금도 두 번째 핸들에서 실패하지만 POSIX flock은
      같은 열린 파일 설명자를 공유하면 성공할 수 있어 등록부로 막는다).
    - 사용 중이면 대기·재시도·파일 삭제·강제 해제를 하지 않는다. 해제는 release()(finally)에서 unlock·close만 하고
      파일은 지우지 않는다.
    - 보유자가 비정상 종료하면 OS가 핸들을 닫으며 잠금을 푼다 (Windows 문서상 해제가 지연될 수 있음).
    - 보호하지 못함: 잠금을 따르지 않는 다른 프로그램·직접 API 호출·같은 인증값을 가진 다른 프로세스,
      다른 PC·다른 사용자(경로가 다름), 네트워크 파일 시스템의 잠금 신뢰성.
    """

    _held: set[str] = set()              # 이 프로세스가 보유 중인 잠금 경로 (재진입 거부용)
    _registry_guard = threading.Lock()

    def __init__(self, path: Path, owner: dict[str, Any]):
        self.path = Path(path)
        self.owner = dict(owner)
        self._key = os.path.normcase(os.path.abspath(self.path))
        self._fd: int | None = None

    def acquire(self) -> "RunLock":
        with RunLock._registry_guard:
            if self._key in RunLock._held:
                raise RunLockError(f"이 프로세스가 이미 실행 잠금을 보유하고 있습니다 (재진입 거부): {self.path}")
            RunLock._held.add(self._key)
        fd, locked = None, False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
            try:
                _os_lock(fd)
            except OSError as error:
                owner = read_lock_owner(self.path)
                raise RunLockError(f"다른 실행이 실행 잠금을 보유하고 있습니다 (대기·삭제·강제 해제 없음): {self.path} "
                                   f"owner={json.dumps(owner, ensure_ascii=False, sort_keys=True)}") from error
            locked = True
            os.lseek(fd, 1, os.SEEK_SET)
            os.ftruncate(fd, 1)
            os.write(fd, json.dumps(self.owner, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        except BaseException:
            if fd is not None:
                try:
                    if locked:
                        _os_unlock(fd)
                finally:
                    os.close(fd)
            with RunLock._registry_guard:
                RunLock._held.discard(self._key)
            raise
        self._fd = fd
        return self

    def is_held(self) -> bool:
        """이 객체가 지금 OS 잠금을 보유하고, 그 핸들이 여전히 잠금 경로의 파일인지 (P1-B lock_checker 계약).
        해제했거나 얻지 못했으면 False. 확인 중 오류는 그대로 던진다 (guard가 거부로 처리)."""
        if self._fd is None:
            return False
        try:
            current = os.stat(self.path)
        except FileNotFoundError:
            return False
        held = os.fstat(self._fd)
        return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _os_unlock(fd)
        finally:
            os.close(fd)
            with RunLock._registry_guard:
                RunLock._held.discard(self._key)

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def lock_owner(run_id: str, identity: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone
    return {"run_id": run_id, "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(),
            "project": PROJECT, "qdrant_port": ENV["qdrant_port"],
            "containers": {service: (identity.get(service) or {}).get("id") for service in sorted(identity)}}


def classify_aliases(aliases: dict[str, str], physical: dict[str, str]) -> dict[str, dict[str, Any]]:
    """alias 분류 (#111 P1-D). 정식 alias마다 ours(이번 실행의 그 키 임시 컬렉션) / absent / foreign_target(그 밖의 대상),
    정식 이름이 아닌 alias는 unexpected."""
    result = {}
    for key, name in sorted(CANONICAL.items()):
        target = aliases.get(name)
        ours = physical.get(key)
        result[name] = {"target": target, "class": "absent" if target is None
                        else "ours" if ours is not None and target == ours else "foreign_target"}
    for name, target in sorted(aliases.items()):
        if name not in result:
            result[name] = {"target": target, "class": "unexpected"}
    return result


def classify_collections(collections: list[str], physical: dict[str, str]) -> dict[str, Any]:
    """컬렉션 분류 (#111 P1-D): ours_temp(이번 실행의 임시 컬렉션) / canonical_name(정식 이름의 실제 컬렉션) /
    other_temp(다른 실행의 임시 이름 형식) / unexpected. ours_absent = 이번 실행 임시 이름 중 서버에 없는 것."""
    ours = set(physical.values())
    canonical = set(CANONICAL.values())
    classes = {}
    for name in sorted(collections):
        classes[name] = ("ours_temp" if name in ours else "canonical_name" if name in canonical
                         else "other_temp" if restore_r3.TEMP_NAME.fullmatch(name) else "unexpected")
    return {"collections": classes, "ours_absent": sorted(ours - set(collections))}


def inspect_failure_state(ctx: "Context", run_id: str | None, progress: dict[str, Any] | None,
                          failed_stage: str) -> dict[str, Any]:
    """apply 실패 직후 실제 남은 상태를 새 연결로 읽기만 해서 보고서 항목을 만든다 (#111 P1-D).

    삭제·alias 변경·Neo4j 초기화·재시도는 하지 않는다. 항목마다 따로 조회하고, 조회하지 못한 항목은 None과
    unavailable[항목] = 예외 종류 이름으로 남긴다 (예외 메시지는 싣지 않음 → 인증값 노출 방지).
    partial_publish는 alias의 실제 상태로만 정한다 (이번 실행 정식 alias가 일부만 있음). 적재 진행은 load_progress에 따로.
    """
    from database.neo4j import load_graph as lg
    progress = progress or {}
    physical = dict(progress.get("physical_collections") or {})
    unavailable: dict[str, str] = {}
    collections = aliases = totals = None
    try:
        client = ctx.qdrant_factory(ctx.values)
    except Exception as error:
        client = None
        unavailable["qdrant_connection"] = type(error).__name__
    if client is not None:
        try:
            try:
                collections = sorted(c.name for c in client.get_collections().collections)
            except Exception as error:
                unavailable["qdrant_collections"] = type(error).__name__
            try:
                aliases = dict(sorted(restore_r3.alias_mapping(client).items()))
            except Exception as error:
                unavailable["qdrant_aliases"] = type(error).__name__
        finally:
            try:
                client.close()
            except Exception as error:
                unavailable["qdrant_close"] = type(error).__name__
    try:
        driver = ctx.neo4j_factory(ctx.values["neo4j"])
    except Exception as error:
        driver = None
        unavailable["neo4j_connection"] = type(error).__name__
    if driver is not None:
        try:
            runner = (ctx.runner_factory or (lambda d: lg.Neo4jDriverRunner(d, database=NEO4J_DATABASE)))(driver)
            row = dict(runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
            totals = {"nodes": row["nodes"], "relationships": row["relationships"]}
        except Exception as error:
            unavailable["neo4j_totals"] = type(error).__name__
        finally:
            try:
                driver.close()
            except Exception as error:
                unavailable["neo4j_close"] = type(error).__name__
    alias_classes = None if aliases is None else classify_aliases(aliases, physical)
    published = None if alias_classes is None else sorted(
        name for name in CANONICAL.values() if alias_classes[name]["class"] == "ours")
    if published is None:
        publish_state, partial = "unknown", None
    else:
        publish_state = ("complete" if len(published) == len(CANONICAL) else "partial" if published else "none")
        partial = publish_state == "partial"
    return {"run_id": run_id, "failed_stage": failed_stage,
            "physical_collections": physical or None,
            "partial_publish": partial, "publish_state": publish_state, "published_aliases": published,
            "aliases": aliases, "alias_classification": alias_classes,
            "collection_classification": None if collections is None else classify_collections(collections, physical),
            "neo4j_totals": totals,
            "neo4j_data_present": None if totals is None else (totals["nodes"], totals["relationships"]) != (0, 0),
            "load_progress": {k: progress.get(k) for k in ("uploads_completed", "payload_alignment_writes",
                                                           "neo4j_load_returned")} if progress else None,
            "unavailable": unavailable,
            "automatic_actions": "none (자동 롤백·삭제·재시도 없음, Runbook 수동 절차로만 복구)"}


def attach_failure_report(error: BaseException, ctx: "Context", run_id: str | None, stage: str, result) -> None:
    """apply 실패 예외에 재조회 보고서를 붙인다 (#111 P1-D M1). 이 함수는 예외를 밖으로 내보내지 않는다.

    - stage == "restore_apply": 본체가 붙인 진행 기록을 쓴다. 기록이 dict가 아니거나 stage가 없어도 최초 예외를 가리지 않는다.
    - 그 밖(apply가 끝난 뒤 실패): 이번 실행의 임시 이름은 apply 결과에서 가져온다 (뒤 verify-only 기록은 쓰지 않음).
    - 재조회 중 예상하지 못한 예외: 상태를 모름(None·unknown)으로 두고 inspection_error = 예외 종류 이름만 남긴다.
    """
    failed_stage = stage
    try:
        if stage == "restore_apply":
            progress = getattr(error, "restore_progress", None)
            progress = progress if isinstance(progress, dict) else None
            inner = progress.get("stage") if progress else None
            failed_stage = f"restore:{inner}" if isinstance(inner, str) and inner else stage
        else:
            physical = result.get("physical_collections") if isinstance(result, dict) else None
            progress = {"stage": "apply_returned", "physical_collections": physical}
        report = inspect_failure_state(ctx, run_id, progress, failed_stage)
    except Exception as inspection:
        report = {"run_id": run_id, "failed_stage": failed_stage, "partial_publish": None, "publish_state": "unknown",
                  "aliases": None, "alias_classification": None, "collection_classification": None,
                  "neo4j_totals": None, "neo4j_data_present": None,
                  "inspection_error": type(inspection).__name__,
                  "automatic_actions": "none (자동 롤백·삭제·재시도 없음, Runbook 수동 절차로만 복구)"}
    try:
        error.failure_report = report
    except AttributeError:                 # 속성을 붙일 수 없는 예외: 보고서 없이 최초 예외만 전파
        pass


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
    # 공백·제어 문자가 섞인 값은 거부한다 (#111 P1-D M2): HTTP 헤더 검사 오류 메시지 등에 값이 실려 나가는 경로를 막는다.
    # 오류에는 변수 이름만 싣는다.
    malformed = [name for name, key in ((QDRANT_KEY_ENV, "qdrant"), (NEO4J_PASSWORD_ENV, "neo4j"))
                 if any(ch.isspace() or not ch.isprintable() for ch in values[key])]
    if malformed:
        raise IsolationError(f"r3 검증용 인증값에 공백·제어 문자가 있습니다 (변수 이름: {malformed})")
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
        """Docker 소유 확인. 기존 IsolationError(소유·포트·이미지 불일치, docker 명령 실패 코드)는 그대로 둔다.
        그 밖의 예외(docker 실행 파일 없음·실행 불가 등 OSError 포함)는 종류 이름과 고정 설명만 담은 IsolationError로
        바꾼다 (#111 C-2: FAILED 보고서에 이유를 남기고, 원본 메시지·환경 값은 싣지 않음)."""
        try:
            return r2w.verify_docker_ownership(self.docker, project=PROJECT, services=SERVICES)
        except IsolationError:
            raise
        except Exception as error:
            raise IsolationError(f"Docker 상태 조회 실패({type(error).__name__}): docker 실행 파일과 Docker 서비스 "
                                 "상태를 확인하세요 → 쓰지 않음") from None

    def snapshot(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        from database.neo4j import load_graph as lg
        identity = self.identity()
        r2w.check_qdrant_auth(self.http_get, url=QDRANT_URL)
        r2w.check_neo4j_auth(self.neo4j_factory)
        client = self.qdrant_factory(self.values)
        driver = None
        try:
            # #111 C-1: driver 생성도 try 안 — 실패해도 이미 만든 Qdrant client를 닫는다. 예외는 그대로 전파.
            driver = self.neo4j_factory(self.values["neo4j"])
            runner = (self.runner_factory or (lambda d: lg.Neo4jDriverRunner(d, database=NEO4J_DATABASE)))(driver)
            servers = inspect_servers(client, runner, self.graph or lg.read_graph_input())
        finally:
            try:
                client.close()
            finally:
                if driver is not None:          # 만들어진 연결만 닫는다
                    driver.close()
        return identity, servers, fingerprint(identity, servers)


def run(mode: str, repo: Path, bundle: Path, ctx: Context, *, confirm_write: bool = False,
        expected_fingerprint: str | None = None, restore_fn=None, pins: dict[str, Any] | None = None,
        lock_path: Path | None = None) -> dict:
    """apply는 DB 연결(ctx.snapshot) 전에 실행 잠금(RunLock)을 얻고, 끝날 때까지(사후 verify-only 포함) 보유한다
    (#111 P1-C). 잠금 소유자 정보의 컨테이너 ID는 Docker 조회(ctx.identity)로만 얻는다 — DB 접속 없음.
    guard의 lock_checker는 그 잠금의 is_held다. 정상·예외 종료 모두 finally에서 해제하고 파일은 지우지 않는다.
    lock_path: 테스트만 임시 경로를 넘긴다 (기본 default_lock_path()). preflight·verify-only는 잠그지 않는다."""
    restore_fn = restore_fn or restore_r3.restore
    require_target_urls()
    source = check_source_and_bundle(repo, bundle, pins)
    run_lock = run_id = locked_identity = None
    if mode == "apply":
        if not confirm_write or not expected_fingerprint:
            raise IsolationError("--apply 에는 --confirm-write 와 --expect-fingerprint 가 필요합니다")
        run_id = secrets.token_hex(16)
        locked_identity = ctx.identity()
        run_lock = RunLock(lock_path or default_lock_path(), lock_owner(run_id, locked_identity)).acquire()
    try:
        return _run_steps(mode, repo, bundle, ctx, source, restore_fn, pins, expected_fingerprint,
                          run_lock, run_id, locked_identity)
    finally:
        if run_lock is not None:
            run_lock.release()


def _run_steps(mode, repo, bundle, ctx, source, restore_fn, pins, expected_fingerprint, run_lock, run_id,
               locked_identity) -> dict:
    identity, servers, current = ctx.snapshot()
    if run_lock is not None and identity != locked_identity:
        raise IsolationError("실행 잠금을 얻은 뒤 대상 컨테이너가 바뀌었습니다 → 쓰지 않음")
    settings = settings_for(ctx.values)
    base = {"project": PROJECT, "qdrant_url": QDRANT_URL, "neo4j_uri": NEO4J_URI, "bundle": source,
            "docker": identity, "servers": servers, "fingerprint": current}

    def call(apply: bool, verify_only: bool, write_guard=None) -> dict[str, Any]:
        kwargs = {"write_guard": write_guard} if apply else {}
        return restore_fn(Namespace(repo=Path(repo), bundle=Path(bundle), apply=apply, verify_only=verify_only),
                          settings=settings, pins=pins, **kwargs)

    if mode == "preflight":
        if servers["state"] != "empty":
            raise IsolationError(f"검증 대상 DB가 비어 있지 않습니다: {servers['state']}")
        result = call(False, False)
        if result.get("status") != "PREFLIGHT_PASS_NO_WRITES":
            raise IsolationError(f"restore preflight 결과가 {result.get('status')}입니다")
        return {"mode": mode, "db_writes": 0, **base, "restore_preflight": result}
    if mode == "apply":
        if current != expected_fingerprint:
            raise IsolationError("fingerprint가 preflight와 다릅니다 (대상 컨테이너·서버가 바뀜) → 쓰지 않음")
        if servers["state"] != "empty":
            raise IsolationError(f"검증 대상 DB가 비어 있지 않습니다: {servers['state']}")
        # 위 검사를 모두 통과한 뒤에만 쓰기 허가 객체를 만든다 (#111 P1-A). 쓰기 직전마다 check (P1-B),
        # 잠금 확인은 실제 RunLock 보유 여부 (P1-C). run_id는 잠금 소유자 정보와 같은 값이다.
        guard = IsolatedWriteGuard(run_id, ctx=ctx, identity=identity, preflight_fingerprint=current,
                                   lock_checker=run_lock.is_held)
        base = {**base, "run_lock": {"lock_file": run_lock.path.name, "owner": run_lock.owner}}
        stage, result = "restore_apply", None
        try:
            result = call(True, False, guard)
            stage = "post_apply_identity_check"
            if ctx.identity() != identity:
                raise IsolationError("복원 중 대상 컨테이너가 바뀌었습니다")
            stage = "apply_result_check"
            if result.get("status") != "PASS":
                raise IsolationError(f"restore 결과가 {result.get('status')}입니다 (부분 자산 보존, 재실행 금지)")
            stage = "post_apply_verify_only"
            verification = call(False, True)
            if verification.get("status") != "PASS" or ctx.identity() != identity:
                raise IsolationError("복원 후 verify-only 검증 실패 (부분 자산 보존)")
        except Exception as error:
            # #111 P1-D: 쓰기가 시작된 뒤의 실패는 잠금을 보유한 채 실제 남은 상태를 읽기 전용으로 재조회해 붙인다.
            # 롤백·삭제·재시도는 하지 않는다. 최초 예외(종류·메시지)는 재조회 결과와 상관없이 그대로 다시 던진다.
            attach_failure_report(error, ctx, run_id, stage, result)
            raise
        return {"mode": mode, **base, "restore_apply": result, "post_verify": verification}
    if mode == "verify-only":
        if servers["state"] != "complete":
            raise IsolationError(f"검증 대상 상태가 {servers['state']}입니다 "
                                 "(complete여야 함: Neo4j complete + Qdrant published)")
        verification = call(False, True)
        if verification.get("status") != "PASS":
            raise IsolationError("verify-only 검증 실패")
        return {"mode": mode, "db_writes": 0, **base, "post_verify": verification}
    raise IsolationError(f"알 수 없는 모드: {mode}")


REDACTED = "[REDACTED]"


def known_secret_values(environ=None) -> list[str]:
    """보고서에서 가릴 인증값: 이 래퍼의 전용 인증 변수 + 재사용 검사 대상 변수의 현재 값 (비어 있지 않은 것만)."""
    environ = os.environ if environ is None else environ
    names = (QDRANT_KEY_ENV, NEO4J_PASSWORD_ENV, *FOREIGN_SECRET_ENVS)
    return sorted({environ.get(name) for name in names if environ.get(name)}, key=len, reverse=True)


def secret_forms(secret_values) -> list[str]:
    """인증값이 메시지에 나타날 수 있는 모양: 원문, str repr 이스케이프, UTF-8 bytes repr 이스케이프 (긴 것부터)."""
    forms = set()
    for value in secret_values:
        forms.update({value, repr(value)[1:-1], repr(value.encode("utf-8"))[2:-1]})
    return sorted((form for form in forms if form), key=len, reverse=True)


def redact(text: str, secret_values) -> str:
    """알려진 인증값을 secret_forms의 모든 모양까지 [REDACTED]로 바꾼다 (#111 P1-D M2).
    긴 모양부터 바꿔 한 값이 다른 값의 일부일 때도 남지 않게 한다."""
    for form in secret_forms(secret_values):
        text = text.replace(form, REDACTED)
    return text


def safe_reason(error: BaseException, secret_values) -> str:
    """FAILED 보고서의 reason: 알려진 인증값을 가린 뒤 800자로 자른다. 가린 결과에 어떤 모양으로든 인증값이
    남으면(가림을 보장할 수 없음) 원문 대신 예외 종류와 고정 설명만 쓴다."""
    text = redact(str(error), secret_values)
    if any(form in text for form in secret_forms(secret_values)):
        return f"{type(error).__name__}: 메시지에 인증값이 포함되어 원문을 기록하지 않음"
    return text[:800]


def failure_fields(error: BaseException) -> dict[str, Any]:
    """FAILED 보고서에 더할 항목 (#111 P1-D).
    - PublishError: 실패 시점에 본체가 읽은 partial_publish·aliases (P1-3, 기존 항목)
    - failure_report(apply 쓰기 시작 뒤 실패): 실패 직후 재조회한 run_id·단계·분류·Neo4j 건수 등. 이것이 있으면
      partial_publish·aliases는 재조회 값으로 쓰고, 실패 시점 값은 *_at_failure로 따로 남긴다."""
    fields: dict[str, Any] = {}
    publish = isinstance(error, restore_r3.PublishError)
    report = getattr(error, "failure_report", None)
    if publish and not report:
        fields.update(partial_publish=error.partial_publish, aliases=error.aliases)
    if report:
        if publish:
            fields.update(partial_publish_at_failure=error.partial_publish, aliases_at_failure=error.aliases)
        fields.update(report)
    if isinstance(error, restore_r3.PayloadIndexError):  # #111 P1-E: 컬렉션·필드별 인덱스 결과(누락·잘못된 타입)
        fields["payload_index_report"] = error.report
    return fields


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
    if args.report and profiles.is_preserved_output(args.report):
        parser.error("보존 자산 폴더(#104 r3, .cache/issue105-r3) 안에는 새 파일도 만들지 않습니다")
    name = "apply" if args.apply else "verify-only" if args.verify_only else "preflight"
    try:
        os.environ["LIFEPOLICY_DATASET_DIR"] = str(ROOT / profiles.RELEASE_DIR)   # database 모듈 import 전에 고정
        output = run(name, args.repo, args.bundle, ctx_factory(read_secrets()), confirm_write=args.confirm_write,
                     expected_fingerprint=args.expect_fingerprint)
        result = {"status": "PASS", **{k: v for k, v in output.items() if k != "status"}}
    except (IsolationError, ValueError) as error:
        # 외부 라이브러리의 ValueError(예: HTTP 헤더 검사)는 메시지에 인증값을 담을 수 있다 → 기록 전에 가린다 (M2)
        result = {"status": "FAILED", "mode": name, "error_type": type(error).__name__,
                  "reason": safe_reason(error, known_secret_values()),
                  "action": "자동 재실행하지 않는다. 대상 DB는 보존한다. 원인 확인 후 새 프로젝트·새 볼륨에서 다시 시작한다."}
        result.update(failure_fields(error))
    except Exception as error:
        result = {"status": "FAILED", "mode": name, "error_type": type(error).__name__,
                  "action": "자동 재실행하지 않는다. 컨테이너 상태와 검증용 인증 변수를 확인한다."}
        result.update(failure_fields(error))
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
