"""
Neo4j 그래프 적재 로더 (안전 버전).

무엇을 하는가?
- 데이터 담당이 만든 인계 파일(data/handoff/neo4j/의 노드 9종 + relationships.jsonl)을 Neo4j에 적재한다.
- 인계 파일은 읽기만 하고 바꾸지 않는다.

인계 로더(data/handoff/neo4j/load_neo4j.py)와 다른 점
1) 관계를 만들 때 "레이블 + 고유 ID"로만 노드를 찾는다.
   인계 로더는 MATCH (src) WHERE src.policy_id = … OR src.document_id = … 처럼 레이블 없이 찾았다.
   Document·ConditionGroup 노드에도 policy_id 속성이 있어서 POL_001 하나가 Policy 1 + Document 3 + ConditionGroup 1
   = 5개 노드에 동시에 걸렸고, 관계가 3,615개가 아니라 11,316개 생긴다. (2026-11-05 오프라인 재현)
2) 쓰기 전에 입력 전체를 검증하고, 하나라도 문제가 있으면 아무것도 쓰지 않는다.
3) 레이블·관계 타입은 허용 목록(handoff_data.NODE_SPECS / RELATIONSHIP_SPECS)에 있는 것만 쿼리에 넣는다.
   데이터 값은 모두 $rows 파라미터로 넘긴다.
4) 삭제 쿼리(DELETE, DETACH DELETE, REMOVE)는 만들지 않는다. 오래된 노드·관계 정리는 이번 범위가 아니다.
5) 기대 건수는 상수가 아니라 실제 입력 파일에서 센다.

트랜잭션 경계 (중간에 실패하면 어디까지 반영되는가)
- 제약조건 1개 = 스키마 트랜잭션 1개 (Neo4j는 스키마 변경과 데이터 쓰기를 한 트랜잭션에 섞을 수 없다)
- 데이터는 "레이블(또는 관계 타입) 하나의 배치 하나(기본 500행)" = 쓰기 트랜잭션 1개
- 배치 안에서 MERGE한 건수가 배치 행 수와 다르면 그 트랜잭션 함수 안에서 예외를 내서 그 배치만 롤백한다.
- 따라서 실패 시점 이전에 커밋된 배치는 DB에 남는다. 모두 MERGE라서 같은 입력으로 다시 실행하면 이어서 완성되고 중복은 생기지 않는다.
- 전체를 한 트랜잭션으로 묶지 않은 이유: 노드 1,034 + 관계 3,615개를 한 번에 쓰면 메모리 사용이 커지고,
  어느 배치에서 실패했는지 보고하기 어렵다. (대안은 보고서에 정리)

빈 DB 보호와 검증 (2026-11-05 추가)
- 쓰기 직전 preflight_database가 DB를 다시 확인한다. 기본은 "빈 DB에만 적재"이고, 같은 데이터 재실행은
  allow_existing=True를 명시해야 한다. 그 경우에도 기존 데이터가 입력의 부분집합인지 먼저 검증한다.
- 적재 후 verify_loaded_graph가 건수 + 스키마(verify_schema) + 노드 ID 집합(verify_node_ids)
  + 관계 양끝 ID 집합(verify_relationship_endpoints)을 원본과 비교한다. 건수가 같다는 것만으로 통과시키지 않는다.
- 조회는 읽기 전용 세션에서 한다. 서버 알림은 끄지 않고 모아서 classify_notifications로 분류한다.

실행: 이 모듈에는 실행 진입점이 없다. 드라이버를 만들어 Neo4jDriverRunner(driver)로 감싼 뒤 load_graph를 호출한다.
읽기 전용 확인은 tests/integration/test_neo4j_smoke.py (RUN_NEO4J_SMOKE=1).
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from database import handoff_data as hd

# ---------------------------------------------------------------------------
# 1. 허용 목록 (검증된 스키마)
# ---------------------------------------------------------------------------
# 레이블 → 고유 ID 필드, 관계 타입 → (출발 레이블, 도착 레이블)
# handoff_data의 표를 그대로 쓴다. (11개 관계 타입 모두 레이블 조합이 1개뿐인 것을 오프라인으로 확인했다)
LABEL_ID_FIELDS: dict[str, str] = dict(hd.LABEL_ID_FIELDS)
RELATIONSHIP_SPECS: dict[str, tuple[str, str]] = dict(hd.RELATIONSHIP_SPECS)

# 관계에 속성으로 옮기는 필드 (relationships.jsonl의 나머지 열)
RELATIONSHIP_PROPERTY_FIELDS = (
    "target_name", "evidence_document_id", "evidence_location", "review_status",
    "raw_value", "condition_type", "doc_type", "evidence_type", "law_article",
)
REQUIRED_RELATIONSHIP_FIELDS = ("source_id", "relation", "target_id")

# 후보 0a09126의 docs/data/data_contract.md §2.1 (2026-11-06 개정)·scripts/load_candidate_neo4j.py NULLABLE_FIELDS.
# 선택(Nullable) 필드는 null 허용(적재 시 속성 생략), 값이 있으면 문자열이어야 한다.
# review_flags는 processed 전용 중첩 객체라 Graph에는 값으로 저장할 수 없다 → null만 허용, 객체는 invalid_property.
NULLABLE_NODE_STRING_FIELDS = {
    "Policy": frozenset(("official_apply_url", "application_start", "application_end", "lastMdfcnDt", "review_flags")),
    "Document": frozenset(("published_at", "effective_at", "doc_type", "law_article", "effective_date",
                           "version", "extracted_text_path", "official_id")),
}
REQUIRED_NODE_STRING_FIELDS = {
    "Policy": ("policy_id", "official_id", "policy_name", "source_type", "category", "agency", "organization",
               "region", "eligibility", "benefit", "application_period", "application_method",
               "source_url", "verification_status", "collected_at"),
    "Document": ("document_id", "policy_id", "title", "source_type", "source_url", "local_raw_path",
                 "content_hash", "verification_status"),
    "Organization": ("agency_id", "name", "type"),
    "Region": ("region_id", "name"),
    "Category": ("category_id", "name"),
    "LifeStage": ("stage_id", "name", "age_range", "description"),
    "TargetGroup": ("group_id", "name", "description"),
    "ConditionGroup": ("group_id", "policy_id", "logic", "description"),
    "EligibilityCondition": ("condition_id", "type", "name", "operator", "value", "unit",
                             "evidence_location", "description"),
}
REQUIRED_NODE_LIST_FIELDS = {"Policy": ("life_stages",)}
# 값이 있으면 문자열 리스트여야 하는 필드 (target_groups는 계약 필수가 아니므로 누락은 허용, null은 불허)
TYPED_NODE_LIST_FIELDS = {"Policy": ("life_stages", "target_groups")}
REQUIRED_NODE_BOOL_FIELDS = {"ConditionGroup": ("is_root",)}

# 제약조건 이름: 인계 DDL(cypher_ddl.cypher)과 같은 이름을 쓴다. → 이미 같은 제약이 있으면 IF NOT EXISTS로 건너뛴다.
CONSTRAINT_NAMES: dict[str, str] = {
    "Policy": "constraint_policy_id",
    "Document": "constraint_document_id",
    "Organization": "constraint_agency_id",
    "Region": "constraint_region_id",
    "Category": "constraint_category_id",
    "LifeStage": "constraint_stage_id",
    "TargetGroup": "constraint_target_group_id",
    "ConditionGroup": "constraint_condition_group_id",
    "EligibilityCondition": "constraint_eligibility_condition_id",
}
# 탐색용 인덱스 (인계 DDL과 같은 이름·대상)
INDEX_SPECS: tuple[tuple[str, str, str], ...] = (
    ("index_policy_category", "Policy", "category"),
    ("index_policy_verification", "Policy", "verification_status"),
    ("index_condition_group_logic", "ConditionGroup", "logic"),
    ("index_eligibility_condition_type", "EligibilityCondition", "type"),
)

DEFAULT_BATCH_SIZE = 500
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# 생성되는 쿼리에 절대 들어가면 안 되는 낱말 (삭제·초기화 방지 검사용)
FORBIDDEN_CYPHER_WORDS = ("DELETE", "DETACH", "REMOVE", "DROP")


class GraphValidationError(ValueError):
    """입력 검증 실패. 이 오류가 나면 DB에는 아무것도 쓰지 않는다."""


class GraphLoadError(RuntimeError):
    """적재 중 실패 (예: 배치 MERGE 건수 불일치). 메시지에 어느 단계·배치인지 적는다."""


# ---------------------------------------------------------------------------
# 2. 입력 읽기
# ---------------------------------------------------------------------------
@dataclass
class GraphInput:
    """인계 파일에서 읽은 노드·관계. nodes_by_label = {"Policy": [ {...}, ... ], ...}"""
    nodes_by_label: dict[str, list[dict[str, Any]]]
    relationships: list[dict[str, Any]]
    source_dir: Path | None = None


def read_graph_input(directory: Path | str | None = None) -> GraphInput:
    """인계 폴더의 노드 파일 9종과 relationships.jsonl을 읽는다. (파일은 바꾸지 않는다)"""
    folder = Path(directory) if directory is not None else hd.NEO4J_HANDOFF_DIR
    nodes = {label: hd.read_jsonl(folder / file_name) for file_name, (label, _id) in hd.NODE_SPECS.items()}
    relationships = hd.read_jsonl(folder / hd.RELATIONSHIPS_PATH.name)
    return GraphInput(nodes_by_label=nodes, relationships=relationships, source_dir=folder)


# ---------------------------------------------------------------------------
# 3. 검증 (DB 없이)
# ---------------------------------------------------------------------------
def _is_neo4j_property_value(value: Any) -> bool:
    """Neo4j에 속성으로 저장할 수 있는 값인가: 문자열·숫자·불리언, 또는 같은 종류 기본값만 담은 리스트. (dict 불가)"""
    if isinstance(value, float):
        return math.isfinite(value)          # NaN·±inf는 비교·정렬이 깨진다 (scripts/load_candidate_neo4j.validate_property와 같음)
    if isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, list):
        kinds = {type(item) for item in value}
        return (all(kind in (str, bool, int, float) for kind in kinds) and len(kinds) <= 1
                and all(math.isfinite(item) for item in value if isinstance(item, float)))
    return False


@dataclass
class ValidationReport:
    node_counts: dict[str, int] = field(default_factory=dict)
    relationship_counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(self.errors.values())

    def add(self, kind: str, detail: Any) -> None:
        self.errors.setdefault(kind, []).append(detail)

    def summary(self) -> dict[str, Any]:
        return {"ok": self.ok, "node_counts": dict(self.node_counts),
                "relationship_counts": dict(self.relationship_counts),
                "error_counts": {kind: len(items) for kind, items in self.errors.items() if items},
                "error_examples": {kind: items[:3] for kind, items in self.errors.items() if items}}


def validate_graph_input(graph: GraphInput) -> ValidationReport:
    """
    쓰기 전에 입력 전체를 검사한다. 반환: ValidationReport (ok가 False면 적재하지 않는다)
    오류 종류:
      unknown_label          허용 목록에 없는 레이블
      missing_id             고유 ID가 없거나 빈 문자열이거나 문자열이 아님
      missing_node_field     Graph 투영 계약의 필수 속성 누락
      duplicate_id           같은 레이블 안에서 고유 ID 중복
      invalid_property       계약과 다른 타입/비허용 null 또는 저장 불가 값 (dict, 섞인 타입 리스트)
      missing_field          관계 필수 필드(source_id·relation·target_id) 누락
      unknown_relation       허용 목록에 없는 관계 타입
      missing_source/target  관계 양끝 노드가 해당 레이블에 없음
      duplicate_relationship (타입, 출발, 도착)이 같은 줄이 두 번 이상 → MERGE로 합쳐져 건수가 달라짐
      invalid_relationship_property  관계 속성값을 저장할 수 없음
    """
    report = ValidationReport()
    ids_by_label: dict[str, set[str]] = {}

    for label, rows in graph.nodes_by_label.items():
        if label not in LABEL_ID_FIELDS:
            report.add("unknown_label", label)
            continue
        id_field = LABEL_ID_FIELDS[label]
        seen: set[str] = set()
        for index, row in enumerate(rows):
            node_id = row.get(id_field)
            if not isinstance(node_id, str) or not node_id.strip():
                report.add("missing_id", (label, index))
                continue
            if node_id in seen:
                report.add("duplicate_id", (label, node_id))
            seen.add(node_id)
            for key in (REQUIRED_NODE_STRING_FIELDS.get(label, ()) + REQUIRED_NODE_LIST_FIELDS.get(label, ())
                        + REQUIRED_NODE_BOOL_FIELDS.get(label, ())):
                if key not in row:
                    report.add("missing_node_field", (label, node_id, key))
            for key, value in row.items():
                if key in NULLABLE_NODE_STRING_FIELDS.get(label, ()):
                    if value is not None and not isinstance(value, str):
                        report.add("invalid_property", (label, node_id, key))
                    continue
                if not _is_neo4j_property_value(value):
                    report.add("invalid_property", (label, node_id, key))
                elif key in REQUIRED_NODE_STRING_FIELDS.get(label, ()) and not isinstance(value, str):
                    report.add("invalid_property", (label, node_id, key))
                elif key in TYPED_NODE_LIST_FIELDS.get(label, ()) and (
                    not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                ):
                    report.add("invalid_property", (label, node_id, key))
                elif key in REQUIRED_NODE_BOOL_FIELDS.get(label, ()) and not isinstance(value, bool):
                    report.add("invalid_property", (label, node_id, key))
        ids_by_label[label] = seen
        report.node_counts[label] = len(rows)

    seen_triples: set[tuple[str, str, str]] = set()
    for index, row in enumerate(graph.relationships):
        missing = [name for name in REQUIRED_RELATIONSHIP_FIELDS if not isinstance(row.get(name), str) or not row[name]]
        if missing:
            report.add("missing_field", (index, missing))
            continue
        relation = row["relation"]
        if relation not in RELATIONSHIP_SPECS:
            report.add("unknown_relation", (index, relation))
            continue
        source_label, target_label = RELATIONSHIP_SPECS[relation]
        if row["source_id"] not in ids_by_label.get(source_label, set()):
            report.add("missing_source", (relation, row["source_id"]))
        if row["target_id"] not in ids_by_label.get(target_label, set()):
            report.add("missing_target", (relation, row["target_id"]))
        triple = (relation, row["source_id"], row["target_id"])
        if triple in seen_triples:
            report.add("duplicate_relationship", triple)
        seen_triples.add(triple)
        for key in RELATIONSHIP_PROPERTY_FIELDS:
            # 선택 관계 메타데이터는 문자열/누락/null. null은 저장 시 속성 없음.
            if key in row and row[key] is not None and not isinstance(row[key], str):
                report.add("invalid_relationship_property", (index, key))
        report.relationship_counts[relation] = report.relationship_counts.get(relation, 0) + 1

    for label in LABEL_ID_FIELDS:
        report.node_counts.setdefault(label, 0)
    return report


# ---------------------------------------------------------------------------
# 4. 쿼리 만들기 (레이블·타입은 허용 목록만, 값은 파라미터)
# ---------------------------------------------------------------------------
def _checked_label(label: str) -> str:
    if label not in LABEL_ID_FIELDS or not _IDENTIFIER.match(label):
        raise GraphValidationError(f"허용되지 않은 레이블: {label!r}")
    return label


def _checked_relation(relation: str) -> str:
    if relation not in RELATIONSHIP_SPECS or not _IDENTIFIER.match(relation):
        raise GraphValidationError(f"허용되지 않은 관계 타입: {relation!r}")
    return relation


def build_schema_statements() -> list[str]:
    """
    제약조건 9개 + 인덱스 4개 (인계 cypher_ddl.cypher와 같은 이름·대상). 모두 IF NOT EXISTS → 이미 있으면 아무 일도 없다.
    고유 제약조건은 MERGE가 "같은 ID 노드를 하나만" 만들도록 DB 차원에서 보장하고, MERGE 조회를 빠르게 한다.
    """
    statements = []
    for label, name in CONSTRAINT_NAMES.items():
        label = _checked_label(label)
        statements.append(f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                          f"FOR (n:{label}) REQUIRE n.{LABEL_ID_FIELDS[label]} IS UNIQUE")
    for name, label, prop in INDEX_SPECS:
        label = _checked_label(label)
        if not _IDENTIFIER.match(prop):
            raise GraphValidationError(f"허용되지 않은 속성 이름: {prop!r}")
        statements.append(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})")
    return statements


def build_node_merge_query(label: str) -> str:
    """
    노드 MERGE 쿼리. 예) Policy →
      UNWIND $rows AS row
      MERGE (n:Policy {policy_id: row.id})
      SET n += row.properties
      RETURN count(n) AS merged
    - MERGE 기준은 "레이블 + 고유 ID" 하나뿐이다 → 다시 실행해도 노드가 늘지 않는다.
    - SET += 는 입력에 있는 속성만 덮어쓴다. 입력에 없는 기존 속성은 지우지 않는다(삭제 동기화는 범위 밖).
    """
    label = _checked_label(label)
    return (f"UNWIND $rows AS row\n"
            f"MERGE (n:{label} {{{LABEL_ID_FIELDS[label]}: row.id}})\n"
            f"SET n += row.properties\n"
            f"RETURN count(n) AS merged")


def build_relationship_merge_query(relation: str) -> str:
    """
    관계 MERGE 쿼리. 예) ADMINISTERED_BY →
      UNWIND $rows AS row
      MATCH (s:Policy {policy_id: row.source_id})
      MATCH (t:Organization {agency_id: row.target_id})
      MERGE (s)-[r:ADMINISTERED_BY]->(t)
      SET r += row.properties
      RETURN count(r) AS merged
    - 양끝 노드를 각자의 레이블 + 고유 ID로 찾는다 → 한 행이 정확히 노드 2개에만 연결된다.
    - 양끝 중 하나가 없으면 그 행은 MATCH에서 사라지고 merged가 배치 행 수보다 작아진다 → 실행기가 롤백한다.
    """
    relation = _checked_relation(relation)
    source_label, target_label = RELATIONSHIP_SPECS[relation]
    return (f"UNWIND $rows AS row\n"
            f"MATCH (s:{source_label} {{{LABEL_ID_FIELDS[source_label]}: row.source_id}})\n"
            f"MATCH (t:{target_label} {{{LABEL_ID_FIELDS[target_label]}: row.target_id}})\n"
            f"MERGE (s)-[r:{relation}]->(t)\n"
            f"SET r += row.properties\n"
            f"RETURN count(r) AS merged")


def build_count_queries() -> dict[str, str]:
    """적재 후 확인용 읽기 쿼리. 키: "node:Policy" / "rel:TARGETS" (레이블을 지정해 센다)"""
    queries = {f"node:{label}": f"MATCH (n:{_checked_label(label)}) RETURN count(n) AS count" for label in LABEL_ID_FIELDS}
    for relation, (source_label, target_label) in RELATIONSHIP_SPECS.items():
        queries[f"rel:{relation}"] = (f"MATCH (:{source_label})-[r:{_checked_relation(relation)}]->(:{target_label}) "
                                      f"RETURN count(r) AS count")
    return queries


def _node_row(label: str, node: dict[str, Any]) -> dict[str, Any]:
    # 선택 null은 속성 없음으로 전달한다. SET += null에 의한 기존 속성 삭제도 피한다.
    return {"id": node[LABEL_ID_FIELDS[label]],
            "properties": {key: value for key, value in node.items() if value is not None}}


def _relationship_row(row: dict[str, Any]) -> dict[str, Any]:
    properties = {key: row[key] for key in RELATIONSHIP_PROPERTY_FIELDS if row.get(key) is not None}
    return {"source_id": row["source_id"], "target_id": row["target_id"], "properties": properties}


@dataclass
class LoadStep:
    """실행할 쓰기 단계 하나 = 쿼리 하나 + 배치 하나(행 목록). kind: node / relationship"""
    kind: str
    name: str
    query: str
    rows: list[dict[str, Any]]
    batch_index: int


def build_load_plan(graph: GraphInput, batch_size: int = DEFAULT_BATCH_SIZE) -> list[LoadStep]:
    """
    검증된 입력으로 쓰기 단계 목록을 만든다. (DB에 접속하지 않는다)
    순서: 노드 9종 → 관계 11종. 관계는 양끝 노드가 먼저 있어야 하므로 노드를 모두 끝낸 뒤 실행한다.
    검증 실패면 GraphValidationError.
    """
    report = validate_graph_input(graph)
    if not report.ok:
        raise GraphValidationError(f"입력 검증 실패 → 적재하지 않음: {report.summary()['error_counts']}")
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")
    steps: list[LoadStep] = []
    for label in LABEL_ID_FIELDS:
        rows = [_node_row(label, node) for node in graph.nodes_by_label.get(label, [])]
        query = build_node_merge_query(label)
        for number, start in enumerate(range(0, len(rows), batch_size)):
            steps.append(LoadStep("node", label, query, rows[start:start + batch_size], number))
    by_relation: dict[str, list[dict[str, Any]]] = {}
    for row in graph.relationships:
        by_relation.setdefault(row["relation"], []).append(_relationship_row(row))
    for relation in RELATIONSHIP_SPECS:
        rows = by_relation.get(relation, [])
        query = build_relationship_merge_query(relation)
        for number, start in enumerate(range(0, len(rows), batch_size)):
            steps.append(LoadStep("relationship", relation, query, rows[start:start + batch_size], number))
    return steps


# ---------------------------------------------------------------------------
# 5. 실행 (DB 연결은 실행기로 주입)
# ---------------------------------------------------------------------------
class GraphRunner(Protocol):
    """
    DB 연결을 감싸는 실행기. 실제 실행기(Neo4jDriverRunner)와 테스트용 가짜 실행기가 같은 모양을 따른다.
    - run_schema(statement): 스키마 문장 1개를 자체 트랜잭션으로 실행
    - write_batch(query, rows): 쓰기 트랜잭션 1개에서 실행하고 merged 건수를 돌려준다.
                                expected와 다르면 트랜잭션 안에서 예외를 내 롤백해야 한다.
    - read_rows(query, parameters): 읽기 쿼리 결과 행 목록 (읽기 전용 세션). 값은 parameters로만 넘긴다.
    - read_count(query): 읽기 쿼리의 count 값 (읽기 전용 세션)
    """
    def run_schema(self, statement: str) -> None: ...
    def write_batch(self, query: str, rows: list[dict[str, Any]], expected: int) -> int: ...
    def read_rows(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def read_count(self, query: str) -> int: ...


# neo4j.READ_ACCESS / WRITE_ACCESS 값과 같다. (이 모듈은 neo4j 패키지를 import하지 않으므로 문자열로 둔다)
READ_ACCESS_MODE = "READ"
WRITE_ACCESS_MODE = "WRITE"


def _notification_records(summary: Any, query: str) -> list[dict[str, Any]]:
    """드라이버 ResultSummary의 알림을 dict로 바꾼다. 알림을 끄지 않고 모아서 classify_notifications로 분류한다."""
    records = []
    for item in getattr(summary, "summary_notifications", None) or []:
        severity = getattr(item, "severity_level", None)
        category = getattr(item, "category", None)
        records.append({"code": getattr(item, "code", None),
                        "severity": getattr(severity, "name", severity),
                        "category": getattr(category, "name", category),
                        "title": getattr(item, "title", None),
                        "query": query.splitlines()[0][:80] if query else ""})
    return records


class Neo4jDriverRunner:
    """
    neo4j 공식 드라이버를 감싼 실행기. 드라이버 객체는 밖에서 만들어 넘긴다. (이 모듈은 neo4j 패키지를 import하지 않는다)
    - write_batch는 session.execute_write(트랜잭션 함수)를 쓴다 → 함수 안에서 예외가 나면 그 배치 전체가 롤백된다.
    - read_rows·read_count는 읽기 전용 세션(default_access_mode="READ")에서 실행한다 → 실수로 쓰기 쿼리를 넣으면 서버가 거부한다.
    - 서버 알림(경고)은 끄지 않고 self.notifications에 모은다. 분류는 classify_notifications.
    """

    def __init__(self, driver, database: str | None = None):
        self.driver = driver
        self.database = database
        self.notifications: list[dict[str, Any]] = []

    def _session(self, access_mode: str):
        kwargs: dict[str, Any] = {"default_access_mode": access_mode}
        if self.database:
            kwargs["database"] = self.database
        return self.driver.session(**kwargs)

    def run_schema(self, statement: str) -> None:
        with self._session(WRITE_ACCESS_MODE) as session:
            summary = session.run(statement).consume()
        self.notifications.extend(_notification_records(summary, statement))

    def write_batch(self, query: str, rows: list[dict[str, Any]], expected: int) -> int:
        attempt_notes: list[dict[str, Any]] = []

        def work(tx):
            attempt_notes.clear()   # execute_write가 일시 오류로 재시도하면 마지막 시도의 알림만 남긴다
            result = tx.run(query, rows=rows)
            merged = result.single()["merged"]
            if merged != expected:
                raise GraphLoadError(f"MERGE 건수 불일치: 기대 {expected}, 실제 {merged} → 이 배치 롤백")
            attempt_notes.extend(_notification_records(result.consume(), query))
            return merged

        with self._session(WRITE_ACCESS_MODE) as session:
            merged = session.execute_write(work)
        self.notifications.extend(attempt_notes)
        return merged

    def read_rows(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session(READ_ACCESS_MODE) as session:
            result = session.run(query, parameters or {})
            rows = [record.data() for record in result]
            summary = result.consume()
        self.notifications.extend(_notification_records(summary, query))
        return rows

    def read_count(self, query: str) -> int:
        return self.read_rows(query)[0]["count"]


def snapshot_counts(runner: GraphRunner) -> dict[str, int]:
    """레이블별 노드 수·관계 타입별 관계 수 (읽기 전용). 적재 전 "빈 DB인가" 확인과 적재 후 검증에 쓴다."""
    return {key: runner.read_count(query) for key, query in build_count_queries().items()}


def expected_counts(graph: GraphInput) -> dict[str, int]:
    """입력 파일에서 센 기대 건수 (상수가 아니라 실제 파일 기준)."""
    report = validate_graph_input(graph)
    counts = {f"node:{label}": report.node_counts.get(label, 0) for label in LABEL_ID_FIELDS}
    counts.update({f"rel:{relation}": report.relationship_counts.get(relation, 0) for relation in RELATIONSHIP_SPECS})
    return counts


# ---------------------------------------------------------------------------
# 5-1. 실제 DB 검증 (읽기 전용) — 건수만이 아니라 "무엇이 들어 있는가"를 원본과 비교한다
# ---------------------------------------------------------------------------
# 모든 레이블의 고유 ID 필드 이름 (TargetGroup·ConditionGroup은 둘 다 group_id → 레이블로 구분한다)
ID_PROPERTY_FIELDS: tuple[str, ...] = tuple(sorted(set(LABEL_ID_FIELDS.values())))
# SHOW CONSTRAINTS의 고유 제약 type 값. Neo4j 5는 "UNIQUENESS", 이후 Cypher 버전은 "NODE_PROPERTY_UNIQUENESS"로 표기한다.
UNIQUENESS_CONSTRAINT_TYPES = ("UNIQUENESS", "NODE_PROPERTY_UNIQUENESS")
EXPECTED_INDEX_TYPE = "RANGE"          # CREATE INDEX 기본 유형 (Neo4j 5)
# 빈 DB의 MATCH (n:없는레이블) 등에서 나오는 "모르는 이름" 알림. 적재 전에는 정상이라 따로 센다.
UNKNOWN_TOKEN_NOTIFICATION_SUFFIXES = ("UnknownLabelWarning", "UnknownRelationshipTypeWarning",
                                       "UnknownPropertyKeyWarning")
# CREATE … IF NOT EXISTS가 이미 있는 스키마라 아무 일도 하지 않았다는 정보 알림. 재실행(allow_existing=True)에서는 정상.
SCHEMA_ALREADY_EXISTS_NOTIFICATION_SUFFIXES = ("IndexOrConstraintAlreadyExists",)

QUERY_TOTAL_COUNTS = ("MATCH (n) WITH count(n) AS nodes "
                      "OPTIONAL MATCH ()-[r]->() RETURN nodes, count(r) AS relationships")
QUERY_SHOW_CONSTRAINTS = "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties, ownedIndex"
QUERY_SHOW_INDEXES = ("SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state, "
                      "owningConstraint")


class PreflightError(GraphLoadError):
    """적재 직전 DB 점검 실패. 아무것도 쓰지 않았다. report 속성에 점검 결과가 있다."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


