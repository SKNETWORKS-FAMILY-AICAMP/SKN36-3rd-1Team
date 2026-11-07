"""
Neo4j 로더 단위 테스트 — 실제 Neo4j에 접속하지 않는다.

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_neo4j_loader.py -v

구성
1) 실제 인계 파일 집계·검증 (건수는 상수가 아니라 파일에서 센다)
2) 인계 로더 결함(레이블 없는 OR 매칭) 재현 vs 새 로더 매칭
3) 잘못된 입력 검출 (가상 데이터, sample_only)
4) 쿼리 안전성: 허용 목록, 파라미터화, 삭제 쿼리 없음, 레이블 지정 MATCH
5) 가짜 실행기(메모리 그래프)로 적재 순서·재적재·중간 실패 시나리오
   ※ 가짜 실행기는 로더가 만드는 쿼리 모양만 흉내 낸다. 실제 Neo4j의 MERGE·트랜잭션 동작은 NOT_TESTED.
"""

import json
import re
import sys
import unittest
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import handoff_data as hd  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402

NODE_QUERY = re.compile(r"MERGE \(n:(\w+) \{(\w+): row\.id\}\)")
REL_QUERY = re.compile(r"MATCH \(s:(\w+) \{(\w+): row\.source_id\}\)\nMATCH \(t:(\w+) \{(\w+): row\.target_id\}\)\n"
                       r"MERGE \(s\)-\[r:(\w+)\]->\(t\)")


class FakeGraphRunner:
    """
    메모리 그래프 가짜 실행기 (sample_only).
    - 모든 호출을 calls에 기록한다.
    - 노드 MERGE: (레이블, ID)가 같으면 하나로 합친다. 관계 MERGE: (타입, 출발, 도착)이 같으면 하나로 합친다.
    - 배치 결과가 expected와 다르거나 fail_on_call 번째 쓰기면 그 배치를 "적용하지 않고" 예외 (= 트랜잭션 롤백 흉내)
    """

    DEFAULT_LOOKUP_INDEXES = [
        {"name": "index_lookup_node_sample", "type": "LOOKUP", "entityType": "NODE", "labelsOrTypes": None,
         "properties": None, "state": "ONLINE", "owningConstraint": None},
        {"name": "index_lookup_rel_sample", "type": "LOOKUP", "entityType": "RELATIONSHIP", "labelsOrTypes": None,
         "properties": None, "state": "ONLINE", "owningConstraint": None},
    ]

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[tuple] = []
        self.nodes: dict[tuple[str, str], dict] = {}
        self.relationships: set[tuple] = set()
        self.relationship_properties: dict[tuple, dict] = {}
        self.write_count = 0
        self.fail_on_call = fail_on_call
        # 실제 Neo4j SHOW 결과 모양을 흉내 낸 스키마 상태 (sample_only)
        self.constraints: list[dict] = []
        self.indexes: list[dict] = [dict(row) for row in self.DEFAULT_LOOKUP_INDEXES]

    def run_schema(self, statement):
        self.calls.append(("schema", statement))
        constraint = re.match(r"CREATE CONSTRAINT (\w+) IF NOT EXISTS FOR \(n:(\w+)\) REQUIRE n\.(\w+) IS UNIQUE",
                              statement)
        index = re.match(r"CREATE INDEX (\w+) IF NOT EXISTS FOR \(n:(\w+)\) ON \(n\.(\w+)\)", statement)
        if constraint:
            name, label, prop = constraint.groups()
            # Neo4j처럼: 같은 이름 또는 같은 정의가 있으면 IF NOT EXISTS로 아무 일도 하지 않는다
            if any(c["name"] == name or (c["labelsOrTypes"], c["properties"]) == ([label], [prop])
                   for c in self.constraints):
                return
            self.constraints.append({"name": name, "type": "UNIQUENESS", "entityType": "NODE",
                                     "labelsOrTypes": [label], "properties": [prop], "ownedIndex": name})
            self.indexes.append({"name": name, "type": "RANGE", "entityType": "NODE", "labelsOrTypes": [label],
                                 "properties": [prop], "state": "ONLINE", "owningConstraint": name})
        elif index:
            name, label, prop = index.groups()
            if any(i["name"] == name or (i["labelsOrTypes"], i["properties"]) == ([label], [prop])
                   for i in self.indexes):
                return
            self.indexes.append({"name": name, "type": "RANGE", "entityType": "NODE", "labelsOrTypes": [label],
                                 "properties": [prop], "state": "ONLINE", "owningConstraint": None})
        else:
            raise AssertionError(f"예상하지 못한 스키마 문장: {statement}")

    def read_rows(self, query, parameters=None):
        self.calls.append(("read", query))
        for label in lg.LABEL_ID_FIELDS:
            if query == lg.build_node_property_query(label):
                return [{"id": node_id, "properties": dict(props)}
                        for (node_label, node_id), props in self.nodes.items() if node_label == label]
        for relation in lg.RELATIONSHIP_SPECS:
            if query == lg.build_relationship_property_query(relation):
                rows = []
                for item in self.relationships:
                    item_relation, (_source_label, source_id), (_target_label, target_id) = item
                    if item_relation == relation:
                        rows.append({"source_id": source_id, "target_id": target_id,
                                     "properties": dict(self.relationship_properties.get(item, {}))})
                return rows
        if query == lg.QUERY_TOTAL_COUNTS:
            return [{"nodes": len(self.nodes), "relationships": len(self.relationships)}]
        if query == lg.QUERY_SHOW_CONSTRAINTS:
            return [dict(row) for row in self.constraints]
        if query == lg.QUERY_SHOW_INDEXES:
            return [dict(row) for row in self.indexes]
        if query == lg.build_node_identity_query():
            return [{"labels": [label], "keys": {f: props.get(f) for f in lg.ID_PROPERTY_FIELDS}}
                    for (label, _id), props in self.nodes.items()]
        if query == lg.build_relationship_endpoint_query():
            rows = []
            for relation, (source_label, source_id), (target_label, target_id) in self.relationships:
                source = self.nodes[(source_label, source_id)]
                target = self.nodes[(target_label, target_id)]
                rows.append({"type": relation, "source_labels": [source_label], "target_labels": [target_label],
                             "source_keys": {f: source.get(f) for f in lg.ID_PROPERTY_FIELDS},
                             "target_keys": {f: target.get(f) for f in lg.ID_PROPERTY_FIELDS}})
            return rows
        raise AssertionError(f"예상하지 못한 읽기 쿼리: {query}")

    def write_batch(self, query, rows, expected):
        self.calls.append(("write", query, rows, expected))
        self.write_count += 1
        if self.fail_on_call is not None and self.write_count == self.fail_on_call:
            raise RuntimeError("가상 장애 (sample_only)")
        node_match = NODE_QUERY.search(query)
        if node_match:
            label = node_match.group(1)
            for row in rows:
                self.nodes.setdefault((label, row["id"]), {}).update(row["properties"])
            return len(rows)
        rel_match = REL_QUERY.search(query)
        if not rel_match:
            raise AssertionError(f"예상하지 못한 쿼리 모양: {query}")
        source_label, _sf, target_label, _tf, relation = rel_match.groups()
        staged = [((relation, (source_label, row["source_id"]), (target_label, row["target_id"])), row["properties"])
                  for row in rows
                  if (source_label, row["source_id"]) in self.nodes and (target_label, row["target_id"]) in self.nodes]
        if len(staged) != expected:
            raise RuntimeError(f"MERGE 건수 불일치: 기대 {expected}, 실제 {len(staged)}")   # 적용 전에 실패 = 롤백
        for item, properties in staged:
            self.relationships.add(item)
            self.relationship_properties.setdefault(item, {}).update(properties)   # SET r += row.properties
        return len(staged)

    def read_count(self, query):
        self.calls.append(("read", query))
        relation = re.search(r"\[r:(\w+)\]", query)
        if relation:
            return sum(1 for item in self.relationships if item[0] == relation.group(1))
        label = re.search(r"MATCH \(n:(\w+)\)", query).group(1)
        return sum(1 for key in self.nodes if key[0] == label)


