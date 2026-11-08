"""Qdrant의 임시 컬렉션에서 후보 청크의 ID·payload 저장을 검증한다.

임베딩 모델을 임의로 정하지 않기 위해 벡터 없는 컬렉션을 사용한다.
실제 의미 검색·벡터 차원·임베딩 품질 검증으로 해석하면 안 된다.
--apply 없이는 파일만 검사한다. 이미 있는 컬렉션에는 쓰지 않는다.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
from scripts.load_candidate_neo4j import KST, load_db_settings, validate_package


class QdrantHttp:
    """새 검증 컬렉션 하나만 사용하는 HTTP 클라이언트."""

    def __init__(self, url: str, key: str, collection: str):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Qdrant URL 형식 오류")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Qdrant URL에는 서버 주소만 지정하세요")
        if not key or not re.fullmatch(r"lifepolicy_review_[A-Za-z0-9_-]+", collection):
            raise ValueError("API 키와 lifepolicy_review_ 접두어의 검증 컬렉션 이름이 필요합니다")
        self.base = f"{url.rstrip('/')}/collections/{collection}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["api-key"] = key

    def request(self, method: str, suffix: str = "", payload: dict | None = None, missing_ok: bool = False) -> dict | None:
        try:
            response = self.session.request(method, self.base + suffix, json=payload, timeout=30)
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            raise RuntimeError(f"Qdrant HTTP 요청 실패 ({type(error).__name__})") from None


def verify_payloads(client: QdrantHttp, chunks: dict[str, dict]) -> dict:
    """두 번 upsert한 뒤 전체 payload와 필터 검색 건수를 대조하고 정리한다."""
    created = False
    report = {"status": "FAILED", "mode": "PAYLOAD_ONLY", "embedding_search": "NOT_TESTED"}
    try:
        if client.request("GET", missing_ok=True) is not None:
            raise ValueError("기존 컬렉션에는 쓰지 않습니다. 새 검증 이름을 지정하세요")
        client.request("PUT", payload={"vectors": {}})
        created = True
        points = [{"id": point_id, "vector": {}, "payload": payload} for point_id, payload in chunks.items()]
        for _ in range(2):
            for start in range(0, len(points), 100):
                client.request("PUT", "/points?wait=true", {"points": points[start:start + 100]})
            count = client.request("POST", "/points/count", {"exact": True})["result"]["count"]
            if count != len(chunks):
                raise ValueError("Qdrant 재적재 건수 불일치")

        actual = {}
        offset = None
        while True:
            query = {"limit": 100, "with_payload": True, "with_vector": False}
            if offset is not None:
                query["offset"] = offset
            result = client.request("POST", "/points/scroll", query)["result"]
            for point in result["points"]:
                if str(point["id"]) in actual:
                    raise ValueError("Qdrant 조회 결과에 중복 ID가 있습니다")
                actual[str(point["id"])] = point["payload"]
            offset = result.get("next_page_offset")
            if offset is None:
                break
        if actual != chunks:
            raise ValueError("Qdrant 전체 ID·payload 대조 실패")

        for policy_id, expected in Counter(row["policy_id"] for row in chunks.values()).items():
            query = {"exact": True, "filter": {"must": [{"key": "policy_id", "match": {"value": policy_id}}]}}
            count = client.request("POST", "/points/count", query)["result"]["count"]
            if count != expected:
                raise ValueError("Qdrant 정책별 필터 건수 불일치")
        report.update(status="PASS", points=len(chunks), two_upserts_verified=True,
                      all_payloads_verified=True, policy_filters_verified=True)
        return report
    finally:
        if created:
            # 이 실행에서 직접 생성한 컬렉션만 삭제한다. 기존 컬렉션은 건드리지 않는다.
            client.request("DELETE")
            if client.request("GET", missing_ok=True) is not None:
                raise RuntimeError("임시 Qdrant 컬렉션 정리 확인 실패")
            report["temporary_collection_removed"] = True
        client.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=BASE_DIR / "data/releases/v1.4.0-candidate")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--collection", default="lifepolicy_review_candidate_20261106")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"checked_at": datetime.now(KST).isoformat(), "qdrant": {"status": "NOT_TESTED"}}
    try:
        _, report["offline"], _, chunks = validate_package(args.package.resolve())
        if args.apply:
            settings = load_db_settings(BASE_DIR)
            url = args.url or settings.get("QDRANT_URL")
            if not url or not settings.get("QDRANT_API_KEY"):
                raise ValueError("Qdrant URL·API 키 설정이 필요합니다")
            client = QdrantHttp(url, settings["QDRANT_API_KEY"], args.collection)
            report["qdrant"] = verify_payloads(client, chunks)
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        report["status"] = "FAILED"
        report["error"] = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
    else:
        report["status"] = "PASS"
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
