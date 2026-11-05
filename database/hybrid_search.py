"""
Hybrid 검색 인터페이스 — Qdrant(정책 원문 근거) + Neo4j(정책 관계·자격 조건 구조)를 함께 조회한다.

흐름 (search_policy_context)
  ① 질문 → 임베딩 벡터 (embedding.make_ollama_embedder, Ollama)
  ② Qdrant Top-K 검색 (qdrant.store.search_chunks) → 근거 청크 + policy_id
  ③ policy_id로 Neo4j 정책 문맥 조회 (파라미터화된 읽기 전용 Cypher)
  ④ policy_id 기준으로 결합 → 정책별 {원문 근거 evidence, 그래프 문맥 graph}

지키는 규칙
- 두 DB 모두 "읽기"만 한다. 이 모듈에는 쓰기·삭제 함수가 없다. Neo4j는 읽기 전용 세션(Neo4jDriverRunner.read_rows)만 쓴다.
- 사용자 입력으로 Cypher·Qdrant 필터 문자열을 만들지 않는다.
  · Qdrant 필터: 허용 필드(ALLOWED_FILTER_FIELDS)의 "값"만 받아 store._to_filter가 객체로 만든다.
  · Cypher: 쿼리 문장은 모듈 상수이고, 정책·문서 ID는 $파라미터로만 넘긴다.
- 검색 점수(score)는 "질문과 청크의 의미 유사도"일 뿐 자격 충족 여부가 아니다.
  이 모듈은 자격을 판정하지 않는다 (eligibility.evaluated = False). 조건 그룹의 AND/OR/NOT/UNKNOWN 구조는 그대로 돌려준다.
- Neo4j에 없는 policy_id는 만들어 내지 않고 MISSING_IN_GRAPH로 표시한다.

반환값은 모두 dict/list (JSON으로 바로 직렬화 가능). RAG 담당 코드에서 그대로 프롬프트 문맥으로 쓸 수 있게 했다.
"""

import os
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

import requests

from database import embedding as em
from database.chunking import make_point_id
from database.neo4j import load_graph as lg
from database.qdrant import store

# ---------------------------------------------------------------------------
# 1. 설정값
# ---------------------------------------------------------------------------
DEFAULT_COLLECTION = "exp_b2_qwen3emb06b_v1"     # 현재 검증된 실험 컬렉션 (서비스 컬렉션 아님)
DEFAULT_K = 5
MAX_K = 200                                     # 진단용 전체 순위(107개)까지 허용하되 무제한은 막는다
MAX_QUERY_CHARS = 2000
MAX_POLICY_IDS = 50
MAX_FILTER_VALUES = 50
MAX_CONDITION_DEPTH = 10                        # ConditionGroup 하위 그룹 최대 깊이 (실제 데이터는 1단계)
# Qdrant 필터로 받을 수 있는 필드 = 실험 컬렉션에 payload 인덱스를 만든 필드
ALLOWED_FILTER_FIELDS: tuple[str, ...] = tuple(store.PAYLOAD_INDEX_FIELDS)
# 데이터 계약 §1의 정책 ID 형식
POLICY_ID_PATTERN = re.compile(r"^POL_[A-Z0-9_]+$")
B_CHUNK_ID_PATTERN = re.compile(r"^CHK_[A-Z0-9]+_(DOC_[A-Z0-9_]+)_C\d+$")

# 검색 결과 한 건에 담는 payload 필드 (원문 근거·위치·출처·버전)
HIT_PAYLOAD_FIELDS = (
    "policy_id", "document_id", "chunk_id", "point_id", "policy_name",
    "text", "char_start", "char_end", "page_start", "page_end", "section_title",
    "source_url", "source_type", "content_hash", "verification_status",
    "chunking_version", "embedding_model", "embedding_input_rule", "index_run_id",
)

# 교차 검증 상태 값
PASS, MISSING, MISMATCH, NOT_TESTED = "PASS", "MISSING", "MISMATCH", "NOT_TESTED"

SCORE_NOTE = ("score는 질문 벡터와 청크 벡터의 코사인 유사도(Qdrant Cosine)다. 높을수록 의미가 가깝다는 뜻일 뿐, "
              "정책 자격을 충족한다는 뜻이 아니다.")