class DatabaseNotEmptyError(PreflightError):
    """DB에 이미 노드·관계가 있는데 allow_existing=False로 적재하려 했다."""


class SchemaConflictError(PreflightError):
    """기존 제약조건·인덱스가 기대 스키마와 충돌한다 (같은 이름·다른 정의, 다른 이름·같은 정의, 예상 밖 스키마)."""


class ExistingDataMismatchError(PreflightError):
    """allow_existing=True인데 기존 데이터가 입력의 부분집합이 아니다 (추가·중복·레이블 오류 등)."""


def _id_projection(variable: str) -> str:
    """n {.agency_id, .category_id, ...} — 모든 ID 필드를 한 번에 가져오는 맵 투영 (필드 이름은 허용 목록에서만)."""
    for name in ID_PROPERTY_FIELDS:
        if not _IDENTIFIER.match(name):
            raise GraphValidationError(f"허용되지 않은 속성 이름: {name!r}")
    return f"{variable} {{{', '.join('.' + name for name in ID_PROPERTY_FIELDS)}}}"


def build_node_identity_query() -> str:
    """
    DB의 "모든" 노드의 레이블과 ID 필드를 읽는다 (읽기 전용).
    레이블 없이 MATCH (n)을 쓰는 이유: 허용 목록 밖 레이블·레이블 없는 노드까지 찾아내야 하기 때문이다.
    """
    return f"MATCH (n) RETURN labels(n) AS labels, {_id_projection('n')} AS keys"


