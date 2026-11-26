"""#79 나이 교정의 경계 회귀 테스트 (v1.4.0 후보 데이터, DB 호출 없음).

- 내부 판정(_walk): 나이 범위 밖이면 '해당 안 됨', 안이면 나머지 UNKNOWN 그룹 때문에 '확인 필요'.
- 공개 Checker(check_eligibility): UNKNOWN 그룹이 있으면 전체 보류 안전장치로 모든 조건이 '확인 필요'.
  즉 이번 교정만으로는 실제 응답에서 나이 범위 밖이 '해당 안 됨'으로 바뀌지 않는다(부분 판정은 #40에서 다룬다).
"""
import json
import unittest
from pathlib import Path

from backend.eligibility import checker

ROOT = Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data/releases/v1.4.0-candidate/neo4j"


def _rows(name):
    with open(GRAPH / name, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class _Graph:
    groups = {row["group_id"]: row for row in _rows("nodes_condition_group.jsonl")}
    conditions = {row["condition_id"]: row for row in _rows("nodes_eligibility_condition.jsonl")}
    child_groups, child_conditions, roots = {}, {}, {}
    for rel in _rows("relationships.jsonl"):
        if rel["relation"] == "CONTAINS_GROUP":
            child_groups.setdefault(rel["source_id"], []).append(rel["target_id"])
        elif rel["relation"] == "CONTAINS_CONDITION":
            child_conditions.setdefault(rel["source_id"], []).append(rel["target_id"])
        elif rel["relation"] == "HAS_ROOT_CONDITION_GROUP":
            roots[rel["source_id"]] = rel["target_id"]

    @classmethod
    def tree(cls, group_id):
        return {"group_id": group_id, "logic": cls.groups[group_id]["logic"],
                "conditions": [cls.conditions[c] for c in cls.child_conditions.get(group_id, [])],
                "groups": [cls.tree(g) for g in cls.child_groups.get(group_id, [])]}

    @classmethod
    def policy(cls, policy_id):
        tree = cls.tree(cls.roots[policy_id])

        def unknown(node):
            own = [node["group_id"]] if node["logic"] == "UNKNOWN" else []
            return own + [g for child in node["groups"] for g in unknown(child)]

        return tree, {"policy_id": policy_id, "graph_status": "FOUND", "data_quality": {},
                      "graph": {"policy": {"eligibility": ""},
                                "eligibility": {"condition_tree": [tree], "unknown_logic_groups": unknown(tree),
                                                "structure_issues": []}}}


class TestAgeBoundaries(unittest.TestCase):
    def walk(self, policy_id, age):
        tree, _ = _Graph.policy(policy_id)
        return checker._walk(tree, {"age": age})[0]

    def public(self, policy_id, age):
        _, policy = _Graph.policy(policy_id)
        return {row["status"] for row in checker.check_eligibility({"age": age}, policy)["conditions"]}

    def test_pol_bok_004_includes_month_of_turning_40(self):
        # 원문: '신청 월의 전월에 만 15세가 된 자 ~ 신청 월에 만 40세가 되는 자'. 신청 월을 모르므로 40세는 확인 필요.
        self.assertEqual(self.walk("POL_BOK_004", 14), checker.NO)
        for age in (15, 39, 40):
            with self.subTest(age=age):
                self.assertEqual(self.walk("POL_BOK_004", age), checker.CHECK)
        self.assertEqual(self.walk("POL_BOK_004", 41), checker.NO)
        self.assertEqual(self.walk("POL_BOK_004", None), checker.CHECK)

    def test_pol_stg_icn_001_uses_guide_range(self):
        # 인천 서구 사업안내서 '서구에 거주하고 있는 청년(…19~39세)'. 정부24 지원조건 코드(18세)와 다르다.
        self.assertEqual(self.walk("POL_STG_ICN_001", 18), checker.NO)
        self.assertEqual(self.walk("POL_STG_ICN_001", 19), checker.CHECK)
        self.assertEqual(self.walk("POL_STG_ICN_001", 39), checker.CHECK)
        self.assertEqual(self.walk("POL_STG_ICN_001", 40), checker.NO)

    def test_pol_stg_icn_001_age_links_to_2026_notice(self):
        # #90: 2026년 제4호 기업안내문을 후보 문서로 추가해 근거를 연결하고 검수 대기를 해제했다.
        rows = [rel for rel in _rows("relationships.jsonl")
                if rel["target_id"] == "COND_AGE_19_39"
                and rel["source_id"] in ("POL_STG_ICN_001", "GRP_POL_STG_ICN_001_ROOT")]
        self.assertEqual(len(rows), 2)
        self.assertEqual({rel["review_status"] for rel in rows}, {"verified"})
        self.assertEqual({rel["evidence_document_id"] for rel in rows}, {"DOC_STG_ICN_001_NOTICE_2026"})
        documents = {row["document_id"] for row in _rows("nodes_document.jsonl")}
        self.assertIn("DOC_STG_ICN_001_NOTICE_2026", documents)

    def test_age_alone_never_yields_eligible(self):
        for policy_id in ("POL_010", "POL_BOK_004", "POL_STG_ICN_001", "POL_YC_024"):
            for age in (None, 10, 20, 30, 70):
                with self.subTest(policy_id=policy_id, age=age):
                    self.assertNotEqual(self.walk(policy_id, age), checker.OK)

    def test_public_checker_keeps_unknown_group_hold(self):
        # 국민취업지원제도(15~69세): _walk는 10세를 '해당 안 됨'으로 보지만 공개 Checker는 전체 보류를 유지한다.
        self.assertEqual(self.walk("POL_010", 10), checker.NO)
        for age in (10, 30, None):
            with self.subTest(age=age):
                self.assertEqual(self.public("POL_010", age), {checker.CHECK})

    def test_real_applicant_age_leaf_boundaries(self):
        """#79: 실제 r2 신청자 연령 조건의 양 끝 경계를 검사한다."""
        cases = {
            "POL_010": (15, 69),
            "POL_STG_ICN_001": (19, 39),
        }

        for policy_id, (low, high) in cases.items():
            tree, _ = _Graph.policy(policy_id)

            # 실제 Neo4j 조건 트리에서 AGE 조건 추출
            stack = [tree]
            age_conditions = []

            while stack:
                node = stack.pop()
                age_conditions.extend(
                    condition
                    for condition in node.get("conditions", [])
                    if str(condition.get("type", "")).upper() == "AGE"
                )
                stack.extend(node.get("groups", []))

            self.assertEqual(
                len(age_conditions), 1,
                f"{policy_id}: AGE 조건 수 확인 필요",
            )

            condition = age_conditions[0]

            self.assertEqual(condition.get("operator"), "BETWEEN")

            match = checker._AGE_RANGE.fullmatch(
                str(condition.get("value") or "")
            )
            self.assertIsNotNone(match)

            self.assertEqual(
                tuple(map(int, match.groups())),
                (low, high),
            )

            boundary_cases = [
                (low - 1, checker.NO),
                (low, checker.OK),
                (high, checker.OK),
                (high + 1, checker.NO),
                (None, checker.CHECK),
            ]

            for age, expected in boundary_cases:
                with self.subTest(policy_id=policy_id, age=age):
                    result = checker._condition(
                        condition, {"age": age}
                    )
                    self.assertEqual(result["field"], "age")
                    self.assertEqual(result["status"], expected)

    def test_issue79_real_r2_age_api_matches_checker(self):
        """#79: 실제 r2 조건 트리의 공개 Checker·API·LLM 결과 비교."""
        from copy import deepcopy
        from unittest.mock import Mock

        from fastapi.testclient import TestClient
        from backend.api.main import create_app

        class FakeSession:
            profile = collection = "issue79_test_only"

            def __init__(self, policy):
                self.policy = policy

            def search_policy_context(self, question, filters=None, k=10):
                return {"policies": [deepcopy(self.policy)]}

            def close(self):
                pass

        cases = {
            "POL_010": (14, 15, 69, 70, None),
            "POL_STG_ICN_001": (18, 19, 39, 40, None),
        }

        for policy_id, ages in cases.items():
            _, policy = _Graph.policy(policy_id)
            policy["policy_name"] = f"{policy_id} 테스트 정책"
            policy["best_rank"] = 1

            # API 검증을 위한 테스트용 증거.
            # 실제 공식 근거 문서를 대체하는 것은 아니다.
            policy["evidence"] = [{
                "rank": 1,
                "chunk_id": f"TEST_{policy_id}",
                "document_id": f"TEST_DOC_{policy_id}",
                "text": "연령 판정 경계 테스트용 증거",
                "source_url": "https://example.org/notice",
            }]

            original = deepcopy(policy)

            for age in ages:
                with self.subTest(policy_id=policy_id, age=age):
                    profile = {
                        "age": age,
                        "region": "",
                        "income": None,
                    }

                    expected = checker.check_eligibility(
                        profile, deepcopy(policy)
                    )

                    generate = Mock(return_value={
                        "status": "answered",
                        "answer": "테스트용 원문 안내 [1]",
                        "used_source_ids": [1],
                        "follow_up_questions": [],
                    })

                    app = create_app(
                        session_factory=lambda: FakeSession(policy),
                        generate_fn=generate,
                    )

                    with TestClient(app) as client:
                        response = client.post("/api/ask", json={
                            "question": "지원 가능한 나이인가요?",
                            "history": [],
                            "profile": profile,
                        })

                    self.assertEqual(
                        response.status_code, 200, response.text
                    )

                    actual = response.json()["policy_checks"][0]
                    self.assertEqual(actual["policy_id"], policy_id)

                    expected_age = [
                        (row["status"], row["reason"])
                        for row in expected["conditions"]
                        if row["field"] == "age"
                    ]
                    actual_age = [
                        (row["status"], row["reason"])
                        for row in actual["conditions"]
                        if row["field"] == "age"
                    ]

                    self.assertTrue(
                        expected_age,
                        f"{policy_id}: AGE 판정 결과가 없음",
                    )
                    self.assertEqual(actual_age, expected_age)

                    llm_conditions = (
                        generate.call_args.args[2][0]["conditions"]
                    )
                    llm_age = [
                        (row["status"], row["reason"])
                        for row in llm_conditions
                        if row["field"] == "age"
                    ]
                    self.assertEqual(llm_age, expected_age)
                    self.assertEqual(policy, original)


if __name__ == "__main__":
    unittest.main()