ELIGIBILITY_NOTE = ("자격 충족 여부는 판정하지 않았다. condition_tree는 원본 ConditionGroup의 logic(AND/OR/NOT/UNKNOWN)과 "
                    "계층을 그대로 보존한 것이며, UNKNOWN 그룹·review_status=extracted 근거는 공고문 확인이 필요하다.")


class HybridSearchInputError(ValueError):
    """잘못된 입력 (빈 질문, 허용되지 않은 필터 필드, 형식이 틀린 policy_id, 범위 밖 k 등). DB에 접속하지 않았다."""


class HybridSearchBackendError(RuntimeError):
    """백엔드 장애. backend 속성: "ollama" / "qdrant" / "neo4j". 메시지에 비밀값을 넣지 않는다."""

    def __init__(self, backend: str, message: str):
        super().__init__(f"[{backend}] {message}")
        self.backend = backend


# ---------------------------------------------------------------------------
# 2. 입력 검증 (DB 접속 전)
# ---------------------------------------------------------------------------
def validate_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise HybridSearchInputError("query는 비어 있지 않은 문자열이어야 합니다.")
    if len(query) > MAX_QUERY_CHARS:
        raise HybridSearchInputError(f"query가 너무 깁니다: {len(query)}자 (최대 {MAX_QUERY_CHARS}자)")
    return query.strip()


def validate_k(k: Any) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise HybridSearchInputError(f"k는 1 이상 {MAX_K} 이하의 정수여야 합니다: {k!r}")
    return k


def validate_filters(filters: Any) -> dict[str, Any] | None:
    """
    허용 필드의 값만 받는다. 값: 비어 있지 않은 문자열 1개, 또는 그런 문자열의 리스트(MatchAny, 하나라도 일치).
    여러 필드는 모두 만족(AND). 예) {"document_id": "DOC_LH_001"}, {"policy_id": ["POL_001", "POL_003"]}
    """
    if filters is None or filters == {}:
        return None
    if not isinstance(filters, dict):
        raise HybridSearchInputError("filters는 dict 또는 None이어야 합니다.")
    normalized: dict[str, Any] = {}
    for key, value in filters.items():
        if key not in ALLOWED_FILTER_FIELDS:
            raise HybridSearchInputError(f"허용되지 않은 필터 필드: {key!r} (허용: {', '.join(ALLOWED_FILTER_FIELDS)})")
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if not values or len(values) > MAX_FILTER_VALUES:
            raise HybridSearchInputError(f"필터 {key}의 값 개수는 1~{MAX_FILTER_VALUES}개여야 합니다.")
        if not all(isinstance(item, str) and item.strip() for item in values):
            raise HybridSearchInputError(f"필터 {key}의 값은 비어 있지 않은 문자열이어야 합니다.")
        normalized[key] = values if isinstance(value, (list, tuple)) else values[0]
    return normalized


def validate_policy_ids(policy_ids: Any) -> list[str]:
    """정책 ID 목록 검사 + 순서를 지킨 중복 제거. 형식: 데이터 계약 ^POL_[A-Z0-9_]+$"""
    if not isinstance(policy_ids, (list, tuple)):
        raise HybridSearchInputError("policy_ids는 문자열 리스트여야 합니다.")
    unique: list[str] = []
    for policy_id in policy_ids:
        if not isinstance(policy_id, str) or not POLICY_ID_PATTERN.match(policy_id):
            raise HybridSearchInputError(f"policy_id 형식이 아님: {policy_id!r}")
        if policy_id not in unique:
            unique.append(policy_id)
    if len(unique) > MAX_POLICY_IDS:
        raise HybridSearchInputError(f"policy_ids는 최대 {MAX_POLICY_IDS}개입니다: {len(unique)}개")
    return unique


# ---------------------------------------------------------------------------
# 3. Qdrant 근거 검색
# ---------------------------------------------------------------------------
def _hit_record(hit: dict[str, Any]) -> dict[str, Any]:
    payload = hit.get("payload") or {}
    record = {"rank": hit["rank"], "score": hit["score"]}
    record.update({field: payload.get(field) for field in HIT_PAYLOAD_FIELDS})
    record["point_id"] = record["point_id"] or hit.get("point_id")
    return record


