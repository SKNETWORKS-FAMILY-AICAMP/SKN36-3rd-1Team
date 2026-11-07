"""승인 v1.4.0 검색 호환성. 실제 DB·임베딩 호출 없이 읽기 전용 fake로 검사."""
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import full_index as fi, handoff_data as hd, hybrid_search as hs, region_search as rs


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "data/releases/v1.4.0-candidate"
ROWS = [json.loads(line) for line in (RELEASE / "qdrant/payload_chunks.jsonl").read_text(encoding="utf-8").splitlines()]


def fake_embedder(texts):
    """sample_only: 모델을 호출하지 않는 가상 벡터."""
    return [[1.0] + [0.0] * 1023 for _ in texts]


fake_embedder.embedding_model = "qwen3-embedding:0.6b"
fake_embedder.embedding_dim = 1024


class ReadOnlyQdrant:
    """읽기 함수만 노출. 실제 Qdrant Filter 객체의 MatchValue/MatchAny를 적용한다."""
    def __init__(self, rows, *, exists=True):
        self.rows = copy.deepcopy(rows)
        self.exists = exists
        self.calls = []

    def get_collection(self, name):
        self.calls.append(("get_collection", name))
        if not self.exists:
            raise ValueError(f"Collection {name} not found")
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=1024))))

    def query_points(self, *, collection_name, query, limit, query_filter, with_payload):
        self.calls.append(("query_points", collection_name))
        points = []
        for row in self.rows:
            for condition in (query_filter.must if query_filter else []):
                match = condition.match
                values = match.any if hasattr(match, "any") else [match.value]
                if row.get(condition.key) not in values:
                    break
            else:
                points.append(SimpleNamespace(id=row.get("point_id", "sample_only"), score=0.5, payload=row))
        return SimpleNamespace(points=points[:limit])


class RegionPlanTests(unittest.TestCase):
    def plan(self, value, rows=ROWS):
        return rs.plan_region_search({"region": value}, rows)

    def test_actual_release_filters(self):
        cases = {
            "서울특별시": {"전국", "서울특별시", "서울특별시 동대문구"},
            "인천광역시": {"전국", "인천광역시", "인천광역시 서구"},
            "광주광역시": {"전국", "광주광역시", "광주광역시, 전라남도"},
            "전라남도": {"전국", "전라남도", "광주광역시, 전라남도"},
            "부산광역시": {"전국", "부산광역시"}, "전국": {"전국"},
        }
        for requested, expected in cases.items():
            with self.subTest(region=requested):
                self.assertEqual(set(self.plan(requested)["filter_values"]), expected)

    def test_query_aliases_are_not_policy_mappings(self):
        for alias, canonical in (("서울", "서울특별시"), ("전남", "전라남도"), ("인천", "인천광역시")):
            self.assertEqual(self.plan(alias)["filter_values"], self.plan(canonical)["filter_values"])

    def test_multi_region_request_is_union_of_observed_values(self):
        result = self.plan(["광주광역시", "전라남도"])
        self.assertEqual(set(result["filter_values"]), {"전국", "광주광역시", "전라남도", "광주광역시, 전라남도"})
        self.assertEqual(result["filter_values"], self.plan("광주광역시, 전라남도")["filter_values"])

    def test_same_sido_different_districts_use_token_boundaries(self):
        rows = [{"policy_id": "POL_SAMPLE", "region": r} for r in
                ("서울특별시", "서울특별시 동대문구", "서울특별시 강남구", "서울특별시민 지원", "서울특별시가짜")]
        self.assertEqual(self.plan("서울특별시 동대문구", rows)["filter_values"], ["서울특별시", "서울특별시 동대문구"])
        self.assertEqual(set(self.plan("서울특별시", rows)["filter_values"]),
                         {"서울특별시", "서울특별시 동대문구", "서울특별시 강남구"})

    def test_unknown_and_missing_regions_have_separate_review_ids(self):
        rows = [{"policy_id": "POL_SAMPLE_A", "region": "unknown"}, {"policy_id": "POL_SAMPLE_B"},
                {"policy_id": "POL_SAMPLE_C", "region": "전국, 미확인"},
                {"policy_id": "POL_SAMPLE_D", "region": "서울특별시, 미확인"}]
        plan = self.plan("서울특별시", rows)
        self.assertEqual(plan["unconfirmed_policy_ids"], ["POL_SAMPLE_A", "POL_SAMPLE_B", "POL_SAMPLE_C", "POL_SAMPLE_D"])
        self.assertEqual(plan["filter_values"], ["서울특별시, 미확인"])
        self.assertEqual(self.plan("부산광역시", rows)["filter_values"], [])
        self.assertEqual(self.plan("unknown", rows)["filter_values"], ["unknown"])
        info = rs.describe_hit_region(None, plan)
        self.assertEqual(info["match_basis"], "unconfirmed")
        self.assertTrue(info["review_required"])

    def test_no_region_filter_is_not_national_only(self):
        self.assertIsNone(rs.plan_region_search(None, ROWS)["applied_filters"])
        self.assertEqual(rs.plan_region_search({"policy_id": "POL_SAMPLE"}, ROWS)["applied_filters"],
                         {"policy_id": "POL_SAMPLE"})

    def test_other_filters_and_input_payload_are_preserved(self):
        inputs = {"region": "전남", "policy_id": ["POL_SAMPLE"]}
        snapshot = copy.deepcopy((inputs, ROWS))
        plan = rs.plan_region_search(inputs, ROWS)
        self.assertEqual(plan["applied_filters"]["policy_id"], ["POL_SAMPLE"])
        self.assertEqual((inputs, ROWS), snapshot)

    def test_all_region_match_bases_are_not_eligibility(self):
        plan = self.plan("서울특별시")
        for value, basis in (("서울특별시", "exact"), ("전국", "national"),
                             ("서울특별시 동대문구", "partial"), ("미확인", "unconfirmed")):
            info = rs.describe_hit_region(value, plan)
            self.assertEqual(info["original_region"], value)
            self.assertEqual(info["match_basis"], basis)
            self.assertFalse(info["is_eligibility_decision"])

    def test_broader_stored_region_is_partial_for_a_district_query(self):
        plan = self.plan("서울특별시 동대문구")
        info = rs.describe_hit_region("서울특별시", plan)
        self.assertEqual(info["match_basis"], "partial")
        self.assertTrue(info["review_required"])
        no_filter = rs.describe_hit_region("서울특별시", rs.plan_region_search(None, ROWS))
        self.assertEqual(no_filter["match_basis"], "unconfirmed")
        self.assertFalse(no_filter["filter_applied"])