def _sample_graph() -> lg.GraphInput:
    """작은 가상 그래프 (sample_only)."""
    return lg.GraphInput(
        nodes_by_label={
            "Policy": [{**{key: "sample_only" for key in lg.REQUIRED_NODE_STRING_FIELDS["Policy"]},
                        "policy_id": "POL_S1", "policy_name": "가상 정책 (sample_only)",
                        "life_stages": ["청년"], "target_groups": ["sample_only"]}],
            "Document": [{**{key: "sample_only" for key in lg.REQUIRED_NODE_STRING_FIELDS["Document"]},
                          "document_id": "DOC_S1", "policy_id": "POL_S1", "content_hash": "h1"}],
            "Organization": [{"agency_id": "AGENCY_S1", "name": "가상 기관", "type": "sample_only"}],
        },
        relationships=[
            {"source_id": "POL_S1", "relation": "SUPPORTED_BY", "target_id": "DOC_S1", "review_status": "sample_only"},
            {"source_id": "POL_S1", "relation": "ADMINISTERED_BY", "target_id": "AGENCY_S1", "target_name": "가상 기관"},
        ],
    )


# ---------------------------------------------------------------------------
# 1) 실제 인계 파일
# ---------------------------------------------------------------------------
class TestRealHandoffGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = lg.read_graph_input()
        cls.report = lg.validate_graph_input(cls.graph)

    def test_counts_are_read_from_files(self):
        line_counts = {}
        for file_name, (label, _id) in hd.NODE_SPECS.items():
            with open(hd.NEO4J_HANDOFF_DIR / file_name, encoding="utf-8") as file:
                line_counts[label] = sum(1 for line in file if line.strip())
        self.assertEqual(self.report.node_counts, line_counts)
        with open(hd.RELATIONSHIPS_PATH, encoding="utf-8") as file:
            self.assertEqual(sum(self.report.relationship_counts.values()), sum(1 for line in file if line.strip()))
        # 인계 문서의 기대값과도 같은지 (상수는 비교 대상일 뿐, 계산은 파일 기준)
        self.assertEqual(sum(self.report.node_counts.values()), hd.EXPECTED_NODE_COUNT)
        self.assertEqual(sum(self.report.relationship_counts.values()), hd.EXPECTED_RELATIONSHIP_COUNT)

    def test_counts_by_label_and_relation(self):
        self.assertEqual(self.report.node_counts, {
            "Policy": 237, "Document": 246, "Organization": 116, "Region": 18, "Category": 5, "LifeStage": 6,
            "TargetGroup": 11, "ConditionGroup": 343, "EligibilityCondition": 52})
        self.assertEqual(self.report.relationship_counts, {
            "ADMINISTERED_BY": 237, "HAS_CATEGORY": 237, "AVAILABLE_IN": 238, "TARGETS": 424,
            "APPLIES_TO_LIFESTAGE": 419, "SUPPORTED_BY": 246, "REQUIRES_CONDITION": 735, "MODIFIES_CONDITION_OF": 1,
            "HAS_ROOT_CONDITION_GROUP": 237, "CONTAINS_GROUP": 106, "CONTAINS_CONDITION": 735})

    def test_real_input_passes_validation(self):
        self.assertTrue(self.report.ok, self.report.summary())

    def test_manifest_checksums_for_neo4j_files(self):
        results = [item for item in hd.verify_manifest_checksums() if item["file"].startswith("neo4j/")]
        self.assertEqual(len(results), 15)
        self.assertTrue(all(item["matched"] for item in results))

    def test_vector_payload_links_to_graph(self):
        result = lg.check_vector_payload_links(self.graph, hd.load_payload_chunks(), hd.load_documents())
        self.assertEqual(result["payload_count"], hd.EXPECTED_CHUNK_COUNT)
        for key in ("missing_policy", "missing_document", "hash_mismatch", "document_policy_mismatch",
                    "node_vs_documents_hash_mismatch", "documents_without_node"):
            with self.subTest(check=key):
                self.assertEqual(result[key], [])

    def test_schema_statements_match_handoff_ddl_names(self):
        ddl = (hd.NEO4J_HANDOFF_DIR / "cypher_ddl.cypher").read_text(encoding="utf-8")
        # findall은 매칭되지 않은 그룹을 '', match.groups()는 None으로 준다 → 둘 다 ''로 맞춘다.
        handoff = {tuple(part or "" for part in item) for item in re.findall(
            r"CREATE (?:CONSTRAINT|INDEX) (\w+) IF NOT EXISTS\s+FOR \((?:\w+):(\w+)\) "
            r"(?:REQUIRE \w+\.(\w+) IS UNIQUE|ON \(\w+\.(\w+)\))", ddl)}
        ours = set()
        for statement in lg.build_schema_statements():
            match = re.match(r"CREATE (?:CONSTRAINT|INDEX) (\w+) IF NOT EXISTS FOR \(n:(\w+)\) "
                             r"(?:REQUIRE n\.(\w+) IS UNIQUE|ON \(n\.(\w+)\))", statement)
            self.assertIsNotNone(match, statement)
            ours.add(tuple(part or "" for part in match.groups()))
        self.assertEqual(len(ours), 13)
        self.assertEqual(ours, handoff)


