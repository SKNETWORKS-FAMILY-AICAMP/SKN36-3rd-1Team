"""
Qdrant 저장·검색 모듈 (실험 컬렉션 전용).

용어 (예시는 가상)
- collection : 벡터를 담는 "표" 하나. 예) exp_b2_qwen3emb06b_v1
               같은 컬렉션 안의 벡터는 모두 같은 차원(1024)과 같은 거리(Cosine)를 쓴다.
- point      : 컬렉션의 "행" 하나 = 청크 1개.  {id, vector, payload} 세 부분으로 되어 있다.
- id         : point의 고유 번호. uuid5(chunk_id)로 고정 → 같은 청크를 다시 넣으면 새로 생기지 않고 덮어쓴다.
- vector     : 임베딩 벡터 (숫자 1024개). 검색할 때 질문 벡터와 비교되는 부분.
- payload    : point에 붙는 메타데이터 dict. 예) {"chunk_id": ..., "policy_id": "POL_001", "text": 원문, ...}
               검색 결과로 돌려받아 "어느 정책·문서의 어떤 원문인가"를 확인하고, 필터 조건에도 쓴다.

안전 규칙
- 이름이 exp_ 로 시작하는 실험 컬렉션만 다룬다. 서비스 컬렉션(lifepolicy_chunks)은 읽기·쓰기 모두 거부한다.
- 컬렉션 삭제 함수는 만들지 않는다. 이미 있는 컬렉션은 설정(차원·거리)만 확인하고, 다르면 멈춘다.
- QdrantClient는 인자로 받는다. 테스트는 메모리 모드(QdrantClient(":memory:")), 실제 실행은 서버 접속.

qdrant-client 패키지는 실제 DB 함수 안에서만 불러온다.
→ 패키지가 설치되지 않아도 이름 검사·payload 생성 함수는 쓰고 테스트할 수 있다.
"""

import os
import re
from pathlib import Path
from typing import Any

from database.chunking import make_point_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
REQUIRED_CONNECTION_VARIABLES = ("QDRANT_URL", "QDRANT_API_KEY")

# ---------------------------------------------------------------------------
# 1. 설정값
# ---------------------------------------------------------------------------
PROTECTED_COLLECTIONS = frozenset({"lifepolicy_chunks"})   # 인계 문서의 서비스 컬렉션 이름
EXPERIMENT_COLLECTION_PATTERN = re.compile(r"^exp_[a-z0-9_]{1,60}$")
DEFAULT_DISTANCE = "Cosine"                                  # 인계 collection_config.json 과 같은 값
UPSERT_BATCH_SIZE = 64

# 모든 point payload에 반드시 있어야 하는 필드 (Neo4j 연결·원문 추적·버전 확인용)
REQUIRED_PAYLOAD_FIELDS = (
    "chunk_id", "point_id", "policy_id", "document_id",          # 연결 키
    "text", "section_title", "source_url",                       # 원문·출처
    "char_start", "char_end", "page_start", "page_end",          # 원문 위치 (없으면 None)
    "chunking_version", "content_hash",                          # 버전 정합성
    "embedding_model", "embedding_dim", "embedding_input_rule", "index_run_id",
    "policy_name", "category", "region", "life_stages", "target_groups",
    "eligibility_attributes", "verification_status",             # 정책 메타데이터 (조인)
)
# 청크에 있으면 그대로 옮기는 선택 필드
OPTIONAL_CHUNK_FIELDS = ("section_group", "section_groups", "boundary_method", "chunk_seq", "merged_from",
                         "overlap_prev_chars", "source_type",
                         "section_titles")   # C1: 합쳐진 섹션 제목 목록 (B1·B2·B3 청크에는 없으므로 기존 payload는 그대로)
# 필터 검색용 payload 인덱스 (실험 컬렉션에만 만든다)
PAYLOAD_INDEX_FIELDS = ("policy_id", "document_id", "chunk_id", "chunking_version",
                        "category", "region", "life_stages", "verification_status")