class V140ProfileTests(unittest.TestCase):
    def test_v140_default_and_both_profile_definitions(self):
        with patch.object(hd, "dataset_version", return_value="1.4.0-candidate"):
            self.assertEqual(hs.default_profile(), "b2_06b_v140")
            for strategy in ("b2", "b3"):
                settings = hs.resolve_search_settings(profile=f"{strategy}_06b_v140")
                self.assertEqual(settings["collection"], f"exp_{strategy}_qwen3emb06b_v140_v1")
                self.assertEqual((settings["embedding_model"], settings["embedding_dim"], settings["query_template"]),
                                 ("qwen3-embedding:0.6b", 1024, None))

    def test_legacy_default_and_issue21_profile_definitions_unchanged(self):
        with patch.object(hd, "dataset_version", return_value="1.3.2"):
            self.assertEqual(hs.default_profile(), "b2_06b_baseline")
            self.assertEqual(hs.resolve_search_settings()["collection"], "exp_b2_qwen3emb06b_full_v1")
        with patch.object(hd, "dataset_version", return_value="issue21-v1.4.0-review"):
            self.assertEqual(hs.default_profile(), "b2_06b_i21")

    def test_wrong_dataset_profiles_and_known_custom_collections_are_blocked(self):
        for version, profile in (("1.3.2", "b2_06b_v140"), ("1.4.0-candidate", "b2_06b_baseline"),
                                 ("issue21-v1.4.0-review", "b3_06b_v140")):
            with self.subTest(version=version, profile=profile), patch.object(hd, "dataset_version", return_value=version):
                with self.assertRaises(fi.FullIndexError):
                    hs.resolve_search_settings(profile=profile)
        with patch.object(hd, "dataset_version", return_value="1.4.0-candidate"):
            with self.assertRaises(fi.FullIndexError):
                hs.resolve_search_settings(collection="exp_b2_qwen3emb06b_full_v1")
            with self.assertRaises(hs.SearchProfileError):
                hs.resolve_search_settings(collection=hs.LEGACY_EXPERIMENT_COLLECTION)

    def test_missing_collection_fails_before_embedding(self):
        for profile in ("b2_06b_v140", "b3_06b_v140"):
            with self.subTest(profile=profile), patch.object(hd, "dataset_version", return_value="1.4.0-candidate"), \
                    patch.object(hs.em, "make_ollama_embedder", side_effect=AssertionError("must not embed")):
                with self.assertRaises(hs.HybridSearchBackendError) as error:
                    hs.search_policy_evidence("sample_only", profile=profile, client=ReadOnlyQdrant([], exists=False))
                self.assertEqual(error.exception.kind, "not_found")
                self.assertIn("v140", str(error.exception))

    def test_http404_is_not_a_success_or_fallback(self):
        import httpx
        from qdrant_client.http.exceptions import UnexpectedResponse
        client = ReadOnlyQdrant([])
        with patch.object(client, "get_collection", side_effect=UnexpectedResponse(404, "Not Found", b"", httpx.Headers())):
            with self.assertRaises(hs.HybridSearchBackendError) as error:
                hs._collection_vector_size(client, "exp_b2_qwen3emb06b_v140_v1")
        self.assertEqual(error.exception.kind, "not_found")