def build_relationship_endpoint_query() -> str:
    """DB의 "모든" 관계의 타입과 양끝 노드 레이블·ID 필드를 읽는다 (읽기 전용). 허용 목록 밖 타입도 찾기 위해 타입을 지정하지 않는다."""
    return ("MATCH (s)-[r]->(t) RETURN type(r) AS type, labels(s) AS source_labels, labels(t) AS target_labels, "
            f"{_id_projection('s')} AS source_keys, {_id_projection('t')} AS target_keys")


def expected_schema() -> dict[str, dict[str, dict[str, Any]]]:
    """build_schema_statements와 같은 내용을 비교용 정의로 만든다. {"constraints": {이름: 정의}, "indexes": {이름: 정의}}"""
    constraints = {name: {"entityType": "NODE", "labelsOrTypes": [label], "properties": [LABEL_ID_FIELDS[label]]}
                   for label, name in CONSTRAINT_NAMES.items()}
    indexes = {name: {"type": EXPECTED_INDEX_TYPE, "entityType": "NODE", "labelsOrTypes": [label], "properties": [prop]}
               for name, label, prop in INDEX_SPECS}
    return {"constraints": constraints, "indexes": indexes}


def _definition(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: (list(row.get(key)) if isinstance(row.get(key), (list, tuple)) else row.get(key)) for key in keys}


