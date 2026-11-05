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

실행 방법은 아직 없다. (Neo4j 실행·접속은 다음 단계 승인 후)
"""

import re
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
RELATIONSHIP_PROPERTY_FIELDS = ("target_name", "evidence_document_id", "evidence_location", "review_status")
REQUIRED_RELATIONSHIP_FIELDS = ("source_id", "relation", "target_id")

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
    if isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        kinds = {type(item) for item in value}
        return all(kind in (str, bool, int, float) for kind in kinds) and len(kinds) <= 1
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
      duplicate_id           같은 레이블 안에서 고유 ID 중복
      invalid_property       Neo4j에 저장할 수 없는 속성값 (예: dict, 섞인 타입 리스트)
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
            for key, value in row.items():
                if not _is_neo4j_property_value(value):
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
            if key in row and row[key] is not None and not _is_neo4j_property_value(row[key]):
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
    return {"id": node[LABEL_ID_FIELDS[label]], "properties": dict(node)}


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
    - read_count(query): 읽기 쿼리의 count 값
    """
    def run_schema(self, statement: str) -> None: ...
    def write_batch(self, query: str, rows: list[dict[str, Any]], expected: int) -> int: ...
    def read_count(self, query: str) -> int: ...


class Neo4jDriverRunner:
    """
    neo4j 공식 드라이버를 감싼 실행기. 드라이버 객체는 밖에서 만들어 넘긴다. (이 모듈은 neo4j 패키지를 import하지 않는다)
    write_batch는 session.execute_write(트랜잭션 함수)를 쓴다 → 함수 안에서 예외가 나면 그 배치 전체가 롤백된다.
    ※ 실제 Neo4j에서는 아직 실행해 보지 않았다 (NOT_TESTED).
    """

    def __init__(self, driver, database: str | None = None):
        self.driver = driver
        self.database = database

    def _session(self):
        return self.driver.session(database=self.database) if self.database else self.driver.session()

    def run_schema(self, statement: str) -> None:
        with self._session() as session:
            session.run(statement).consume()

    def write_batch(self, query: str, rows: list[dict[str, Any]], expected: int) -> int:
        def work(tx):
            merged = tx.run(query, rows=rows).single()["merged"]
            if merged != expected:
                raise GraphLoadError(f"MERGE 건수 불일치: 기대 {expected}, 실제 {merged} → 이 배치 롤백")
            return merged
        with self._session() as session:
            return session.execute_write(work)

    def read_count(self, query: str) -> int:
        with self._session() as session:
            return session.run(query).single()["count"]


def snapshot_counts(runner: GraphRunner) -> dict[str, int]:
    """레이블별 노드 수·관계 타입별 관계 수 (읽기 전용). 적재 전 "빈 DB인가" 확인과 적재 후 검증에 쓴다."""
    return {key: runner.read_count(query) for key, query in build_count_queries().items()}


def expected_counts(graph: GraphInput) -> dict[str, int]:
    """입력 파일에서 센 기대 건수 (상수가 아니라 실제 파일 기준)."""
    report = validate_graph_input(graph)
    counts = {f"node:{label}": report.node_counts.get(label, 0) for label in LABEL_ID_FIELDS}
    counts.update({f"rel:{relation}": report.relationship_counts.get(relation, 0) for relation in RELATIONSHIP_SPECS})
    return counts


def load_graph(graph: GraphInput, runner: GraphRunner, *, batch_size: int = DEFAULT_BATCH_SIZE,
               apply_schema: bool = True) -> dict[str, Any]:
    """
    전체 적재. 순서: ① 입력 검증 → ② 스키마(IF NOT EXISTS) → ③ 노드 배치 → ④ 관계 배치 → ⑤ 건수 검증
    - ①에서 실패하면 DB에 아무것도 쓰지 않는다. (계획을 만들 수 없으므로 runner를 한 번도 호출하지 않는다)
    - ③·④ 중 실패하면 GraphLoadError. 이미 커밋된 배치는 남는다(같은 입력으로 재실행하면 완성됨).
    반환: {"steps", "merged", "after_counts", "expected_counts", "count_mismatches"}
    count_mismatches: DB 건수 ≠ 입력 건수인 항목 (DB에 원래 다른 데이터가 있었으면 여기에 나타난다. 지우지 않는다)
    """
    plan = build_load_plan(graph, batch_size)
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
    expected = expected_counts(graph)
    after = snapshot_counts(runner)
    mismatches = {key: {"expected": expected[key], "actual": after.get(key)}
                  for key in expected if after.get(key) != expected[key]}
    return {"steps": len(plan), "merged": merged, "after_counts": after, "expected_counts": expected,
            "count_mismatches": mismatches}


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