class V140SearchContractTests(unittest.TestCase):
    def setUp(self):
        self.dataset = patch.object(hd, "dataset_version", return_value="1.4.0-candidate")
        self.payloads = patch.object(hd, "load_payload_chunks", return_value=ROWS)
        self.dataset.start()
        self.payloads.start()
        self.addCleanup(self.dataset.stop)
        self.addCleanup(self.payloads.stop)
        self.client = ReadOnlyQdrant(ROWS)

    def search(self, filters=None, profile=None):
        return hs.search_policy_evidence("sample_only", filters=filters, client=self.client,
                                         embedder=fake_embedder, profile=profile, k=200)

    def test_b2_and_b3_search_only_the_v140_collection(self):
        for profile in (None, "b3_06b_v140"):
            result = self.search(profile=profile)
            self.assertIn("v140", result["collection"])
        self.assertTrue(all("v140" in collection for _, collection in self.client.calls))

    def test_actual_district_policies_are_retrievable_without_payload_changes(self):
        for requested, raw in (("서울", "서울특별시 동대문구"), ("인천", "인천광역시 서구"),
                               ("전남", "광주광역시, 전라남도"), ("광주", "광주광역시, 전라남도")):
            target = next(row for row in ROWS if row["region"] == raw)
            result = self.search({"region": requested, "policy_id": target["policy_id"]})
            self.assertTrue(result["hits"])
            self.assertEqual(result["filters"]["region"], requested)
            self.assertIn(raw, result["region_search"]["applied_filters"]["region"])
            self.assertTrue(all(hit["region"] == raw for hit in result["hits"]))
            self.assertTrue(all(hit["region_match"]["review_required"] for hit in result["hits"]))
            self.assertEqual(result["hits"][0]["region_match"]["match_basis"], "partial")
        self.assertEqual(self.client.rows, ROWS)

    def test_no_substring_or_unrelated_sido_inclusion(self):
        result = self.search({"region": "부산광역시"})
        self.assertTrue(all(h["region"] in {"전국", "부산광역시"} for h in result["hits"]))
        self.assertEqual(self.search({"region": "없는지역"})["hits"], [])

    def test_partial_warning_and_graph_evidence_are_kept_separate(self):
        row = next(row for row in ROWS if row["region"] == "서울특별시 동대문구")
        result = self.search({"region": "서울", "policy_id": row["policy_id"]})
        context = {"policy_id": row["policy_id"], "policy": {"region": row["region"]},
                   "regions": [{"name": "서울특별시", "relationship": {"review_status": "extracted"}}],
                   "documents": [], "eligibility": {"evaluated": False, "condition_tree": [], "structure_issues": []}}
        snapshot = copy.deepcopy(context)
        combined = hs.combine_policy_evidence(result, {"policies": {row["policy_id"]: context}})
        entry = combined["policies"][0]
        self.assertEqual(entry["evidence"][0]["region"], row["region"])
        self.assertEqual(entry["region_search"]["graph_available_in"], context["regions"])
        self.assertFalse(entry["graph"]["eligibility"]["evaluated"])
        self.assertEqual(context, snapshot)
        self.assertIn(hs.QUALITY_SEARCH_REGION, [w["code"] for w in entry["data_quality"]["warnings"]])
        self.assertTrue(entry["data_quality"]["hold_auto_eligibility"])
        self.assertIn(row["policy_id"], combined["hold_auto_eligibility_policy_ids"])

    def test_unknown_payload_and_missing_graph_keep_warnings(self):
        rows = [{**ROWS[0], "region": "unknown"}]
        self.client = ReadOnlyQdrant(rows)
        with patch.object(hd, "load_payload_chunks", return_value=rows):
            result = self.search({"region": "unknown"})
        combined = hs.combine_policy_evidence(result, {"policies": {}})
        codes = {w["code"] for w in combined["policies"][0]["data_quality"]["warnings"]}
        self.assertIn(hs.QUALITY_GRAPH_MISSING, codes)
        self.assertIn(hs.QUALITY_SEARCH_REGION, codes)
        self.assertEqual(result["hits"][0]["region_match"]["match_basis"], "unconfirmed")

    def test_no_region_filter_keeps_missing_regions_as_review_candidates(self):
        rows = [{k: v for k, v in ROWS[0].items() if k != "region"}]
        self.client = ReadOnlyQdrant(rows)
        with patch.object(hd, "load_payload_chunks", return_value=rows):
            result = self.search()
        self.assertTrue(result["hits"])
        self.assertIsNone(result["hits"][0]["region"])
        self.assertTrue(result["hits"][0]["region_match"]["review_required"])

    def test_v132_filters_still_use_exact_values(self):
        with patch.object(hd, "dataset_version", return_value="1.3.2"), \
                patch.object(hd, "load_payload_chunks", side_effect=AssertionError("legacy does not load catalog")):
            result = self.search({"region": "서울특별시"})
        self.assertNotIn("region_search", result)
        self.assertTrue(all(h["region"] == "서울특별시" for h in result["hits"]))


if __name__ == "__main__":
    unittest.main()
