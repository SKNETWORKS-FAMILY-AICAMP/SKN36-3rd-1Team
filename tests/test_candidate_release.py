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
        self.assertEqual(report["body_hashes_checked"], 261)
        self.assertEqual(report["chunks_checked_against_body"], 599)

    def test_package_is_valid_for_both_database_payloads(self):
        _, report, _, chunks = validate_package(PACKAGE)
        self.assertEqual(report["counts"]["total_graph_nodes"], 1005)
        self.assertEqual(report["counts"]["total_graph_edges"], 2442)
        self.assertEqual(len(chunks), 599)

    def test_only_declared_baseline_policy_property_changed(self):
        delta = json.loads((PACKAGE / "release_delta_manifest.json").read_text(encoding="utf-8"))
        declared = {row["policy_id"]: row for row in delta["updated_policy_properties"]}
        actual_changes = set()
        corrections = dc.load_corrections()
        removed_groups = dc.removed_group_ids(corrections)
        for _, (name, key) in NODE_CONFIG.items():
            old = rows(ROOT / "data/handoff/neo4j" / f"nodes_{name}.jsonl")
            if name == "condition_group":
                old = dc.apply_group_corrections(old, corrections)
            if name == "policy":
                # 원문 대조 교정(설명 문구·지역)은 선언 파일을 그대로 적용한 값과 같아야 한다
                corrected_ids = {item["policy_id"] for group in ("text_corrections", "region_corrections", "life_stage_corrections")
                                 for item in corrections[group]}
                dc.apply_policy_corrections([row for row in old if row["policy_id"] in corrected_ids],
                                            {**corrections, "category_corrections": []})
            new = {row[key]: row for row in rows(PACKAGE / "neo4j" / f"nodes_{name}.jsonl")}
            for original in old:
                if name == "condition_group" and original[key] in removed_groups:
                    self.assertNotIn(original[key], new)
                    continue
                current = dict(new[original[key]])
                if name == "policy":
                    for field in dc.SOURCE_REVIEW_FIELDS:
                        current.pop(field, None)
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
            self.assertIn(json.dumps(row, sort_keys=True), new_chunks)

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
        self.assertEqual(policies["POL_009"]["application_start"], "2026-03-30")
        self.assertNotIn("application_end", policies["POL_002"])
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
        self.assertIn(("POL_GOV_050", "REQUIRES_CONDITION", "COND_CHILD_AGE_UNDER_24M"), edges)
        conditions = {row["condition_id"]: row for row in rows(PACKAGE / "neo4j/nodes_eligibility_condition.jsonl")}
        self.assertEqual(conditions["COND_CHILD_AGE_UNDER_24M"]["type"], "SPECIAL_TARGET")

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

    def test_review_is_repeatable_from_captured_evidence(self):
        policies = rows(PACKAGE / "neo4j/nodes_policy.jsonl")
        old = rows(ROOT / "data/handoff/neo4j/nodes_policy.jsonl")
        copied = [dict(row) for row in policies]
        _, changes = review_candidate_policies(copied, old)
        self.assertEqual(copied, policies)
        self.assertEqual(len(changes), 219)


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