def search_policy_evidence(query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K, *,
                           client=None, embedder: em.Embedder | None = None,
                           collection: str = DEFAULT_COLLECTION, query_template: str | None = None) -> dict[str, Any]:
    """
    질문을 임베딩해 Qdrant에서 Top-K 근거 청크를 찾는다. (읽기 전용)
    입력
      query          : 질문 문자열
      filters        : None 또는 {허용 필드: 값 | [값, ...]} (validate_filters 참고)
      k              : 돌려받을 청크 수 (1~MAX_K)
      client         : QdrantClient (None이면 .env로 store.get_client())
      embedder       : 문자열 목록 → 벡터 목록 함수 (None이면 Ollama qwen3-embedding:0.6b, batch 1)
      query_template : None(질문 그대로, 기본) 또는 "{question}"이 든 지시문 템플릿. 질의 지시문 채택은 미확정(PRD §13)
    반환
      {"query", "query_template", "filters", "k", "collection", "query_embedding_model",
       "latency_seconds": {"query_embedding", "qdrant_search"}, "warnings": [...],
       "hits": [{"rank", "score", "policy_id", "document_id", "chunk_id", "text", "char_start", "char_end",
                 "page_start", "page_end", "section_title", "source_url", "source_type", ...}, ...]}  (rank 오름차순)
    오류: HybridSearchInputError(입력), HybridSearchBackendError(backend="ollama"|"qdrant")
    """
    query = validate_query(query)
    filters = validate_filters(filters)
    k = validate_k(k)
    store.assert_experiment_collection(collection)
    query_model = em.DEFAULT_EMBEDDING_MODEL if embedder is None else None
    embedder = embedder or em.make_ollama_embedder(batch_size=1)

    started = time.perf_counter()
    try:
        query_vector = embedder([em.build_query_text(query, query_template)])[0]
    except (em.EmbeddingError, requests.RequestException) as error:
        raise HybridSearchBackendError("ollama", f"질의 임베딩 실패: {error}") from error
    embed_seconds = time.perf_counter() - started

    try:
        client = client if client is not None else store.get_client()
        started = time.perf_counter()
        hits = store.search_chunks(client, collection, query_vector, k=k, filters=filters)
        search_seconds = time.perf_counter() - started
    except store.ConnectionSettingsError as error:
        raise HybridSearchBackendError("qdrant", f"접속 설정 문제: {error}") from error
    except store.CollectionGuardError:
        raise
    except Exception as error:   # qdrant-client는 HTTP·gRPC 오류를 여러 클래스로 낸다 → 백엔드 장애로 묶는다
        raise HybridSearchBackendError("qdrant", f"검색 실패 ({type(error).__name__}): {error}") from error

    records = [_hit_record(hit) for hit in hits]
    warnings = []
    index_models = sorted({record["embedding_model"] for record in records if record["embedding_model"]})
    if query_model and index_models and index_models != [query_model]:
        warnings.append(f"질의 임베딩 모델({query_model})과 색인 모델({index_models})이 다름 → 점수 비교 불가")
    return {"query": query, "query_template": query_template, "filters": filters, "k": k, "collection": collection,
            "query_embedding_model": query_model,
            "latency_seconds": {"query_embedding": round(embed_seconds, 4), "qdrant_search": round(search_seconds, 4)},
            "warnings": warnings, "hits": records}


# ---------------------------------------------------------------------------
# 4. Neo4j 정책 문맥 (파라미터화된 읽기 전용 Cypher)
# ---------------------------------------------------------------------------
# (결과 키, 관계 타입) — 양끝 레이블은 load_graph.RELATIONSHIP_SPECS에서 가져온다 (허용 목록과 항상 같게)
POLICY_NEIGHBOR_SPECS: tuple[tuple[str, str], ...] = (
    ("organizations", "ADMINISTERED_BY"),
    ("regions", "AVAILABLE_IN"),
    ("categories", "HAS_CATEGORY"),
    ("target_groups", "TARGETS"),
    ("life_stages", "APPLIES_TO_LIFESTAGE"),
    ("documents", "SUPPORTED_BY"),
)


def _neighbor_comprehension(key: str, relation: str) -> str:
    source_label, target_label = lg.RELATIONSHIP_SPECS[lg._checked_relation(relation)]
    if source_label != "Policy":
        raise lg.GraphValidationError(f"정책에서 출발하는 관계가 아님: {relation}")
    return (f"[(p)-[r:{relation}]->(n:{target_label}) | "
            f"{{node: properties(n), relationship: properties(r)}}] AS {key}")


