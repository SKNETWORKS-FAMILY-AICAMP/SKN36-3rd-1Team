"""여러 계층에 같은 값이 있어야 하는 상수·문구가 서로 어긋나지 않는지 확인한다. DB·LLM 호출 없음.

1) 화면(frontend)이 Checker·Pipeline 문구로 그룹 행·미판정·보류 안내를 알아본다 (ask_contract §8.3, 계약 변경 전 임시 방식).
   Checker 문구를 바꾸면 이 테스트가 실패한다. 그때는 아래 frontend 파일의 문구와
   frontend/src/components/__fixtures__/policyChecksFromChecker.json(실제 Checker 출력에서 가져온 픽스처)을
   같은 변경에서 다시 맞춘다. 픽스처는 v1.4.0 검색 결과의 정책 객체로 check_eligibility(profile, policy)를
   실행한 결과를 {정책ID_나이: 결과} 형태로 저장한 것이다.
2) 데이터 품질 '영향 항목'은 database/eligibility_fields.py 한 곳에서 관리한다.
3) 기본 데이터 릴리스 버전은 백엔드(handoff_data)와 프론트(loadGraph·vite.config) 경로가 같아야 한다.
"""

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.eligibility import checker  # noqa: E402
from backend.rag import pipeline  # noqa: E402
from database import eligibility_fields as fields  # noqa: E402

FRONTEND = PROJECT_ROOT / "frontend"
FIXTURE = FRONTEND / "src" / "components" / "__fixtures__" / "policyChecksFromChecker.json"


def ts_source(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def ts_string(source: str, pattern: str) -> str:
    match = re.search(pattern, source)
    if match is None:
        raise AssertionError(f"프론트 문구를 찾지 못했습니다: {pattern}")
    return match.group(1)


def group_policy(logic: str) -> dict:
    """가상 조건 트리 (sample_only): 나이 조건 1개를 가진 루트 그룹."""
    tree = {"group_id": "G_SAMPLE", "logic": logic, "groups": [],
            "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~34", "name": "가상 나이"}]}
    return {"policy_id": "POL_SAMPLE", "graph_status": "FOUND",
            "graph": {"policy": {}, "eligibility": {"condition_tree": [tree]}}, "data_quality": {}}


class TestFrontendCheckerWording(unittest.TestCase):
    def test_group_reason_prefixes_match_checker(self):
        source = ts_source("src/lib/conditionGroups.ts")
        for logic in ("AND", "OR", "NOT"):
            with self.subTest(logic=logic):
                frontend = ts_string(source, rf"\b{logic}: '([^']+)'")
                result = checker.check_eligibility({"age": 25, "region": None, "income": None}, group_policy(logic))
                self.assertEqual(result["conditions"][-1]["reason"], frontend)

    def test_unjudged_prefixes_match_checker(self):
        source = ts_source("src/lib/conditionGroups.ts")
        prefixes = re.findall(r"'([^']+)'", ts_string(source, r"UNJUDGED_REASON_PREFIXES = \[([^\]]+)\]"))
        unknown = checker.check_eligibility({"age": 25, "region": None, "income": None}, group_policy("UNKNOWN"))
        missing = checker.check_eligibility({"age": 25, "region": None, "income": None},
                                            {"policy_id": "POL_SAMPLE", "graph_status": "MISSING_IN_GRAPH"})
        reasons = [unknown["conditions"][0]["reason"], missing["conditions"][0]["reason"]]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertTrue(any(reason.startswith(prefix) for prefix in prefixes), reason)

    def test_missing_age_reason_matches_checker(self):
        frontend = ts_string(ts_source("src/components/TurnView.tsx"), r"MISSING_INPUT_REASON[^=]*= \{ age: '([^']+)'")
        result = checker.check_eligibility({"age": None, "region": None, "income": None}, group_policy("AND"))
        self.assertEqual(result["conditions"][0]["reason"], frontend)

    def test_hold_notice_matches_pipeline(self):
        frontend = ts_string(ts_source("src/lib/answerView.ts"), r"HOLD_NOTICE_TEXT = '([^']+)'")
        self.assertEqual(pipeline.HOLD_NOTICE_TEXT, frontend)

    def test_fixture_group_rows_use_current_checker_wording(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        allowed = set(checker._GROUP_REASONS.values())
        statuses = {"eligible", "ineligible", "needs_check"}
        for key, check in fixture.items():
            for condition in check["conditions"]:
                with self.subTest(policy=key, reason=condition["reason"][:40]):
                    self.assertIn(condition["status"], statuses)
                    if condition["reason"].endswith("(종합 결과)."):
                        self.assertIn(condition["reason"], allowed)


class TestSharedEligibilityFields(unittest.TestCase):
    def test_pipeline_and_checker_use_shared_fields(self):
        self.assertIs(pipeline.SCOPED_CONDITION_FIELDS, fields.SCOPED_ELIGIBILITY_FIELDS)
        quality = {"hold_auto_eligibility": True, "affected_attributes": sorted(fields.SCOPED_ELIGIBILITY_FIELDS)}
        self.assertEqual(checker._affected_fields(quality, {}, False), set(fields.SCOPED_ELIGIBILITY_FIELDS))
        unknown = {"hold_auto_eligibility": True, "affected_attributes": ["not_a_field"]}
        self.assertEqual(checker._affected_fields(unknown, {}, False), {fields.ALL_ELIGIBILITY_FIELDS})

    def test_search_quality_codes_only_use_shared_fields(self):
        from database import hybrid_search as hs

        used = {field for meta in hs.QUALITY_CODE_META.values() for field in meta.get("affects", [])}
        self.assertLessEqual(used, fields.SCOPED_ELIGIBILITY_FIELDS | {fields.ALL_ELIGIBILITY_FIELDS})


class TestDatasetVersionPaths(unittest.TestCase):
    def test_frontend_and_backend_point_to_same_release(self):
        from database import handoff_data as hd

        folder = f"v{hd.RELEASE_V140_VERSION}"
        self.assertEqual(hd.DEFAULT_HANDOFF_DIR.name, folder)
        load_graph = ts_source("src/graph/loadGraph.ts")
        vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
        # 주석이 아니라 코드의 문자열 경로만 본다 ('...data/releases/<버전>/...' 또는 '...data/releases/<버전>').
        frontend_folders = re.findall(r"""['"][^'"]*data/releases/(v[^/'"]+)[^'"]*['"]""", load_graph + vite)
        self.assertGreaterEqual(len(frontend_folders), 3)      # loadGraph glob 2곳 + vite fs.allow 1곳 이상
        self.assertEqual(set(frontend_folders), {folder})


if __name__ == "__main__":
    unittest.main(verbosity=2)
