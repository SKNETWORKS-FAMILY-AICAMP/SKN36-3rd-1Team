import os
import sys
import json
import hashlib
import datetime
from pathlib import Path
import requests
import urllib3

urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
METADATA_DIR = BASE_DIR / "data" / "metadata"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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

manifest = []
session = requests.Session()

print("=== Starting Official Document Downloads ===")
for item in DOWNLOAD_TARGETS:
    provider = item["provider"]
    dest_dir = RAW_DIR / provider
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / item["file_name"]

    req_headers = dict(HEADERS)
    if "referer" in item:
        req_headers["Referer"] = item["referer"]

    download_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status_entry = {
        "raw_id": item["doc_id"],
        "title": item["title"],
        "provider": provider,
        "type": item["type"],
        "url": item["url"],
        "downloaded_at": download_time,
        "http_status": None,
        "success": False,
        "file_path": str(dest_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "file_size_bytes": 0,
        "sha256": None,
        "error_message": None,
    }

    try:
        r = session.get(item["url"], headers=req_headers, timeout=15, verify=False)
        status_entry["http_status"] = r.status_code
        if r.status_code == 200:
            content = r.content
            with open(dest_path, "wb") as f:
                f.write(content)
            h = hashlib.sha256(content).hexdigest()
            status_entry["success"] = True
            status_entry["file_size_bytes"] = len(content)
            status_entry["sha256"] = h
            print(f"[SUCCESS] {item['doc_id']} ({item['file_name']}) - {len(content)} bytes - SHA256: {h[:12]}...")
        else:
            status_entry["error_message"] = f"HTTP {r.status_code}"
            print(f"[FAIL] {item['doc_id']} ({item['file_name']}) - HTTP {r.status_code}")
    except Exception as e:
        status_entry["error_message"] = str(e)
        print(f"[ERROR] {item['doc_id']} ({item['file_name']}) - {e}")

    manifest.append(status_entry)

# Write download manifest
manifest_path = METADATA_DIR / "download_manifest.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print(f"\nManifest written to {manifest_path}")
print(f"Total targets: {len(manifest)}, Succeeded: {sum(1 for m in manifest if m['success'])}, Failed: {sum(1 for m in manifest if not m['success'])}")
