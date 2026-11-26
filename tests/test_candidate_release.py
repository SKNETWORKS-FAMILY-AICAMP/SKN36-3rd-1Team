"""실제 후보의 무결성과 신청 링크 오류의 재발을 검사한다.

example.org 주소는 URL 검사만을 위한 가상 테스트 값이다.
DB 서버·API 키·추가 테스트 패키지가 필요 없는 unittest를 사용한다.
"""

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from collectors.preprocessing.application_urls import application_url_or_none, first_application_url
from collectors.api.gov24_collector import normalize_gov24_record
from collectors.api.youthcenter_collector import load_code_definitions, map_zip_codes_to_regions, normalize_youthcenter_item
from collectors.preprocessing.normalize import parse_bokjiro_detail_xml
from scripts import data_corrections as dc
from scripts.load_candidate_neo4j import (
    NODE_CONFIG, relationship_query, validate_package, validate_property, verify_live,
)
from scripts.review_candidate_application_urls import review_candidate_policies
from scripts.verify_candidate_qdrant import verify_payloads
from scripts.verify_candidate_sources import verify_sources

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data/releases/v1.4.0-candidate"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ApplicationPeriodTests(unittest.TestCase):
    def test_youth_application_period_uses_only_source_application_field(self):
        codes = load_code_definitions()
        for source, expected in (("", "미확인"), ("20261101 ~ 20261110", "20261101 ~ 20261110"),
                                 ("상시신청", "상시신청")):
            with self.subTest(source=source):
                item = {"plcyNo": "SAMPLE_ONLY", "aplyYmd": source,
                        "bizPrdBgngYmd": "20260101", "bizPrdEndYmd": "20261231"}
                result = normalize_youthcenter_item(item, codes)
                self.assertEqual(result["application_period"], expected)
                self.assertIn(f"- 신청기간: {expected}", result["text"])
                self.assertNotIn("20260101 ~ 20261231", result["text"])

    def test_unverified_period_is_absent_in_candidate_evidence(self):
        from backend.eligibility.checker import _reception_status

        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        documents = {row["document_id"]: row for row in rows(PACKAGE / "neo4j/nodes_document.jsonl")}
        chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        corrections = dc.load_corrections()["unverified_application_periods"]
        self.assertEqual(len(corrections), 12)
        for item in corrections:
            with self.subTest(policy_id=item["policy_id"]):
                policy = policies[item["policy_id"]]
                self.assertEqual(policy["application_period"], "미확인")
                self.assertEqual(_reception_status({"graph": {"policy": policy}}), "unknown")
                document = documents[item["document_id"]]
                excerpt = (ROOT / document["extracted_text_path"]).read_text(encoding="utf-8").strip()
                self.assertEqual(hashlib.sha256(excerpt.encode()).hexdigest(), document["content_hash"])
                evidence = [row["text"] for row in chunks if row["document_id"] == document["document_id"]]
                self.assertTrue(evidence)
                for text in [excerpt, *evidence]:
                    self.assertNotIn(f'- 신청기간: {item["before"]}', text)
        ongoing = policies["POL_STG_MOLIT_002"]
        self.assertTrue(ongoing["application_is_ongoing"])
        self.assertIn("예산 소진", ongoing["application_status_note"])

    def test_collection_default_period_is_unverified_in_candidate(self):
        from backend.eligibility.checker import _reception_status

        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        delta = json.loads((PACKAGE / "release_delta_manifest.json").read_text(encoding="utf-8"))
        replaced = set(delta["data_corrections"]["default_application_periods_replaced"])
        # 복지로 98건 + 온통청년 21건. 이미 공식 일정으로 교정한 정책은 포함되지 않는다(#107).
        self.assertEqual(len(replaced), 119)
        self.assertEqual({pid.split("_")[1] for pid in replaced}, {"BOK", "YC"})
        for policy in policies:
            with self.subTest(policy_id=policy["policy_id"]):
                self.assertNotIn(policy["application_period"], dc.COLLECTION_DEFAULT_PERIODS)
                for default in dc.COLLECTION_DEFAULT_PERIODS:
                    self.assertNotIn(default, policy["application_period"])
                if policy["application_period"] == dc.UNVERIFIED_APPLICATION_PERIOD:
                    # 날짜·상시 값이 없으므로 Checker는 접수 상태를 정하지 않는다.
                    self.assertEqual(_reception_status({"graph": {"policy": policy}}), "unknown")
        self.assertTrue(replaced <= {p["policy_id"] for p in policies
                                     if p["application_period"] == dc.UNVERIFIED_APPLICATION_PERIOD})
        # 새 정책(STG)에 원천 신청기간이 없을 때도 같은 값을 쓴다.
        nts = next(p for p in policies if p["policy_id"] == "POL_STG_NTS_001")
        self.assertEqual(nts["application_period"], dc.UNVERIFIED_APPLICATION_PERIOD)

    def test_default_period_guard_stops_on_conflicting_values(self):
        policies = [{"policy_id": "SAMPLE_ONLY_A", "application_period": "상시/공고참조"},
                    {"policy_id": "SAMPLE_ONLY_B", "application_period": "상시신청", "application_is_ongoing": True}]
        self.assertEqual(dc.replace_default_application_periods(policies), ["SAMPLE_ONLY_A"])
        self.assertEqual(policies[0]["application_period"], "미확인")
        self.assertEqual(policies[1]["application_period"], "상시신청")
        for policy in ({"policy_id": "SAMPLE_ONLY_C", "application_period": "상시/공고참조", "application_start": "2026-01-01"},
                       {"policy_id": "SAMPLE_ONLY_D", "application_period": "2026-01-01 ~ 상시"}):
            with self.subTest(policy_id=policy["policy_id"]), self.assertRaises(ValueError):
                dc.replace_default_application_periods([policy])
        ongoing = [{"policy_id": "SAMPLE_ONLY_E", "application_period": "2026-01-01 ~ 상시", "application_is_ongoing": True}]
        self.assertEqual(dc.replace_default_application_periods(ongoing), [])

    def test_collectors_do_not_write_ongoing_like_default_period(self):
        # 신청기간 칸이 비면 수집 단계부터 "미확인"을 쓴다. 사업기간을 신청기간으로 쓰지 않는다(#107).
        result = normalize_gov24_record("SAMPLE_ONLY", {"서비스명": "가상 서비스(sample_only)", "신청기한": ""})
        self.assertIn("- 신청기한: 미확인", result["text"])
        self.assertNotIn("상시/별도공고", result["text"])

    def test_stored_reception_status_matches_schedule(self):
        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        dc.check_stored_reception_status(policies)
        stored = {p["policy_id"]: p["reception_status"] for p in policies if "reception_status" in p}
        # 신규 정책 12건(STG 9 + 복지로 2026 3)에 저장값이 있다.
        self.assertEqual(len(stored), 12)
        for policy_id in ("POL_STG_MOHW_001", "POL_STG_NTS_001", "POL_STG_MOIS_001", "POL_STG_ICN_001"):
            self.assertEqual(stored[policy_id], dc.STORED_RECEPTION_UNKNOWN)
        with self.assertRaises(ValueError):
            dc.check_stored_reception_status([{"policy_id": "SAMPLE_ONLY", "reception_status": "apply_always"}])

    def test_recruitment_period_and_ongoing_label_follow_official_notice(self):
        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        # 정부24의 2026.06.24~06.26은 최종선발자 서류 접수 기간이다. 모집 신청기간은 세종시 공고 기준(#107).
        intern = policies["POL_GOV_059"]
        self.assertEqual((intern["application_start"], intern["application_end"]), ("2026-06-15", "2026-06-21"))
        self.assertIn("최종선발자 21명의 서류 접수", intern["application_status_note"])
        self.assertTrue(intern["application_period"].startswith("2026.06.15~2026.06.21"))
        # 정정한 날짜의 근거 문서가 후보에 있고, 정책과 SUPPORTED_BY로 이어지며, 청크 본문에 모집 신청기간이 있다.
        notice_id = dc.load_corrections()["application_dates"]["POL_GOV_059"]["evidence_document_id"]
        documents = {row["document_id"]: row for row in rows(PACKAGE / "neo4j/nodes_document.jsonl")}
        self.assertEqual(documents[notice_id]["policy_id"], "POL_GOV_059")
        edges = rows(PACKAGE / "neo4j/relationships.jsonl")
        self.assertIn(("POL_GOV_059", "SUPPORTED_BY", notice_id),
                      {(e["source_id"], e["relation"], e["target_id"]) for e in edges})
        notice_chunks = [c["text"] for c in rows(PACKAGE / "qdrant/payload_chunks.jsonl") if c["document_id"] == notice_id]
        self.assertTrue(any("2026. 6. 15.(월) ~ 2026. 6. 21.(일)" in text for text in notice_chunks))
        # 정부24 원문(선발자 서류 접수 안내)은 지우지 않는다.
        gov24 = [c["text"] for c in rows(PACKAGE / "qdrant/payload_chunks.jsonl") if c["document_id"] == "DOC_GOV_059"]
        self.assertTrue(any("최종선발자(21명)에 한하여 접수" in text for text in gov24))
        # 정부24 '상시신청'인 정책의 저장 접수 상태 값이 상시 표시와 어긋나지 않는다.
        always = policies["POL_STG_DGU_001"]
        self.assertTrue(always["application_is_ongoing"])
        self.assertEqual(always["reception_status"], "apply_always")

    def test_youth_policies_follow_checked_official_notices(self):
        from backend.eligibility.checker import _reception_status

        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        # 온통청년 API 신청기간이 비어 있던 정책 중 공식 공고로 신청기간을 확인한 5건(#107, 2026-11-23)
        checked = {"POL_YC_014": ("2026-08-05", "2026-08-24"), "POL_YC_035": ("2026-03-30", "2026-05-29"),
                   "POL_YC_039": ("2026-11-09", "2026-11-11"), "POL_YC_048": ("2026-01-12", "2026-01-23"),
                   "POL_YC_051": ("2026-03-13", "2026-04-13")}
        for policy_id, period in checked.items():
            with self.subTest(policy_id=policy_id):
                policy = policies[policy_id]
                self.assertEqual((policy["application_start"], policy["application_end"]), period)
                self.assertEqual(policy["application_period"], " ~ ".join(period))
                self.assertIn(_reception_status({"graph": {"policy": policy}}), {"closed", "open", "before"})
        self.assertTrue(policies["POL_YC_029"]["application_is_ongoing"])
        # 자립수당의 상시 근거는 '상시 신청 가능'이 적힌 공식 안내 문서이고, 그 문장이 상담 청크에도 들어 있다.
        corrections = dc.load_corrections()
        self.assertIn("DOC_YC_029_SAHA_GUIDE_2026", corrections["application_ongoing"]["POL_YC_029"])
        saha = [c["text"] for c in rows(PACKAGE / "qdrant/payload_chunks.jsonl")
                if c["document_id"] == "DOC_YC_029_SAHA_GUIDE_2026"]
        self.assertTrue(any("상시 신청 가능" in text and "보호종료 5년 이내" in text for text in saha))
        # 종료일 16시 마감인 정책은 날짜 단위 판정만으로 알 수 없으니 마감 시각을 메모로 남긴다.
        for policy_id in ("POL_YC_035", "POL_YC_051"):
            with self.subTest(policy_id=policy_id):
                self.assertIn("16", policies[policy_id]["application_status_note"])
                self.assertIn(policy_id, corrections["application_notes_with_dates"])
        # 출처 연결이 확정되지 않은 정책은 날짜를 넣지 않는다.
        for policy_id in ("POL_YC_012", "POL_YC_031"):
            with self.subTest(policy_id=policy_id):
                self.assertNotIn("application_end", policies[policy_id])
        # 2026년 회차를 확인했지만 정책 근거가 2025년인 정책은 날짜를 두고 메모로 남긴다.
        for policy_id in ("POL_GOV_033", "POL_GOV_061"):
            with self.subTest(policy_id=policy_id):
                self.assertIn("2026", policies[policy_id]["application_status_note"])

    def test_fund_loans_are_unverified_with_personal_deadline_note(self):
        from backend.eligibility.checker import _reception_status

        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        # 주택도시기금 상품 안내·자주하는질문에 연중 신청 근거가 없어 상시로 두지 않는다(2026-11-24, #107).
        # 개인별 신청 기한은 상시 여부와 별도로 메모에 남긴다.
        for policy_id in ("POL_003", "POL_004", "POL_006", "POL_007"):
            with self.subTest(policy_id=policy_id):
                loan = policies[policy_id]
                self.assertEqual(loan["application_period"], dc.UNVERIFIED_APPLICATION_PERIOD)
                self.assertNotIn("application_is_ongoing", loan)
                self.assertFalse(loan.get("application_end"))
                self.assertIn("개인별 기한", loan["application_status_note"])
                self.assertEqual(_reception_status({"graph": {"policy": loan}}), "unknown")


