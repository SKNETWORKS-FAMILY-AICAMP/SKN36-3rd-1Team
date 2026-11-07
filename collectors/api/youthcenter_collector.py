"""온통청년 청년정책 Open API v2 수집 및 정제 모듈 (youthcenter_collector.py).

근거:
  - 온통청년 공식 API 명세: https://www.youthcenter.go.kr/go/ythip/getPlcy
  - 공식 API 코드정의서: https://www.youthcenter.go.kr/downloadform/API%EC%BD%94%EB%93%9C%EC%A0%95%EB%B3%B4.xlsx

요청 파라미터:
  - apiKeyNm: YOUTH_POLICY_API_KEY
  - pageNum, pageSize
  - pageType (1: 목록, 2: 상세)
  - plcyNo (상세 조회 시 필수)
  - rtnType (json)
  - zipCd, lclsfNm, mclsfNm

코드 매핑:
  - mrgSttsCd (혼인상태): 0055001(기혼), 0055002(미혼), 0055003(제한없음)
  - earnCndSeCd (소득구분): 0043001(무관), 0043002(연소득), 0043003(기타)
  - jobCd (취업요건): 0013001(재직자) ~ 0013010(제한없음)
  - schoolCd (학력요건): 0049001(고졸 미만) ~ 0049010(제한없음)
  - plcyMajorCd (전공요건): 0011001(인문계열) ~ 0011009(제한없음)
  - sbizCd (특화요건): 0014001(중소기업) ~ 0014010(제한없음)
  - pvsnInstGroupCd (제공기관그룹): 0054001(중앙부처), 0054002(지자체)
  - plcyPvsnMthdCd (제공방법): 0042001(인프라 구축) ~ 0042013(기타)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RAW_ROOT, get_logger, load_env_value, now_kst, write_run_log  # noqa: E402
from collectors.preprocessing.application_urls import application_url_or_none

PROCESSED_ROOT = BASE_DIR / "data" / "processed"
METADATA_ROOT = BASE_DIR / "data" / "metadata"
OUT_RAW = RAW_ROOT / "youthcenter"
OUT_PROCESSED_JSONL = PROCESSED_ROOT / "youthcenter_policies.jsonl"
OUT_CODE_JSON = METADATA_ROOT / "youthcenter_code_definitions.json"

DEFAULT_API_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"

ZIP_CODE_TO_REGION = {
    "11": "서울특별시",
    "26": "부산광역시",
    "27": "대구광역시",
    "28": "인천광역시",
    "29": "광주광역시",
    "30": "대전광역시",
    "31": "울산광역시",
    "36": "세종특별자치시",
    "41": "경기도",
    "42": "강원특별자치도",
    "51": "강원특별자치도",
    "43": "충청북도",
    "44": "충청남도",
    "45": "전북특별자치도",
    "52": "전북특별자치도",
    "46": "전라남도",
    "47": "경상북도",
    "48": "경상남도",
    "50": "제주특별자치도",
}


def load_code_definitions() -> Dict[str, Dict[str, Any]]:
    """코드정의서 JSON을 로드하거나 기본 사전을 반환."""
    if OUT_CODE_JSON.exists():
        try:
            return json.loads(OUT_CODE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def parse_comma_codes(raw_val: Any, code_map: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """콤마로 구분된 다중 코드값을 원본 코드 리스트와 한글 라벨 리스트로 분리."""
    if not raw_val or str(raw_val).strip() in ("", "null", "None"):
        return [], []
    codes = [c.strip() for c in str(raw_val).split(",") if c.strip()]
    labels = [code_map.get(c, f"미등록코드({c})") for c in codes]
    return codes, labels


def map_zip_codes_to_regions(zip_cd_str: Any) -> List[str]:
    """5자리 또는 2자리 법정동/우편번호 목록을 광역시·도 명칭으로 변환."""
    if not zip_cd_str or str(zip_cd_str).strip() in ("", "null"):
        return ["전국"]
    raw_zips = [z.strip() for z in str(zip_cd_str).split(",") if z.strip()]
    regions: List[str] = []
    for z in raw_zips:
        prefix = z[:2]
        reg_name = ZIP_CODE_TO_REGION.get(prefix)
        if reg_name and reg_name not in regions:
            regions.append(reg_name)
    if len(regions) >= 15:
        return ["전국"]
    return regions if regions else ["전국"]


def normalize_youthcenter_item(
    item: Dict[str, Any],
    code_defs: Dict[str, Dict[str, Any]],
    policy_id: Optional[str] = None,
    document_id: Optional[str] = None,
    raw_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    온통청년 단일 정책 원본 레코드를 코드정의서 기준으로 정규화하고
    조건 검토 플래그(review_status) 및 구조화 필드를 생성한다.

    raw_path: 이 레코드의 원문이 실제로 저장된 파일 경로. 주지 않으면
    items/{plcyNo}.json으로 기록하므로, 호출하는 쪽이 그 파일을 저장해야 한다.
    """
    pno = str(item.get("plcyNo", "")).strip()
    pol_id = policy_id or f"POL_YC_{pno}"
    doc_id = document_id or f"DOC_YC_{pno}"

    # 코드 딕셔너리 추출
    mrg_codes = code_defs.get("mrgSttsCd", {}).get("items", {})
    earn_codes = code_defs.get("earnCndSeCd", {}).get("items", {})
    job_codes = code_defs.get("jobCd", {}).get("items", {})
    school_codes = code_defs.get("schoolCd", {}).get("items", {})
    major_codes = code_defs.get("plcyMajorCd", {}).get("items", {})
    sbiz_codes = code_defs.get("sbizCd", {}).get("items", {})
    pvsn_group_codes = code_defs.get("pvsnInstGroupCd", {}).get("items", {})
    pvsn_mthd_codes = code_defs.get("plcyPvsnMthdCd", {}).get("items", {})

    # 1. 기본 정보
    plcy_nm = (item.get("plcyNm") or "").strip()
    expln = (item.get("plcyExplnCn") or "").strip()
    sprt_cn = (item.get("plcySprtCn") or "").strip()
    lclsf = (item.get("lclsfNm") or "").strip()
    mclsf = (item.get("mclsfNm") or "").strip()
    category = f"{lclsf} > {mclsf}".strip(" >") or "청년지원"

    sprvsn = (item.get("sprvsnInstCdNm") or "").strip()
    oper = (item.get("operInstCdNm") or "").strip()
    agency = f"{sprvsn} / {oper}".strip(" /") if oper and oper != sprvsn else (sprvsn or oper or "관계부처/지자체")

    # 2. 연령 조건 및 검토 플래그
    min_age_raw = item.get("sprtTrgtMinAge")
    max_age_raw = item.get("sprtTrgtMaxAge")
    age_lmt_yn = str(item.get("sprtTrgtAgeLmtYn") or "").strip().upper()

    min_age = int(min_age_raw) if str(min_age_raw).isdigit() else None
    max_age = int(max_age_raw) if str(max_age_raw).isdigit() else None

    age_review_status = "정상"
    effective_age_limit = True

    if min_age == 0 and max_age == 0:
        if age_lmt_yn == "N":
            # 연령제한없음: min/max는 None으로 정규화하고 age_limit는 False로 설정
            min_age = None
            max_age = None
            effective_age_limit = False
            age_review_status = "정상 (연령제한없음: 원문 0~0세, 제한여부 N 정규화)"
        else:
            # 연령제한여부가 Y이거나 불명확한 0~0세는 입력오류 / 검토대상
            min_age = None
            max_age = None
            effective_age_limit = True
            age_review_status = "검토대상: 최소/최대연령 0세 표기 (입력오류, 공고문 확인 필요)"
    elif min_age is not None and max_age is not None and min_age > max_age:
        age_review_status = "검토대상: 최소연령이 최대연령보다 큼 (모순 데이터)"
    elif min_age is None and max_age is None:
        if age_lmt_yn == "N":
            effective_age_limit = False
            age_review_status = "정상 (연령제한없음)"
        else:
            age_review_status = "검토대상: 연령 필드 누락"
    elif age_lmt_yn == "N":
        effective_age_limit = False

    if min_age is None and max_age is None:
        age_desc = "제한없음" if not effective_age_limit else "미상 (원문 0~0세 표기 오류, 공고문 확인 필요)"
    elif min_age is not None and max_age is not None:
        age_desc = f"만 {min_age}~{max_age}세"
    elif min_age is not None:
        age_desc = f"만 {min_age}세 이상"
    else:
        age_desc = f"만 {max_age}세 이하"

    # 3. 소득 조건 및 검토 플래그
    earn_cd = str(item.get("earnCndSeCd", "")).strip()
    earn_label = earn_codes.get(earn_cd, f"미등록코드({earn_cd})" if earn_cd else "미확인")
    earn_min = item.get("earnMinAmt")
    earn_max = item.get("earnMaxAmt")
    earn_etc = (item.get("earnEtcCn") or "").strip()

    income_review_status = "정상"
    if earn_cd == "0043002" and (not earn_max or str(earn_max).strip() == "0"):
        income_review_status = "검토대상: 연소득 조건이나 최대금액 0원 표기 (기타소득설명 참조)"
    elif not earn_cd:
        income_review_status = "검토대상: 소득조건구분코드 누락"

    # 4. 혼인 상태
    mrg_cd = str(item.get("mrgSttsCd", "")).strip()
    mrg_label = mrg_codes.get(mrg_cd, f"미등록코드({mrg_cd})" if mrg_cd else "미확인")
    marriage_review_status = "정상" if mrg_cd in mrg_codes else "검토대상: 혼인상태코드 미등록/누락"

    # 5. 취업, 학력, 전공, 특화
    job_raw_codes, job_labels = parse_comma_codes(item.get("jobCd"), job_codes)
    school_raw_codes, school_labels = parse_comma_codes(item.get("schoolCd"), school_codes)
    major_raw_codes, major_labels = parse_comma_codes(item.get("plcyMajorCd"), major_codes)
    sbiz_raw_codes, sbiz_labels = parse_comma_codes(item.get("sbizCd"), sbiz_codes)

    pvsn_group_cd = str(item.get("pvsnInstGroupCd", "")).strip()
    pvsn_group_label = pvsn_group_codes.get(pvsn_group_cd, pvsn_group_cd)
    pvsn_mthd_cd = str(item.get("plcyPvsnMthdCd", "")).strip()
    pvsn_mthd_label = pvsn_mthd_codes.get(pvsn_mthd_cd, pvsn_mthd_cd)

    # 6. 지역 (zipCd)
    zip_cd_raw = (item.get("zipCd") or "").strip()
    regions = map_zip_codes_to_regions(zip_cd_raw)
    region_review_status = "정상" if zip_cd_raw else "검토대상: zipCd 누락(전국 간주)"

    # 7. 신청 및 접수 정보
    aply_ymd = (item.get("aplyYmd") or "").strip()
    biz_bgng = (item.get("bizPrdBgngYmd") or "").strip()
    biz_end = (item.get("bizPrdEndYmd") or "").strip()
    aply_method = (item.get("plcyAplyMthdCn") or item.get("plcyAplyMtdCn") or "").strip()
    sbmsn_dcmnt = (item.get("sbmsnDcmntCn") or "").strip()
    add_aply = (item.get("addAplyQlfcCndCn") or "").strip()
    ptcp_prp = (item.get("ptcpPrpTrgtCn") or "").strip()
    source_url = f"https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/{pno}"
    # 참고URL은 신청URL의 대체값이 아니다. 원천 신청 필드만 검토한다.
    raw_aply_url = (item.get("aplyUrlAddr") or "").strip()
    aply_url = application_url_or_none(raw_aply_url, source_url)
    last_mdfcn_dt = (item.get("lastMdfcnDt") or "").strip()

    # RAG 청킹용 본문 텍스트 생성
    sections = [
        f"[정책명] {plcy_nm} (정책번호: {pno})",
        f"[주관/운영기관] {agency} (기관구분: {pvsn_group_label or '-'})",
        f"[정책분류] {category} (제공방법: {pvsn_mthd_label or '-'})",
        f"[정책개요(설명)]\n{expln}" if expln else "",
        f"[지원내용]\n{sprt_cn}" if sprt_cn else "",
        f"[지원대상 및 자격요건]\n"
        f"- 대상연령: {age_desc} (제한여부: {age_lmt_yn or '-'})\n"
        f"- 혼인상태: {mrg_label}\n"
        f"- 소득요건: {earn_label}" + (f" ({earn_min}원 ~ {earn_max}원)" if earn_max else "") + (f" / {earn_etc}" if earn_etc else "") + "\n"
        f"- 취업상태: " + (", ".join(job_labels) if job_labels else "제한없음") + "\n"
        f"- 학력요건: " + (", ".join(school_labels) if school_labels else "제한없음") + "\n"
        f"- 전공요건: " + (", ".join(major_labels) if major_labels else "제한없음") + "\n"
        f"- 특화요건: " + (", ".join(sbiz_labels) if sbiz_labels else "-"),
        f"[추가 신청자격조건]\n{add_aply}" if add_aply else "",
        f"[참여 제한대상]\n{ptcp_prp}" if ptcp_prp else "",
        f"[신청기간 및 신청방법]\n- 신청기간: {aply_ymd or f'{biz_bgng} ~ {biz_end}'}\n- 신청방법: {aply_method or '공고문 참조'}",
        f"[제출서류]\n{sbmsn_dcmnt}" if sbmsn_dcmnt else "",
        f"[신청 URL] {aply_url}" if aply_url else "",
        f"[출처 URL] {source_url}",
        f"[최종수정일시] {last_mdfcn_dt}",
    ]
    full_text = "\n\n".join(s for s in sections if s)

    return {
        "policy_id": pol_id,
        "document_id": doc_id,
        "official_id": pno,
        "plcyNo": pno,
        "policy_name": plcy_nm,
        "plcyNm": plcy_nm,
        "agency": agency,
        "category": category,
        "regions": regions,
        "region": ", ".join(regions),
        "target_groups": ["청년"],
        "target_group": "청년",
        "description": expln,
        "plcyExplnCn": expln,
        "benefit": sprt_cn,
        "plcySprtCn": sprt_cn,
        "eligibility": f"연령: {age_desc} / 혼인: {mrg_label} / 소득: {earn_label} / 취업: {', '.join(job_labels) if job_labels else '제한없음'}",
        "requirements": {
            "min_age": min_age,
            "max_age": max_age,
            "age_limit_yn": age_lmt_yn,
            "age_limit": effective_age_limit,
            "age_review_needed": "검토대상" in age_review_status,
            "marriage_status_code": mrg_cd,
            "marriage_status_label": mrg_label,
            "income_condition_code": earn_cd,
            "income_condition_label": earn_label,
            "income_min_amount": earn_min,
            "income_max_amount": earn_max,
            "income_etc_details": earn_etc,
            "job_codes": job_raw_codes,
            "job_labels": job_labels,
            "school_codes": school_raw_codes,
            "school_labels": school_labels,
            "major_codes": major_raw_codes,
            "major_labels": major_labels,
            "special_codes": sbiz_raw_codes,
            "special_labels": sbiz_labels,
            "additional_qualification": add_aply,
            "excluded_participants": ptcp_prp,
        },
        "review_flags": {
            "age_review_status": age_review_status,
            "income_review_status": income_review_status,
            "marriage_review_status": marriage_review_status,
            "region_review_status": region_review_status,
            "needs_human_review": any("검토대상" in s for s in [age_review_status, income_review_status, marriage_review_status, region_review_status]),
        },
        "application_period": aply_ymd or f"{biz_bgng} ~ {biz_end}".strip(" ~") or "상시/공고참조",
        "aplyYmd": aply_ymd,
        "application_method": aply_method or "온라인/방문 신청",
        "plcyAplyMthdCn": aply_method,
        "required_documents": [line.strip("- ·*") for line in sbmsn_dcmnt.splitlines() if line.strip()][:8] if sbmsn_dcmnt else [],
        "sbmsnDcmntCn": sbmsn_dcmnt,
        "official_apply_url": aply_url,
        "aplyUrlAddr": raw_aply_url,
        "source_url": source_url,
        "lastMdfcnDt": last_mdfcn_dt,
        "local_raw_path": raw_path or f"data/raw/youthcenter/items/{pno}.json",
        "full_text": full_text,
        "text": full_text,
        "content": full_text,
        "content_hash": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        "verification_status": "verified" if pno in {"20260710005400113256", "20261004005400113874", "20250123005400110393", "20241231005400100180"} else "extracted",
        "retrieved_at": now_kst(),
        "collected_at": now_kst(),
        "raw_attributes": item,
    }


