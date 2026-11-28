"""수집기 공통 HTTP 재시도·인증키 가리기 테스트. 실제 공공 API에 접속하지 않는다 (가짜 키, sample_only)."""

import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote_plus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402
from urllib3.exceptions import ReadTimeoutError  # noqa: E402

from collectors.api import _common  # noqa: E402
from collectors.api import gov24_collector, youthcenter_collector  # noqa: E402

FAKE_KEY = "FAKE+KEY/sample_only=="          # URL 인코딩되는 문자를 일부러 넣는다
LOGGER = logging.getLogger("test_collector_common")


def response(status: int, text: str = "", payload=None):
    return SimpleNamespace(status_code=status, text=text, json=lambda: payload or {})


def connection_error(*_args, params=None, **_kwargs):
    # requests 예외 문장처럼 쿼리(인코딩된 키 포함)를 담는다.
    query = "&".join(f"{name}={quote_plus(str(value))}" for name, value in (params or {}).items())
    raise requests.ConnectionError(f"Max retries exceeded with url: /api?{query}")


class TestHttpGetRetry(unittest.TestCase):
    def call(self, fake_get):
        calls = []
        self.sleeps = []

        def counted(*args, **kwargs):
            calls.append(kwargs)
            return fake_get(*args, **kwargs)

        with mock.patch("requests.get", counted), mock.patch.object(_common.time, "sleep", self.sleeps.append):
            try:
                result = _common.http_get("https://example.invalid/api", {"serviceKey": FAKE_KEY}, logger=LOGGER)
            except RuntimeError as error:
                result = error
        return calls, result

    def test_429_is_retried_up_to_max_with_short_waits(self):
        calls, result = self.call(lambda *a, **k: response(429))
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleeps, [0.5, 1.0])          # 실패로 끝나기까지 추가 대기 합계 1.5초
        self.assertIsInstance(result, RuntimeError)

    def test_connect_timeout_is_short_and_read_timeout_is_not_retried(self):
        def read_timeout(*_args, **_kwargs):
            raise requests.ReadTimeout("slow server (sample_only)")

        def wrapped_read_timeout(*_args, **_kwargs):
            raise requests.ConnectionError(ReadTimeoutError(None, "/api", "slow body (sample_only)"))

        for timeout in (read_timeout, wrapped_read_timeout):
            with self.subTest(timeout=timeout.__name__):
                calls, result = self.call(timeout)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.sleeps, [])
                self.assertEqual(calls[0]["timeout"], (_common.CONNECT_TIMEOUT_SECONDS, 30))
                self.assertIsInstance(result, RuntimeError)

    def test_retryable_failures_keep_backoff(self):
        failures = [requests.ConnectTimeout(), *(response(code) for code in (500, 502, 503, 504))]

        def fail(*_args, **_kwargs):
            if isinstance(failure, Exception):
                raise failure
            return failure

        for failure in failures:
            with self.subTest(failure=failure):
                calls, result = self.call(fail)
                self.assertEqual(len(calls), 3)
                self.assertEqual(self.sleeps, [0.5, 1.0])
                self.assertIsInstance(result, RuntimeError)

    def test_501_is_not_retried(self):
        calls, result = self.call(lambda *a, **k: response(501))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertIsInstance(result, RuntimeError)

    def test_5xx_is_retried_and_success_returns(self):
        answers = iter([response(503), response(200, payload={"ok": True})])
        calls, result = self.call(lambda *a, **k: next(answers))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.status_code, 200)

    def test_404_is_not_retried(self):
        calls, result = self.call(lambda *a, **k: response(404, "not found"))
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(result))

    def test_connection_error_message_hides_key(self):
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            calls, result = self.call(connection_error)
        self.assertEqual(len(calls), 3)
        for text in [str(result), *logs.output]:
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn(quote_plus(FAKE_KEY), text)
            self.assertIn("***", text)


class TestMaskSecrets(unittest.TestCase):
    def test_masks_plain_and_url_encoded_forms(self):
        text = f"a {FAKE_KEY} b {quote_plus(FAKE_KEY)} c"
        masked = _common.mask_secrets(text, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, masked)
        self.assertNotIn(quote_plus(FAKE_KEY), masked)

    def test_logger_filter_masks_encoded_key(self):
        logger = _common.get_logger("test_collector_mask", [FAKE_KEY])
        with self.assertLogs(logger, level="ERROR") as logs:
            logger.error("failed: %s", f"url?serviceKey={quote_plus(FAKE_KEY)}")
        self.assertNotIn(quote_plus(FAKE_KEY), logs.output[0])


