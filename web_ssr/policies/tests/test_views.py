import re
from datetime import date, timedelta

from django.test import SimpleTestCase
from django.urls import reverse

from policies.tests.fixtures import (
    CONNECTION_ERROR, TIMEOUT, make_item, make_page, patch_get, response, today,
)
from policies.views import schedule_view


def _visible_text(html: str) -> str:
    """script·style을 뺀 HTML 본문(JS 실행 전 사용자가 보는 내용)."""
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html)
    return html


class PolicyDetailViewTests(SimpleTestCase):
    def get(self, policy_id: str, *replies):
        with patch_get(*replies):
            return self.client.get(f"/policies/{policy_id}/")

    def test_ok_renders_policy_in_server_html(self):
        res = self.get("POL_TEST_001", response(make_page([make_item()])))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "policies/detail.html")
        html = _visible_text(res.content.decode())
        for text in ("가상 정책 POL_TEST_001", "가상 기관 담당과", "접수 중", "상시 접수",
                     "가상 지원대상 원문 15세~69세", "(필수) 가상 신청서", "전국", "가상 분야"):
            self.assertIn(text, html)
        self.assertIn("<title>가상 정책 POL_TEST_001 · SSOK 정책 상세</title>", html)
        self.assertIn('name="ssok-render" content="django-ssr"', html)

    def test_reverse_url_and_trailing_slash(self):
        self.assertEqual(reverse("policies:detail", args=["POL_010"]), "/policies/POL_010/")

    def test_content_is_readable_without_javascript(self):
        html = self.get("POL_TEST_001", response(make_page([make_item()]))).content.decode()
        # 서류 영역은 서버 HTML에서 접힌 상태가 아니며, 접기 버튼만 JS 전까지 숨겨 둔다.
        self.assertIn('<div class="docs-body" id="docs-body" data-docs-body>', html)
        self.assertNotIn("is-collapsed", html)
        self.assertRegex(html, r"<button[^>]*data-docs-toggle[^>]*aria-controls=\"docs-body\"[^>]*hidden")
        self.assertLess(html.index("(필수) 가상 신청서"), html.index("<script"))

    def test_external_links_are_safe(self):
        item = make_item(official_apply_url="https://apply.example.org/POL_TEST_001")
        html = self.get("POL_TEST_001", response(make_page([item]))).content.decode()
        for href in ("https://apply.example.org/POL_TEST_001", "https://example.org/notice/POL_TEST_001"):
            self.assertRegex(html, rf'href="{re.escape(href)}" target="_blank" rel="noopener noreferrer"')
        self.assertIn('href="http://127.0.0.1:5173/#/map?policy=POL_TEST_001"', html)
        self.assertIn('href="http://127.0.0.1:5173/#/"', html)

    def test_policy_text_is_html_escaped(self):
        item = make_item(policy_name="<script>alert(1)</script>정책", agency="<b>기관</b>")
        html = self.get("POL_TEST_001", response(make_page([item]))).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;정책", html)
        self.assertIn("&lt;b&gt;기관&lt;/b&gt;", html)

    def test_missing_optional_data_is_not_invented(self):
        item = make_item(agency="", source_url=None, application_schedule=None, required_documents=None,
                         requirement_excerpts=None, reception_status="unknown")
        res = self.get("POL_TEST_001", response(make_page([item])))
        html = res.content.decode()
        self.assertEqual(res.status_code, 200)
        self.assertIn("담당 기관은 공식 원문에서 확인해 주세요.", html)
        self.assertIn("확인된 서류 목록이 제공되지 않았어요", html)
        self.assertIn("확인된 지원 자격 원문 발췌가 없어요", html)
        self.assertIn("일정 확인 필요", html)
        self.assertNotIn("공식 신청 페이지", html)
        self.assertNotIn("data-docs-toggle", html)

    def test_not_found_policy_is_404(self):
        res = self.get("POL_NOT_EXIST", response(make_page([make_item()])))
        self.assertEqual(res.status_code, 404)
        self.assertContains(res, "정책을 찾을 수 없어요", status_code=404)

    def test_malformed_ids_are_404_without_calling_api(self):
        for path in ("/policies/pol_010/", "/policies/POL-010/", "/policies/abc/", "/policies/POL_010",
                     "/policies/" + "POL_" + "A" * 200 + "/", "/policies/POL_%3Cx%3E/"):
            with self.subTest(path), patch_get() as get:
                res = self.client.get(path)
                self.assertEqual(res.status_code, 404 if path != "/policies/POL_010" else 301)
                get.assert_not_called()

    def test_backend_failures_are_503_without_internal_details(self):
        cases = {
            "timeout": TIMEOUT,
            "connection": CONNECTION_ERROR,
            "http 500": response(None, status=500),
            "invalid json": response(invalid_json=True),
            "missing fields": response(make_page([make_item(policy_name=None)])),
        }
        for name, reply in cases.items():
            with self.subTest(name), self.assertLogs("policies.views", "WARNING"):
                res = self.get("POL_TEST_001", reply)
                self.assertEqual(res.status_code, 503)
                body = res.content.decode()
                self.assertIn("정책 정보를 잠시 불러올 수 없어요", body)
                self.assertNotIn("fastapi.internal.test", body)
                self.assertNotIn("Traceback", body)

    def test_only_get_is_allowed(self):
        with patch_get() as get:
            self.assertEqual(self.client.post("/policies/POL_TEST_001/").status_code, 405)
            self.assertEqual(self.client.put("/policies/POL_TEST_001/").status_code, 405)
        get.assert_not_called()


class ScheduleViewTests(SimpleTestCase):
    def schedule(self, **values):
        base = {"evaluated_on": today(), "start_date": None, "end_date": None, "is_ongoing": False,
                "days_to_start": None, "days_to_end": None}
        base.update(values)
        return base

    def test_open_with_deadline(self):
        end = (date.fromisoformat(today()) + timedelta(days=5)).isoformat()
        view = schedule_view("open", self.schedule(end_date=end, days_to_end=5), today())
        self.assertEqual((view["label"], view["badge"]), ("접수 중", "마감 D-5"))
        self.assertIn(end, view["period"])

    def test_stale_or_inconsistent_schedule_is_not_asserted(self):
        stale = schedule_view("open", self.schedule(evaluated_on="2000-01-01", is_ongoing=True), today())
        self.assertEqual((stale["status"], stale["label"]), ("unknown", "일정 재확인 필요"))
        wrong = schedule_view("closed", self.schedule(is_ongoing=True), today())
        self.assertEqual((wrong["status"], wrong["label"]), ("unknown", "일정 확인 필요"))

    def test_no_schedule_keeps_reception_without_dates(self):
        view = schedule_view("before", None, today())
        self.assertEqual((view["label"], view["badge"]), ("접수 예정", ""))
        self.assertEqual(view["period"], "공식 원문에서 확인해 주세요.")
