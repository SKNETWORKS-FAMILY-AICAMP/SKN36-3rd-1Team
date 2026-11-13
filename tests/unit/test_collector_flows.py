"""수집기 전체 흐름(페이지 반복 → 원본 저장 → 정제 JSONL → 실행 기록)을 가짜 응답으로 확인한다.

실제 공공 API·인증키·data/ 폴더를 쓰지 않는다. 출력 경로는 임시 폴더로 바꾼다 (sample_only).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote_plus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from collectors.api import gov24_collector as gov24, youthcenter_collector as youth  # noqa: E402

FAKE_KEY = "FAKE+FLOW/KEY_sample_only=="


def ok(payload):
    return SimpleNamespace(status_code=200, text=json.dumps(payload, ensure_ascii=False), json=lambda: payload)


def youth_item(number: int) -> dict:
    return {"plcyNo": f"2026000000{number:010d}", "plcyNm": f"가상 청년 정책 {number} (sample_only)",
            "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34", "sprtTrgtAgeLmtYn": "Y", "zipCd": "11110"}


class CollectorFlowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="collector_flow_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.raw_root = self.tmp / "data" / "raw"
        common = sys.modules["_common"]              # 수집기는 _common을 최상위 모듈로 import한다
        for target, name, value in ((common, "RAW_ROOT", self.raw_root),):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sleeps = []
        for patcher in (mock.patch("time.sleep", self.sleeps.append),
                        mock.patch.dict("os.environ", {"YOUTH_POLICY_API_KEY": FAKE_KEY,
                                                       "DATA_GO_KR_API_KEY": FAKE_KEY})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_log(self, source: str) -> str:
        return (self.raw_root / source / "run_log.jsonl").read_text(encoding="utf-8")

    def assert_no_key(self, text: str):
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn(quote_plus(FAKE_KEY), text)


class TestYouthcenterFlow(CollectorFlowBase):
    def patch_paths(self):
        processed = self.tmp / "data" / "processed"
        for name, value in (("BASE_DIR", self.tmp), ("OUT_RAW", self.raw_root / "youthcenter"),
                            ("PROCESSED_ROOT", processed),
                            ("OUT_PROCESSED_JSONL", processed / "youthcenter_policies.jsonl")):
            patcher = mock.patch.object(youth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return processed / "youthcenter_policies.jsonl"

    def test_pages_are_collected_and_written(self):
        out = self.patch_paths()
        pages = {1: [youth_item(1), youth_item(2)], 2: [youth_item(3)]}
        calls = []

        def fake_get(url, params, **kwargs):
            calls.append(params["pageNum"])
            return ok({"result": {"youthPolicyList": pages[params["pageNum"]]}})

        with mock.patch("requests.get", fake_get):
            summary = youth.run_collection(page_size=2, use_cache=False)
        self.assertEqual(calls, [1, 2])                            # 페이지마다 한 번씩만 요청
        self.assertEqual(summary["total_records_collected"], 3)
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["plcyNo"] for row in rows], [item["plcyNo"] for item in pages[1] + pages[2]])
        self.assertTrue((self.raw_root / "youthcenter" / "api" / "page_0002.json").is_file())
        self.assert_no_key(self.run_log("youthcenter"))

    def test_network_failure_is_recorded_without_key(self):
        self.patch_paths()

        def fake_get(url, params, **kwargs):
            if params["pageNum"] == 1:
                return ok({"result": {"youthPolicyList": [youth_item(1), youth_item(2)]}})
            query = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
            raise requests.ConnectionError(f"Max retries exceeded with url: /go/ythip/getPlcy?{query}")

        with mock.patch("requests.get", fake_get):
            summary = youth.run_collection(page_size=2, use_cache=False)
        self.assertEqual(summary["total_records_collected"], 2)
        self.assertEqual(len(summary["errors"]), 1)
        self.assert_no_key(json.dumps(summary, ensure_ascii=False))
        self.assert_no_key(self.run_log("youthcenter"))
        self.assertEqual(sum(1 for s in self.sleeps if s in (0.5, 1.0)), 2)   # 재시도 대기 0.5초 → 1초


class TestGov24Flow(CollectorFlowBase):
    def test_three_endpoints_are_merged(self):
        processed = self.tmp / "data" / "processed"
        out = processed / "gov24_public_services.jsonl"
        for name, value in (("OUT_RAW", self.raw_root / "gov24"), ("PROCESSED_ROOT", processed),
                            ("OUT_PROCESSED_JSONL", out)):
            patcher = mock.patch.object(gov24, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        services = [{"서비스ID": f"SVC_SAMPLE_{n}", "서비스명": f"가상 서비스 {n} (sample_only)"} for n in (1, 2)]
        calls = []

        def fake_get(url, params, **kwargs):
            calls.append(url.rsplit("/", 1)[-1])
            return ok({"data": services, "matchCount": len(services)})

        with mock.patch("requests.get", fake_get), \
                mock.patch.object(gov24, "write_mapping_table_markdown", return_value=self.tmp / "mapping.md"):
            summary = gov24.run_collection(per_page=10, use_cache=False)
        self.assertEqual(sorted(calls), ["serviceDetail", "serviceList", "supportConditions"])   # 엔드포인트당 1회
        self.assertEqual(summary["merge_summary"]["combined_success_count"], 2)
        self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 2)
        self.assert_no_key(self.run_log("gov24"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
