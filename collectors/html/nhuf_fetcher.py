"""주택도시기금(nhuf.molit.go.kr) 상품 안내 페이지 원본 수집기.

- robots.txt: Allow: / (2026-11-04 확인, docs/DATA_COLLECTION_REPORT.md 기록)
- 단건 순차 요청 + 요청 간 지연. 기존 원본 파일은 덮어쓰지 않는다(파일명이 다르면 새로 저장).
- 저장 후 <title>을 검사하여 기대 상품명이 포함되지 않으면 실패로 기록한다.

실행: python collectors/html/nhuf_fetcher.py
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw" / "nhuf"
MANIFEST = BASE_DIR / "data" / "metadata" / "nhuf_fetch_manifest.json"
KST = timezone(timedelta(hours=9))
HEADERS = {"User-Agent": "LifePolicyAI-academic-project/0.3 (SKN36 bootcamp)"}

# (파일명, URL, 기대 title 키워드 목록)
TARGETS = [
    ("nhuf_youth_monthly_rent_deposit_guide.html",
     "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020701.jsp",
     ["대출안내", "청년전용 보증부월세대출"]),
    ("nhuf_youth_monthly_rent_deposit_process.html",
     "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020702.jsp",
     ["청년전용 보증부월세대출"]),
    ("nhuf_monthly_rent_process.html",
     "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020202.jsp",
     ["주거안정월세대출"]),
]


def fetch_all() -> list[dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for fname, url, keywords in TARGETS:
        entry = {"file_name": fname, "url": url, "fetched_at": datetime.now(KST).isoformat(),
                 "http_status": None, "title": None, "title_ok": False, "sha256": None,
                 "local_raw_path": f"data/raw/nhuf/{fname}", "error": None}
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            entry["http_status"] = r.status_code
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.title.get_text(strip=True) if soup.title else ""
                entry["title"] = title
                entry["title_ok"] = all(k in title for k in keywords)
                if entry["title_ok"]:
                    dest = RAW_DIR / fname
                    if dest.exists():
                        entry["error"] = "file_exists_not_overwritten"
                    else:
                        dest.write_text(r.text, encoding="utf-8")
                    entry["sha256"] = hashlib.sha256(r.text.encode("utf-8")).hexdigest()
                else:
                    entry["error"] = "unexpected_page_title"
            else:
                entry["error"] = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            entry["error"] = type(e).__name__
        results.append(entry)
        print(f"[{entry['http_status']}] title_ok={entry['title_ok']} {fname} :: {entry['title']} {entry['error'] or ''}")
        time.sleep(1.5)
    MANIFEST.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    fetch_all()