def fetch_youthcenter_api(
    page_type: str = "1",
    page_num: int = 1,
    page_size: int = 100,
    plcy_no: Optional[str] = None,
    key: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """온통청년 API 호출 (pageType=1 목록 또는 pageType=2 상세)."""
    api_key = key or load_env_value("YOUTH_POLICY_API_KEY")
    if not api_key:
        raise ValueError("YOUTH_POLICY_API_KEY 가 설정되지 않았습니다.")

    params = {
        "apiKeyNm": api_key.strip(),
        "pageType": str(page_type),
        "rtnType": "json",
    }
    if page_type == "1":
        params["pageNum"] = page_num
        params["pageSize"] = page_size
    elif page_type == "2":
        if not plcy_no:
            raise ValueError("pageType=2(상세조회)는 plcyNo가 필수입니다.")
        params["plcyNo"] = plcy_no

    headers = {"User-Agent": "LifePolicyAI-YouthCenterCollector/2.0"}
    r = requests.get(DEFAULT_API_URL, params=params, headers=headers, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} error from YouthCenter API: {r.text[:200]}")
    return r.json()


def run_collection(
    page_size: int = 100,
    max_pages: Optional[int] = None,
    use_cache: bool = True,
    fetch_details: bool = False,
) -> Dict[str, Any]:
    """온통청년 정책 전수 수집, 코드정의서 적용 정제 및 JSONL 산출물 생성."""
    key = load_env_value("YOUTH_POLICY_API_KEY")
    if not key:
        raise ValueError("YOUTH_POLICY_API_KEY 가 .env 에 설정되지 않았습니다.")
    logger = get_logger("youthcenter", [key])

    summary: Dict[str, Any] = {
        "source": "온통청년 청년정책 Open API",
        "endpoint": DEFAULT_API_URL,
        "started_at": now_kst(),
        "total_pages_fetched": 0,
        "total_records_collected": 0,
        "unique_plcy_no_count": 0,
        "duplicate_count": 0,
        "detail_fetch_success_count": 0,
        "refined_jsonl_written": 0,
        "field_missing_rates": {},
        "code_conversion_rates": {},
        "review_flag_counts": {},
        "errors": [],
    }

    raw_api_dir = OUT_RAW / "api"
    raw_items_dir = OUT_RAW / "items"
    raw_api_dir.mkdir(parents=True, exist_ok=True)
    raw_items_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)

    code_defs = load_code_definitions()
    logger.info("Loaded %d code definition categories", len(code_defs))

    # 1. 목록 수집 (pageType=1)
    all_raw_items: List[Dict[str, Any]] = []
    # all_raw_items와 같은 순서로, 각 레코드를 실제로 읽어 온 API 응답 페이지 파일.
    # 같은 정책번호가 여러 페이지에 나와도 레코드마다 자기 페이지를 원본으로 기록한다.
    # (items/ 개별 파일은 일부만 저장되므로 원본 경로로 쓰지 않는다)
    raw_item_pages: List[str] = []
    seen_plcy_nos: Set[str] = set()
    page = 1

    while not (max_pages and page > max_pages):
        cache_path = raw_api_dir / f"page_{page:04d}.json"
        data: Dict[str, Any]
        if use_cache and cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            try:
                data = fetch_youthcenter_api(page_type="1", page_num=page, page_size=page_size, key=key, logger=logger)
                cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(0.3)
            except Exception as e:
                summary["errors"].append(f"Page {page} error: {e}")
                logger.error("Page %d fetch failed: %s", page, e)
                break

        res_obj = data.get("result", {})
        items = res_obj.get("youthPolicyList", [])
        if not items:
            break

        all_raw_items.extend(items)
        raw_item_pages.extend([cache_path.relative_to(BASE_DIR).as_posix()] * len(items))
        summary["total_pages_fetched"] += 1

        for it in items:
            pno = str(it.get("plcyNo", "")).strip()
            if pno:
                if pno in seen_plcy_nos:
                    summary["duplicate_count"] += 1
                seen_plcy_nos.add(pno)

        logger.info("Page %d: %d items (cumulative: %d)", page, len(items), len(all_raw_items))
        if len(items) < page_size:
            break
        page += 1

    summary["total_records_collected"] = len(all_raw_items)
    summary["unique_plcy_no_count"] = len(seen_plcy_nos)

    # 2. 필요 시 개별 상세 조회 (pageType=2) 샘플 테스트
    if fetch_details and seen_plcy_nos:
        sample_targets = list(seen_plcy_nos)[:10]
        det_ok = 0
        for pno in sample_targets:
            try:
                d_resp = fetch_youthcenter_api(page_type="2", plcy_no=pno, key=key, logger=logger)
                if d_resp.get("result", {}).get("youthPolicyList"):
                    det_ok += 1
                time.sleep(0.2)
            except Exception as e:
                summary["errors"].append(f"Detail fetch {pno}: {e}")
        summary["detail_fetch_success_count"] = det_ok

    # 3. 코드정의서 적용 정규화 & 통계 산출
    refined_records: List[Dict[str, Any]] = []
    req_fields = [
        "plcyNo", "plcyNm", "plcyExplnCn", "plcySprtCn",
        "sprtTrgtMinAge", "sprtTrgtMaxAge", "sprtTrgtAgeLmtYn",
        "earnCndSeCd", "earnMinAmt", "earnMaxAmt", "earnEtcCn",
        "mrgSttsCd", "zipCd", "jobCd", "schoolCd", "plcyMajorCd",
        "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "aplyYmd",
        "plcyAplyMthdCn", "sbmsnDcmntCn", "aplyUrlAddr", "lastMdfcnDt",
    ]
    missing_counts = {f: 0 for f in req_fields}
    code_field_stats = {
        "mrgSttsCd": {"total": 0, "converted": 0},
        "earnCndSeCd": {"total": 0, "converted": 0},
        "jobCd": {"total": 0, "converted": 0},
        "schoolCd": {"total": 0, "converted": 0},
        "plcyMajorCd": {"total": 0, "converted": 0},
        "sbizCd": {"total": 0, "converted": 0},
        "pvsnInstGroupCd": {"total": 0, "converted": 0},
        "plcyPvsnMthdCd": {"total": 0, "converted": 0},
    }
    review_counts = {
        "age_review_needed": 0,
        "income_review_needed": 0,
        "marriage_review_needed": 0,
        "region_review_needed": 0,
        "total_needs_review": 0,
    }

    total_n = len(all_raw_items)

    for it, raw_page in zip(all_raw_items, raw_item_pages):
        pno = str(it.get("plcyNo", "")).strip()

        # 결측률 집계
        for f in req_fields:
            v = it.get(f)
            if v is None or str(v).strip() in ("", "null", "None"):
                missing_counts[f] += 1

        # 코드 변환율 집계
        for cf in code_field_stats.keys():
            raw_v = it.get(cf)
            if raw_v and str(raw_v).strip() not in ("", "null"):
                code_field_stats[cf]["total"] += 1
                c_items = code_defs.get(cf, {}).get("items", {})
                parts = [p.strip() for p in str(raw_v).split(",") if p.strip()]
                if parts and all(p in c_items for p in parts):
                    code_field_stats[cf]["converted"] += 1

        # 정규화 레코드 생성
        norm_rec = normalize_youthcenter_item(it, code_defs, raw_path=raw_page)
        flags = norm_rec["review_flags"]
        if "검토대상" in flags["age_review_status"]:
            review_counts["age_review_needed"] += 1
        if "검토대상" in flags["income_review_status"]:
            review_counts["income_review_needed"] += 1
        if "검토대상" in flags["marriage_review_status"]:
            review_counts["marriage_review_needed"] += 1
        if "검토대상" in flags["region_review_status"]:
            review_counts["region_review_needed"] += 1
        if flags["needs_human_review"]:
            review_counts["total_needs_review"] += 1

        refined_records.append(norm_rec)

        # 상위 500개 및 주요 정책 개별 원본 JSON 파일 보존
        if len(refined_records) <= 500 or pno in {"20260710005400113256", "20261004005400113874", "20250123005400110393", "20241231005400100180"}:
            (raw_items_dir / f"{pno}.json").write_text(json.dumps(it, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. JSONL 파일 저장
    with open(OUT_PROCESSED_JSONL, "w", encoding="utf-8") as f:
        for r in refined_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary["refined_jsonl_written"] = len(refined_records)
    summary["field_missing_rates"] = {
        f: {
            "missing_count": missing_counts[f],
            "missing_rate_pct": round((missing_counts[f] / total_n) * 100, 2) if total_n > 0 else 0,
        }
        for f in req_fields
    }
    summary["code_conversion_rates"] = {
        cf: {
            "total_present": code_field_stats[cf]["total"],
            "converted_count": code_field_stats[cf]["converted"],
            "conversion_rate_pct": round((code_field_stats[cf]["converted"] / code_field_stats[cf]["total"]) * 100, 2) if code_field_stats[cf]["total"] > 0 else 0,
        }
        for cf in code_field_stats.keys()
    }
    summary["review_flag_counts"] = review_counts

    summary["finished_at"] = now_kst()
    write_run_log("youthcenter", summary)

    logger.info("YouthCenter collection complete: %d records written to %s", len(refined_records), OUT_PROCESSED_JSONL)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="온통청년 API 수집 및 정제 모듈")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--fetch-details", action="store_true")
    args = parser.parse_args()

    res = run_collection(
        page_size=args.page_size,
        max_pages=args.max_pages,
        use_cache=not args.no_cache,
        fetch_details=args.fetch_details,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
