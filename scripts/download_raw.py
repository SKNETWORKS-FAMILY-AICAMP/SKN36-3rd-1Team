"""공식 원문(LH 공고·주택도시기금·온통청년 안내 등) 원본 다운로드.

- 인증서를 검증하고, 프로젝트를 밝히는 User-Agent로 요청 사이 1초씩 쉰다.
- 받은 내용이 예상 형식(PDF/HTML)이 아니면 저장하지 않는다(오류 HTML을 .pdf로 저장하는 문제 방지).
- 이미 있는 원본 파일은 덮어쓰지 않는다(--force로만 덮어씀).
- 다운로드 기록은 기본으로 data/raw/download_manifest.latest.json(Git 제외)에 쓰고,
  추적 중인 data/metadata/download_manifest.json은 --update-manifest를 줄 때만 바꾼다.
"""
import argparse
import json
import time
import hashlib
import datetime
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
METADATA_DIR = BASE_DIR / "data" / "metadata"

HEADERS = {
    # 브라우저로 위장하지 않고 학습용 프로젝트임을 밝힌다(collectors/api/_common.py와 같은 값).
    "User-Agent": "LifePolicyAI-academic-project/0.3 (SKN36 bootcamp)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

DOWNLOAD_TARGETS = [
    # 1. LH 청약플러스
    {
        "provider": "lh",
        "doc_id": "RAW_LH_001",
        "title": "[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고 웹 상세",
        "url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
        "file_name": "lh_notice_2015122300019712_seoul_newlywed.html",
        "type": "html",
        "referer": "https://apply.lh.or.kr/",
    },
    {
        "provider": "lh",
        "doc_id": "RAW_LH_002",
        "title": "26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf",
        "url": "https://apply.lh.or.kr/lhapply/lhFile.do?fileid=66370408",
        "file_name": "26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf",
        "type": "pdf",
        "referer": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
    },
    {
        "provider": "lh",
        "doc_id": "RAW_LH_003",
        "title": "서울지역본부신혼신생아매입임대Ⅰ신청시유의사항(정정공고).pdf",
        "url": "https://apply.lh.or.kr/lhapply/lhFile.do?fileid=66375944",
        "file_name": "서울지역본부신혼신생아매입임대Ⅰ신청시유의사항(정정공고).pdf",
        "type": "pdf",
        "referer": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
    },
    {
        "provider": "lh",
        "doc_id": "RAW_LH_004",
        "title": "26년신혼·신생아매입임대Ⅰ_QnA.hwp",
        "url": "https://apply.lh.or.kr/lhapply/lhFile.do?fileid=66372097",
        "file_name": "26년신혼·신생아매입임대Ⅰ_QnA.hwp",
        "type": "hwp",
        "referer": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
    },
    # 2. 주택도시기금 (NHUF)
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_001",
        "title": "기금대출 신혼부부 소득요건 합리화 관련 안내 공지 (2026-10-19 적용)",
        "url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
        "file_name": "nhuf_notice_1316_newlywed_income.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_002",
        "title": "신혼부부 소득요건 합리화 FAQ.pdf",
        "url": "https://nhuf.molit.go.kr/cms/cwboard/skin/download.jsp?id=3&fileId=1800",
        "file_name": "신혼부부_소득요건_합리화_FAQ.pdf",
        "type": "pdf",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_003",
        "title": "신혼부부전용 전세자금 대출안내 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
        "file_name": "nhuf_newlywed_jeonse_guide.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_004",
        "title": "신혼부부전용 전세자금 이용절차 및 제출서류 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020402.jsp",
        "file_name": "nhuf_newlywed_jeonse_process.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_005",
        "title": "청년전용 버팀목전세자금 대출안내 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
        "file_name": "nhuf_youth_jeonse_guide.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_006",
        "title": "청년전용 버팀목전세자금 이용절차 및 제출서류 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020302.jsp",
        "file_name": "nhuf_youth_jeonse_process.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_007",
        "title": "주거안정월세대출 대출안내 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
        "file_name": "nhuf_monthly_rent_guide.html",
        "type": "html",
    },
    {
        "provider": "nhuf",
        "doc_id": "RAW_NHUF_008",
        "title": "청년전용 보증부월세대출 대출안내 웹 상세",
        "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020303.jsp",
        "file_name": "nhuf_youth_rent_deposit_guide.html",
        "type": "html",
    },
    # 3. 고용24 (Work24)
    {
        "provider": "work24",
        "doc_id": "RAW_WORK24_001",
        "title": "국민내일배움카드 발급안내 및 가이드 웹 상세",
        "url": "https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
        "file_name": "work24_tomorrow_learning_card.html",
        "type": "html",
    },
    # 4. 온통청년 (YouthCenter)
    {
        "provider": "youthcenter",
        "doc_id": "RAW_YC_001",
        "title": "온통청년 오픈(OPEN) API 이용방법 가이드",
        "url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide",
        "file_name": "youthcenter_openapi_guide.html",
        "type": "html",
    },
    {
        "provider": "youthcenter",
        "doc_id": "RAW_YC_002",
        "title": "온통청년 오픈(OPEN) API 제공목록 및 명세서",
        "url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc",
        "file_name": "youthcenter_openapi_doc.html",
        "type": "html",
    },
]