BOKJIRO_2026 = ROOT / "data/staging/bokjiro_2026"


class Bokjiro2026PolicyTests(unittest.TestCase):
    """복지로 2026 검수 결과에서 고른 신규 정책 3건(조장 결정)과 인계 자료(handoff)를 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.selection = json.loads((BOKJIRO_2026 / "selection.json").read_text(encoding="utf-8"))["policies"]
        cls.targets = {row["target_id"]: row for row in rows(BOKJIRO_2026 / "handoff/targets.jsonl")}
        cls.review_rows = rows(BOKJIRO_2026 / "handoff/review_rows.jsonl")
        cls.policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        cls.documents = {row["document_id"]: row for row in rows(PACKAGE / "neo4j/nodes_document.jsonl")}
        cls.chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        cls.relationships = rows(PACKAGE / "neo4j/relationships.jsonl")

    def test_handoff_counts_match_manifest(self):
        manifest = json.loads((BOKJIRO_2026 / "handoff/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"]["targets"], len(self.targets))
        self.assertEqual(manifest["counts"]["rows"], len(self.review_rows))
        self.assertEqual(manifest["counts"]["targets_all_rows_ok"],
                         sum(t["review_status"] == "OK" for t in self.targets.values()))
        for target in self.targets.values():
            with self.subTest(target=target["target_id"]):
                text = (ROOT / target["source_text_path"]).read_text(encoding="utf-8").strip()
                self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), target["source_text_sha256"])

    def test_selected_policies_are_fully_reviewed_and_not_duplicates(self):
        self.assertEqual(len(self.selection), 3)
        existing_official_ids = [p["official_id"] for p in self.policies.values()]
        self.assertEqual(len(existing_official_ids), len(set(existing_official_ids)))
        for item in self.selection:
            target = self.targets[item["target_id"]]
            with self.subTest(policy_id=item["policy_id"]):
                self.assertEqual(target["review_status"], "OK")
                target_rows = [r for r in self.review_rows if r["target_id"] == item["target_id"]]
                self.assertTrue(target_rows)
                self.assertTrue(all(r["review_status"] == "OK" for r in target_rows))
                self.assertEqual(target["name_overlap_policy_ids"], [])

    def test_selected_policies_are_in_candidate_without_guessed_values(self):
        from backend.eligibility.checker import _reception_status

        groups = {row["group_id"]: row for row in rows(PACKAGE / "neo4j/nodes_condition_group.jsonl")}
        for item in self.selection:
            policy = self.policies[item["policy_id"]]
            target = self.targets[item["target_id"]]
            document_id = item["policy_id"].replace("POL_", "DOC_")
            with self.subTest(policy_id=item["policy_id"]):
                self.assertEqual(policy["official_id"], target["servId"])
                self.assertEqual(policy["policy_name"], target["policy_name"])
                for key in ("eligibility", "benefit", "application_method", "agency", "source_url"):
                    self.assertTrue(policy[key], key)
                # 복지로 XML에 신청 주소·신청기간이 없다. 지어내지 않는다.
                self.assertIsNone(policy["official_apply_url"])
                self.assertEqual(policy["application_period"], dc.UNVERIFIED_APPLICATION_PERIOD)
                self.assertNotIn("application_is_ongoing", policy)
                self.assertEqual(_reception_status({"graph": {"policy": policy}}), "unknown")
                self.assertEqual(groups[f"GRP_{item['policy_id']}_ROOT"]["logic"], "UNKNOWN")
                document = self.documents[document_id]
                self.assertEqual(document["content_hash"], target["source_text_sha256"])
                self.assertEqual(document["extracted_text_path"], target["source_text_path"])
                self.assertIn(("SUPPORTED_BY", document_id),
                              {(r["relation"], r["target_id"]) for r in self.relationships
                               if r["source_id"] == item["policy_id"]})
                policy_chunks = [c for c in self.chunks if c["policy_id"] == item["policy_id"]]
                self.assertTrue(policy_chunks)
                body = (ROOT / target["source_text_path"]).read_text(encoding="utf-8")
                for chunk in policy_chunks:
                    self.assertIn(chunk["text"], body)


class ApplicationUrlTests(unittest.TestCase):
    def test_missing_invalid_and_guidance_addresses_are_not_apply_links(self):
        for value in [None, "", "www.example.org", "javascript:alert(1)", "https://user@example.org", "https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?id=1"]:
            with self.subTest(value=value):
                self.assertIsNone(application_url_or_none(value))
        self.assertIsNone(application_url_or_none("https://example.org/notice", "https://example.org/notice"))
        self.assertEqual(first_application_url(["invalid", "https://example.org/apply"], "https://example.org/source"), "https://example.org/apply")

    def test_gov24_does_not_replace_missing_application_url(self):
        item = {"서비스명": "가상 테스트 정책", "상세조회URL": "https://example.org/source"}
        result = normalize_gov24_record("SAMPLE_ONLY", item)
        self.assertIsNone(result["official_apply_url"])
        self.assertEqual(result["source_url"], item["상세조회URL"])

    def test_youth_reference_url_is_not_promoted_to_application_url(self):
        item = {"plcyNo": "SAMPLE_ONLY", "plcyNm": "가상 테스트 정책", "aplyUrlAddr": "", "refUrlAddr1": "https://example.org/reference"}
        result = normalize_youthcenter_item(item, load_code_definitions())
        self.assertIsNone(result["official_apply_url"])
        self.assertNotIn("[신청 URL] https://example.org/reference", result["text"])

    def test_bokjiro_textual_procedure_is_not_an_application_link(self):
        path = ROOT / "tests/fixtures/bokjiro_no_apply_sample_only.xml"
        result = parse_bokjiro_detail_xml(path)
        self.assertIsNone(result["application_url"])


class CandidateIntegrityTests(unittest.TestCase):
    def test_chunk_texts_and_document_hashes_match_saved_bodies(self):
        report = verify_sources()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["body_hashes_checked"], 273)
        self.assertEqual(report["chunks_checked_against_body"], 672)

    def test_package_is_valid_for_both_database_payloads(self):
        _, report, _, chunks = validate_package(PACKAGE)
        self.assertEqual(report["counts"]["total_graph_nodes"], 1147)
        self.assertEqual(report["counts"]["total_graph_edges"], 2702)
        self.assertEqual(len(chunks), 672)

    def test_only_declared_baseline_policy_property_changed(self):
        delta = json.loads((PACKAGE / "release_delta_manifest.json").read_text(encoding="utf-8"))
        declared = {row["policy_id"]: row for row in delta["updated_policy_properties"]}
        actual_changes = set()
        corrections = dc.load_corrections()
        removed_groups = dc.removed_group_ids(corrections)
        period_edits = corrections["unverified_application_periods"]
        periods_by_policy = {item["policy_id"]: item for item in period_edits}
        default_periods = set(delta["data_corrections"]["default_application_periods_replaced"])
        # 행 교정(document_line_rewrites, #107)도 검색용 본문을 따로 쓰는 문서다.
        changed_documents = {item["document_id"]: item
                             for item in [*corrections["outdated_document_lines"], *period_edits,
                                          *corrections["document_line_rewrites"]]}
        for _, (name, key) in NODE_CONFIG.items():
            old = rows(ROOT / "data/handoff/neo4j" / f"nodes_{name}.jsonl")
            if name == "condition_group":
                # 동결 v1.3.2에는 신규 9건(POL_STG_*) 그룹이 없으므로, 그 그룹을 대상으로 한 논리 교정은 빼고 적용한다
                old_ids = {row[key] for row in old}
                old = dc.apply_group_corrections(old, {**corrections, "empty_group_logic_corrections": [
                    item for item in corrections["empty_group_logic_corrections"] if item["group_id"] in old_ids]})
            if name == "policy":
                # 원문 대조 교정(설명 문구·지역)은 선언 파일을 그대로 적용한 값과 같아야 한다
                corrected_ids = {item["policy_id"] for group in ("text_corrections", "region_corrections", "life_stage_corrections")
                                 for item in corrections[group]}
                # 동결 v1.3.2에 없는 신규 정책(POL_STG_*)의 설명 문구 교정(예: 모두의카드 정책명)은 빼고 적용한다
                old_policy_ids = {row[key] for row in old}
                dc.apply_policy_corrections([row for row in old if row["policy_id"] in corrected_ids],
                                            {**corrections, "category_corrections": [], "text_corrections": [
                                                item for item in corrections["text_corrections"]
                                                if item["policy_id"] in old_policy_ids]})
            new = {row[key]: row for row in rows(PACKAGE / "neo4j" / f"nodes_{name}.jsonl")}
            for original in old:
                if name == "condition_group" and original[key] in removed_groups:
                    self.assertNotIn(original[key], new)
                    continue
                current = dict(new[original[key]])
                if name == "document" and original[key] in changed_documents:
                    self.assertEqual(current.pop("extracted_text_path"),
                                     f"data/releases/v1.4.0-candidate/source_text/{original[key]}.txt")
                    self.assertNotEqual(current["content_hash"], original["content_hash"])
                    current["content_hash"] = original["content_hash"]
                if name == "policy":
                    for field in dc.SOURCE_REVIEW_FIELDS:
                        current.pop(field, None)
                    if original[key] in periods_by_policy:
                        self.assertEqual(original["application_period"], periods_by_policy[original[key]]["before"])
                        self.assertEqual(current["application_period"], "미확인")
                        current["application_period"] = original["application_period"]
                    elif original[key] in default_periods:
                        # 수집 기본 문구만 "미확인"으로 바뀌고 나머지 속성은 동결본과 같다(#107).
                        self.assertIn(original["application_period"], dc.COLLECTION_DEFAULT_PERIODS)
                        self.assertEqual(current["application_period"], dc.UNVERIFIED_APPLICATION_PERIOD)
                        current["application_period"] = original["application_period"]
                if name == "policy" and original != current:
                    change = declared[original[key]]
                    self.assertEqual(change["before"], original["official_apply_url"])
                    self.assertEqual(change["after"], current["official_apply_url"])
                    actual_changes.add(original[key])
                    current["official_apply_url"] = original["official_apply_url"]
                self.assertEqual(original, current)
        self.assertEqual(actual_changes, set(declared))
        removed = {(r["source_id"], r["relation"], r["target_id"]) for r in delta["data_corrections"]["removed_relationships"]}
        region_policies = {item["policy_id"]: item for item in corrections["region_corrections"]}
        new_edges = {json.dumps(row, sort_keys=True) for row in rows(PACKAGE / "neo4j/relationships.jsonl")}
        for row in rows(ROOT / "data/handoff/neo4j/relationships.jsonl"):
            if (row["source_id"], row["relation"], row["target_id"]) in removed:
                self.assertNotIn(json.dumps(row, sort_keys=True), new_edges)
                continue
            if row["relation"] == "AVAILABLE_IN" and row["source_id"] in region_policies:
                item = region_policies[row["source_id"]]
                row = {**row, "target_id": item["after_region_id"], "target_name": item["after"]}
            self.assertIn(json.dumps(row, sort_keys=True), new_edges)
        life_stage_policies = {item["policy_id"]: item for item in corrections["life_stage_corrections"]}
        new_chunks = {json.dumps(row, sort_keys=True) for row in rows(PACKAGE / "qdrant/payload_chunks.jsonl")}
        for row in rows(ROOT / "data/handoff/qdrant/payload_chunks.jsonl"):
            if row["policy_id"] in region_policies:
                row = {**row, "region": region_policies[row["policy_id"]]["after"]}
            if row["policy_id"] in life_stage_policies:
                row = {**row, "life_stages": life_stage_policies[row["policy_id"]]["after"]}
            correction = changed_documents.get(row["document_id"])
            if correction and "replacement" in correction:
                # 행 교정: 선언된 행만 바뀌고 나머지 본문은 동결 인계본과 같아야 한다.
                if correction["line"] in row["text"].splitlines():
                    row = {**row, "text": dc.rewrite_line(row["text"], correction)}
            elif correction:
                line = correction["text"] if "text" in correction else f'- 신청기간: {correction["before"]}'
                if line in row["text"]:
                    row = {**row, "text": dc.omit_outdated_line(row["text"], line)}
            self.assertIn(json.dumps(row, sort_keys=True), new_chunks)

    def test_after_school_birth_dates_use_the_current_guide(self):
        guide_id = "DOC_BOK_031_MOE_2026"
        condition_id = "COND_CHILD_BIRTH_2013_2019"
        chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        guide = next(row for row in chunks if row["document_id"] == guide_id)
        self.assertIn("ʼ19. 01. 01. ~ ʼ13. 12. 31.", guide["text"])
        self.assertTrue(all("'18. 01. 01. ~ '12. 12. 31." not in row["text"] for row in chunks))
        links = [row for row in rows(PACKAGE / "neo4j/relationships.jsonl") if row["target_id"] == condition_id]
        self.assertEqual({row["relation"] for row in links}, {"CONTAINS_CONDITION", "REQUIRES_CONDITION"})
        self.assertEqual({row["evidence_document_id"] for row in links}, {guide_id})

    def test_merged_gwangju_jeonnam_code_is_not_nationwide(self):
        self.assertEqual(map_zip_codes_to_regions("12870"), ["광주광역시", "전라남도"])

    def test_data_corrections_reach_the_package(self):
        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        edges = {(r["source_id"], r["relation"], r["target_id"]) for r in rows(PACKAGE / "neo4j/relationships.jsonl")}
        self.assertNotIn(("POL_GOV_023", "REQUIRES_CONDITION", "COND_HH_무주택세대"), edges)
        self.assertNotIn(("GRP_POL_GOV_023_ROOT", "CONTAINS_GROUP", "GRP_POL_GOV_023_OR_HH"), edges)
        self.assertNotIn(("POL_GOV_008", "REQUIRES_CONDITION", "COND_AGE_60_120"), edges)
        # 원문에 나이가 분명한 3건은 조건을 지우지 않고 원문 값으로 교체한다
        for policy_id, before, after in [("POL_GOV_011", "COND_AGE_13_18", "COND_AGE_12_24"),
                                         ("POL_GOV_016", "COND_AGE_18_65", "COND_AGE_19_120"),
                                         ("POL_GOV_023", "COND_AGE_0_120", "COND_AGE_6_64")]:
            self.assertNotIn((policy_id, "REQUIRES_CONDITION", before), edges)
            self.assertNotIn((f"GRP_{policy_id}_ROOT", "CONTAINS_CONDITION", before), edges)
            self.assertIn((policy_id, "REQUIRES_CONDITION", after), edges)
            self.assertIn((f"GRP_{policy_id}_ROOT", "CONTAINS_CONDITION", after), edges)
        conditions = {row["condition_id"]: row for row in rows(PACKAGE / "neo4j/nodes_eligibility_condition.jsonl")}
        self.assertEqual(conditions["COND_AGE_12_24"]["value"], "12~24")
        self.assertEqual(conditions["COND_AGE_6_64"]["value"], "6~64")
        # 원문에 무주택 요건이 있는 정책과 API가 무주택만 지정한 정책은 유지한다
        self.assertIn(("POL_GOV_025", "REQUIRES_CONDITION", "COND_HH_무주택세대"), edges)
        self.assertIn(("POL_GOV_001", "REQUIRES_CONDITION", "COND_HH_무주택세대"), edges)
        self.assertIn(("POL_GOV_028", "AVAILABLE_IN", "REGION_GWANGJU"), edges)
        self.assertIn(("POL_YC_002", "AVAILABLE_IN", "REGION_JEONNAM"), edges)
        self.assertIn("총자산 3억 4,500만원", policies["POL_001"]["eligibility"])
        self.assertIn("3.45억원", policies["POL_003"]["eligibility"])
        self.assertIn("일반형 연 1.8%", policies["POL_006"]["benefit"])

    def test_taxonomy_uses_standard_labels_everywhere(self):
        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        edges = rows(PACKAGE / "neo4j/relationships.jsonl")
        names = lambda relation, policy_id: sorted(e["target_name"] for e in edges  # noqa: E731
                                                   if e["source_id"] == policy_id and e["relation"] == relation)
        standard_stages = {row["name"] for row in rows(PACKAGE / "neo4j/nodes_lifestage.jsonl")} | {"unknown"}
        by_id = {row["policy_id"]: row for row in policies}
        for policy in policies:
            self.assertEqual(names("HAS_CATEGORY", policy["policy_id"]), [policy["category"]], policy["policy_id"])
            self.assertEqual(names("TARGETS", policy["policy_id"]), sorted(policy["target_groups"]), policy["policy_id"])
            if policy["life_stages"] != ["unknown"]:
                self.assertEqual(names("APPLIES_TO_LIFESTAGE", policy["policy_id"]), sorted(policy["life_stages"]), policy["policy_id"])
        for chunk in chunks:
            self.assertEqual(chunk["category"], by_id[chunk["policy_id"]]["category"], chunk["chunk_id"])
            self.assertLessEqual(set(chunk["life_stages"]), standard_stages, chunk["chunk_id"])
            self.assertEqual(set(chunk["life_stages"]), set(by_id[chunk["policy_id"]]["life_stages"]), chunk["chunk_id"])

    def test_no_or_group_selects_every_income_band(self):
        bands = {row["condition_id"] for row in rows(PACKAGE / "neo4j/nodes_eligibility_condition.jsonl") if row["type"] == "INCOME"}
        members = {}
        for edge in rows(PACKAGE / "neo4j/relationships.jsonl"):
            if edge["relation"] == "CONTAINS_CONDITION" and edge["target_id"] in bands:
                members.setdefault(edge["source_id"], set()).add(edge["target_id"])
        for group in rows(PACKAGE / "neo4j/nodes_condition_group.jsonl"):
            if group["logic"] == "OR":
                self.assertNotEqual(members.get(group["group_id"], set()), bands, group["group_id"])

    def test_income_corrections_match_declared_actions(self):
        corrections = dc.load_corrections()["income_condition_corrections"]
        groups = {row["group_id"]: row for row in rows(PACKAGE / "neo4j/nodes_condition_group.jsonl")}
        edges = {(r["source_id"], r["relation"], r["target_id"]) for r in rows(PACKAGE / "neo4j/relationships.jsonl")}
        self.assertEqual(len(corrections), 51)
        for item in corrections:
            with self.subTest(policy=item["policy_id"]):
                if item["action"] == "remove":
                    self.assertNotIn(item["group_id"], groups)
                    self.assertFalse({(item["policy_id"], "REQUIRES_CONDITION", band) for band in item["before"]} & edges)
                elif item["action"] == "remove_condition":
                    self.assertFalse({(item["group_id"], "CONTAINS_CONDITION", band) for band in item["before"]} & edges)
                    self.assertFalse({(item["policy_id"], "REQUIRES_CONDITION", band) for band in item["before"]} & edges)
                else:
                    self.assertEqual(groups[item["group_id"]]["logic"], "UNKNOWN")

    def test_source_review_is_on_every_policy_and_consistent(self):
        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        review = dc.load_corrections()["source_review"]
        allowed = {"not_specified", "specified", "needs_review", "not_reviewed"}
        for policy_id, policy in policies.items():
            for attribute in dc.SOURCE_REVIEW_ATTRIBUTES:
                with self.subTest(policy=policy_id, attribute=attribute):
                    status = policy[f"{attribute}_source_review"]
                    self.assertIn(status, allowed)
                    self.assertEqual(status, review[attribute].get(policy_id, {}).get("status", "not_reviewed"))
                    if status == "needs_review":
                        self.assertIn(policy[f"{attribute}_followup"], {"ask_user", "check_notice"})
                        self.assertTrue(policy[f"{attribute}_check_point"])
                    if status != "not_reviewed":
                        self.assertTrue(policy[f"{attribute}_source_note"])
        for item in dc.load_corrections()["income_condition_corrections"]:
            if item["action"] == "remove":
                self.assertEqual(policies[item["policy_id"]]["income_source_review"], "not_specified", item["policy_id"])
            else:
                self.assertEqual(policies[item["policy_id"]]["income_source_review"], "needs_review", item["policy_id"])

    def test_application_is_ongoing_only_when_source_confirms(self):
        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        ongoing = [row for row in policies if "application_is_ongoing" in row]
        # 건수는 교정 파일 기준으로 비교해, 교정 파일이 바뀌어도 이 테스트를 고치지 않게 한다.
        expected = set(dc.load_corrections()["application_ongoing"])
        self.assertEqual({row["policy_id"] for row in ongoing}, expected)
        for row in ongoing:
            self.assertIs(row["application_is_ongoing"], True, row["policy_id"])
            self.assertIn(row["application_period"], ("상시신청", "상시"), row["policy_id"])
            self.assertNotIn("application_end", row, row["policy_id"])
        # 주택도시기금 대출 4건은 연중 신청 근거가 없어 2026-11-24에 상시에서 뺐다(#107).
        excluded = {"POL_002", "POL_GOV_003", "POL_003", "POL_004", "POL_006", "POL_007"}
        self.assertFalse(excluded & {row["policy_id"] for row in ongoing})

    def test_application_ongoing_rejects_unknown_policy_id(self):
        corrections = {
            "source_review": {"income": {}, "household": {}},
            "application_dates": {},
            "application_status_notes": {},
            "application_notes_with_dates": [],
            "application_ongoing": ["POL_001", "POL_TYPO"],
        }
        with self.assertRaisesRegex(AssertionError, "POL_TYPO"):
            dc.apply_source_review([{"policy_id": "POL_001"}], corrections)

    def test_group_without_children_is_unknown(self):
        parents = {edge["source_id"] for edge in rows(PACKAGE / "neo4j/relationships.jsonl")
                   if edge["relation"] in ("CONTAINS_CONDITION", "CONTAINS_GROUP")}
        for group in rows(PACKAGE / "neo4j/nodes_condition_group.jsonl"):
            if group["group_id"] not in parents:
                self.assertEqual(group["logic"], "UNKNOWN", group["group_id"])

    def test_application_dates_are_iso_and_period_status_is_gone(self):
        policies = {row["policy_id"]: row for row in rows(PACKAGE / "neo4j/nodes_policy.jsonl")}
        for policy_id, policy in policies.items():
            self.assertNotIn("application_status_review", policy)
            for field in ("application_start", "application_end"):
                if field in policy:
                    self.assertRegex(policy[field], r"^\d{4}-\d{2}-\d{2}$", policy_id)
            if "application_start" in policy and "application_end" in policy:
                self.assertLessEqual(policy["application_start"], policy["application_end"], policy_id)
        self.assertEqual((policies["POL_001"]["application_start"], policies["POL_001"]["application_end"]), ("2026-04-15", "2026-04-17"))
        self.assertEqual((policies["POL_009"]["application_start"], policies["POL_009"]["application_end"]), ("2024-02-26", "2025-02-25"))
        self.assertIn("2026-03-30", policies["POL_009"]["application_status_note"])
        self.assertEqual(policies["POL_002"]["application_start"], "2026-10-19")
        self.assertNotIn("application_end", policies["POL_002"])
        self.assertIn("종료일은 미기재", policies["POL_002"]["application_status_note"])
        for policy_id in ("POL_GOV_006", "POL_GOV_020"):
            self.assertNotIn("application_start", policies[policy_id])
            self.assertNotIn("application_end", policies[policy_id])
            self.assertTrue(policies[policy_id]["application_status_note"])

    def test_age_corrections_from_official_recheck(self):
        corrections = dc.load_corrections()
        edges = {(r["source_id"], r["relation"], r["target_id"]) for r in rows(PACKAGE / "neo4j/relationships.jsonl")}
        for item in corrections["age_condition_removals"]:
            with self.subTest(policy=item["policy_id"]):
                self.assertNotIn((item["policy_id"], "REQUIRES_CONDITION", item["condition_id"]), edges)
                self.assertNotIn((f"GRP_{item['policy_id']}_ROOT", "CONTAINS_CONDITION", item["condition_id"]), edges)
        self.assertIn(("POL_GOV_002", "REQUIRES_CONDITION", "COND_AGE_0_65"), edges)
        self.assertNotIn(("POL_GOV_050", "REQUIRES_CONDITION", "COND_AGE_0_1"), edges)
        # #39: 전주시 조례 '24개월 이하'. 다른 정책이 쓰는 24개월 미만 조건은 그대로 둔다.
        self.assertIn(("POL_GOV_050", "REQUIRES_CONDITION", "COND_CHILD_AGE_LE_24M"), edges)
        self.assertNotIn(("POL_GOV_050", "REQUIRES_CONDITION", "COND_CHILD_AGE_UNDER_24M"), edges)
        self.assertIn(("POL_BOK_005", "REQUIRES_CONDITION", "COND_CHILD_AGE_UNDER_24M"), edges)
        conditions = {row["condition_id"]: row for row in rows(PACKAGE / "neo4j/nodes_eligibility_condition.jsonl")}
        self.assertEqual(conditions["COND_CHILD_AGE_LE_24M"]["type"], "SPECIAL_TARGET")
        self.assertEqual((conditions["COND_CHILD_AGE_LE_24M"]["operator"], conditions["COND_CHILD_AGE_LE_24M"]["value"],
                          conditions["COND_CHILD_AGE_LE_24M"]["unit"]), ("LTE", "24", "개월"))
        self.assertEqual(conditions["COND_CHILD_AGE_UNDER_24M"]["operator"], "LT")

    def test_detail_targets_stay_and_review_corrections_apply(self):
        chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        detail = {value for chunk in chunks for value in chunk["target_groups"]}
        self.assertLessEqual({"예비신혼부부", "신생아가구", "한부모가족"}, detail)
        edges = {(r["source_id"], r["relation"], r["target_id"]) for r in rows(PACKAGE / "neo4j/relationships.jsonl")}
        self.assertNotIn(("POL_001", "APPLIES_TO_LIFESTAGE", "LIFESTAGE_노년"), edges)
        self.assertEqual({target for source, relation, target in edges
                          if source == "POL_GOV_050" and relation == "APPLIES_TO_LIFESTAGE"}, {"LIFESTAGE_영유아"})
        self.assertIn(("POL_GOV_017", "APPLIES_TO_LIFESTAGE", "LIFESTAGE_중장년"), edges)
        members = {target for source, relation, target in edges
                   if source == "GRP_POL_005_OR_EXCL" and relation == "CONTAINS_CONDITION"}
        # 3값 논리에서 NOT은 단일 하위만 받는다
        self.assertEqual({target for source, relation, target in edges if source == "GRP_POL_005_NOT_EXCL"}, {"GRP_POL_005_OR_EXCL"})
        self.assertEqual(len(members), 13)
        self.assertIn("COND_AGE_75_120", members)
        # 제외 조건을 필수 조건으로 연결하면 뜻이 반대가 된다
        self.assertFalse(members & {target for source, relation, target in edges
                                    if source == "POL_005" and relation == "REQUIRES_CONDITION"})

    def test_frozen_manifests_match_actual_files(self):
        for directory in [ROOT / "data/handoff", ROOT / "data/releases/v1"]:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            for name, metadata in manifest["files"].items():
                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), metadata["sha256"])

    def test_invalid_graph_property_is_rejected(self):
        for value in [{"nested": "object"}, ["a", 1], [None], [[1]], float("nan")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_property(value)
        validate_property(None)  # null 허용 여부는 속성 계약에서 별도로 판단한다.

    def test_relationship_query_cannot_match_document_as_policy(self):
        query = relationship_query("SUPPORTED_BY")
        self.assertIn("src:Policy {policy_id: row.source_id}", query)
        self.assertIn("tgt:Document {document_id: row.target_id}", query)
        self.assertNotIn(" OR ", query)
        with self.assertRaises(KeyError):
            relationship_query("UNTRUSTED_RELATION")

    def test_manual_apply_url_flag_follows_verified_stage(self):
        # 신청 버튼까지만 본 주소는 남기되 '실제 신청 화면 확인'으로 올리지 않는다(가상 데이터).
        def run(after, stage):
            policies = [{"policy_id": "POL_S1", "official_apply_url": "https://example.test/"}]
            reviews = [{"policy_id": "POL_S1", "official_apply_url": "https://example.test/"}]
            item = {"policy_id": "POL_S1", "before": "https://example.test/", "after": after, "reason": "sample_only",
                    "evidence": "sample_only", "checked_at": "2026-11-20", "verified_stage": stage}
            dc.apply_application_url_corrections(policies, reviews, [], {"application_url_corrections": [item]})
            return policies[0]["official_apply_url"], reviews[0]["live_application_page_verified"]

        self.assertEqual(run("https://example.test/apply", "identity_check"), ("https://example.test/apply", True))
        self.assertEqual(run("https://example.test/info", "apply_button_out_of_period"), ("https://example.test/info", False))
        self.assertEqual(run(None, None), (None, False))
        with self.assertRaises(AssertionError):
            run("https://example.test/info", None)
        with self.assertRaises(AssertionError):
            run(None, "booking_screen")

    def test_review_is_repeatable_from_captured_evidence(self):
        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        old = rows(ROOT / "data/handoff/neo4j/nodes_policy.jsonl")
        copied = [dict(row) for row in policies]
        reviews, _ = review_candidate_policies(copied, old)
        # 원천 신청 필드 판정 뒤, 화면을 직접 확인한 교정(#18)까지 적용해야 후보와 같다.
        changes = dc.apply_application_url_corrections(copied, reviews, old, dc.load_corrections())
        self.assertEqual(copied, policies)
        self.assertEqual(len(changes), 235)


class DatabaseSafetyTests(unittest.TestCase):
    def test_nonempty_neo4j_is_rejected_before_any_write(self):
        class Client:
            session = Mock()
            requests = []

            def request(self, method, suffix="", statements=None):
                self.requests.append((method, suffix))
                return {"commit": "http://localhost/db/neo4j/tx/123/commit"}

            def query(self, suffix, statement, parameters=None):
                self.requests.append(("QUERY", statement))
                return [[1]]

        client = Client()
        with self.assertRaisesRegex(ValueError, "기존 데이터"):
            verify_live(client, {}, [])
        self.assertFalse(any("UNWIND" in value for _, value in client.requests))
        self.assertIn(("DELETE", "/123"), client.requests)  # 노드 삭제가 아니라 트랜잭션 취소다.

    def test_existing_qdrant_collection_is_never_changed_or_deleted(self):
        class Client:
            session = Mock()
            methods = []

            def request(self, method, **kwargs):
                self.methods.append(method)
                return {"result": {"points_count": 1}}

        client = Client()
        with self.assertRaisesRegex(ValueError, "기존 컬렉션"):
            verify_payloads(client, {})
        self.assertEqual(client.methods, ["GET"])


if __name__ == "__main__":
    unittest.main()


class DocumentLineRewriteTests(unittest.TestCase):
    """#107(PR #113 리뷰): 정부24 '신청기한' 행의 칸 이름 교정과 지난 예고 제거."""

    ITEM = {"document_id": "SAMPLE_ONLY", "line": "- 신청기한: 2025.10.24~2025.11.7. / ※ 2026년 일정 10월 초 게시 예정",
            "replacement": "- 신청기한(2025년 회차): 2025.10.24~2025.11.7.",
            "kept_text": "2025.10.24~2025.11.7.", "dropped_text": "※ 2026년 일정 10월 초 게시 예정"}

    def test_rewrite_keeps_original_value_and_drops_only_declared_text(self):
        text = "[신청기간 및 신청방법]\r\n" + self.ITEM["line"] + "\r\n- 신청방법: 방문"
        result = dc.rewrite_line(text, self.ITEM)
        self.assertEqual(result, "[신청기간 및 신청방법]\r\n" + self.ITEM["replacement"] + "\r\n- 신청방법: 방문")

    def test_rewrite_rejects_changed_value_or_missing_line(self):
        changed = {**self.ITEM, "replacement": "- 신청기한(2025년 회차): 2025.10.24~2025.11.8."}
        with self.assertRaisesRegex(ValueError, "원문 값"):
            dc.rewrite_line(self.ITEM["line"], changed)
        kept_dropped = {**self.ITEM, "replacement": self.ITEM["line"]}
        with self.assertRaisesRegex(ValueError, "뺄 문구"):
            dc.rewrite_line(self.ITEM["line"], kept_dropped)
        with self.assertRaisesRegex(ValueError, "한 번만"):
            dc.rewrite_line("다른 행", self.ITEM)

    def test_candidate_chunks_use_rewritten_lines(self):
        chunks = rows(PACKAGE / "qdrant/payload_chunks.jsonl")
        for item in dc.load_corrections()["document_line_rewrites"]:
            with self.subTest(document_id=item["document_id"]):
                texts = [c["text"] for c in chunks if c["document_id"] == item["document_id"]]
                self.assertTrue(any(item["replacement"] in text.splitlines() for text in texts))
                self.assertFalse(any(item["line"] in text.splitlines() for text in texts))
                if item.get("dropped_text"):
                    self.assertFalse(any(item["dropped_text"] in text for text in texts))
