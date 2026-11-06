# -*- coding: utf-8 -*-
"""
data/processed/youthcenter_policies.jsonl의 local_raw_path를 실제 원문 파일로 교정한다.

배경 (2026-11-06):
  수집기(collectors/api/youthcenter_collector.py)가 모든 레코드의 원본 경로를
  data/raw/youthcenter/items/{plcyNo}.json으로 기록했지만, items/ 파일은 상위 500개와
  주요 정책 4개만 저장되어 3,861건 중 3,358건이 없는 파일을 가리켰다.
  원문은 전부 API 응답 페이지(data/raw/youthcenter/api/page_*.json)에 있으므로
  각 레코드를 실제로 읽어 온 페이지 파일을 원본 경로로 기록한다.
  (수집기도 같은 방식으로 수정됨 — 다음 수집부터는 이 스크립트가 필요 없다)

수집기는 페이지 번호순으로 읽은 원본 항목을 같은 순서로 정제해 기록한다. 그래서 정제 레코드
i번째는 페이지 순서상 i번째 원본 항목에서 나온 것이다. 정책번호로 첫 페이지를 찾는 대신 이 위치
대응을 쓰므로, 같은 정책번호가 여러 페이지에 나와도 레코드마다 자기 페이지를 가리킨다.

local_raw_path 외의 필드는 바꾸지 않는다.

사용법:
  python scripts/fix_youthcenter_raw_paths.py
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
API_DIR = BASE_DIR / "data" / "raw" / "youthcenter" / "api"
PROCESSED_PATH = BASE_DIR / "data" / "processed" / "youthcenter_policies.jsonl"


def read_raw_sequence() -> list:
    """수집기가 읽은 순서대로 (정책번호, 페이지 경로) 목록을 만든다."""
    sequence = []
    for page in sorted(API_DIR.glob("page_*.json")):
        data = json.loads(page.read_text(encoding="utf-8"))
        page_path = page.relative_to(BASE_DIR).as_posix()
        for item in data.get("result", {}).get("youthPolicyList", []):
            sequence.append((str(item.get("plcyNo", "")).strip(), page_path))
    return sequence


def main() -> None:
    sequence = read_raw_sequence()
    records = [json.loads(line) for line in PROCESSED_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    # 위치 대응이 성립하는지 확인: 건수와 정책번호 순서가 원본 페이지와 완전히 같아야 한다
    assert len(records) == len(sequence), f"정제 {len(records)}건 ≠ 원본 페이지 항목 {len(sequence)}건"
    mismatch = [i for i, (r, (pno, _)) in enumerate(zip(records, sequence)) if r["plcyNo"] != pno]
    assert not mismatch, f"정책번호 순서 불일치 {len(mismatch)}건 (첫 위치 {mismatch[0]})"

    changed = 0
    for r, (_, page_path) in zip(records, sequence):
        if r.get("local_raw_path") != page_path:
            r["local_raw_path"] = page_path
            changed += 1

    with open(PROCESSED_PATH, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(records)}건 중 {changed}건 local_raw_path 교정 (API 응답 페이지 {len({p for _, p in sequence})}개)")


if __name__ == "__main__":
    main()