def _compare_named(actual_rows: list[dict[str, Any]], expected: dict[str, dict[str, Any]],
                   keys: tuple[str, ...], type_ok) -> dict[str, list[Any]]:
    """이름·정의 비교. type_ok(row)는 유형이 기대와 맞는지 판정한다."""
    result: dict[str, list[Any]] = {"matched": [], "missing": [], "definition_mismatch": [],
                                    "same_definition_other_name": [], "unexpected": []}
    by_name = {row["name"]: row for row in actual_rows}
    expected_defs = {name: definition for name, definition in expected.items()}
    for name, definition in expected_defs.items():
        row = by_name.get(name)
        if row is None:
            result["missing"].append(name)
        elif _definition(row, keys) == definition and type_ok(row):
            result["matched"].append(name)
        else:
            result["definition_mismatch"].append({"name": name, "expected": definition,
                                                  "actual": {**_definition(row, keys), "type": row.get("type")}})
    for row in actual_rows:
        if row["name"] in expected_defs:
            continue
        same = [name for name, definition in expected_defs.items()
                if _definition(row, keys) == definition and type_ok(row)]
        if same:
            # CREATE … IF NOT EXISTS는 같은 정의가 다른 이름으로 있으면 조용히 건너뛴다 → 기대 이름이 끝내 생기지 않는다.
            result["same_definition_other_name"].append({"expected_name": same[0], "actual_name": row["name"]})
        else:
            result["unexpected"].append({**_definition(row, keys), "name": row["name"], "type": row.get("type")})
    return result