POLICY_CONTEXT_QUERY = (
    "UNWIND $policy_ids AS policy_id\n"
    "MATCH (p:Policy {policy_id: policy_id})\n"
    "RETURN policy_id, properties(p) AS policy,\n       "
    + ",\n       ".join(_neighbor_comprehension(key, relation) for key, relation in POLICY_NEIGHBOR_SPECS)
    + ",\n       [(p)-[r:MODIFIES_CONDITION_OF]->(n:Policy) | "
      "{policy_id: n.policy_id, policy_name: n.policy_name, relationship: properties(r)}] AS modifies_condition_of"
    + ",\n       [(n:Policy)-[r:MODIFIES_CONDITION_OF]->(p) | "
      "{policy_id: n.policy_id, policy_name: n.policy_name, relationship: properties(r)}] AS condition_modified_by"
    + ",\n       [(p)-[:REQUIRES_CONDITION]->(c:EligibilityCondition) | c.condition_id] AS requires_condition_ids"
    + ",\n       [(p)-[r:HAS_ROOT_CONDITION_GROUP]->(g:ConditionGroup) | "
      "{group_id: g.group_id, relationship: properties(r)}] AS root_groups"
)

# 루트 그룹에서 CONTAINS_GROUP을 따라 내려가며 그룹마다 하위 그룹·조건을 한 줄씩 읽는다. 트리 조립은 Python에서 한다.
CONDITION_TREE_QUERY = (
    "UNWIND $policy_ids AS policy_id\n"
    "MATCH (p:Policy {policy_id: policy_id})-[:HAS_ROOT_CONDITION_GROUP]->(root:ConditionGroup)\n"
    f"MATCH (root)-[:CONTAINS_GROUP*0..{int(MAX_CONDITION_DEPTH)}]->(g:ConditionGroup)\n"
    "WITH DISTINCT policy_id, root, g\n"
    "RETURN policy_id, root.group_id AS root_group_id, g.group_id AS group_id, properties(g) AS group,\n"
    "       [(g)-[r:CONTAINS_GROUP]->(child:ConditionGroup) | "
    "{group_id: child.group_id, relationship: properties(r)}] AS child_groups,\n"
    "       [(g)-[r:CONTAINS_CONDITION]->(c:EligibilityCondition) | "
    "{condition: properties(c), relationship: properties(r)}] AS conditions"
)

_NEIGHBOR_ID_FIELDS = {key: lg.LABEL_ID_FIELDS[lg.RELATIONSHIP_SPECS[relation][1]]
                       for key, relation in POLICY_NEIGHBOR_SPECS}


