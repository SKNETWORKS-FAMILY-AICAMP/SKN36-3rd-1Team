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
from collectors.api.youthcenter_collector import load_code_definitions, normalize_youthcenter_item
from collectors.preprocessing.normalize import parse_bokjiro_detail_xml
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
        self.assertEqual(report["counts"]["total_graph_nodes"], 1074)
        self.assertEqual(report["counts"]["total_graph_edges"], 3703)
        self.assertEqual(len(chunks), 599)

    def test_only_declared_baseline_policy_property_changed(self):
        delta = json.loads((PACKAGE / "release_delta_manifest.json").read_text(encoding="utf-8"))
        declared = {row["policy_id"]: row for row in delta["updated_policy_properties"]}
        actual_changes = set()
        for _, (name, key) in NODE_CONFIG.items():
            old = rows(ROOT / "data/handoff/neo4j" / f"nodes_{name}.jsonl")
            new = {row[key]: row for row in rows(PACKAGE / "neo4j" / f"nodes_{name}.jsonl")}
            for original in old:
                current = dict(new[original[key]])
                if name == "policy" and original != current:
                    change = declared[original[key]]
                    self.assertEqual(change["before"], original["official_apply_url"])
                    self.assertEqual(change["after"], current["official_apply_url"])
                    actual_changes.add(original[key])
                    current["official_apply_url"] = original["official_apply_url"]
                self.assertEqual(original, current)
        self.assertEqual(actual_changes, set(declared))
        for relative in ["neo4j/relationships.jsonl", "qdrant/payload_chunks.jsonl"]:
            old = rows(ROOT / "data/handoff" / relative)
            new = {json.dumps(row, sort_keys=True) for row in rows(PACKAGE / relative)}
            self.assertTrue(all(json.dumps(row, sort_keys=True) in new for row in old))

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