# ---------------------------------------------------------------------------
# 2) 인계 로더 결함 재현
# ---------------------------------------------------------------------------
LEGACY_SOURCE_FIELDS = ("policy_id", "document_id", "group_id", "agency_id", "region_id")
LEGACY_TARGET_FIELDS = ("policy_id", "document_id", "group_id", "agency_id", "region_id", "category_id",
                        "stage_id", "condition_id")


def simulate_legacy_relationship_count(graph: lg.GraphInput) -> tuple[int, dict[str, int]]:
    """인계 load_neo4j.py의 "레이블 없는 OR 매칭" 규칙을 그대로 흉내 내, 만들어질 관계 수를 센다."""
    all_nodes = [node for rows in graph.nodes_by_label.values() for node in rows]
    by_source, by_target = defaultdict(set), defaultdict(set)
    for index, node in enumerate(all_nodes):
        for name in LEGACY_SOURCE_FIELDS:
            if isinstance(node.get(name), str):
                by_source[node[name]].add(index)
        for name in LEGACY_TARGET_FIELDS:
            if isinstance(node.get(name), str):
                by_target[node[name]].add(index)
    created = defaultdict(set)
    for row in graph.relationships:
        for source in by_source[row["source_id"]]:
            for target in by_target[row["target_id"]]:
                created[row["relation"]].add((source, target))
    per_type = {relation: len(pairs) for relation, pairs in created.items()}
    return sum(per_type.values()), per_type


class TestLegacyDefectReproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = lg.read_graph_input()

    def test_legacy_unlabeled_or_match_inflates_relationships(self):
        total, per_type = simulate_legacy_relationship_count(self.graph)
        self.assertEqual(total, 11316)                 # 3,615가 아니라 11,316 (2026-11-05 재현값)
        self.assertEqual(per_type["REQUIRES_CONDITION"], 3390)
        self.assertEqual(per_type["CONTAINS_CONDITION"], 735)   # 출발이 ConditionGroup인 타입은 부풀지 않음

    def test_policy_id_matches_several_labels_without_label(self):
        carriers = [label for label, rows in self.graph.nodes_by_label.items()
                    if any(node.get("policy_id") == "POL_001" for node in rows)]
        self.assertEqual(sorted(carriers), ["ConditionGroup", "Document", "Policy"])

    def test_new_loader_matches_each_row_to_exactly_one_pair(self):
        runner = FakeGraphRunner()
        result = lg.load_graph(self.graph, runner)
        self.assertEqual(len(runner.relationships), hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(len(runner.nodes), hd.EXPECTED_NODE_COUNT)
        self.assertEqual(result["count_mismatches"], {})


# ---------------------------------------------------------------------------
# 3) 잘못된 입력 검출 (sample_only)
# ---------------------------------------------------------------------------
class TestValidationRejectsBadInput(unittest.TestCase):
    def _errors(self, graph):
        return lg.validate_graph_input(graph).summary()["error_counts"]

    def test_sample_graph_is_valid(self):
        self.assertEqual(self._errors(_sample_graph()), {})

    def test_each_problem_is_detected(self):
        cases = {
            "duplicate_id": lambda g: g.nodes_by_label["Policy"].append({"policy_id": "POL_S1"}),
            "missing_id": lambda g: g.nodes_by_label["Policy"].append({"policy_name": "ID 없음"}),
            "invalid_property": lambda g: g.nodes_by_label["Document"][0].update({"meta": {"nested": 1}}),
            "unknown_label": lambda g: g.nodes_by_label.update({"Hacker": [{"id": "X"}]}),
            "unknown_relation": lambda g: g.relationships.append(
                {"source_id": "POL_S1", "relation": "OWNS", "target_id": "DOC_S1"}),
            "missing_source": lambda g: g.relationships.append(
                {"source_id": "POL_NONE", "relation": "SUPPORTED_BY", "target_id": "DOC_S1"}),
            "missing_target": lambda g: g.relationships.append(
                {"source_id": "POL_S1", "relation": "SUPPORTED_BY", "target_id": "DOC_NONE"}),
            "duplicate_relationship": lambda g: g.relationships.append(dict(g.relationships[0])),
            "missing_field": lambda g: g.relationships.append({"source_id": "POL_S1", "relation": "SUPPORTED_BY"}),
        }
        for expected, mutate in cases.items():
            with self.subTest(problem=expected):
                graph = _sample_graph()
                mutate(graph)
                self.assertIn(expected, self._errors(graph))

    def test_mixed_type_list_is_not_a_valid_property(self):
        graph = _sample_graph()
        graph.nodes_by_label["Policy"][0]["life_stages"] = ["청년", 3]
        self.assertIn("invalid_property", self._errors(graph))

    def test_invalid_input_writes_nothing(self):
        graph = _sample_graph()
        graph.relationships.append({"source_id": "POL_NONE", "relation": "SUPPORTED_BY", "target_id": "DOC_S1"})
        runner = FakeGraphRunner()
        with self.assertRaises(lg.GraphValidationError):
            lg.load_graph(graph, runner)
        self.assertEqual(runner.calls, [], "검증 실패 시 스키마·쓰기·읽기 호출이 하나도 없어야 한다")


# ---------------------------------------------------------------------------
# 4) 쿼리 안전성
# ---------------------------------------------------------------------------
class TestQuerySafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = lg.read_graph_input()
        cls.plan = lg.build_load_plan(cls.graph)
        cls.queries = sorted({step.query for step in cls.plan}) + lg.build_schema_statements() \
            + list(lg.build_count_queries().values())

    def test_labels_and_relations_outside_allowlist_are_rejected(self):
        for bad in ("Hacker", "Policy) DETACH DELETE (n", "policy", ""):
            with self.subTest(label=bad):
                with self.assertRaises(lg.GraphValidationError):
                    lg.build_node_merge_query(bad)
        for bad in ("OWNS", "TARGETS]->() DELETE r//", "targets"):
            with self.subTest(relation=bad):
                with self.assertRaises(lg.GraphValidationError):
                    lg.build_relationship_merge_query(bad)

    def test_no_delete_or_reset_queries_anywhere(self):
        for query in self.queries:
            for word in lg.FORBIDDEN_CYPHER_WORDS:
                with self.subTest(word=word, query=query[:40]):
                    self.assertNotRegex(query.upper(), rf"\b{word}\b")
        source = (PROJECT_ROOT / "database" / "neo4j" / "load_graph.py").read_text(encoding="utf-8")
        cypher_lines = [line for line in source.splitlines() if line.strip().startswith(('f"', '"')) and
                        re.search(r"\b(MATCH|MERGE|CREATE)\b", line)]
        self.assertTrue(all(not re.search(r"\b(DELETE|DETACH|REMOVE|DROP)\b", line) for line in cypher_lines))

    def test_data_values_are_parameters_not_query_text(self):
        values = {node[lg.LABEL_ID_FIELDS[label]] for label, rows in self.graph.nodes_by_label.items() for node in rows}
        for query in {step.query for step in self.plan}:
            self.assertIn("$rows", query)
            for value in ("POL_001", "DOC_LH_001", "GRP_POL_001_ROOT"):
                self.assertIn(value, values)
                self.assertNotIn(value, query)

    def test_every_match_and_merge_has_a_label(self):
        # 속성 조건 {…}으로 노드를 찾는 패턴은 반드시 레이블이 있어야 한다.
        # (관계 MERGE의 (s)·(t)는 앞에서 레이블로 MATCH한 변수를 다시 쓰는 것이라 조건 없이 쓴다)
        for query in {step.query for step in self.plan}:
            patterns = re.findall(r"\((\w+)(:\w+)?\s*\{", query)
            self.assertTrue(patterns)
            for variable, label in patterns:
                with self.subTest(query=query[:60], variable=variable):
                    self.assertTrue(label, f"레이블 없는 노드 패턴: {query}")
            self.assertNotIn(" OR ", query)
            self.assertNotRegex(query, r"\bWHERE\b")

    def test_relationship_queries_use_spec_labels_and_id_fields(self):
        for relation, (source_label, target_label) in lg.RELATIONSHIP_SPECS.items():
            query = lg.build_relationship_merge_query(relation)
            match = REL_QUERY.search(query)
            with self.subTest(relation=relation):
                self.assertEqual(match.groups(), (source_label, lg.LABEL_ID_FIELDS[source_label], target_label,
                                                  lg.LABEL_ID_FIELDS[target_label], relation))

    def test_schema_statements_are_idempotent_and_label_scoped(self):
        statements = lg.build_schema_statements()
        self.assertEqual(len(statements), 13)
        for statement in statements:
            self.assertIn("IF NOT EXISTS", statement)
            self.assertRegex(statement, r"FOR \(n:\w+\)")

    def test_module_does_not_import_neo4j_driver(self):
        source = (PROJECT_ROOT / "database" / "neo4j" / "load_graph.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"^\s*(import neo4j|from neo4j)", )


# ---------------------------------------------------------------------------
# 5) 가짜 실행기 시나리오 (실제 Neo4j 동작은 NOT_TESTED)
# ---------------------------------------------------------------------------
class TestLoadPlanAndFakeRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = lg.read_graph_input()

    def test_plan_order_batches_and_row_shape(self):
        plan = lg.build_load_plan(self.graph, batch_size=500)
        kinds = [step.kind for step in plan]
        self.assertEqual(kinds, sorted(kinds, key=lambda kind: kind != "node"), "노드 단계가 모두 관계 단계보다 먼저")
        self.assertTrue(all(len(step.rows) <= 500 for step in plan))
        self.assertEqual(sum(len(s.rows) for s in plan if s.kind == "node"), hd.EXPECTED_NODE_COUNT)
        self.assertEqual(sum(len(s.rows) for s in plan if s.kind == "relationship"), hd.EXPECTED_RELATIONSHIP_COUNT)
        relation_steps = [s for s in plan if s.name == "REQUIRES_CONDITION"]
        self.assertEqual([len(s.rows) for s in relation_steps], [500, 235])
        row = relation_steps[0].rows[0]
        self.assertEqual(set(row), {"source_id", "target_id", "properties"})
        self.assertLessEqual(set(row["properties"]), set(lg.RELATIONSHIP_PROPERTY_FIELDS))

    def test_reload_does_not_increase_counts(self):
        runner = FakeGraphRunner()
        first = lg.load_graph(self.graph, runner)
        self.assertTrue(first["ok"], first["verification"])
        nodes_before, relationships_before = dict(runner.nodes), set(runner.relationships)
        # 기본값(allow_existing=False)으로는 두 번째 적재를 거부하고, 아무것도 쓰지 않는다
        writes_before = runner.write_count
        with self.assertRaises(lg.DatabaseNotEmptyError):
            lg.load_graph(self.graph, runner)
        self.assertEqual(runner.write_count, writes_before)
        # 명시적 재실행 경로: 기존 데이터가 입력과 같음을 확인(complete)한 뒤 MERGE → 변화 없음
        second = lg.load_graph(self.graph, runner, allow_existing=True)
        self.assertEqual(second["preflight"]["state"], "complete")
        self.assertTrue(second["ok"], second["verification"])
        self.assertEqual(first["after_counts"], second["after_counts"])
        self.assertEqual(set(runner.nodes), set(nodes_before))
        self.assertEqual(runner.relationships, relationships_before)
        self.assertEqual(len(runner.nodes), hd.EXPECTED_NODE_COUNT)
        self.assertEqual(len(runner.relationships), hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(second["count_mismatches"], {})

    def test_failure_mid_load_keeps_committed_batches_and_rerun_completes(self):
        runner = FakeGraphRunner(fail_on_call=12)            # 노드 단계 9개 + 관계 2번째 배치 근처에서 실패
        with self.assertRaises(lg.GraphLoadError) as context:
            lg.load_graph(self.graph, runner)
        self.assertIn("롤백", str(context.exception))
        partial_relationships = len(runner.relationships)
        self.assertEqual(len(runner.nodes), hd.EXPECTED_NODE_COUNT)
        self.assertLess(partial_relationships, hd.EXPECTED_RELATIONSHIP_COUNT)
        runner.fail_on_call = None
        with self.assertRaises(lg.DatabaseNotEmptyError):     # 기본값으로는 이어서 적재하지 않는다
            lg.load_graph(self.graph, runner)
        result = lg.load_graph(self.graph, runner, allow_existing=True)
        self.assertEqual(result["preflight"]["state"], "partial")
        self.assertEqual(len(runner.relationships), hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(result["count_mismatches"], {})
        self.assertTrue(result["ok"], result["verification"])

    def test_existing_unrelated_data_is_refused_and_not_deleted(self):
        for allow_existing, error in ((False, lg.DatabaseNotEmptyError), (True, lg.ExistingDataMismatchError)):
            with self.subTest(allow_existing=allow_existing):
                runner = FakeGraphRunner()
                runner.nodes[("Policy", "POL_EXISTING_SAMPLE_ONLY")] = {"policy_id": "POL_EXISTING_SAMPLE_ONLY"}
                with self.assertRaises(error) as context:
                    lg.load_graph(self.graph, runner, allow_existing=allow_existing)
                self.assertEqual(runner.write_count, 0)
                self.assertFalse([call for call in runner.calls if call[0] in ("schema", "write")])
                self.assertIn(("Policy", "POL_EXISTING_SAMPLE_ONLY"), runner.nodes)   # 지우지 않는다
                if allow_existing:
                    self.assertEqual(context.exception.report["nodes"]["problems"]["extra"],
                                     {"Policy": ["POL_EXISTING_SAMPLE_ONLY"]})

    def test_sample_graph_properties_are_passed_as_parameters(self):
        runner = FakeGraphRunner()
        lg.load_graph(_sample_graph(), runner)
        self.assertEqual(runner.nodes[("Policy", "POL_S1")]["life_stages"], ["청년"])
        writes = [call for call in runner.calls if call[0] == "write"]
        supported = [call for call in writes if "SUPPORTED_BY" in call[1]][0]
        self.assertEqual(supported[2], [{"source_id": "POL_S1", "target_id": "DOC_S1",
                                         "properties": {"review_status": "sample_only"}}])

    def test_driver_runner_rolls_back_inside_transaction_function(self):
        """Neo4jDriverRunner가 execute_write의 트랜잭션 함수 안에서 건수를 확인하는지 (가짜 드라이버, sample_only)."""
        log = []

        class FakeResult:
            def __init__(self, merged):
                self.merged = merged

            def single(self):
                return {"merged": self.merged, "count": self.merged}

            def consume(self):
                return None

        class FakeTx:
            def run(self, query, **parameters):
                log.append(("tx.run", parameters["rows"]))
                return FakeResult(len(parameters["rows"]) - 1)        # 한 행이 MATCH에서 빠진 상황

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute_write(self, work):
                log.append("execute_write")
                try:
                    return work(FakeTx())
                except Exception:
                    log.append("rolled_back")
                    raise

        class FakeDriver:
            def session(self, **kwargs):
                return FakeSession()

        runner = lg.Neo4jDriverRunner(FakeDriver())
        with self.assertRaises(lg.GraphLoadError):
            runner.write_batch("UNWIND $rows AS row RETURN 1 AS merged", [{"id": 1}, {"id": 2}], expected=2)
        self.assertEqual(log[0], "execute_write")
        self.assertEqual(log[-1], "rolled_back")


# ---------------------------------------------------------------------------
# 6) 실제 DB 검증 함수 (verify_schema / verify_node_ids / verify_relationship_endpoints) — sample_only
# ---------------------------------------------------------------------------
def _keys(**values):
    """ID 필드 맵 투영 결과 흉내: 모든 ID 필드를 None으로 채운 뒤 주어진 값만 넣는다."""
    return {**{name: None for name in lg.ID_PROPERTY_FIELDS}, **values}


class StaticRowsRunner:
    """쿼리별로 미리 정한 행을 돌려주는 읽기 전용 가짜 실행기 (sample_only). 쓰기 호출은 실패시킨다."""

    def __init__(self, rows_by_query):
        self.rows_by_query = rows_by_query

    def read_rows(self, query, parameters=None):
        if query not in self.rows_by_query:
            raise AssertionError(f"예상하지 못한 읽기 쿼리: {query}")
        return [dict(row) for row in self.rows_by_query[query]]

    def read_count(self, query):
        return self.read_rows(query)[0]["count"]

    def run_schema(self, statement):
        raise AssertionError("읽기 전용 검증에서 스키마 쓰기 호출")

    def write_batch(self, query, rows, expected):
        raise AssertionError("읽기 전용 검증에서 데이터 쓰기 호출")


def _loaded_fake(graph=None) -> FakeGraphRunner:
    runner = FakeGraphRunner()
    lg.load_graph(graph or _sample_graph(), runner)
    return runner


def _schema_runner(constraints, indexes):
    return StaticRowsRunner({lg.QUERY_SHOW_CONSTRAINTS: constraints, lg.QUERY_SHOW_INDEXES: indexes})


class TestVerifySchema(unittest.TestCase):
    def setUp(self):
        self.runner = _loaded_fake()

    def test_created_schema_passes_post_check(self):
        report = lg.verify_schema(self.runner, phase="post")
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(len(report["constraints"]["matched"]), 9)
        self.assertEqual(len(report["indexes"]["matched"]), 4)
        self.assertEqual(len(report["constraint_owned_indexes"]), 9)
        self.assertEqual(len(report["default_lookup_indexes"]), 2)   # 기본 LOOKUP은 예상 밖이 아님

    def test_empty_schema_passes_pre_but_fails_post(self):
        runner = FakeGraphRunner()
        self.assertTrue(lg.verify_schema(runner, phase="pre")["ok"])
        post = lg.verify_schema(runner, phase="post")
        self.assertFalse(post["ok"])
        self.assertEqual(len(post["problems"]["missing_constraints"]), 9)
        self.assertEqual(len(post["problems"]["missing_indexes"]), 4)

    def test_same_name_different_definition_fails(self):
        self.runner.constraints[0]["properties"] = ["name"]           # constraint_policy_id가 다른 속성
        self.runner.indexes[-1]["labelsOrTypes"] = ["Policy"]          # 마지막 인덱스가 다른 레이블
        for phase in ("pre", "post"):
            report = lg.verify_schema(self.runner, phase=phase)
            with self.subTest(phase=phase):
                self.assertFalse(report["ok"])
                self.assertIn("constraint_definition_mismatch", report["problems"])
                self.assertIn("index_definition_mismatch", report["problems"])

    def test_wrong_constraint_type_fails(self):
        self.runner.constraints[0]["type"] = "NODE_KEY"
        self.assertIn("constraint_definition_mismatch", lg.verify_schema(self.runner)["problems"])

    def test_same_definition_with_other_name_fails(self):
        constraints = [{"name": "constraint_policy_legacy", "type": "UNIQUENESS", "entityType": "NODE",
                        "labelsOrTypes": ["Policy"], "properties": ["policy_id"], "ownedIndex": "constraint_policy_legacy"}]
        indexes = [{"name": "constraint_policy_legacy", "type": "RANGE", "entityType": "NODE",
                    "labelsOrTypes": ["Policy"], "properties": ["policy_id"], "state": "ONLINE",
                    "owningConstraint": "constraint_policy_legacy"}]
        report = lg.verify_schema(_schema_runner(constraints, indexes), phase="pre")
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"]["constraint_same_definition_other_name"],
                         [{"expected_name": "constraint_policy_id", "actual_name": "constraint_policy_legacy"}])

    def test_unexpected_schema_is_reported(self):
        # 인계 schema_constraints.cypher 쪽 정의(name 기준)가 이미 있는 상황
        constraints = [{"name": "constraint_region_name", "type": "UNIQUENESS", "entityType": "NODE",
                        "labelsOrTypes": ["Region"], "properties": ["name"], "ownedIndex": "constraint_region_name"}]
        indexes = [{"name": "index_condition_type", "type": "RANGE", "entityType": "NODE",
                    "labelsOrTypes": ["EligibilityCondition"], "properties": ["type"], "state": "ONLINE",
                    "owningConstraint": None}]
        report = lg.verify_schema(_schema_runner(constraints, indexes), phase="pre")
        self.assertFalse(report["ok"])
        self.assertEqual([item["name"] for item in report["problems"]["unexpected_constraints"]],
                         ["constraint_region_name"])
        self.assertEqual(report["problems"]["index_same_definition_other_name"],
                         [{"expected_name": "index_eligibility_condition_type", "actual_name": "index_condition_type"}])

    def test_index_not_online_fails(self):
        self.runner.indexes[-1]["state"] = "POPULATING"
        report = lg.verify_schema(self.runner)
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"]["index_not_online"][0]["state"], "POPULATING")

    def test_invalid_phase_is_rejected(self):
        with self.assertRaises(ValueError):
            lg.verify_schema(self.runner, phase="during")


class TestVerifyNodeIds(unittest.TestCase):
    def setUp(self):
        self.graph = _sample_graph()
        self.query = lg.build_node_identity_query()
        self.good_rows = [
            {"labels": ["Policy"], "keys": _keys(policy_id="POL_S1")},
            # Document도 policy_id를 갖지만, Document의 ID는 document_id로만 읽어야 한다
            {"labels": ["Document"], "keys": _keys(document_id="DOC_S1", policy_id="POL_S1")},
            {"labels": ["Organization"], "keys": _keys(agency_id="AGENCY_S1")},
        ]

    def _verify(self, rows):
        return lg.verify_node_ids(self.graph, StaticRowsRunner({self.query: rows}))

    def test_exact_match_passes(self):
        report = self._verify(self.good_rows)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["expected_total"], 3)
        self.assertEqual(report["actual_total"], 3)

    def test_each_problem_is_detected(self):
        cases = {
            "missing": lambda rows: rows.pop(2),
            "extra": lambda rows: rows.append({"labels": ["Policy"], "keys": _keys(policy_id="POL_EXTRA")}),
            "duplicate": lambda rows: rows.append(dict(rows[0])),
            "missing_id": lambda rows: rows.append({"labels": ["Policy"], "keys": _keys()}),
            "unknown_label": lambda rows: rows.append({"labels": ["Hacker"], "keys": _keys()}),
            "multi_label": lambda rows: rows.append({"labels": ["Policy", "Document"], "keys": _keys(policy_id="X")}),
            "unlabeled": lambda rows: rows.append({"labels": [], "keys": _keys()}),
        }
        for problem, mutate in cases.items():
            rows = [dict(row) for row in self.good_rows]
            mutate(rows)
            report = self._verify(rows)
            with self.subTest(problem=problem):
                self.assertFalse(report["ok"])
                self.assertIn(problem, report["problems"])

    def test_same_count_with_different_ids_fails(self):
        rows = [dict(row) for row in self.good_rows]
        rows[2] = {"labels": ["Organization"], "keys": _keys(agency_id="AGENCY_OTHER")}
        report = self._verify(rows)
        self.assertEqual(report["actual_total"], report["expected_total"])     # 건수는 같다
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"]["missing"], {"Organization": ["AGENCY_S1"]})
        self.assertEqual(report["problems"]["extra"], {"Organization": ["AGENCY_OTHER"]})

    def test_real_handoff_graph_on_fake_db_passes(self):
        graph = lg.read_graph_input()
        report = lg.verify_node_ids(graph, _loaded_fake(graph))
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["actual_total"], hd.EXPECTED_NODE_COUNT)


class TestVerifyRelationshipEndpoints(unittest.TestCase):
    def setUp(self):
        self.graph = _sample_graph()
        self.query = lg.build_relationship_endpoint_query()
        self.good_rows = [
            {"type": "SUPPORTED_BY", "source_labels": ["Policy"], "target_labels": ["Document"],
             "source_keys": _keys(policy_id="POL_S1"), "target_keys": _keys(document_id="DOC_S1", policy_id="POL_S1")},
            {"type": "ADMINISTERED_BY", "source_labels": ["Policy"], "target_labels": ["Organization"],
             "source_keys": _keys(policy_id="POL_S1"), "target_keys": _keys(agency_id="AGENCY_S1")},
        ]

    def _verify(self, rows):
        return lg.verify_relationship_endpoints(self.graph, StaticRowsRunner({self.query: rows}))

    def test_exact_match_passes(self):
        report = self._verify(self.good_rows)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["actual_by_type"]["SUPPORTED_BY"], 1)

    def test_each_problem_is_detected(self):
        def wrong_label(rows):
            rows[0] = {**rows[0], "source_labels": ["Document"],
                       "source_keys": _keys(document_id="DOC_S1", policy_id="POL_S1")}

        def endpoint_missing_id(rows):
            rows[1] = {**rows[1], "target_keys": _keys()}

        cases = {
            "missing": lambda rows: rows.pop(1),
            "extra": lambda rows: rows.append({**rows[0], "target_keys": _keys(document_id="DOC_OTHER")}),
            "duplicate": lambda rows: rows.append(dict(rows[0])),
            "wrong_label": wrong_label,
            "unknown_type": lambda rows: rows.append({**rows[0], "type": "OWNS"}),
            "endpoint_label_issue": lambda rows: rows.append({**rows[0], "source_labels": ["Policy", "Document"]}),
            "endpoint_missing_id": endpoint_missing_id,
        }
        for problem, mutate in cases.items():
            rows = [dict(row) for row in self.good_rows]
            mutate(rows)
            report = self._verify(rows)
            with self.subTest(problem=problem):
                self.assertFalse(report["ok"])
                self.assertIn(problem, report["problems"])

    def test_same_count_with_wrong_endpoint_fails(self):
        """건수(2개)는 같지만 한 관계가 다른 문서에 붙어 있으면 실패해야 한다."""
        rows = [dict(row) for row in self.good_rows]
        rows[0] = {**rows[0], "target_keys": _keys(document_id="DOC_OTHER")}
        report = self._verify(rows)
        self.assertEqual(report["actual_total"], report["expected_total"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"]["missing"], [("SUPPORTED_BY", "Policy", "POL_S1", "Document", "DOC_S1")])
        self.assertEqual(report["problems"]["extra"], [("SUPPORTED_BY", "Policy", "POL_S1", "Document", "DOC_OTHER")])

    def test_legacy_style_label_less_match_is_detected(self):
        """인계 로더처럼 Document(policy_id=POL_S1)에서 출발한 SUPPORTED_BY가 섞이면 wrong_label로 잡힌다."""
        rows = [dict(row) for row in self.good_rows]
        rows.append({"type": "SUPPORTED_BY", "source_labels": ["Document"], "target_labels": ["Document"],
                     "source_keys": _keys(document_id="DOC_S1", policy_id="POL_S1"),
                     "target_keys": _keys(document_id="DOC_S1", policy_id="POL_S1")})
        report = self._verify(rows)
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"]["wrong_label"], [["SUPPORTED_BY", "Document", "Document", 1]])

    def test_real_handoff_graph_on_fake_db_passes(self):
        graph = lg.read_graph_input()
        report = lg.verify_relationship_endpoints(graph, _loaded_fake(graph))
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["actual_total"], hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(report["actual_by_type"], report["expected_by_type"])


class TestVerifyProperties(unittest.TestCase):
    """노드·관계 속성값 비교 (sample_only)."""

    def setUp(self):
        self.graph = _sample_graph()
        self.runner = _loaded_fake(self.graph)

    def test_loaded_sample_graph_properties_match(self):
        nodes = lg.verify_node_properties(self.graph, self.runner)
        relationships = lg.verify_relationship_properties(self.graph, self.runner)
        self.assertTrue(nodes["ok"], nodes)
        self.assertTrue(relationships["ok"], relationships)
        self.assertEqual(nodes["compared_nodes"], 3)
        self.assertEqual(relationships["compared_relationships"], 2)
        self.assertEqual(set(relationships["excluded_structural_fields"]), {"source_id", "relation", "target_id"})

    def test_missing_extra_type_and_value_differences_are_separated(self):
        policy = self.runner.nodes[("Policy", "POL_S1")]
        del policy["policy_name"]                         # missing_property
        policy["added_in_db"] = "sample_only"             # extra_property
        policy["life_stages"] = "청년"                     # type_mismatch: list[str] → str
        self.runner.nodes[("Organization", "AGENCY_S1")]["name"] = "다른 기관"   # value_mismatch
        report = lg.verify_node_properties(self.graph, self.runner)
        self.assertFalse(report["ok"])
        self.assertEqual(report["counts"], {"missing_property": 1, "extra_property": 1, "type_mismatch": 1,
                                            "value_mismatch": 1})
        mismatch = report["examples"]["type_mismatch"][0]
        self.assertEqual((mismatch["property"], mismatch["expected_kind"], mismatch["actual_kind"]),
                         ("life_stages", "list[str]", "str"))

    def test_number_string_bool_and_list_order_are_strict(self):
        cases = [("3", 3, "type_mismatch"), (True, 1, "type_mismatch"), (1, 1.5, "type_mismatch"),
                 (["a", "b"], ["b", "a"], "value_mismatch"), (["a"], [], "type_mismatch")]
        for expected, actual, kind in cases:
            with self.subTest(expected=expected, actual=actual):
                graph = _sample_graph()
                graph.nodes_by_label["Organization"][0]["score"] = expected
                runner = _loaded_fake(graph)
                runner.nodes[("Organization", "AGENCY_S1")]["score"] = actual
                report = lg.verify_node_properties(graph, runner)
                self.assertEqual(report["counts"][kind], 1, report["counts"])

    def test_required_null_input_is_rejected_before_load(self):
        """선택 필드 null을 허용해도 필수 출처의 null은 쓰기 전에 거부한다."""
        graph = _sample_graph()
        graph.nodes_by_label["Document"][0]["source_url"] = None
        self.assertIn("invalid_property", lg.validate_graph_input(graph).summary()["error_counts"])
        runner = FakeGraphRunner()
        with self.assertRaises(lg.GraphValidationError):
            lg.load_graph(graph, runner)
        self.assertEqual(runner.calls, [])

    def test_null_input_is_expected_to_be_absent_not_converted(self):
        """검증 함수 쪽 규칙: 원본 null은 DB에서 "속성 없음"으로 기대한다 (다른 값으로 바뀌어 있으면 실패)."""
        graph = _sample_graph()
        graph.nodes_by_label["Document"][0]["published_at"] = None     # 비교 기준에만 null을 넣는다 (sample_only)
        runner = self.runner                                             # null 없이 적재된 상태
        report = lg.verify_node_properties(graph, runner)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["null_properties_not_stored"], {"Document.published_at": 1})
        runner.nodes[("Document", "DOC_S1")]["published_at"] = "unknown"   # null을 다른 값으로 바꿔 저장했다면 실패
        self.assertEqual(lg.verify_node_properties(graph, runner)["counts"]["extra_property"], 1)

    def test_relationship_property_difference_and_unloaded_input_field(self):
        item = ("SUPPORTED_BY", ("Policy", "POL_S1"), ("Document", "DOC_S1"))
        self.runner.relationship_properties[item]["review_status"] = "verified"
        report = lg.verify_relationship_properties(self.graph, self.runner)
        self.assertEqual(report["counts"]["value_mismatch"], 1)
        graph = _sample_graph()
        graph.relationships[0]["confidence"] = 0.9                       # 로더가 싣지 않는 필드 (sample_only)
        report = lg.verify_relationship_properties(graph, _loaded_fake(graph))
        self.assertFalse(report["ok"])
        self.assertEqual(report["input_fields_not_loaded"], {"confidence": 1})

    def test_real_handoff_graph_properties_on_fake_db_pass(self):
        graph = lg.read_graph_input()
        runner = _loaded_fake(graph)
        nodes = lg.verify_node_properties(graph, runner)
        relationships = lg.verify_relationship_properties(graph, runner)
        self.assertTrue(nodes["ok"], nodes["counts"])
        self.assertTrue(relationships["ok"], relationships["counts"])
        self.assertEqual(nodes["compared_nodes"], hd.EXPECTED_NODE_COUNT)
        self.assertEqual(relationships["compared_relationships"], hd.EXPECTED_RELATIONSHIP_COUNT)

    def test_value_kind(self):
        self.assertEqual([lg.value_kind(v) for v in (None, True, 1, 1.0, "a", [], ["a"], ["a", 1])],
                         ["null", "bool", "int", "float", "str", "list[empty]", "list[str]", "list[int,str]"])


class TestPreflightAndEmptyDatabaseGuard(unittest.TestCase):
    def test_empty_database_is_allowed(self):
        report = lg.preflight_database(_sample_graph(), FakeGraphRunner())
        self.assertEqual(report["state"], "empty")

    def test_schema_conflict_blocks_all_writes(self):
        runner = FakeGraphRunner()
        runner.constraints.append({"name": "constraint_policy_id", "type": "UNIQUENESS", "entityType": "NODE",
                                   "labelsOrTypes": ["Policy"], "properties": ["policy_name"],
                                   "ownedIndex": "constraint_policy_id"})
        with self.assertRaises(lg.SchemaConflictError) as context:
            lg.load_graph(_sample_graph(), runner)
        self.assertEqual(context.exception.report["state"], "schema_conflict")
        self.assertFalse([call for call in runner.calls if call[0] in ("schema", "write")])

    def test_matching_existing_schema_without_data_is_allowed(self):
        runner = FakeGraphRunner()
        for statement in lg.build_schema_statements():
            runner.run_schema(statement)
        result = lg.load_graph(_sample_graph(), runner)
        self.assertEqual(result["preflight"]["state"], "empty")
        self.assertTrue(result["ok"], result["verification"])

    def test_relationship_only_database_is_not_empty(self):
        runner = StaticRowsRunner({lg.QUERY_TOTAL_COUNTS: [{"nodes": 0, "relationships": 1}],
                                   lg.QUERY_SHOW_CONSTRAINTS: [], lg.QUERY_SHOW_INDEXES: []})
        with self.assertRaises(lg.DatabaseNotEmptyError):
            lg.preflight_database(_sample_graph(), runner)

    def test_allow_existing_rejects_wrong_relationship_even_with_same_count(self):
        graph = _sample_graph()
        runner = _loaded_fake(graph)
        # 같은 건수를 유지한 채 관계 하나의 도착 노드를 바꾼다 (sample_only)
        runner.nodes[("Document", "DOC_S2")] = {"document_id": "DOC_S2"}
        runner.relationships = {item for item in runner.relationships if item[0] != "SUPPORTED_BY"}
        runner.relationships.add(("SUPPORTED_BY", ("Policy", "POL_S1"), ("Document", "DOC_S2")))
        writes = runner.write_count
        with self.assertRaises(lg.ExistingDataMismatchError) as context:
            lg.load_graph(graph, runner, allow_existing=True)
        self.assertEqual(runner.write_count, writes)
        problems = context.exception.report["relationships"]["problems"]
        self.assertEqual(problems["extra"], [("SUPPORTED_BY", "Policy", "POL_S1", "Document", "DOC_S2")])

    def test_preflight_and_verification_only_read(self):
        runner = _loaded_fake()
        reads_before = len(runner.calls)
        writes_before = runner.write_count
        lg.verify_loaded_graph(_sample_graph(), runner)
        self.assertEqual(runner.write_count, writes_before)
        self.assertTrue(all(call[0] == "read" for call in runner.calls[reads_before:]))

    def test_read_queries_contain_no_write_clauses(self):
        queries = [lg.QUERY_TOTAL_COUNTS, lg.QUERY_SHOW_CONSTRAINTS, lg.QUERY_SHOW_INDEXES,
                   lg.build_node_identity_query(), lg.build_relationship_endpoint_query()]
        for query in queries:
            with self.subTest(query=query[:40]):
                self.assertNotRegex(query.upper(), r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP)\b")


class TestDriverRunnerReadSessionAndNotifications(unittest.TestCase):
    """Neo4jDriverRunner가 읽기 전용 세션을 쓰고 알림을 모으는지 (가짜 드라이버, sample_only)."""

    class FakeNotification:
        def __init__(self, code, severity="WARNING", category="UNRECOGNIZED"):
            self.code, self.title = code, "sample_only"
            self.severity_level = type("Level", (), {"name": severity})()
            self.category = type("Category", (), {"name": category})()

    def _driver(self, notifications, log):
        class FakeResult:
            def __iter__(self):
                return iter([type("Record", (), {"data": lambda self: {"count": 0}})()])

            def consume(self):
                return type("Summary", (), {"summary_notifications": notifications})()

            def single(self):
                return {"merged": 1}

        class FakeSession:
            def __init__(self, kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def run(self, query, *args, **kwargs):
                log.append(("run", self.kwargs.get("default_access_mode"), query))
                return FakeResult()

            def execute_write(self, work):
                log.append(("execute_write", self.kwargs.get("default_access_mode")))
                return work(type("Tx", (), {"run": lambda self, query, **params: FakeResult()})())

        class FakeDriver:
            def session(self, **kwargs):
                return FakeSession(kwargs)

        return FakeDriver()

    def test_reads_use_read_access_mode_and_writes_use_write_mode(self):
        log = []
        runner = lg.Neo4jDriverRunner(self._driver([], log))
        runner.read_count("MATCH (n:Policy) RETURN count(n) AS count")
        runner.read_rows(lg.QUERY_SHOW_INDEXES)
        runner.write_batch("UNWIND $rows AS row RETURN 1 AS merged", [{"id": 1}], expected=1)
        runner.run_schema(lg.build_schema_statements()[0])
        self.assertEqual([entry[1] for entry in log], ["READ", "READ", "WRITE", "WRITE"])

    def test_notifications_are_collected_and_classified(self):
        notes = [self.FakeNotification("Neo.ClientNotification.Statement.UnknownLabelWarning"),
                 self.FakeNotification("Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists", "INFORMATION",
                                       "SCHEMA"),
                 self.FakeNotification("Neo.ClientNotification.Statement.CartesianProduct", "INFORMATION",
                                       "PERFORMANCE")]
        runner = lg.Neo4jDriverRunner(self._driver(notes, []))
        runner.read_rows("MATCH (n:Unknown) RETURN count(n) AS count")
        self.assertEqual(len(runner.notifications), 3)
        classified = lg.classify_notifications(runner.notifications)
        self.assertEqual(classified["unknown_token"], 1)
        self.assertEqual(classified["schema_already_exists"], 1)
        self.assertEqual([item["code"] for item in classified["needs_review"]],
                         ["Neo.ClientNotification.Statement.CartesianProduct"])
        self.assertEqual(classified["needs_review"][0]["category"], "PERFORMANCE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
