"""Neo4j 로더의 null 처리 계약 검사 (Issue #5 리뷰 댓글: 선택 속성 null 허용, 필수 속성 null만 오류).

기준은 docs/data/data_contract.md §2.1의 "필수(NOT NULL)"/"선택(Nullable)" 목록이다.
문서 목록을 직접 읽어 비교하므로, 계약과 로더가 어긋나면 이 테스트가 실패한다. DB 접속 없음.
"""
import copy
import math
import re
import unittest
from pathlib import Path

from database.neo4j import load_graph as lg
from test_neo4j_loader import _sample_graph

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "data" / "data_contract.md"


def contract_fields(section_label: str, kind: str) -> set[str]:
    """§2.1에서 `정책(`Policy`)`·`근거 문서(`Document`)` 블록의 필수/선택 필드 이름을 읽는다."""
    text = CONTRACT.read_text(encoding="utf-8")
    block = text.split(f"(`{section_label}`)", 1)[1].split("- **", 1)[1]
    block = "- **" + block.split("\n- **", 1)[0]
    line = next(l for l in block.splitlines() if f"**{kind}" in l)
    return set(re.findall(r"`([A-Za-z_]+)`", line))


def errors(graph):
    return lg.validate_graph_input(graph).summary()["error_counts"]


class NullableContractTests(unittest.TestCase):
    def test_every_contract_nullable_field_accepts_null(self):
        for label in ("Policy", "Document"):
            for field in sorted(contract_fields(label, "선택")):
                with self.subTest(label=label, field=field):
                    graph = _sample_graph()
                    graph.nodes_by_label[label][0][field] = None
                    self.assertEqual(errors(graph), {}, f"{label}.{field}=None은 계약상 허용")

    def test_every_graph_required_field_rejects_null(self):
        for label, fields in lg.REQUIRED_NODE_STRING_FIELDS.items():
            if not _sample_graph().nodes_by_label.get(label):
                continue
            for field in fields:
                with self.subTest(label=label, field=field):
                    graph = _sample_graph()
                    graph.nodes_by_label[label][0][field] = None
                    # 고유 ID null은 missing_id, 그 밖의 필수 속성 null은 invalid_property로 적재 전에 거부된다.
                    expected = "missing_id" if field == lg.LABEL_ID_FIELDS[label] else "invalid_property"
                    self.assertIn(expected, errors(graph))

    def test_graph_required_fields_match_data_side_graph_contract(self):
        # Graph 투영은 processed 레코드와 필드가 다르다(region 단일 값 등). 데이터 담당 검증기
        # (scripts/load_candidate_neo4j.py REQUIRED_GRAPH_FIELDS·NULLABLE_FIELDS)와 같은 기준이어야 한다.
        from scripts.load_candidate_neo4j import NULLABLE_FIELDS, REQUIRED_GRAPH_FIELDS
        for label in ("Policy", "Document"):
            required = set(lg.REQUIRED_NODE_STRING_FIELDS[label]) | set(lg.REQUIRED_NODE_LIST_FIELDS.get(label, ()))
            with self.subTest(label=label):
                self.assertEqual(required, REQUIRED_GRAPH_FIELDS[label])
                self.assertEqual(set(lg.NULLABLE_NODE_STRING_FIELDS[label]), NULLABLE_FIELDS[label])
                self.assertEqual(set(lg.NULLABLE_NODE_STRING_FIELDS[label]), contract_fields(label, "선택"))
                self.assertFalse(required & contract_fields(label, "선택"))

    def test_target_groups_may_be_absent_but_not_null_or_mixed(self):
        graph = _sample_graph()
        graph.nodes_by_label["Policy"][0].pop("target_groups", None)
        self.assertEqual(errors(graph), {})
        for value in (None, ["a", 1], "a"):
            with self.subTest(value=value):
                graph = _sample_graph()
                graph.nodes_by_label["Policy"][0]["target_groups"] = value
                self.assertIn("invalid_property", errors(graph))

    def test_nullable_field_still_rejects_unstorable_value(self):
        for value in ({"nested": 1}, ["a", 1], 3):
            with self.subTest(value=value):
                graph = _sample_graph()
                graph.nodes_by_label["Policy"][0]["official_apply_url"] = value
                self.assertIn("invalid_property", errors(graph))

    def test_review_flags_object_is_not_stored_but_null_is_allowed(self):
        graph = _sample_graph()
        graph.nodes_by_label["Policy"][0]["review_flags"] = {"age_review_needed": True}
        self.assertIn("invalid_property", errors(graph))

    def test_undeclared_null_is_rejected(self):
        # 계약에 선택으로 적히지 않은 필드는 null 대신 속성을 생략해야 한다 (예: Issue #21 region_detail).
        graph = _sample_graph()
        graph.nodes_by_label["Policy"][0]["region_detail"] = None
        self.assertIn("invalid_property", errors(graph))

    def test_non_finite_float_is_rejected(self):
        for value in (math.nan, math.inf, -math.inf, [1.0, math.nan]):
            with self.subTest(value=value):
                graph = _sample_graph()
                graph.nodes_by_label["Document"][0]["score_sample_only"] = value
                self.assertIn("invalid_property", errors(graph))

    def test_null_is_omitted_when_writing_nodes_and_relationships(self):
        node = {"policy_id": "POL_S1", "official_apply_url": None, "policy_name": "가상"}
        row = lg._node_row("Policy", node)
        self.assertNotIn("official_apply_url", row["properties"])
        self.assertEqual(node["official_apply_url"], None)          # 입력은 바꾸지 않는다
        rel = {"source_id": "a", "relation": "SUPPORTED_BY", "target_id": "b", "evidence_location": None,
               "review_status": "extracted"}
        self.assertEqual(lg._relationship_row(rel)["properties"], {"review_status": "extracted"})

    def test_relationship_optional_null_ok_but_non_string_rejected(self):
        graph = _sample_graph()
        graph.relationships[0]["evidence_location"] = None
        self.assertEqual(errors(graph), {})
        graph = _sample_graph()
        graph.relationships[0]["raw_value"] = {"income": 1}
        self.assertIn("invalid_relationship_property", errors(graph))


if __name__ == "__main__":
    unittest.main()
