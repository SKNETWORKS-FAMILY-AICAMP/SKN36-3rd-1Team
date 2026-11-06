# -*- coding: utf-8 -*-
"""
data/processed/youthcenter_policies.jsonl의 local_raw_path를 실제 원문 파일로 교정한다.

배경 (2026-11-06):
  수집기(collectors/api/youthcenter_collector.py)가 모든 레코드의 원본 경로를
  data/raw/youthcenter/items/{plcyNo}.json으로 기록했지만, items/ 파일은 상위 500개와
  주요 정책 4개만 저장되어 3,861건 중 3,358건이 없는 파일을 가리켰다.
  원문은 전부 API 응답 페이지(data/raw/youthcenter/api/page_*.json)에 있으므로
  각 레코드가 들어 있는 페이지 파일을 원본 경로로 기록한다.
  (수집기도 같은 방식으로 수정됨 — 다음 수집부터는 이 스크립트가 필요 없다)

local_raw_path 외의 필드는 바꾸지 않는다.

사용법:
  python scripts/fix_youthcenter_raw_paths.py
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
API_DIR = BASE_DIR / "data" / "raw" / "youthcenter" / "api"
PROCESSED_PATH = BASE_DIR / "data" / "processed" / "youthcenter_policies.jsonl"


def build_page_index() -> dict:
    """정책번호 → 처음 등장한 API 응답 페이지 경로 (수집기의 처리 순서와 같게 페이지 번호순)."""
    index = {}
    for page in sorted(API_DIR.glob("page_*.json")):
        data = json.loads(page.read_text(encoding="utf-8"))
        for item in data.get("result", {}).get("youthPolicyList", []):
            pno = str(item.get("plcyNo", "")).strip()
            if pno:
                index.setdefault(pno, page.relative_to(BASE_DIR).as_posix())
    return index


def main() -> None:
    index = build_page_index()
    records = [json.loads(line) for line in PROCESSED_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    missing = [r["plcyNo"] for r in records if r["plcyNo"] not in index]
    assert not missing, f"API 응답 페이지에서 찾지 못한 정책: {missing[:5]} 외 {len(missing)}건"

    changed = 0
    for r in records:
        new_path = index[r["plcyNo"]]
        if r.get("local_raw_path") != new_path:
            r["local_raw_path"] = new_path
            changed += 1

    with open(PROCESSED_PATH, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(records)}건 중 {changed}건 local_raw_path 교정 (API 응답 페이지 {len(set(index.values()))}개)")


if __name__ == "__main__":
    main()