def verify_schema(runner: GraphRunner, phase: str = "post") -> dict[str, Any]:
    """
    실제 제약조건·인덱스를 SHOW로 읽어 기대 스키마(제약 9 + 인덱스 4)와 비교한다. (읽기 전용)
    비교 기준: 이름 + 레이블 + 속성 + 유형(고유 제약 / RANGE 인덱스) + 인덱스 ONLINE 상태
    phase="pre"  (적재 전): 기대 스키마 일부·전부가 이미 똑같이 있는 것은 괜찮다(IF NOT EXISTS로 건너뜀).
                            같은 이름·다른 정의, 다른 이름·같은 정의, 예상 밖 스키마, ONLINE 아닌 인덱스는 실패.
    phase="post" (적재 후): 위 조건 + 기대 13개가 모두 정확히 있어야 통과.
    Neo4j 기본 LOOKUP 인덱스(레이블·속성 없음)와 고유 제약이 자동으로 만든 인덱스는 "예상 밖"으로 치지 않고 따로 적는다.
    """
    if phase not in ("pre", "post"):
        raise ValueError("phase는 'pre' 또는 'post'")
    expected = expected_schema()
    constraint_rows = runner.read_rows(QUERY_SHOW_CONSTRAINTS)
    index_rows = runner.read_rows(QUERY_SHOW_INDEXES)

    constraints = _compare_named(constraint_rows, expected["constraints"],
                                 ("entityType", "labelsOrTypes", "properties"),
                                 lambda row: row.get("type") in UNIQUENESS_CONSTRAINT_TYPES)

    default_lookup = [row["name"] for row in index_rows
                      if row.get("type") == "LOOKUP" and not row.get("labelsOrTypes") and not row.get("properties")]
    constraint_owned = [row["name"] for row in index_rows if row.get("owningConstraint")]
    plain_rows = [row for row in index_rows
                  if row["name"] not in default_lookup and row["name"] not in constraint_owned]
    indexes = _compare_named(plain_rows, expected["indexes"], ("type", "entityType", "labelsOrTypes", "properties"),
                             lambda row: True)
    # 고유 제약이 만든 인덱스는 반드시 그 제약 이름과 같아야 한다 (Neo4j 5 규칙). 다르면 예상 밖으로 본다.
    owned_mismatch = [{"index": row["name"], "owningConstraint": row["owningConstraint"]} for row in index_rows
                      if row.get("owningConstraint") and row["name"] != row["owningConstraint"]]
    not_online = [{"name": row["name"], "state": row.get("state")} for row in index_rows if row.get("state") != "ONLINE"]

    problems = {
        "constraint_definition_mismatch": constraints["definition_mismatch"],
        "constraint_same_definition_other_name": constraints["same_definition_other_name"],
        "unexpected_constraints": constraints["unexpected"],
        "index_definition_mismatch": indexes["definition_mismatch"],
        "index_same_definition_other_name": indexes["same_definition_other_name"],
        "unexpected_indexes": indexes["unexpected"],
        "constraint_owned_index_name_mismatch": owned_mismatch,
        "index_not_online": not_online,
    }
    if phase == "post":
        problems["missing_constraints"] = constraints["missing"]
        problems["missing_indexes"] = indexes["missing"]
    return {
        "phase": phase,
        "ok": not any(problems.values()),
        "problems": {key: value for key, value in problems.items() if value},
        "constraints": {"expected": len(expected["constraints"]), "matched": sorted(constraints["matched"]),
                        "missing": sorted(constraints["missing"])},
        "indexes": {"expected": len(expected["indexes"]), "matched": sorted(indexes["matched"]),
                    "missing": sorted(indexes["missing"])},
        "constraint_owned_indexes": sorted(constraint_owned),
        "default_lookup_indexes": sorted(default_lookup),
        "actual_constraint_types": sorted({str(row.get("type")) for row in constraint_rows}),
    }


def _expected_node_ids(graph: GraphInput) -> dict[str, set[str]]:
    return {label: {node[LABEL_ID_FIELDS[label]] for node in graph.nodes_by_label.get(label, [])}
            for label in LABEL_ID_FIELDS}


def verify_node_ids(graph: GraphInput, runner: GraphRunner) -> dict[str, Any]:
    """
    원본 노드 ID와 DB 노드 ID를 레이블별 "집합"으로 비교한다. (읽기 전용, 건수만 비교하지 않는다)
    찾는 문제: missing(원본에 있고 DB에 없음), extra(DB에만 있음), duplicate(같은 레이블·ID 노드 2개 이상),
              missing_id(레이블은 맞는데 ID 필드가 없음), unknown_label(허용 목록 밖 레이블),
              multi_label(레이블 2개 이상), unlabeled(레이블 없음)
    """
    expected = _expected_node_ids(graph)
    actual: dict[str, Counter] = {label: Counter() for label in LABEL_ID_FIELDS}
    missing_id: Counter = Counter()
    unknown_label: Counter = Counter()
    multi_label: Counter = Counter()
    unlabeled = 0
    for row in runner.read_rows(build_node_identity_query()):
        labels = list(row.get("labels") or [])
        if not labels:
            unlabeled += 1
            continue
        if len(labels) > 1:
            multi_label[tuple(sorted(labels))] += 1
            continue
        label = labels[0]
        if label not in LABEL_ID_FIELDS:
            unknown_label[label] += 1
            continue
        node_id = (row.get("keys") or {}).get(LABEL_ID_FIELDS[label])
        if not isinstance(node_id, str) or not node_id:
            missing_id[label] += 1
            continue
        actual[label][node_id] += 1

    missing = {label: sorted(expected[label] - set(actual[label])) for label in LABEL_ID_FIELDS}
    extra = {label: sorted(set(actual[label]) - expected[label]) for label in LABEL_ID_FIELDS}
    duplicate = {label: sorted(node_id for node_id, count in actual[label].items() if count > 1)
                 for label in LABEL_ID_FIELDS}
    problems = {
        "missing": {label: ids for label, ids in missing.items() if ids},
        "extra": {label: ids for label, ids in extra.items() if ids},
        "duplicate": {label: ids for label, ids in duplicate.items() if ids},
        "missing_id": dict(missing_id),
        "unknown_label": dict(unknown_label),
        "multi_label": {"+".join(key): count for key, count in multi_label.items()},
        "unlabeled": unlabeled,
    }
    return {
        "ok": not any(problems.values()),
        "problems": {key: value for key, value in problems.items() if value},
        "expected_by_label": {label: len(ids) for label, ids in expected.items()},
        "actual_by_label": {label: sum(counter.values()) for label, counter in actual.items()},
        "expected_total": sum(len(ids) for ids in expected.values()),
        "actual_total": sum(sum(counter.values()) for counter in actual.values()) + sum(missing_id.values())
        + sum(unknown_label.values()) + sum(multi_label.values()) + unlabeled,
    }


def _expected_relationship_keys(graph: GraphInput) -> Counter:
    keys: Counter = Counter()
    for row in graph.relationships:
        source_label, target_label = RELATIONSHIP_SPECS[row["relation"]]
        keys[(row["relation"], source_label, row["source_id"], target_label, row["target_id"])] += 1
    return keys