REQUEST_DELAY_SECONDS = 1.0


def looks_like(item_type: str, content_type: str, content: bytes) -> bool:
    """응답이 기대한 형식인지. 서버가 200으로 오류 페이지를 돌려주는 경우를 거른다."""
    content_type = (content_type or "").lower()
    if item_type == "pdf":
        return content.startswith(b"%PDF") or "application/pdf" in content_type
    if item_type == "html":
        return "html" in content_type or content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
    return True


def download(item: dict, session: requests.Session, force: bool) -> dict:
    provider = item["provider"]
    dest_dir = RAW_DIR / provider
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / item["file_name"]

    req_headers = dict(HEADERS)
    if "referer" in item:
        req_headers["Referer"] = item["referer"]

    entry = {
        "raw_id": item["doc_id"],
        "title": item["title"],
        "provider": provider,
        "type": item["type"],
        "url": item["url"],
        "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "http_status": None,
        "success": False,
        "file_path": str(dest_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "file_size_bytes": 0,
        "sha256": None,
        "error_message": None,
    }
    if dest_path.exists() and not force:
        content = dest_path.read_bytes()
        entry.update(success=True, file_size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                     error_message="skipped: file exists (use --force to re-download)")
        print(f"[SKIP] {item['doc_id']} ({item['file_name']}) - 이미 있음")
        return entry

    try:
        r = session.get(item["url"], headers=req_headers, timeout=15)
        entry["http_status"] = r.status_code
        if r.status_code != 200:
            entry["error_message"] = f"HTTP {r.status_code}"
            print(f"[FAIL] {item['doc_id']} ({item['file_name']}) - HTTP {r.status_code}")
            return entry
        content = r.content
        if not looks_like(item["type"], r.headers.get("Content-Type", ""), content):
            entry["error_message"] = f"unexpected content-type: {r.headers.get('Content-Type', '')}"
            print(f"[FAIL] {item['doc_id']} ({item['file_name']}) - 예상한 {item['type']} 형식이 아님")
            return entry
        tmp = dest_path.with_name(dest_path.name + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(dest_path)
        h = hashlib.sha256(content).hexdigest()
        entry.update(success=True, file_size_bytes=len(content), sha256=h)
        print(f"[SUCCESS] {item['doc_id']} ({item['file_name']}) - {len(content)} bytes - SHA256: {h[:12]}...")
    except requests.RequestException as e:
        entry["error_message"] = type(e).__name__
        print(f"[ERROR] {item['doc_id']} ({item['file_name']}) - {type(e).__name__}")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="공식 원문 원본 다운로드")
    parser.add_argument("--force", action="store_true", help="이미 있는 원본 파일도 다시 받아 덮어쓴다")
    parser.add_argument("--update-manifest", action="store_true",
                        help="추적 중인 data/metadata/download_manifest.json을 이번 결과로 바꾼다")
    args = parser.parse_args()

    session = requests.Session()
    manifest = []
    print("=== Starting Official Document Downloads ===")
    for index, item in enumerate(DOWNLOAD_TARGETS):
        if index:
            time.sleep(REQUEST_DELAY_SECONDS)
        manifest.append(download(item, session, args.force))

    manifest_path = (METADATA_DIR / "download_manifest.json" if args.update_manifest
                     else RAW_DIR / "download_manifest.latest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print()
    print(f"Manifest written to {manifest_path}")
    print(f"Total targets: {len(manifest)}, Succeeded: {sum(1 for m in manifest if m['success'])}, "
          f"Failed: {sum(1 for m in manifest if not m['success'])}")


if __name__ == "__main__":
    main()
