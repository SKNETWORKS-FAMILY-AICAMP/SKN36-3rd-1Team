"""수집기 공통 HTTP 재시도·인증키 가리기 테스트. 실제 공공 API에 접속하지 않는다 (가짜 키, sample_only)."""

import logging
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

        calls, result = self.call(read_timeout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], (_common.CONNECT_TIMEOUT_SECONDS, 30))
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


class TestCuratedYouthcenterIds(unittest.TestCase):
    def test_curated_ids_get_verified_status(self):
        item = {"plcyNo": "20260710005400113256", "plcyNm": "가상 정책 (sample_only)"}
        other = {"plcyNo": "99999999999999999999", "plcyNm": "가상 정책 (sample_only)"}
        self.assertEqual(youthcenter_collector.normalize_youthcenter_item(item, {})["verification_status"], "verified")
        self.assertEqual(youthcenter_collector.normalize_youthcenter_item(other, {})["verification_status"], "extracted")
        self.assertEqual(len(youthcenter_collector.CURATED_YOUTHCENTER_PLCY_NOS), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