# 실행 정보(run_info)에 꼭 있어야 하는 값
RUN_INFO_FIELDS = ("embedding_model", "embedding_dim", "embedding_input_rule", "index_run_id")


class CollectionGuardError(ValueError):
    """실험 컬렉션이 아니거나, 기존 컬렉션 설정이 기대와 다를 때."""


# ---------------------------------------------------------------------------
# 2. 컬렉션 이름 보호
# ---------------------------------------------------------------------------
def assert_experiment_collection(name: str) -> str:
    """실험 컬렉션 이름이면 그대로 돌려주고, 아니면 CollectionGuardError. 모든 DB 함수가 맨 먼저 호출한다."""
    if name in PROTECTED_COLLECTIONS:
        raise CollectionGuardError(f"보호된 서비스 컬렉션은 다루지 않습니다: {name}")
    if not EXPERIMENT_COLLECTION_PATTERN.match(name or ""):
        raise CollectionGuardError(f"실험 컬렉션 이름은 exp_ 로 시작하는 소문자·숫자·_ 이어야 합니다: {name!r}")
    return name


# ---------------------------------------------------------------------------
# 3. point 만들기 (DB 없이 동작)
# ---------------------------------------------------------------------------
def build_point_payload(
    chunk: dict[str, Any],
    *,
    policy_meta: dict[str, Any],
    content_hash: str,
    run_info: dict[str, Any],
) -> dict[str, Any]:
    """
    청크 1개의 payload dict를 만든다. 입력 dict는 바꾸지 않는다.
    입력: 청크, 그 정책의 메타데이터(load_policy_metadata 결과의 한 항목), 문서 content_hash, 실행 정보
    반환: REQUIRED_PAYLOAD_FIELDS를 모두 가진 dict (+ 청크에 있던 선택 필드)
    """
    missing_run = [field for field in RUN_INFO_FIELDS if field not in run_info]
    if missing_run:
        raise ValueError(f"run_info에 없는 값: {missing_run}")
    payload = {
        "chunk_id": chunk["chunk_id"],
        "point_id": make_point_id(chunk["chunk_id"]),
        "policy_id": chunk["policy_id"],
        "document_id": chunk["document_id"],
        "text": chunk["text"],
        "section_title": chunk.get("section_title"),
        "source_url": chunk["source_url"],
        "char_start": chunk.get("char_start"),
        "char_end": chunk.get("char_end"),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "chunking_version": chunk["chunking_version"],
        "content_hash": content_hash,
        **{field: run_info[field] for field in RUN_INFO_FIELDS},
        **{field: policy_meta[field] for field in ("policy_name", "category", "region", "life_stages",
                                                    "target_groups", "eligibility_attributes",
                                                    "verification_status")},
    }
    for field in OPTIONAL_CHUNK_FIELDS:
        if field in chunk:
            payload[field] = chunk[field]
    # Issue #21 후보의 정책 단위 선택 필드 (시군구 상세, 자격 판정 보류). v1.3.2 메타데이터에는 없다.
    for field in ("region_detail", "eligibility_review_status"):
        if field in policy_meta:
            payload[field] = policy_meta[field]
    return payload