def verify_relationship_endpoints(graph: GraphInput, runner: GraphRunner) -> dict[str, Any]:
    """
    관계마다 (타입, 출발 레이블, 출발 ID, 도착 레이블, 도착 ID)를 DB에서 읽어 원본 relationships와 비교한다. (읽기 전용)
    "관계 3,615개"라는 건수가 맞아도, 엉뚱한 노드에 붙은 관계가 있으면 여기서 실패한다.
    찾는 문제: missing, extra, duplicate(같은 키 2개 이상), wrong_label(타입은 허용인데 양끝 레이블이 사양과 다름),
              unknown_type(허용 목록 밖 타입), endpoint_label_issue(양끝 노드가 레이블 0개·2개 이상·허용 밖),
              endpoint_missing_id(양끝 노드에 ID 필드 없음)
    """
    expected = _expected_relationship_keys(graph)
    actual: Counter = Counter()
    wrong_label: Counter = Counter()
    unknown_type: Counter = Counter()
    endpoint_label_issue: Counter = Counter()
    endpoint_missing_id: Counter = Counter()
    for row in runner.read_rows(build_relationship_endpoint_query()):
        relation = row.get("type")
        if relation not in RELATIONSHIP_SPECS:
            unknown_type[relation] += 1
            continue
        source_labels, target_labels = list(row.get("source_labels") or []), list(row.get("target_labels") or [])
        if len(source_labels) != 1 or len(target_labels) != 1 or source_labels[0] not in LABEL_ID_FIELDS \
                or target_labels[0] not in LABEL_ID_FIELDS:
            endpoint_label_issue[(relation, "+".join(source_labels), "+".join(target_labels))] += 1
            continue
        source_label, target_label = source_labels[0], target_labels[0]
        if (source_label, target_label) != RELATIONSHIP_SPECS[relation]:
            wrong_label[(relation, source_label, target_label)] += 1
            continue
        source_id = (row.get("source_keys") or {}).get(LABEL_ID_FIELDS[source_label])
        target_id = (row.get("target_keys") or {}).get(LABEL_ID_FIELDS[target_label])
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            endpoint_missing_id[relation] += 1
            continue
        actual[(relation, source_label, source_id, target_label, target_id)] += 1

    missing = sorted(key for key in expected if key not in actual)
    extra = sorted(key for key in actual if key not in expected)
    duplicate = sorted([key, count] for key, count in actual.items() if count > 1)
    problems = {
        "missing": missing, "extra": extra, "duplicate": duplicate,
        "wrong_label": [[*key, count] for key, count in sorted(wrong_label.items())],
        "unknown_type": dict(unknown_type),
        "endpoint_label_issue": [[*key, count] for key, count in sorted(endpoint_label_issue.items())],
        "endpoint_missing_id": dict(endpoint_missing_id),
        # 원본 자체에 같은 키가 2번 있으면 MERGE가 하나로 합친다 (validate_graph_input이 먼저 막는다)
        "input_duplicate": sorted([key, count] for key, count in expected.items() if count > 1),
    }
    expected_by_type = Counter(key[0] for key in expected.elements())
    actual_by_type = Counter(key[0] for key in actual.elements())
    return {
        "ok": not any(problems.values()),
        "problems": {key: value for key, value in problems.items() if value},
        "expected_by_type": {relation: expected_by_type.get(relation, 0) for relation in RELATIONSHIP_SPECS},
        "actual_by_type": {relation: actual_by_type.get(relation, 0) for relation in RELATIONSHIP_SPECS},
        "expected_total": sum(expected.values()),
        "actual_total": sum(actual.values()) + sum(wrong_label.values()) + sum(unknown_type.values())
        + sum(endpoint_label_issue.values()) + sum(endpoint_missing_id.values()),
    }


# ---------------------------------------------------------------------------
# 5-2. 속성값 검증 (읽기 전용) — ID·연결이 맞아도 값이 다르면 실패
# ---------------------------------------------------------------------------
# 관계 입력 줄에서 "속성으로 저장하지 않는" 필드와 이유. 이 필드는 속성 비교 대상에서 제외하고,
# 대신 verify_relationship_endpoints가 관계 타입·양끝 노드로 검증한다.
RELATIONSHIP_STRUCTURAL_FIELDS: dict[str, str] = {
    "source_id": "출발 노드 고유 ID → 관계의 출발 노드로 표현 (verify_relationship_endpoints에서 검증)",
    "relation": "관계 타입 → type(r)로 표현 (verify_relationship_endpoints에서 검증)",
    "target_id": "도착 노드 고유 ID → 관계의 도착 노드로 표현 (verify_relationship_endpoints에서 검증)",
}
# 속성 비교 예시를 보고서에 몇 개까지 담을지 (전체 건수는 따로 센다)
PROPERTY_EXAMPLE_LIMIT = 20


def build_node_property_query(label: str) -> str:
    """레이블 하나의 노드 고유 ID와 전체 속성 (읽기 전용)."""
    label = _checked_label(label)
    return f"MATCH (n:{label}) RETURN n.{LABEL_ID_FIELDS[label]} AS id, properties(n) AS properties"


def build_relationship_property_query(relation: str) -> str:
    """관계 타입 하나의 양끝 고유 ID와 관계 전체 속성 (읽기 전용). 양끝 레이블은 사양대로 지정한다."""
    relation = _checked_relation(relation)
    source_label, target_label = RELATIONSHIP_SPECS[relation]
    return (f"MATCH (s:{source_label})-[r:{relation}]->(t:{target_label}) "
            f"RETURN s.{LABEL_ID_FIELDS[source_label]} AS source_id, t.{LABEL_ID_FIELDS[target_label]} AS target_id, "
            f"properties(r) AS properties")


def value_kind(value: Any) -> str:
    """비교용 값 종류. bool은 int와 구분하고, 리스트는 원소 종류까지 적는다. 예) "list[str]", "list[empty]" """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float, str)):
        return type(value).__name__
    if isinstance(value, (list, tuple)):
        if not value:
            return "list[empty]"
        return "list[" + ",".join(sorted({value_kind(item) for item in value})) + "]"
    return type(value).__name__