class TestCollectorErrorSinks(unittest.TestCase):
    """수집기가 HTTP 오류를 summary·print에 남길 때 인증키가 없어야 한다."""

    def test_gov24_fetch_error_hides_key(self):
        with mock.patch("requests.get", connection_error), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as raised:
                gov24_collector.fetch_api_json("https://example.invalid/gov24", {"serviceKey": FAKE_KEY}, LOGGER)
        self.assertNotIn(quote_plus(FAKE_KEY), str(raised.exception))
        self.assertNotIn(FAKE_KEY, str(raised.exception))

    def test_youthcenter_fetch_error_hides_key(self):
        with mock.patch("requests.get", connection_error), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as raised:
                youthcenter_collector.fetch_youthcenter_api(page_type="1", key=FAKE_KEY, logger=LOGGER)
        self.assertNotIn(quote_plus(FAKE_KEY), str(raised.exception))
        self.assertNotIn(FAKE_KEY, str(raised.exception))

    def test_bokjiro_network_error_hides_key(self):
        from collectors.api import bokjiro_collector

        with mock.patch("requests.get", connection_error):
            with self.assertRaises(bokjiro_collector.BokjiroApiError) as raised:
                bokjiro_collector.make_api_request("https://example.invalid/bokjiro", {"serviceKey": FAKE_KEY}, LOGGER)
        self.assertNotIn(quote_plus(FAKE_KEY), str(raised.exception))
        self.assertNotIn(FAKE_KEY, str(raised.exception))


class TestWriteProcessedJsonl(unittest.TestCase):
    """수집 오류가 있으면 추적 중인 정제 파일을 덮어쓰지 않는다. 임시 폴더만 쓴다 (sample_only)."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="write_processed_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.path = self.dir / "sample.jsonl"
        self.path.write_text('{"old": true}\n', encoding="utf-8")
        self.partial = self.dir / "sample.partial.jsonl"

    def test_success_replaces_file(self):
        summary = {"errors": []}
        written = _common.write_processed_jsonl(self.path, [{"id": 1}], summary, LOGGER)
        self.assertEqual(written, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"id": 1}\n')
        self.assertFalse(summary["refined_jsonl_partial"])
        self.assertFalse(list(self.dir.glob("*.tmp")))

    def test_errors_keep_existing_file(self):
        summary = {"errors": ["page 2 failed"]}
        with mock.patch.dict(os.environ):
            os.environ.pop(_common.ALLOW_PARTIAL_ENV, None)
            written = _common.write_processed_jsonl(self.path, [{"id": 1}], summary, LOGGER)
        self.assertEqual(written, self.partial)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"old": true}\n')
        self.assertEqual(self.partial.read_text(encoding="utf-8"), '{"id": 1}\n')
        self.assertTrue(summary["refined_jsonl_partial"])

    def test_errors_overwrite_only_when_allowed(self):
        summary = {"errors": ["page 2 failed"]}
        with mock.patch.dict(os.environ, {_common.ALLOW_PARTIAL_ENV: "1"}):
            written = _common.write_processed_jsonl(self.path, [{"id": 1}], summary, LOGGER)
        self.assertEqual(written, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"id": 1}\n')


class TestGov24UrlEnvNames(unittest.TestCase):
    """.env.example의 MOIS_BENEFITS_* 이름과 예전 MOIS_SERVICE_* 이름을 모두 읽는다. 주소는 가상 값이다 (sample_only)."""

    NAMES = ("MOIS_BENEFITS_LIST_API_URL", "MOIS_BENEFITS_DETAIL_API_URL", "MOIS_BENEFITS_CONDITIONS_API_URL",
             "MOIS_SERVICE_LIST_API_URL", "MOIS_SERVICE_DETAIL_API_URL", "MOIS_SUPPORT_CONDITIONS_API_URL",
             "MOIS_BENEFITS_API_URL")

    def urls(self, env):
        with mock.patch.dict(os.environ):
            for name in self.NAMES:
                os.environ.pop(name, None)
            os.environ.update(env)
            return gov24_collector.get_gov24_urls()

    def test_env_example_names_are_read(self):
        self.assertEqual(self.urls({
            "MOIS_BENEFITS_LIST_API_URL": "https://example.org/list",
            "MOIS_BENEFITS_DETAIL_API_URL": "https://example.org/detail",
            "MOIS_BENEFITS_CONDITIONS_API_URL": "https://example.org/cond",
        }), ("https://example.org/list", "https://example.org/detail", "https://example.org/cond"))

    def test_old_names_still_work(self):
        self.assertEqual(self.urls({
            "MOIS_SERVICE_LIST_API_URL": "https://example.org/old-list",
            "MOIS_SERVICE_DETAIL_API_URL": "https://example.org/old-detail",
            "MOIS_SUPPORT_CONDITIONS_API_URL": "https://example.org/old-cond",
        }), ("https://example.org/old-list", "https://example.org/old-detail", "https://example.org/old-cond"))

    def test_defaults_without_env(self):
        self.assertEqual(self.urls({}), (gov24_collector.DEFAULT_LIST_URL, gov24_collector.DEFAULT_DETAIL_URL,
                                         gov24_collector.DEFAULT_COND_URL))


class TestCuratedYouthcenterIds(unittest.TestCase):
    def test_curated_ids_get_verified_status(self):
        item = {"plcyNo": "20260710005400113256", "plcyNm": "가상 정책 (sample_only)"}
        other = {"plcyNo": "99999999999999999999", "plcyNm": "가상 정책 (sample_only)"}
        self.assertEqual(youthcenter_collector.normalize_youthcenter_item(item, {})["verification_status"], "verified")
        self.assertEqual(youthcenter_collector.normalize_youthcenter_item(other, {})["verification_status"], "extracted")
        self.assertEqual(len(youthcenter_collector.CURATED_YOUTHCENTER_PLCY_NOS), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
