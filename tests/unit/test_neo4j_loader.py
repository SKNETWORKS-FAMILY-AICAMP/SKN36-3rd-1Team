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

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[tuple] = []
        self.nodes: dict[tuple[str, str], dict] = {}
        self.relationships: set[tuple] = set()
        self.write_count = 0
        self.fail_on_call = fail_on_call

    def run_schema(self, statement):
        self.calls.append(("schema", statement))

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
        staged = [(relation, (source_label, row["source_id"]), (target_label, row["target_id"])) for row in rows
                  if (source_label, row["source_id"]) in self.nodes and (target_label, row["target_id"]) in self.nodes]
        if len(staged) != expected:
            raise RuntimeError(f"MERGE 건수 불일치: 기대 {expected}, 실제 {len(staged)}")   # 적용 전에 실패 = 롤백
        self.relationships.update(staged)
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
            "Policy": [{"policy_id": "POL_S1", "policy_name": "가상 정책 (sample_only)", "life_stages": ["청년"]}],
            "Document": [{"document_id": "DOC_S1", "policy_id": "POL_S1", "content_hash": "h1"}],
            "Organization": [{"agency_id": "AGENCY_S1", "name": "가상 기관"}],
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
        second = lg.load_graph(self.graph, runner)
        self.assertEqual(first["after_counts"], second["after_counts"])
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
        result = lg.load_graph(self.graph, runner)
        self.assertEqual(len(runner.relationships), hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(result["count_mismatches"], {})

    def test_existing_unrelated_data_is_reported_not_deleted(self):
        runner = FakeGraphRunner()
        runner.nodes[("Policy", "POL_EXISTING_SAMPLE_ONLY")] = {"policy_id": "POL_EXISTING_SAMPLE_ONLY"}
        result = lg.load_graph(self.graph, runner)
        self.assertEqual(result["count_mismatches"], {"node:Policy": {"expected": 237, "actual": 238}})
        self.assertIn(("Policy", "POL_EXISTING_SAMPLE_ONLY"), runner.nodes)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
