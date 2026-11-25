from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from policies import services
from policies.tests.fixtures import (
    CONNECTION_ERROR, TIMEOUT, make_item, make_page, patch_get, response,
)


class FetchPolicyTests(SimpleTestCase):
    def test_exact_policy_id_match_only(self):
        # POL_01이 POL_010의 접두사여도 다른 정책을 고르지 않는다.
        page = make_page([make_item("POL_010"), make_item("POL_01")])
        with patch_get(response(page)) as get:
            result = services.fetch_policy("POL_01")
        self.assertEqual(result.policy["policy_id"], "POL_01")
        self.assertEqual(result.policy["policy_name"], "가상 정책 POL_01")
        # 목록 API를 limit·offset으로만 호출하고 정책명 검색(q)은 쓰지 않는다.
        params = get.call_args.kwargs["params"]
        self.assertEqual(params, {"limit": 500, "offset": 0})
        self.assertNotIn("q", params)
        self.assertIsInstance(get.call_args.kwargs["timeout"], tuple)
        self.assertTrue(get.call_args.args[0].endswith("/api/policies"))

    def test_not_found_when_absent(self):
        with patch_get(response(make_page([make_item("POL_010")]))):
            with self.assertRaises(services.PolicyNotFound):
                services.fetch_policy("POL_999")

    def test_invalid_id_format_never_calls_api(self):
        with patch_get() as get:
            for bad in ("pol_010", "POL-010", "POL_" + "A" * 200, "", "../POL_1"):
                with self.assertRaises(services.PolicyNotFound):
                    services.fetch_policy(bad)
        get.assert_not_called()

    def test_reads_every_page(self):
        first = make_page([make_item("POL_A")], total=3, offset=0, limit=1)
        second = make_page([make_item("POL_B")], total=3, offset=1, limit=1)
        third = make_page([make_item("POL_C")], total=3, offset=2, limit=1)
        with patch_get(response(first), response(second), response(third)) as get:
            result = services.fetch_policy("POL_C")
        self.assertEqual(result.policy["policy_id"], "POL_C")
        self.assertEqual([call.kwargs["params"]["offset"] for call in get.call_args_list], [0, 1, 2])

    def test_manifest_change_between_pages_is_unavailable(self):
        first = make_page([make_item("POL_A")], total=2, limit=1)
        second = make_page([make_item("POL_B")], total=2, offset=1, limit=1, manifest_sha256="b" * 64)
        with patch_get(response(first), response(second)):
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy("POL_B")

    def test_offset_mismatch_and_empty_middle_page_are_unavailable(self):
        first = make_page([make_item("POL_A")], total=2, limit=1)
        wrong_offset = make_page([make_item("POL_B")], total=2, offset=0, limit=1)
        with patch_get(response(first), response(wrong_offset)):
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy("POL_B")
        empty = make_page([], total=2, offset=1, limit=1)
        with patch_get(response(first), response(empty)):
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy("POL_B")

    def test_duplicate_ids_are_unavailable(self):
        with patch_get(response(make_page([make_item("POL_A"), make_item("POL_A")]))):
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy("POL_A")

    def test_transport_and_format_failures_are_unavailable(self):
        cases = {
            "timeout": TIMEOUT,
            "connection": CONNECTION_ERROR,
            "http 503": response({"detail": {"code": "POLICY_CATALOG_UNAVAILABLE"}}, status=503),
            "http 500": response(None, status=500),
            "invalid json": response(invalid_json=True),
            "json list": response([]),
            "missing meta": response({"items": []}),
            "bad manifest": response(make_page([make_item()], manifest_sha256="xyz")),
            "bad source kind": response(make_page([make_item()], source_kind="db")),
        }
        for name, reply in cases.items():
            with self.subTest(name), patch_get(reply):
                with self.assertRaises(services.PolicyServiceUnavailable):
                    services.fetch_policy("POL_TEST_001")

    def test_missing_required_fields_are_unavailable(self):
        for broken in ({"policy_name": ""}, {"policy_name": None}, {"reception_status": "maybe"}):
            with self.subTest(broken), patch_get(response(make_page([make_item(**broken)]))):
                with self.assertRaises(services.PolicyServiceUnavailable):
                    services.fetch_policy("POL_TEST_001")

    def test_unsafe_links_are_dropped_and_optional_fields_tolerated(self):
        item = make_item(source_url="javascript:alert(1)", official_apply_url="http://user:pw@example.org/x",
                         regions=None, categories=["", "분야"], application_schedule={"evaluated_on": "bad"},
                         required_documents=[{"text": "서류", "document_id": "D", "chunk_id": "C",
                                              "source_url": "ftp://example.org"}, {"text": ""}],
                         requirement_excerpts="not a list")
        with patch_get(response(make_page([item]))):
            policy = services.fetch_policy("POL_TEST_001").policy
        self.assertIsNone(policy["source_url"])
        self.assertIsNone(policy["official_apply_url"])
        self.assertEqual(policy["regions"], [])
        self.assertEqual(policy["categories"], ["분야"])
        self.assertIsNone(policy["application_schedule"])
        self.assertEqual(policy["required_documents"],
                         [{"text": "서류", "document_id": "D", "chunk_id": "C", "source_url": None}])
        self.assertEqual(policy["requirement_excerpts"], [])

    def test_same_excerpt_labels_are_merged_without_changing_text(self):
        with patch_get(response(make_page([make_item()]))):
            excerpts = services.fetch_policy("POL_TEST_001").policy["requirement_excerpts"]
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(excerpts[0]["labels"], ["지원대상", "선정기준"])
        self.assertEqual(excerpts[0]["text"], "가상 지원대상 원문 15세~69세")

    def test_apply_url_equal_to_source_is_not_shown_as_separate_link(self):
        item = make_item(official_apply_url="https://example.org/notice/POL_TEST_001")
        with patch_get(response(make_page([item]))):
            self.assertIsNone(services.fetch_policy("POL_TEST_001").policy["official_apply_url"])

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_successful_catalog_is_cached_but_failures_are_not(self):
        cache.clear()
        self.addCleanup(cache.clear)
        with patch_get(TIMEOUT, response(make_page([make_item()]))) as get:
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy("POL_TEST_001")
            services.fetch_policy("POL_TEST_001")
            services.fetch_policy("POL_TEST_001")
        self.assertEqual(get.call_count, 2)
