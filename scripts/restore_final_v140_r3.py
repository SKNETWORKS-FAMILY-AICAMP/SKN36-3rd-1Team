"""Issue #105: r3 번들(B2·B3 snapshot + 최신 그래프)을 NEW 로컬 DB에만 복원한다 — 기본 preflight(쓰기 없음).

r2 복원 도구(scripts/restore_final_v140.py, 컬렉션 1개)는 바꾸지 않는다. r2 번들은 동봉된 r2 소스로 계속 복원한다.
이 도구는 package.json schema_version 2(컬렉션 여러 개)만 받는다.

순서
  1. check_bundle (fail-closed): SHA256SUMS가 번들 구성 파일 6개(snapshot 2·source ZIP·compose·복원 도구·package.json)를
     정확히 나열하고 모두 일치, schema 2, source_commit(40자리)·source_archive(source-<sha12>.zip) 형식과 짝,
     컬렉션 B2·B3 이름·차원·Point 수·vector digest = R3_PINS, 각 snapshot SHA-256 (R3_PINS 미확정이면 거부)
  2. check_repo_matches_source: --repo의 복원 도구·기준값 모듈·r2 복원 도구·manifest가 번들 source ZIP과 같은지
     (줄바꿈 차이만 허용), package의 Neo4j release_files = --repo manifest의 neo4j/* SHA-256
  3. 소스 릴리스: manifest LF SHA-256·릴리스 체크섬·문서 해시·컬렉션별 청크 수·그래프 건수 = R3_PINS
  3. 대상 DB: loopback·자격 증명 없는 주소만. Qdrant 컬렉션 0개·alias 0개·Neo4j 노드/관계 0개일 때만 쓴다
     (기존 데이터 삭제 없음)
  4. --apply (임시 컬렉션 복원 + alias 공개, #111 P1-3):
     a. 컬렉션마다 임시 이름 <정식>__tmp_<secrets.token_hex(16)>를 만든다. snapshot은 임시 이름에만 올린다
        (upload_snapshot이 임시 이름 형식이 아니면 HTTP 호출 전에 거부 → 정식 이름 업로드 0회).
     b. 업로드마다 직전·직후 컬렉션 집합·alias 매핑 재검사, 다음 업로드 전 이미 올린 임시 컬렉션 Point 수 확인
     c. 임시 컬렉션마다 차원·거리·Point 수·전체 벡터 digest·payload 정렬과 protected digest
     d. Neo4j load_graph(verify=True) → 임시 컬렉션마다 verify_cross_db + verify_full_index(원문 슬라이스)
     e. c·d가 모두 통과한 뒤에만: 공개 직전 검사(정식 이름 컬렉션·alias 없음) → 한 요청으로 정식 alias 2개 생성
        → 공개 직후 전체 매핑 검사 → alias 경유 Point 수·전체 벡터 digest 재확인 → PASS
  5. --verify-only: 쓰기 없이 published 상태(임시 컬렉션 2개 + 정식 alias 2개가 정확히 짝)에서만 c·d·e 마지막 재확인

실패 시 자동 재실행·삭제·rollback은 없다. 새 DB에 부분 자산(임시 컬렉션, 경우에 따라 일부 alias)이 남으며
새 프로젝트·새 볼륨에서 다시 시작한다.

덮어쓰기 안전 경계 (#111 P1-3, Qdrant v1.12.4 서버 소스 기준 — 실서버 실행 검증은 아직 없음)
  - Qdrant snapshot 업로드 복구는 같은 이름(또는 그 이름의 alias가 가리키는) 컬렉션이 있으면 덮어쓰고, 없으면 만든다.
    "없을 때만 복원" 옵션은 없다. 그래서 정식 이름에는 snapshot을 절대 올리지 않는다.
  - 정식 이름의 기존 "컬렉션"과의 충돌: alias 생성은 서버가 컬렉션 쓰기 잠금 안에서 같은 이름 컬렉션이 있으면
    거부한다(update_aliases의 validate_collection_not_exists). 기존 컬렉션 데이터는 바뀌지 않고 이 도구는 FAILED.
  - 정식 이름의 기존 "alias"와의 충돌 (해결되지 않은 위험): 서버의 alias 생성은 같은 이름 alias가 있으면 오류 없이
    대상을 바꾼다(alias_mapping insert). 이 도구는 공개 직전에 alias가 없음을 확인할 뿐이므로, 그 검사와 alias 생성
    요청 사이(요청 1회 왕복)에 외부 alias가 생기면 그 alias는 우리 임시 컬렉션으로 바뀌고 직후 검사도 통과한다.
    Point 데이터는 지워지지 않지만 외부 alias 매핑은 사라진다. 현재 API로 완전히 막을 수 없다.
  - 부분 공개: 서버는 alias 작업을 하나씩 적용·저장하므로(요청 전체 원자성 없음) B2만 공개되고 B3가 실패할 수 있다.
    이 도구는 FAILED + partial_publish + 현재 alias 매핑을 보고하고 alias를 지우거나 바꾸지 않는다.
  - 임시 이름의 잔여 TOCTOU: 직전 검사 뒤 업로드가 서버에서 존재를 확인하기 전(전송·해시·압축 해제 동안)에
    누군가 같은 임시 이름을 만들면 덮어쓴다. 128비트 난수 이름이라 실행 밖에서 알 수 없지만, 서버가 막는 것이 아니라
    확률로만 막는다. 서버 확인 뒤에 생기면 서버 생성이 AlreadyExists로 실패해 덮어쓰지 않는다.
  - scripts/restore_r3_isolated.py의 전용 Compose 프로젝트·볼륨·포트·인증·Docker 소유 확인·fingerprint는 다른 쓰기
    주체가 접근할 가능성을 낮출 뿐 잠금(배타 제어)이 아니다. 같은 전용 키를 가진 동시 작업은 막지 못한다.
    이 스크립트를 래퍼 없이 직접 실행하면 그 보호도 없다.

실행 (압축을 푼 r3 소스 트리에서):
    python scripts/restore_final_v140_r3.py --repo <소스> --bundle <번들 폴더>              # preflight
    python scripts/restore_final_v140_r3.py --repo <소스> --bundle <번들 폴더> --apply
    python scripts/restore_final_v140_r3.py --repo <소스> --bundle <번들 폴더> --verify-only
접속 정보는 QDRANT_URL·QDRANT_API_KEY·NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD 환경 변수로 받는다.
격리 검증 환경에서는 scripts/restore_r3_isolated.py가 고정 주소·전용 인증으로 settings를 직접 넘긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import zipfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
if (_HERE.parents[1] / "scripts" / "restore_profiles_v140.py").is_file() and str(_HERE.parents[1]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[1]))

SETTING_KEYS = ("QDRANT_URL", "QDRANT_API_KEY", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
UPLOAD_TIMEOUT_SECONDS = 300
RESTORE_MEMBER = "restore_final_v140_r3.py"
COMPOSE_MEMBER = "compose.yaml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
TEMP_INFIX = "__tmp_"   # 임시 컬렉션: <정식 이름>__tmp_<32자리 hex> (26+6+32=64자 = store 실험 컬렉션 이름 상한)
TEMP_TOKEN_BYTES = 16                           # secrets.token_hex(16) = 128비트
TEMP_NAME = re.compile(r"^(?P<canonical>.+)" + re.escape(TEMP_INFIX) + r"[0-9a-f]{32}$")


class PublishError(ValueError):
    """alias 공개 단계 실패. 보고서에 partial_publish와 실패 시점의 alias 매핑을 남긴다 (alias 삭제·변경 없음)."""

    def __init__(self, message: str, *, partial_publish: bool, aliases: dict[str, str]):
        super().__init__(message)
        self.partial_publish, self.aliases = partial_publish, dict(aliases)


def _deps():
    """r2 도구(표준 라이브러리 함수)와 기준값 모듈. --repo가 sys.path에 들어간 뒤에 부른다."""
    from scripts import restore_final_v140 as r2
    from scripts import restore_profiles_v140 as profiles
    return r2, profiles


def check_bundle(folder, pins: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Path]]:
    r2, profiles = _deps()
    pins = profiles.require_finalized(pins)
    folder = Path(folder).resolve()
    verified: set[Path] = set()
    listed: list[str] = []
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or r2.file_sha(path) != expected:
            raise ValueError("Bundle path/checksum mismatch")
        verified.add(path)
        listed.append(name)
    if (folder / "package.json") not in verified:
        raise ValueError("Package manifest must be checksummed")
    package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    if package.get("schema_version") != profiles.SCHEMA_VERSION or package.get("bundle") != profiles.BUNDLE_NAME:
        raise ValueError("Unexpected bundle schema (r3 restore accepts schema_version 2 only)")
    commit, archive = package.get("source_commit"), package.get("source_archive")
    if (not isinstance(commit, str) or not FULL_SHA.match(commit)
            or archive != f"source-{commit[:12]}.zip" or package.get("restore_tool") != RESTORE_MEMBER):
        raise ValueError("Bundle source_commit/source_archive/restore_tool metadata mismatch")
    members = sorted([*(profiles.R3_COLLECTIONS[k]["snapshot_member"] for k in profiles.COLLECTION_KEYS),
                      archive, COMPOSE_MEMBER, RESTORE_MEMBER, "package.json"])
    if sorted(listed) != members:
        raise ValueError("SHA256SUMS must list exactly the r3 bundle members (snapshots, source, compose, tool, package)")
    if (package.get("manifest_lf_sha256") != pins["manifest_lf_sha256"]
            or package.get("release_counts") != pins["release_counts"]):
        raise ValueError("Bundle release manifest/counts differ from r3 pins")
    entries = package.get("collections")
    if not isinstance(entries, dict) or set(entries) != set(profiles.COLLECTION_KEYS):
        raise ValueError("Bundle must contain exactly the r3 collections (b2, b3)")
    snapshots: dict[str, Path] = {}
    for key in profiles.COLLECTION_KEYS:
        spec, entry, pinned = profiles.collection_spec(key), entries[key], pins["collections"][key]
        if (entry.get("collection") != spec["collection"] or entry.get("config") != spec["config"]
                or entry.get("dimensions") != spec["dimensions"] or entry.get("points") != pinned["points"]
                or entry.get("vector_digest") != pinned["vector_digest"]
                or entry.get("snapshot") != spec["snapshot_member"]):
            raise ValueError(f"Unexpected bundle collection/dimension/count/digest: {key}")
        snapshot = (folder / entry["snapshot"]).resolve()
        if (snapshot not in verified or not snapshot.is_relative_to(folder)
                or r2.file_sha(snapshot) != entry.get("snapshot_sha256")):
            raise ValueError(f"Snapshot checksum mismatch: {key}")
        snapshots[key] = snapshot
    return package, snapshots


def check_repo_matches_source(repo, folder, package: dict[str, Any]) -> dict[str, Any]:
    """
    --repo가 번들 source ZIP에서 나온 소스인지 확인한다 (fail-closed). 줄바꿈 차이만 허용한다.
    - 복원 도구·기준값 모듈·r2 복원 도구·manifest 바이트가 source ZIP의 같은 경로와 같아야 한다
    - package의 Neo4j release_files가 --repo manifest의 neo4j/* SHA-256과 같아야 한다
    """
    _r2, profiles = _deps()
    repo, folder = Path(repo).resolve(), Path(folder).resolve()
    with zipfile.ZipFile(folder / package["source_archive"]) as archive:
        names = set(archive.namelist())
        for path in profiles.SOURCE_IDENTITY_FILES:
            local = repo / path
            if path not in names or not local.is_file() or not profiles.same_text_ignoring_eol(
                    archive.read(path), local.read_bytes()):
                raise ValueError(f"--repo differs from the bundle source archive: {path}")
    manifest = json.loads((repo / profiles.RELEASE_DIR / "manifest.json").read_text(encoding="utf-8"))
    neo4j_files = {name: item.get("sha256") for name, item in sorted((manifest.get("files") or {}).items())
                   if name.startswith("neo4j/")}
    if not neo4j_files or (package.get("neo4j") or {}).get("release_files") != neo4j_files:
        raise ValueError("Bundle Neo4j release_files differ from the source manifest")
    return {"source_identity_files": list(profiles.SOURCE_IDENTITY_FILES), "neo4j_release_files": len(neo4j_files)}


def scan(client, collection: str) -> dict[str, dict[str, Any]]:
    records, offset = {}, None
    while True:
        points, offset = client.scroll(collection, offset=offset, limit=128, with_payload=True, with_vectors=True)
        records.update({str(p.id): {"payload": p.payload or {}, "vector": p.vector} for p in points})
        if offset is None:
            return records


def read_settings(environ=None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    settings = {key: environ.get(key, "") for key in SETTING_KEYS}
    if not all(settings.values()):
        raise ValueError("Required DB environment variable is missing")
    return settings


def temp_collection_name(canonical: str, token: str | None = None) -> str:
    """정식 이름에서 이번 실행 전용 임시 이름을 만든다. token 기본값은 secrets.token_hex(16) (예측 불가)."""
    token = secrets.token_hex(TEMP_TOKEN_BYTES) if token is None else token
    name = f"{canonical}{TEMP_INFIX}{token}"
    match = TEMP_NAME.fullmatch(name)
    if not match or match.group("canonical") != canonical:
        raise ValueError(f"Invalid temporary collection token for {canonical}")
    return name


def upload_snapshot(settings: dict[str, str], collection: str, snapshot: Path, sha256: str, http_post=None) -> None:
    # 정식 이름(또는 임시 형식이 아닌 이름)에는 snapshot을 올리지 않는다: 업로드 복구는 같은 이름을 덮어쓰기 때문
    if not TEMP_NAME.fullmatch(collection):
        raise ValueError(f"Refusing snapshot upload to a non-temporary collection name: {collection}")
    if http_post is None:
        import requests
        http_post = requests.post
    with Path(snapshot).open("rb") as stream:
        response = http_post(settings["QDRANT_URL"].rstrip("/") + f"/collections/{collection}/snapshots/upload",
                             params={"wait": "true", "priority": "snapshot", "checksum": sha256},
                             headers={"api-key": settings["QDRANT_API_KEY"]},
                             files={"snapshot": (Path(snapshot).name, stream)}, timeout=UPLOAD_TIMEOUT_SECONDS)
    status = getattr(response, "status_code", None)
    if status != 200:
        raise ValueError(f"Snapshot upload failed: HTTP {status} ({collection})")


def alias_mapping(client) -> dict[str, str]:
    """서버 alias 매핑 {alias 이름: 실제 컬렉션 이름} (읽기 전용)."""
    return {a.alias_name: a.collection_name for a in client.get_aliases().aliases}


def require_collection_set(client, expected: set[str], stage: str, aliases: dict[str, str] | None = None) -> None:
    """서버 컬렉션 집합 = expected, alias 매핑 = aliases(기본 없음)여야 한다. 다르면 ValueError (삭제·재시도 없음).

    검사 시점의 상태만 본다. 이 검사와 다음 서버 요청 사이의 경쟁은 막지 못한다 (모듈 docstring "덮어쓰기 안전 경계").
    """
    aliases = {} if aliases is None else aliases
    current = {c.name for c in client.get_collections().collections}
    if current != expected:
        raise ValueError(f"Qdrant collection set mismatch {stage}: unexpected={sorted(current - expected)}, "
                         f"missing={sorted(expected - current)}; restore stopped; partial state preserved; "
                         "inspect before retry")
    mapping = alias_mapping(client)
    if mapping != aliases:
        raise ValueError(f"Qdrant alias mapping mismatch {stage}: current={dict(sorted(mapping.items()))}, "
                         f"expected={dict(sorted(aliases.items()))}; restore stopped; partial state preserved; "
                         "inspect before retry")


def published_state(collections, aliases: dict[str, str], canonical: dict[str, str]) -> dict[str, str] | None:
    """published 상태면 {키: 임시 컬렉션}, 아니면 None.

    published = 실제 컬렉션이 정확히 임시 컬렉션 키마다 1개(<정식>__tmp_<32 hex>)이고,
    alias 매핑이 정확히 {정식 이름: 그 임시 컬렉션}인 상태. 다른 컬렉션·alias가 하나라도 있으면 아니다.
    """
    physical = {key: aliases.get(name) for key, name in canonical.items()}
    if any(value is None for value in physical.values()) or set(aliases) != set(canonical.values()):
        return None
    for key, temp in physical.items():
        match = TEMP_NAME.fullmatch(temp)
        if not match or match.group("canonical") != canonical[key]:
            return None
    if set(collections) != set(physical.values()) or len(set(physical.values())) != len(physical):
        return None
    return physical


def publish_aliases(client, physical: dict[str, str], canonical: dict[str, str]) -> dict[str, str]:
    """정식 alias를 한 요청으로 만든다. 공개 직전 검사는 호출한 쪽에서 끝낸 상태여야 한다.

    서버 보장(v1.12.4 소스): 같은 이름 "컬렉션"이 있으면 create_alias를 거부한다(기존 데이터 불변).
    보장 안 됨: 같은 이름 "alias"가 있으면 서버는 오류 없이 대상을 바꾼다 → 공개 직전 검사와 이 요청 사이에 생긴
    외부 alias는 덮어써지고 감지되지 않는다. 작업은 하나씩 저장되므로 실패 시 앞의 alias만 남을 수 있다(부분 공개).
    실패해도 alias를 지우거나 바꾸지 않는다.
    """
    from qdrant_client import models
    expected = {canonical[key]: physical[key] for key in canonical}
    operations = [models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=physical[key],
                                                                              alias_name=canonical[key]))
                  for key in canonical]
    try:
        client.update_collection_aliases(change_aliases_operations=operations)
    except Exception as error:   # 서버 거부(같은 이름 컬렉션 등) 포함. 원문 응답은 싣지 않는다
        mapping = alias_mapping(client)
        ours = {name: target for name, target in mapping.items() if expected.get(name) == target}
        raise PublishError(f"Alias publish failed ({type(error).__name__}); published {sorted(ours)} of "
                           f"{sorted(expected)}; restore stopped; partial state preserved; inspect before retry",
                           partial_publish=0 < len(ours) < len(expected), aliases=mapping) from None
    return expected


def verify_published_aliases(client, r2, physical: dict[str, str], canonical: dict[str, str],
                             entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    """공개 직후(또는 verify-only): 전체 매핑이 정확한지, alias 경유 Point 수·전체 벡터 digest가 확정값과 같은지."""
    expected = {canonical[key]: physical[key] for key in canonical}
    collections = {c.name for c in client.get_collections().collections}
    mapping = alias_mapping(client)
    if collections != set(physical.values()) or mapping != expected:
        ours = {name: target for name, target in mapping.items() if expected.get(name) == target}
        raise PublishError(f"Published alias/collection state mismatch: collections={sorted(collections)}, "
                           f"aliases={dict(sorted(mapping.items()))}; restore stopped; partial state preserved; "
                           "inspect before retry", partial_publish=0 < len(ours) < len(expected), aliases=mapping)
    for key, name in canonical.items():
        if (client.count(name, exact=True).count != entries[key]["points"]
                or r2.vector_digest(scan(client, name)) != entries[key]["vector_digest"]):
            raise PublishError(f"Alias {name} does not serve the verified collection data; restore stopped; "
                               "partial state preserved; inspect before retry", partial_publish=False, aliases=mapping)
    return mapping


def restore(args, settings: dict[str, str] | None = None, pins: dict[str, Any] | None = None,
            http_post=None, client_factory=None, driver_factory=None, token_factory=None) -> dict[str, Any]:
    """token_factory: 임시 이름 토큰 생성기 (기본 secrets.token_hex(16)). 테스트만 고정값을 넘긴다."""
    repo = Path(args.repo).resolve()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    r2, profiles = _deps()
    os.environ["LIFEPOLICY_DATASET_DIR"] = str(repo / profiles.RELEASE_DIR)
    from database import full_index as fi, handoff_data as hd, hybrid_search as hs
    from database.qdrant import store
    from database.neo4j import load_graph as lg

    pins = profiles.require_finalized(pins)
    package, snapshots = check_bundle(args.bundle, pins)
    source_check = check_repo_matches_source(repo, args.bundle, package)
    if fi.manifest_sha256() != package["manifest_lf_sha256"]:
        raise ValueError("Source release manifest differs from bundle")
    if any(not row["matched"] for row in hd.verify_manifest_checksums()):
        raise ValueError("Source release checksum mismatch")
    documents = hd.load_documents(verify_hash=True)
    document_hashes = r2.build_document_hashes(documents)
    metadata = hd.load_payload_metadata()
    chunks: dict[str, list[dict[str, Any]]] = {}
    for key in profiles.COLLECTION_KEYS:
        spec = profiles.collection_spec(key)
        chunks[key], _ = fi.build_config_chunks(spec["config"], documents)
        missing_ids = sorted({c["document_id"] for c in chunks[key]} - document_hashes.keys())
        if missing_ids:
            raise ValueError(f"Missing content_hash for chunk document_id(s): {missing_ids}")
        if len(chunks[key]) != pins["collections"][key]["points"]:
            raise ValueError(f"Latest source chunk count mismatch: {key}")
    graph = lg.read_graph_input()
    validation = lg.validate_graph_input(graph)
    counts = {"policies": len({d["policy_id"] for d in documents}), "documents": len(documents),
              "release_chunks": len(hd.load_payload_chunks()),
              "neo4j_nodes": sum(validation.node_counts.values()),
              "neo4j_relationships": sum(validation.relationship_counts.values()),
              "requires_condition": validation.relationship_counts.get("REQUIRES_CONDITION", 0)}
    if not validation.ok or counts != pins["release_counts"]:
        raise ValueError("Latest source counts/integrity mismatch")
    settings = dict(settings) if settings is not None else read_settings()
    if not all(settings.get(key) for key in SETTING_KEYS):
        raise ValueError("Required DB setting is missing")
    r2.require_local_url(settings["QDRANT_URL"])
    r2.require_local_url(settings["NEO4J_URI"])
    if client_factory is None:
        from qdrant_client import QdrantClient
        client_factory = lambda s: QdrantClient(url=s["QDRANT_URL"], api_key=s["QDRANT_API_KEY"], timeout=30)  # noqa: E731
    if driver_factory is None:
        from neo4j import GraphDatabase
        driver_factory = lambda s: GraphDatabase.driver(s["NEO4J_URI"], auth=(s["NEO4J_USER"], s["NEO4J_PASSWORD"]),  # noqa: E731
                                                        connection_timeout=5)
    client = client_factory(settings)
    driver = driver_factory(settings)
    try:
        driver.verify_connectivity()
        runner = lg.Neo4jDriverRunner(driver)
        canonical = {key: package["collections"][key]["collection"] for key in profiles.COLLECTION_KEYS}
        entries = {key: package["collections"][key] for key in profiles.COLLECTION_KEYS}
        if not args.verify_only:
            r2.require_empty([c.name for c in client.get_collections().collections],
                             runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0])
            if alias_mapping(client):
                raise ValueError("Restore requires no Qdrant aliases; existing aliases are never changed")
            if not args.apply:
                return {"status": "PREFLIGHT_PASS_NO_WRITES", "counts": counts,
                        "snapshots": {k: package["collections"][k]["snapshot_sha256"] for k in snapshots}}
            # 두 DB가 비어 있음을 확인한 뒤 첫 쓰기. snapshot은 이번 실행 전용 임시 이름에만 올린다 (정식 이름 0회).
            # 업로드마다 직전·직후 컬렉션 집합·alias 재검사 + 이미 올린 임시 컬렉션 Point 수 확인.
            # 직전 검사와 서버 존재 확인 사이의 임시 이름 경쟁은 확률로만 막는다 (모듈 docstring "덮어쓰기 안전 경계").
            token_factory = token_factory or (lambda: secrets.token_hex(TEMP_TOKEN_BYTES))
            physical = {key: temp_collection_name(canonical[key], token_factory()) for key in profiles.COLLECTION_KEYS}
            if len(set(physical.values())) != len(physical):
                raise ValueError("Temporary collection names must be distinct")
            for name in physical.values():            # 검증 함수들(store 경유)이 받는 이름인지 쓰기 전에 확인 (64자 상한 등)
                store.assert_experiment_collection(name)
            restored: list[str] = []                    # 이 실행이 이미 올린 컬렉션 키 (순서대로)
            for key in profiles.COLLECTION_KEYS:
                entry, target = entries[key], physical[key]
                done_names = {physical[k] for k in restored}
                require_collection_set(client, done_names, f"before uploading {target}")   # 임시 이름 충돌도 여기서 거부
                for done in restored:
                    if client.count(physical[done], exact=True).count != entries[done]["points"]:
                        raise ValueError(f"Restored collection point count changed before uploading {target}: "
                                         f"{physical[done]}")
                upload_snapshot(settings, target, snapshots[key], entry["snapshot_sha256"], http_post)
                restored.append(key)
                require_collection_set(client, done_names | {target}, f"after uploading {target}")
            require_collection_set(client, set(physical.values()), "before verification")
        else:
            physical = published_state({c.name for c in client.get_collections().collections}, alias_mapping(client),
                                       canonical)
            if physical is None:
                raise ValueError("verify-only requires the published state (two temporary collections and exactly "
                                 "their two public aliases)")
        qdrant: dict[str, Any] = {}
        for key in profiles.COLLECTION_KEYS:
            spec, entry, target = profiles.collection_spec(key), entries[key], physical[key]
            info = client.get_collection(target)
            params = info.config.params.vectors
            if (params.size != spec["dimensions"] or str(params.distance).split(".")[-1].lower() != "cosine"
                    or info.points_count != entry["points"]):
                raise ValueError(f"Collection size/distance/count mismatch: {key}")
            records = scan(client, target)
            expected = {}
            for chunk in chunks[key]:
                old = records.get(chunk["point_id"], {}).get("payload", {})
                run = {"embedding_model": spec["model"], "embedding_dim": spec["dimensions"],
                       "embedding_input_rule": fi.EMBEDDING_INPUT_RULE, "index_run_id": old.get("index_run_id")}
                expected[chunk["point_id"]] = store.build_point_payload(
                    chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
                    content_hash=document_hashes[chunk["document_id"]], run_info=run)
            changes, field_counts = r2.alignment_plan(records, expected)
            before = r2.protected_digest(records)
            vectors = r2.vector_digest(records)
            if vectors != entry["vector_digest"]:
                raise ValueError(f"Full vector digest differs from verified source: {key}")
            if args.verify_only and changes:
                raise ValueError(f"Current payload needs alignment; verify-only refuses writes: {key}")
            for point_id, payload in changes:
                client.set_payload(target, payload=payload, points=[point_id], wait=True)
            after = scan(client, target) if changes else records
            remaining, _ = r2.alignment_plan(after, expected)
            if remaining or r2.protected_digest(after) != before:
                raise ValueError(f"Payload alignment/invariance verification failed: {key}")
            qdrant[key] = {"collection": spec["collection"], "physical_collection": target, "points": len(after),
                           "vector_digest": vectors, "metadata_points_updated": len(changes),
                           "metadata_field_updates": field_counts}
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
        for key in profiles.COLLECTION_KEYS:
            spec = profiles.collection_spec(key)
            links = hs.verify_cross_db(client=client, runner=runner, collection=physical[key])
            r2.require_cross_db_pass(links)
            slices = fi.verify_full_index(spec["config"], client=client, chunks=chunks[key], documents=documents,
                                          collection=physical[key])
            if slices.get("passed") is not True:
                raise ValueError(f"verify_full_index failed: {key}")
            qdrant[key]["cross_db"] = {k: links[k] for k in ("status", "qdrant_points", "qdrant_count",
                                                             "all_points_read", "summary", "failures")}
            qdrant[key]["verify_full_index_passed"] = True
        # 여기까지(임시 컬렉션 검증·Neo4j·Cross-DB·원문 슬라이스) 모두 통과한 뒤에만 정식 alias를 공개한다.
        if not args.verify_only:
            require_collection_set(client, set(physical.values()), "before publishing aliases")  # 정식 컬렉션·alias 없음
            publish_aliases(client, physical, canonical)
        aliases = verify_published_aliases(client, r2, physical, canonical, entries)
        return {"status": "PASS", "bundle": package["bundle"], "counts": counts, "collections": qdrant,
                "source_check": source_check, "physical_collections": physical, "aliases": aliases,
                "partial_publish": False,
                "all_vectors_and_protected_payload_unchanged": True,
                "graph_totals": runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0],
                "full_graph_properties_verified": verification["ok"], "graph_verification_source": verification_source,
                "manifest_lf_sha256": fi.manifest_sha256(), "embedding_calls": 0, "verify_only": args.verify_only}
    finally:
        client.close()
        driver.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r3 번들 복원 (B2·B3 + Neo4j, 기본 preflight)")
    parser.add_argument("--repo", type=Path, default=_HERE.parents[1])
    parser.add_argument("--bundle", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("Existing report will not be overwritten")
    try:
        result = restore(args)
    except Exception as error:
        # 네트워크 오류 원문·설정값은 출력하지 않는다.
        result = {"status": "FAILED", "error_type": type(error).__name__,
                  "action": "Stop. Preserve partial NEW DB assets; never delete or overwrite existing data."}
        if isinstance(error, ValueError):
            result["reason"] = str(error)
        if isinstance(error, PublishError):              # alias 공개 단계 실패: 부분 공개 여부·현재 매핑 기록
            result["partial_publish"], result["aliases"] = error.partial_publish, error.aliases
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] in {"PASS", "PREFLIGHT_PASS_NO_WRITES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