def expected_stored_properties(properties: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """
    원본 속성 → Neo4j에 실제로 남아야 하는 속성. 반환: (기대 속성, null이라 저장되지 않는 키 목록)
    Neo4j는 null을 속성값으로 저장하지 않는다. (SET n += {k: null}은 그 속성을 만들지 않거나 없앤다)
    → 데이터 계약(§3)의 null은 "값 없음"이므로, DB에서는 "속성 없음"으로 기대한다. 다른 값으로 바꾸지 않는다.
    그 밖의 정규화는 하지 않는다. (JSON 문자열·숫자·불리언·문자열 리스트는 드라이버가 같은 Python 타입으로 돌려준다)
    """
    stored = {key: value for key, value in properties.items() if value is not None}
    return stored, sorted(key for key, value in properties.items() if value is None)


def _same_value(expected: Any, actual: Any) -> bool:
    """종류가 같은 두 값이 같은가. 리스트는 순서까지 같아야 한다 (Neo4j는 리스트 순서를 보존한다)."""
    if isinstance(expected, (list, tuple)):
        return list(expected) == list(actual)
    return expected == actual


def _short(value: Any, limit: int = 120) -> Any:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


class _PropertyDiff:
    """속성 비교 결과 누적 (missing / extra / type_mismatch / value_mismatch)."""

    KINDS = ("missing_property", "extra_property", "type_mismatch", "value_mismatch")

    def __init__(self):
        self.counts: Counter = Counter()
        self.by_key: dict[str, Counter] = {kind: Counter() for kind in self.KINDS}
        self.examples: dict[str, list[dict[str, Any]]] = {kind: [] for kind in self.KINDS}

    def add(self, kind: str, owner: dict[str, Any], key: str, expected: Any = None, actual: Any = None) -> None:
        self.counts[kind] += 1
        self.by_key[kind][key] += 1
        if len(self.examples[kind]) < PROPERTY_EXAMPLE_LIMIT:
            self.examples[kind].append({**owner, "property": key,
                                        "expected_kind": value_kind(expected) if kind != "extra_property" else None,
                                        "actual_kind": value_kind(actual) if kind != "missing_property" else None,
                                        "expected": _short(expected) if kind != "extra_property" else None,
                                        "actual": _short(actual) if kind != "missing_property" else None})

    def compare(self, owner: dict[str, Any], expected: dict[str, Any], actual: dict[str, Any]) -> None:
        for key, value in expected.items():
            if key not in actual:
                self.add("missing_property", owner, key, expected=value)
            elif value_kind(value) != value_kind(actual[key]):
                self.add("type_mismatch", owner, key, expected=value, actual=actual[key])
            elif not _same_value(value, actual[key]):
                self.add("value_mismatch", owner, key, expected=value, actual=actual[key])
        for key, value in actual.items():
            if key not in expected:
                self.add("extra_property", owner, key, actual=value)

    def summary(self) -> dict[str, Any]:
        return {"counts": {kind: self.counts.get(kind, 0) for kind in self.KINDS},
                "by_property": {kind: dict(counter) for kind, counter in self.by_key.items() if counter},
                "examples": {kind: items for kind, items in self.examples.items() if items}}


def verify_node_properties(graph: GraphInput, runner: GraphRunner) -> dict[str, Any]:
    """
    원본 노드 속성과 DB 노드 속성을 레이블별로 비교한다. (읽기 전용, DB를 고치지 않는다)
    - 기대 속성 = 원본 줄 전체(고유 ID 포함) − null 값 (expected_stored_properties)
    - 구분: missing_property(원본에 있는데 DB에 없음), extra_property(DB에만 있음),
            type_mismatch(값 종류가 다름: 예 str ↔ int, list[str] ↔ str), value_mismatch(종류는 같고 값이 다름)
    - 노드 자체의 누락·추가는 verify_node_ids가 다룬다. 여기서는 양쪽에 다 있는 노드만 비교하고, 짝이 없는 수만 센다.
    """
    diff = _PropertyDiff()
    null_keys: Counter = Counter()
    unmatched = {"missing_node": 0, "extra_node": 0, "duplicate_node_rows": 0}
    compared = 0
    for label, id_field in LABEL_ID_FIELDS.items():
        rows = runner.read_rows(build_node_property_query(label))
        actual_by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["id"] in actual_by_id:
                unmatched["duplicate_node_rows"] += 1
            actual_by_id[row["id"]] = row["properties"] or {}
        expected_ids = set()
        for node in graph.nodes_by_label.get(label, []):
            node_id = node[id_field]
            expected_ids.add(node_id)
            expected, nulls = expected_stored_properties(node)
            null_keys.update(f"{label}.{key}" for key in nulls)
            if node_id not in actual_by_id:
                unmatched["missing_node"] += 1
                continue
            compared += 1
            diff.compare({"label": label, "id": node_id}, expected, actual_by_id[node_id])
        unmatched["extra_node"] += len(set(actual_by_id) - expected_ids)
    summary = diff.summary()
    return {"ok": not any(summary["counts"].values()) and not any(unmatched.values()),
            "compared_nodes": compared, **summary, "unmatched": unmatched,
            "null_properties_not_stored": dict(null_keys),
            "normalization": "null 값은 저장되지 않는 것으로 기대(속성 없음). 그 밖의 값은 정규화 없이 종류·값을 그대로 비교."}


def verify_relationship_properties(graph: GraphInput, runner: GraphRunner) -> dict[str, Any]:
    """
    원본 관계 속성과 DB 관계 속성을 관계 타입별로 비교한다. (읽기 전용)
    - 기대 속성 = 원본 줄에서 RELATIONSHIP_PROPERTY_FIELDS 중 null이 아닌 것 (적재 쿼리 _relationship_row와 같은 규칙)
    - RELATIONSHIP_STRUCTURAL_FIELDS(source_id·relation·target_id)는 구조로 표현되므로 비교에서 제외한다.
    - 그 밖에 원본에 있는데 적재하지 않는 필드가 있으면 input_fields_not_loaded로 보고한다 (조용히 버리지 않는다).
    """
    diff = _PropertyDiff()
    null_keys: Counter = Counter()
    not_loaded: Counter = Counter()
    unmatched = {"missing_relationship": 0, "extra_relationship": 0, "duplicate_relationship_rows": 0}
    compared = 0
    by_relation: dict[str, list[dict[str, Any]]] = {}
    for row in graph.relationships:
        by_relation.setdefault(row["relation"], []).append(row)
        for key in row:
            if key not in RELATIONSHIP_STRUCTURAL_FIELDS and key not in RELATIONSHIP_PROPERTY_FIELDS:
                not_loaded[key] += 1
    for relation in RELATIONSHIP_SPECS:
        actual: dict[tuple[str, str], dict[str, Any]] = {}
        for row in runner.read_rows(build_relationship_property_query(relation)):
            key = (row["source_id"], row["target_id"])
            if key in actual:
                unmatched["duplicate_relationship_rows"] += 1
            actual[key] = row["properties"] or {}
        expected_keys = set()
        for row in by_relation.get(relation, []):
            key = (row["source_id"], row["target_id"])
            expected_keys.add(key)
            loaded = {name: row[name] for name in RELATIONSHIP_PROPERTY_FIELDS if name in row}
            expected, nulls = expected_stored_properties(loaded)
            null_keys.update(f"{relation}.{name}" for name in nulls)
            if key not in actual:
                unmatched["missing_relationship"] += 1
                continue
            compared += 1
            diff.compare({"relation": relation, "source_id": key[0], "target_id": key[1]}, expected, actual[key])
        unmatched["extra_relationship"] += len(set(actual) - expected_keys)
    summary = diff.summary()
    return {"ok": not any(summary["counts"].values()) and not any(unmatched.values()) and not not_loaded,
            "compared_relationships": compared, **summary, "unmatched": unmatched,
            "null_properties_not_stored": dict(null_keys), "input_fields_not_loaded": dict(not_loaded),
            "excluded_structural_fields": dict(RELATIONSHIP_STRUCTURAL_FIELDS)}


def verify_loaded_graph(graph: GraphInput, runner: GraphRunner) -> dict[str, Any]:
    """적재 후 종합 검증: 건수 + 스키마(post) + 노드 ID 집합 + 관계 양끝 ID 집합 + 노드·관계 속성값. 하나라도 실패면 ok=False."""
    expected = expected_counts(graph)
    after = snapshot_counts(runner)
    count_mismatches = {key: {"expected": expected[key], "actual": after.get(key)}
                        for key in expected if after.get(key) != expected[key]}
    totals = runner.read_rows(QUERY_TOTAL_COUNTS)[0]
    totals_ok = (totals["nodes"] == sum(len(rows) for rows in graph.nodes_by_label.values())
                 and totals["relationships"] == len(graph.relationships))
    schema = verify_schema(runner, phase="post")
    nodes = verify_node_ids(graph, runner)
    relationships = verify_relationship_endpoints(graph, runner)
    node_properties = verify_node_properties(graph, runner)
    relationship_properties = verify_relationship_properties(graph, runner)
    return {
        "ok": (not count_mismatches and totals_ok and schema["ok"] and nodes["ok"] and relationships["ok"]
               and node_properties["ok"] and relationship_properties["ok"]),
        "totals": dict(totals), "totals_ok": totals_ok,
        "after_counts": after, "expected_counts": expected, "count_mismatches": count_mismatches,
        "schema": schema, "nodes": nodes, "relationships": relationships,
        "node_properties": node_properties, "relationship_properties": relationship_properties,
    }


def preflight_database(graph: GraphInput, runner: GraphRunner, *, allow_existing: bool = False) -> dict[str, Any]:
    """
    쓰기 직전 DB 점검 (읽기 전용). 통과하지 못하면 PreflightError를 내고, 이 경우 load_graph는 아무것도 쓰지 않는다.
    1) 스키마 pre 검증 실패 → SchemaConflictError
    2) 노드·관계가 0개 → state="empty" (적재 가능)
    3) 데이터가 있는데 allow_existing=False → DatabaseNotEmptyError (기본 동작: 빈 DB에만 적재)
    4) allow_existing=True (승인된 재실행 경로): 기존 데이터가 입력의 "부분집합"인지 확인한다.
       extra·duplicate·레이블 오류·ID 없음 등이 하나라도 있으면 ExistingDataMismatchError.
       부족분(missing)만 있으면 state="partial"(중단된 적재 이어서 완성), 없으면 state="complete"(재적재 = 변화 없어야 함).
    ※ 기존 데이터는 어떤 경우에도 지우거나 고치지 않는다.
    """
    totals = runner.read_rows(QUERY_TOTAL_COUNTS)[0]
    schema = verify_schema(runner, phase="pre")
    report: dict[str, Any] = {"allow_existing": allow_existing, "totals": dict(totals), "schema": schema}
    if not schema["ok"]:
        report["state"] = "schema_conflict"
        raise SchemaConflictError(f"기존 스키마가 기대 스키마와 충돌 → 적재하지 않음: {sorted(schema['problems'])}", report)
    if totals["nodes"] == 0 and totals["relationships"] == 0:
        report["state"] = "empty"
        return report
    if not allow_existing:
        report["state"] = "not_empty"
        raise DatabaseNotEmptyError(
            f"DB가 비어 있지 않음(노드 {totals['nodes']}, 관계 {totals['relationships']}) → 적재하지 않음. "
            "같은 데이터의 재실행이면 allow_existing=True로 명시한다.", report)
    nodes = verify_node_ids(graph, runner)
    relationships = verify_relationship_endpoints(graph, runner)
    report["nodes"], report["relationships"] = nodes, relationships
    foreign = {f"node:{key}": value for key, value in nodes["problems"].items() if key != "missing"}
    foreign.update({f"relationship:{key}": value for key, value in relationships["problems"].items() if key != "missing"})
    if foreign:
        report["state"] = "foreign_data"
        raise ExistingDataMismatchError(f"기존 데이터가 입력과 맞지 않음 → 적재하지 않음: {sorted(foreign)}", report)
    has_missing = "missing" in nodes["problems"] or "missing" in relationships["problems"]
    report["state"] = "partial" if has_missing else "complete"
    return report


def classify_notifications(notifications: list[dict[str, Any]]) -> dict[str, Any]:
    """
    서버 알림 분류. 알림을 끄지 않고 모은 뒤 나눈다.
    - unknown_token: 아직 없는 레이블·관계 타입·속성 이름 경고 (빈 DB 점검·적재 전 조회에서는 정상)
    - schema_already_exists: IF NOT EXISTS 스키마 문장이 이미 있는 스키마라 건너뛰었다는 정보 (재실행에서는 정상.
                             첫 적재에서 나오면 기존 스키마가 있었다는 뜻이므로 preflight 결과와 함께 본다)
    - needs_review: 그 밖의 모든 알림 (성능·문법·사용 중단 경고 등) → 사람이 확인해야 한다
    """
    by_code = Counter(str(item.get("code")) for item in notifications)
    unknown = [item for item in notifications
               if str(item.get("code", "")).endswith(UNKNOWN_TOKEN_NOTIFICATION_SUFFIXES)]
    already = [item for item in notifications
               if str(item.get("code", "")).endswith(SCHEMA_ALREADY_EXISTS_NOTIFICATION_SUFFIXES)]
    review = [item for item in notifications if item not in unknown and item not in already]
    return {"total": len(notifications), "by_code": dict(by_code), "unknown_token": len(unknown),
            "schema_already_exists": len(already), "needs_review": review}


def load_graph(graph: GraphInput, runner: GraphRunner, *, batch_size: int = DEFAULT_BATCH_SIZE,
               apply_schema: bool = True, allow_existing: bool = False, verify: bool = True) -> dict[str, Any]:
    """
    전체 적재. 순서:
      ① 입력 검증(DB 접속 전) → ② 쓰기 직전 DB 점검 preflight_database → ③ 스키마(IF NOT EXISTS)
      → ④ 노드 배치 → ⑤ 관계 배치 → ⑥ 종합 검증 verify_loaded_graph
    - ①에서 실패하면 runner를 한 번도 호출하지 않는다. ②에서 실패하면 읽기만 하고 쓰지 않는다(PreflightError).
    - 기본(allow_existing=False)은 빈 DB에만 적재한다. 같은 데이터 재실행은 allow_existing=True를 명시해야 한다.
    - ④·⑤ 중 실패하면 GraphLoadError. 이미 커밋된 배치는 남는다(allow_existing=True로 같은 입력을 다시 실행하면 완성됨).
    반환: {"steps", "merged", "preflight", "after_counts", "expected_counts", "count_mismatches", "verification", "ok"}
    count_mismatches·verification은 기존 데이터를 지우지 않고 그대로 보고한다.
    """
    plan = build_load_plan(graph, batch_size)
    preflight = preflight_database(graph, runner, allow_existing=allow_existing)
    if apply_schema:
        for statement in build_schema_statements():
            runner.run_schema(statement)
    merged: dict[str, int] = {}
    for step in plan:
        try:
            count = runner.write_batch(step.query, step.rows, expected=len(step.rows))
        except Exception as error:
            raise GraphLoadError(f"{step.kind} {step.name} 배치 {step.batch_index} 실패 "
                                 f"(이 배치는 롤백, 앞선 배치는 커밋됨): {error}") from error
        merged[f"{step.kind}:{step.name}"] = merged.get(f"{step.kind}:{step.name}", 0) + count
    result: dict[str, Any] = {"steps": len(plan), "merged": merged, "preflight": preflight}
    if verify:
        verification = verify_loaded_graph(graph, runner)
        result.update({"after_counts": verification["after_counts"], "expected_counts": verification["expected_counts"],
                       "count_mismatches": verification["count_mismatches"], "verification": verification,
                       "ok": verification["ok"]})
    else:
        expected = expected_counts(graph)
        after = snapshot_counts(runner)
        mismatches = {key: {"expected": expected[key], "actual": after.get(key)}
                      for key in expected if after.get(key) != expected[key]}
        result.update({"after_counts": after, "expected_counts": expected, "count_mismatches": mismatches,
                       "ok": not mismatches})
    return result


# ---------------------------------------------------------------------------
# 6. Qdrant payload와의 연결 확인 (DB 없이)
# ---------------------------------------------------------------------------
def check_vector_payload_links(graph: GraphInput, payloads: list[dict[str, Any]],
                               documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Qdrant payload(또는 인계 payload_chunks)의 policy_id·document_id가 그래프 노드에 있는지,
    content_hash가 있으면 Document 노드 값과 같은지 확인한다.
    documents(documents.jsonl)를 주면 Document 노드 content_hash와 정제 문서 content_hash도 비교한다.
    """
    policy_ids = {node["policy_id"] for node in graph.nodes_by_label.get("Policy", [])}
    document_nodes = {node["document_id"]: node for node in graph.nodes_by_label.get("Document", [])}
    result = {
        "payload_count": len(payloads),
        "missing_policy": sorted({p["policy_id"] for p in payloads if p["policy_id"] not in policy_ids}),
        "missing_document": sorted({p["document_id"] for p in payloads if p["document_id"] not in document_nodes}),
        "hash_mismatch": sorted({p["document_id"] for p in payloads
                                 if p.get("content_hash") and p["document_id"] in document_nodes
                                 and document_nodes[p["document_id"]].get("content_hash") != p["content_hash"]}),
        "document_policy_mismatch": sorted({p["document_id"] for p in payloads if p["document_id"] in document_nodes
                                            and document_nodes[p["document_id"]].get("policy_id") != p["policy_id"]}),
    }
    if documents is not None:
        result["node_vs_documents_hash_mismatch"] = sorted(
            d["document_id"] for d in documents
            if d["document_id"] in document_nodes and document_nodes[d["document_id"]].get("content_hash") != d["content_hash"])
        result["documents_without_node"] = sorted(d["document_id"] for d in documents if d["document_id"] not in document_nodes)
    return result