def _read(runner, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return runner.read_rows(query, parameters)
    except Exception as error:   # neo4j 드라이버의 ServiceUnavailable·AuthError·ClientError 등
        raise HybridSearchBackendError("neo4j", f"조회 실패 ({type(error).__name__}): {error}") from error


def _neighbor_items(items: list[dict[str, Any]] | None, id_field: str) -> list[dict[str, Any]]:
    flat = [{**(item.get("node") or {}), "relationship": item.get("relationship") or {}} for item in items or []]
    return sorted(flat, key=lambda item: str(item.get(id_field)))


def _build_condition_tree(policy_id: str, root_id: str, groups: dict[str, dict[str, Any]],
                          root_relationship: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """그룹 행들(group_id → 행)을 트리로 조립한다. logic 값은 바꾸지 않는다. 순환·미조회 하위 그룹은 issues에 적는다."""

    def build(group_id: str, relationship: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
        if group_id in path:
            issues.append({"issue": "cycle", "group_id": group_id, "path": list(path)})
            return {"group_id": group_id, "cycle": True, "relationship": relationship}
        row = groups.get(group_id)
        if row is None:
            issues.append({"issue": "child_group_not_loaded", "group_id": group_id,
                           "reason": f"최대 깊이 {MAX_CONDITION_DEPTH} 초과 또는 조회 누락"})
            return {"group_id": group_id, "not_loaded": True, "relationship": relationship}
        group = row["group"] or {}
        if group.get("policy_id") != policy_id:
            issues.append({"issue": "group_policy_mismatch", "group_id": group_id,
                           "group_policy_id": group.get("policy_id"), "policy_id": policy_id})
        conditions = sorted(({**(item.get("condition") or {}), "relationship": item.get("relationship") or {}}
                             for item in row["conditions"] or []), key=lambda item: str(item.get("condition_id")))
        children = sorted(row["child_groups"] or [], key=lambda item: str(item.get("group_id")))
        return {
            "group_id": group_id,
            "logic": group.get("logic"),               # AND / OR / NOT / UNKNOWN — 그대로
            "is_root": group.get("is_root"),
            "description": group.get("description"),
            "relationship": relationship,
            "conditions": conditions,
            "groups": [build(child["group_id"], child.get("relationship") or {}, path + (group_id,))
                       for child in children],
        }

    return build(root_id, root_relationship, ())


def _tree_condition_ids(tree: dict[str, Any]) -> set[str]:
    ids = {item["condition_id"] for item in tree.get("conditions", []) if item.get("condition_id")}
    for child in tree.get("groups", []):
        ids |= _tree_condition_ids(child)
    return ids


def _tree_groups(tree: dict[str, Any]) -> list[dict[str, Any]]:
    found = [tree]
    for child in tree.get("groups", []):
        found.extend(_tree_groups(child))
    return found


def get_policy_graph_context(policy_ids: list[str], *, runner) -> dict[str, Any]:
    """
    정책 ID 목록의 그래프 문맥을 Neo4j에서 읽는다. (읽기 전용 세션, 파라미터화된 Cypher 2개)
    입력: policy_ids (데이터 계약 형식, 최대 MAX_POLICY_IDS개), runner (load_graph.Neo4jDriverRunner 등 read_rows 제공 객체)
    반환
      {"requested_policy_ids": [...], "missing_policy_ids": [...],
       "policies": {policy_id: {
           "policy_id", "policy": {Policy 속성},
           "organizations" | "regions" | "categories" | "target_groups" | "life_stages" | "documents":
               [{노드 속성..., "relationship": {target_name, evidence_document_id, evidence_location, review_status}}],
           "modifies_condition_of", "condition_modified_by",
           "eligibility": {"evaluated": False, "note", "condition_tree": [루트 그룹 트리],
                           "requires_condition_ids", "tree_condition_ids", "tree_matches_requires_condition",
                           "unknown_logic_groups", "structure_issues"}}}}
    Neo4j에 없는 정책은 policies에 넣지 않고 missing_policy_ids에 적는다 (만들어 내지 않는다).
    """
    ids = validate_policy_ids(policy_ids)
    if not ids:
        return {"requested_policy_ids": [], "missing_policy_ids": [], "policies": {}}
    base_rows = _read(runner, POLICY_CONTEXT_QUERY, {"policy_ids": ids})
    tree_rows = _read(runner, CONDITION_TREE_QUERY, {"policy_ids": ids})

    groups_by_policy: dict[str, dict[str, dict[str, Any]]] = {}
    for row in tree_rows:
        groups_by_policy.setdefault(row["policy_id"], {})[row["group_id"]] = row

    policies: dict[str, dict[str, Any]] = {}
    for row in base_rows:
        policy_id = row["policy_id"]
        issues: list[dict[str, Any]] = []
        groups = groups_by_policy.get(policy_id, {})
        trees = [_build_condition_tree(policy_id, root["group_id"], groups, root.get("relationship") or {}, issues)
                 for root in sorted(row["root_groups"] or [], key=lambda item: str(item.get("group_id")))]
        if not trees:
            issues.append({"issue": "no_root_condition_group"})
        tree_ids = set().union(*(_tree_condition_ids(tree) for tree in trees)) if trees else set()
        requires = sorted(set(row["requires_condition_ids"] or []))
        context = {"policy_id": policy_id, "policy": row["policy"] or {}}
        for key, _relation in POLICY_NEIGHBOR_SPECS:
            context[key] = _neighbor_items(row[key], _NEIGHBOR_ID_FIELDS[key])
        context["modifies_condition_of"] = sorted(row["modifies_condition_of"] or [], key=lambda i: i["policy_id"])
        context["condition_modified_by"] = sorted(row["condition_modified_by"] or [], key=lambda i: i["policy_id"])
        context["eligibility"] = {
            "evaluated": False,
            "note": ELIGIBILITY_NOTE,
            "condition_tree": trees,
            "requires_condition_ids": requires,
            "tree_condition_ids": sorted(tree_ids),
            # REQUIRES_CONDITION(평탄화 목록)과 트리의 조건 집합이 같은가. 다르면 그래프 인계 데이터 확인 필요
            "tree_matches_requires_condition": set(requires) == tree_ids,
            "unknown_logic_groups": sorted(group["group_id"] for tree in trees for group in _tree_groups(tree)
                                           if group.get("logic") == "UNKNOWN"),
            "structure_issues": issues,
        }
        policies[policy_id] = context
    return {"requested_policy_ids": ids, "missing_policy_ids": [pid for pid in ids if pid not in policies],
            "policies": policies}


# ---------------------------------------------------------------------------
# 5. 결합
# ---------------------------------------------------------------------------
def combine_policy_evidence(search_result: dict[str, Any], graph_context: dict[str, Any]) -> dict[str, Any]:
    """
    Qdrant 검색 결과와 Neo4j 정책 문맥을 policy_id로 묶는다. (DB 접속 없음)
    - 정책 순서: 그 정책 근거 청크의 가장 좋은 순위(best_rank) 순
    - 정책 안 근거 청크: 검색 순위 순, chunk_id 중복 제거
    - evidence(원문 근거)와 graph(그래프 정보)를 다른 키로 나눈다. graph가 없으면 graph_status="MISSING_IN_GRAPH", graph=None
    - evidence_document_check: 근거 청크의 document_id가 그래프에서 그 정책의 SUPPORTED_BY 문서인지 (PASS/MISMATCH/NOT_TESTED)
    """
    contexts = graph_context.get("policies", {})
    grouped: dict[str, dict[str, Any]] = {}
    unlinked: list[dict[str, Any]] = []
    for hit in sorted(search_result.get("hits", []), key=lambda item: item["rank"]):
        policy_id = hit.get("policy_id")
        if not policy_id:
            unlinked.append(hit)
            continue
        entry = grouped.setdefault(policy_id, {"policy_id": policy_id, "policy_name": hit.get("policy_name"),
                                               "best_rank": hit["rank"], "best_score": hit["score"],
                                               "evidence": [], "_chunk_ids": set()})
        if hit.get("chunk_id") in entry["_chunk_ids"]:
            continue
        entry["_chunk_ids"].add(hit.get("chunk_id"))
        entry["evidence"].append(hit)

    policies = []
    for entry in sorted(grouped.values(), key=lambda item: item["best_rank"]):
        entry.pop("_chunk_ids")
        context = contexts.get(entry["policy_id"])
        entry["graph_status"] = "FOUND" if context is not None else "MISSING_IN_GRAPH"
        entry["graph"] = context
        linked_documents = {doc.get("document_id") for doc in (context or {}).get("documents", [])}
        entry["evidence_document_check"] = {
            hit["document_id"]: (NOT_TESTED if context is None else PASS if hit["document_id"] in linked_documents
                                 else MISMATCH)
            for hit in entry["evidence"]}
        policies.append(entry)
    return {"query": search_result.get("query"), "filters": search_result.get("filters"),
            "k": search_result.get("k"), "collection": search_result.get("collection"),
            "query_template": search_result.get("query_template"),
            "policies": policies, "unlinked_hits": unlinked,
            "missing_policy_ids": graph_context.get("missing_policy_ids", []),
            "warnings": list(search_result.get("warnings", [])),
            "latency_seconds": dict(search_result.get("latency_seconds", {})),
            "notes": {"score": SCORE_NOTE, "eligibility": ELIGIBILITY_NOTE}}


# ---------------------------------------------------------------------------
# 6. 통합 진입 함수
# ---------------------------------------------------------------------------
@contextmanager
def open_neo4j_runner() -> Iterator[lg.Neo4jDriverRunner]:
    """.env의 NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD로 드라이버를 열고 읽기용 실행기를 준다. 끝나면 닫는다. (값은 출력하지 않는다)"""
    try:
        from neo4j import GraphDatabase
    except ImportError as error:
        raise HybridSearchBackendError("neo4j", "neo4j 드라이버가 설치되어 있지 않음") from error
    em.load_env_file()
    missing = [name for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise HybridSearchBackendError("neo4j", f"접속 설정 누락: {', '.join(missing)} (.env 확인)")
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    try:
        try:
            driver.verify_connectivity()
        except Exception as error:
            raise HybridSearchBackendError("neo4j", f"연결 실패 ({type(error).__name__})") from error
        yield lg.Neo4jDriverRunner(driver)
    finally:
        driver.close()


def search_policy_context(query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K, *,
                          client=None, runner=None, embedder: em.Embedder | None = None,
                          collection: str = DEFAULT_COLLECTION, query_template: str | None = None) -> dict[str, Any]:
    """
    통합 진입 함수: 질의 임베딩 → Qdrant Top-K → policy_id 수집 → Neo4j 정책 문맥 → 결합.
    client·runner·embedder를 넘기지 않으면 .env 설정으로 만든다 (Neo4j 드라이버는 이 호출 안에서 열고 닫는다).
    반환: combine_policy_evidence 결과 + latency_seconds["neo4j_context"], "invalid_policy_ids"
    """
    search = search_policy_evidence(query, filters, k, client=client, embedder=embedder, collection=collection,
                                    query_template=query_template)
    ordered_ids: list[str] = []
    invalid: list[str] = []
    for hit in search["hits"]:
        policy_id = hit.get("policy_id")
        if not policy_id or policy_id in ordered_ids or policy_id in invalid:
            continue
        (ordered_ids if POLICY_ID_PATTERN.match(policy_id) else invalid).append(policy_id)
    started = time.perf_counter()
    if runner is not None:
        context = get_policy_graph_context(ordered_ids, runner=runner)
    else:
        with open_neo4j_runner() as opened:
            context = get_policy_graph_context(ordered_ids, runner=opened)
    result = combine_policy_evidence(search, context)
    result["latency_seconds"]["neo4j_context"] = round(time.perf_counter() - started, 4)
    result["invalid_policy_ids"] = invalid
    return result


# ---------------------------------------------------------------------------
# 7. Qdrant ↔ Neo4j 교차 검증 (읽기 전용)
# ---------------------------------------------------------------------------
CROSS_POLICY_QUERY = ("UNWIND $policy_ids AS policy_id\n"
                      "OPTIONAL MATCH (p:Policy {policy_id: policy_id})\n"
                      "RETURN policy_id, p IS NOT NULL AS exists")
CROSS_DOCUMENT_QUERY = ("UNWIND $document_ids AS document_id\n"
                        "OPTIONAL MATCH (d:Document {document_id: document_id})\n"
                        "OPTIONAL MATCH (p:Policy)-[:SUPPORTED_BY]->(d)\n"
                        "RETURN document_id, d IS NOT NULL AS exists, d.content_hash AS content_hash,\n"
                        "       d.policy_id AS policy_id, [x IN collect(p.policy_id) WHERE x IS NOT NULL] AS supported_by")
CROSS_PAYLOAD_FIELDS = ("chunk_id", "point_id", "policy_id", "document_id", "content_hash")
CROSS_CHECKS = ("point_id", "chunk_id_document", "policy_exists", "document_exists", "supported_by",
                "document_policy_id", "content_hash")


def read_payload_keys(client, collection: str = DEFAULT_COLLECTION, page_size: int = 256) -> list[dict[str, Any]]:
    """Qdrant 컬렉션 전체 point의 연결 키(CROSS_PAYLOAD_FIELDS)와 point id를 읽는다. (scroll, 읽기 전용)"""
    store.assert_experiment_collection(collection)
    rows, offset = [], None
    while True:
        records, offset = client.scroll(collection_name=collection, limit=page_size, offset=offset,
                                        with_payload=list(CROSS_PAYLOAD_FIELDS), with_vectors=False)
        rows.extend({"id": str(record.id), **{field: (record.payload or {}).get(field) for field in CROSS_PAYLOAD_FIELDS}}
                    for record in records)
        if offset is None:
            return rows


def _check_chunk(row: dict[str, Any], policies: dict[str, bool], documents: dict[str, dict[str, Any]]) -> dict[str, str]:
    policy_id, document_id = row["policy_id"], row["document_id"]
    document = documents.get(document_id) or {}
    document_found = bool(document.get("exists"))
    policy_found = bool(policies.get(policy_id))
    result = {
        "point_id": PASS if row["chunk_id"] and row["id"] == make_point_id(row["chunk_id"]) == row["point_id"]
        else MISMATCH,
        "policy_exists": PASS if policy_found else MISSING,
        "document_exists": PASS if document_found else MISSING,
    }
    match = B_CHUNK_ID_PATTERN.match(row["chunk_id"] or "")
    result["chunk_id_document"] = (NOT_TESTED if not match else PASS if match.group(1) == document_id else MISMATCH)
    if not (document_found and policy_found):
        result["supported_by"] = NOT_TESTED
    elif policy_id in (document.get("supported_by") or []):
        result["supported_by"] = PASS
    else:
        result["supported_by"] = MISMATCH if document.get("supported_by") else MISSING
    result["document_policy_id"] = (NOT_TESTED if not document_found
                                    else PASS if document.get("policy_id") == policy_id else MISMATCH)
    if not document_found:
        result["content_hash"] = NOT_TESTED
    elif not row["content_hash"] or not document.get("content_hash"):
        result["content_hash"] = MISSING
    else:
        result["content_hash"] = PASS if row["content_hash"] == document["content_hash"] else MISMATCH
    return result


def verify_cross_db(*, client, runner, collection: str = DEFAULT_COLLECTION) -> dict[str, Any]:
    """
    Qdrant point(청크)마다 Neo4j와 연결이 맞는지 확인한다. (두 DB 모두 읽기 전용)
    검사 항목 (CROSS_CHECKS) — 각 결과는 PASS / MISSING / MISMATCH / NOT_TESTED
      point_id           : Qdrant id == payload point_id == uuid5(chunk_id)
      chunk_id_document  : chunk_id(CHK_{버전}_{문서}_C…) 안의 문서 ID == payload document_id (형식이 다르면 NOT_TESTED)
      policy_exists      : payload policy_id인 Policy 노드가 있는가
      document_exists    : payload document_id인 Document 노드가 있는가
      supported_by       : (Policy {payload policy_id})-[:SUPPORTED_BY]->(Document {payload document_id})가 있는가
                           (없으면 MISSING, 다른 정책에만 연결되어 있으면 MISMATCH = 청크가 잘못된 정책에 붙음)
      document_policy_id : Document 노드의 policy_id == payload policy_id
      content_hash       : Document 노드의 content_hash == payload content_hash
    반환: {"status", "qdrant_points", "qdrant_count", "summary": {검사: {상태: 수}}, "failures": [...], "by_document": {...}}
    """
    try:
        rows = read_payload_keys(client, collection)
        qdrant_count = store.count_points(client, collection)
    except store.CollectionGuardError:
        raise
    except Exception as error:
        raise HybridSearchBackendError("qdrant", f"payload 읽기 실패 ({type(error).__name__}): {error}") from error
    policy_ids = sorted({row["policy_id"] for row in rows if row["policy_id"]})
    document_ids = sorted({row["document_id"] for row in rows if row["document_id"]})
    policies = {row["policy_id"]: row["exists"] for row in _read(runner, CROSS_POLICY_QUERY, {"policy_ids": policy_ids})}
    documents = {row["document_id"]: row for row in _read(runner, CROSS_DOCUMENT_QUERY, {"document_ids": document_ids})}

    summary = {check: {PASS: 0, MISSING: 0, MISMATCH: 0, NOT_TESTED: 0} for check in CROSS_CHECKS}
    failures, by_document = [], {}
    for row in rows:
        checks = _check_chunk(row, policies, documents)
        for check, status in checks.items():
            summary[check][status] += 1
        doc = by_document.setdefault(row["document_id"], {"chunks": 0, "policy_ids": set(), "all_pass": True})
        doc["chunks"] += 1
        doc["policy_ids"].add(row["policy_id"])
        if any(status != PASS for status in checks.values()):
            doc["all_pass"] = False
            failures.append({"chunk_id": row["chunk_id"], "policy_id": row["policy_id"],
                             "document_id": row["document_id"],
                             "checks": {check: status for check, status in checks.items() if status != PASS}})
    for doc in by_document.values():
        doc["policy_ids"] = sorted(doc["policy_ids"])
    complete = len(rows) == qdrant_count
    return {"status": PASS if not failures and complete and rows else MISMATCH if rows else NOT_TESTED,
            "collection": collection, "qdrant_points": len(rows), "qdrant_count": qdrant_count,
            "all_points_read": complete, "policy_ids": policy_ids, "document_ids": document_ids,
            "summary": summary, "failures": failures, "by_document": by_document}