def build_points(
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    *,
    policy_metadata: dict[str, dict[str, Any]],
    content_hashes: dict[str, str],
    run_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    청크와 벡터를 짝지어 point 목록을 만든다. (아직 DB에 넣지 않는다)
    입력: 청크 목록, 같은 순서의 벡터 목록, policy_id→메타데이터, document_id→content_hash, 실행 정보
    반환: [{"id": point_id, "vector": [...], "payload": {...}}, ...]
    검사: 개수 일치, 차원 = run_info["embedding_dim"], point_id 중복 없음, 정책·문서 메타데이터 존재
    """
    if len(chunks) != len(vectors):
        raise ValueError(f"청크 {len(chunks)}개와 벡터 {len(vectors)}개의 수가 다릅니다.")
    expected_dim = run_info["embedding_dim"]
    points, seen_ids = [], set()
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != expected_dim:
            raise ValueError(f"{chunk['chunk_id']} 벡터 차원 {len(vector)} ≠ {expected_dim}")
        if chunk["policy_id"] not in policy_metadata:
            raise ValueError(f"정책 메타데이터 없음: {chunk['policy_id']} ({chunk['chunk_id']})")
        if chunk["document_id"] not in content_hashes:
            raise ValueError(f"문서 content_hash 없음: {chunk['document_id']}")
        payload = build_point_payload(chunk, policy_meta=policy_metadata[chunk["policy_id"]],
                                      content_hash=content_hashes[chunk["document_id"]], run_info=run_info)
        if payload["point_id"] in seen_ids:
            raise ValueError(f"point_id 중복: {payload['chunk_id']}")
        seen_ids.add(payload["point_id"])
        points.append({"id": payload["point_id"], "vector": vector, "payload": payload})
    return points


# ---------------------------------------------------------------------------
# 4. Qdrant 접속·컬렉션 (qdrant-client 필요)
# ---------------------------------------------------------------------------
def _models():
    """qdrant_client.models 를 필요할 때만 불러온다. 설치되지 않았으면 이유를 알려 준다."""
    try:
        from qdrant_client import models
    except ImportError as error:
        raise ImportError("qdrant-client가 설치되어 있지 않습니다. (설치는 사용자 승인 후 진행)") from error
    return models


class ConnectionSettingsError(ValueError):
    """Qdrant 접속 설정(.env)이 없거나 비어 있을 때. 메시지에 비밀값을 넣지 않는다."""


def load_project_env(env_path: Path | str | None = None) -> bool:
    """
    프로젝트 루트의 .env를 python-dotenv로 읽어 환경 변수에 넣는다.
    - override=False: 이미 설정된 시스템 환경 변수는 덮어쓰지 않는다.
    - 값은 출력하지 않는다.
    반환: .env 파일을 찾아 읽었으면 True, 파일이 없으면 False
    """
    try:
        from dotenv import load_dotenv
    except ImportError as error:
        raise ImportError("python-dotenv가 설치되어 있지 않습니다. (.venv에 설치 후 사용)") from error
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not path.is_file():
        return False
    load_dotenv(dotenv_path=path, override=False)
    return True


def resolve_connection_settings(
    url: str | None = None, api_key: str | None = None, env_path: Path | str | None = None
) -> tuple[str, str]:
    """
    Qdrant 접속 주소와 API 키를 정한다. (서버에는 접속하지 않는다)
    우선순위: 함수 인자 → 이미 설정된 환경 변수 → .env 파일
    빠진 값이 있으면 무엇을 어디에 설정해야 하는지 알려 주는 ConnectionSettingsError. 값 자체는 메시지에 넣지 않는다.
    """
    env_found = load_project_env(env_path)
    url = url or os.environ.get("QDRANT_URL", "").strip()
    api_key = api_key or os.environ.get("QDRANT_API_KEY", "").strip()
    missing = [name for name, value in (("QDRANT_URL", url), ("QDRANT_API_KEY", api_key)) if not value]
    if missing:
        where = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
        hint = (f"{where} 파일이 없습니다. .env.example을 복사해 .env를 만들고 값을 입력하세요."
                if not env_found else f"{where} 에 값이 비어 있습니다.")
        raise ConnectionSettingsError(f"Qdrant 접속 설정 누락: {', '.join(missing)} — {hint}")
    return url, api_key


def get_client(url: str | None = None, api_key: str | None = None, location: str | None = None):
    """
    QdrantClient를 만든다.
    - location=":memory:" → 메모리 모드 (테스트용, 서버 불필요, .env를 읽지 않음)
    - 그 밖에는 url/api_key 인자, 환경 변수, 프로젝트 루트 .env 순서로 QDRANT_URL / QDRANT_API_KEY를 찾는다.
      (resolve_connection_settings 참고. 값은 출력하지 않는다)
    """
    try:
        from qdrant_client import QdrantClient
    except ImportError as error:
        raise ImportError("qdrant-client가 설치되어 있지 않습니다. (설치는 사용자 승인 후 진행)") from error
    if location is not None:
        return QdrantClient(location=location)
    url, api_key = resolve_connection_settings(url, api_key)
    return QdrantClient(url=url, api_key=api_key)


def ensure_collection(client, name: str, dim: int, distance: str = DEFAULT_DISTANCE) -> str:
    """
    실험 컬렉션이 없으면 만들고 "created", 이미 있으면 설정을 확인하고 "exists"를 돌려준다.
    이미 있는 컬렉션의 차원·거리가 다르면 CollectionGuardError (지우거나 바꾸지 않는다).
    새로 만들 때 PAYLOAD_INDEX_FIELDS 인덱스도 만든다.
    """
    assert_experiment_collection(name)
    models = _models()
    wanted_distance = models.Distance(distance)
    if client.collection_exists(name):
        params = client.get_collection(name).config.params.vectors
        if params.size != dim or params.distance != wanted_distance:
            raise CollectionGuardError(
                f"기존 컬렉션 {name} 설정이 다릅니다: size={params.size}, distance={params.distance} "
                f"(기대 size={dim}, distance={wanted_distance})")
        return "exists"
    client.create_collection(collection_name=name,
                             vectors_config=models.VectorParams(size=dim, distance=wanted_distance))
    for field in PAYLOAD_INDEX_FIELDS:
        client.create_payload_index(collection_name=name, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
    return "created"


def upsert_points(client, name: str, points: list[dict[str, Any]], batch_size: int = UPSERT_BATCH_SIZE) -> int:
    """
    point 목록을 저장한다. 같은 id가 이미 있으면 새로 추가하지 않고 덮어쓴다(upsert) → 재실행해도 중복이 없다.
    반환: 보낸 point 수
    """
    assert_experiment_collection(name)
    models = _models()
    for start in range(0, len(points), batch_size):
        batch = [models.PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
                 for point in points[start:start + batch_size]]
        client.upsert(collection_name=name, points=batch, wait=True)
    return len(points)


def _to_filter(filters: dict[str, Any] | None):
    """{"document_id": "DOC_X"} 또는 {"policy_id": ["POL_1", "POL_2"]} → Qdrant Filter (모든 조건 AND)."""
    if not filters:
        return None
    models = _models()
    conditions = []
    for key, value in filters.items():
        match = models.MatchAny(any=list(value)) if isinstance(value, (list, tuple, set)) else models.MatchValue(value=value)
        conditions.append(models.FieldCondition(key=key, match=match))
    return models.Filter(must=conditions)


def count_points(client, name: str, filters: dict[str, Any] | None = None) -> int:
    """컬렉션(또는 필터 조건)에 있는 point 수 (정확히 센다)."""
    assert_experiment_collection(name)
    return client.count(collection_name=name, count_filter=_to_filter(filters), exact=True).count


def list_point_ids(client, name: str, filters: dict[str, Any] | None = None, page_size: int = 256) -> set[str]:
    """컬렉션의 point id 전체 집합. 재적재 전후 비교용."""
    assert_experiment_collection(name)
    ids: set[str] = set()
    offset = None
    while True:
        records, offset = client.scroll(collection_name=name, scroll_filter=_to_filter(filters), limit=page_size,
                                        offset=offset, with_payload=False, with_vectors=False)
        ids.update(str(record.id) for record in records)
        if offset is None:
            return ids


def search_chunks(
    client, name: str, query_vector: list[float], k: int = 5, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """
    질문 벡터와 가장 가까운 청크 k개를 찾는다.
    반환: [{"rank", "score", "point_id", "chunk_id", "policy_id", "document_id", "payload"}, ...]  (점수 높은 순)
    """
    assert_experiment_collection(name)
    response = client.query_points(collection_name=name, query=query_vector, limit=k,
                                   query_filter=_to_filter(filters), with_payload=True)
    results = []
    for rank, hit in enumerate(response.points, start=1):
        payload = hit.payload or {}
        results.append({"rank": rank, "score": hit.score, "point_id": str(hit.id),
                        "chunk_id": payload.get("chunk_id"), "policy_id": payload.get("policy_id"),
                        "document_id": payload.get("document_id"), "payload": payload})
    return results
