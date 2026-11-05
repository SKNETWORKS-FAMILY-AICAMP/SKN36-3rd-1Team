"""한국사회보장정보원 중앙부처복지서비스 API v2.2 수집 및 정제 모듈 (bokjiro_collector.py).

근거: 한국사회보장정보원 중앙부처복지서비스 API 활용가이드 v2.2 (공공데이터포털 15090532)

설정 (환경변수 또는 .env):
  - 목록 조회 URL: WELFARE_LIST_API_URL
  - 상세 조회 URL: WELFARE_DETAIL_API_URL
  - 인증키: DATA_GO_KR_API_KEY (또는 BOKJIRO_API_KEY)

요청 프로토콜:
  - REST GET, XML 응답
  - 목록: callTp=L, pageNo, numOfRows, srchKeyCode=003
  - 상세: callTp=D, servId=<목록 결과에서 추출>

오류 코드 검증:
  - 공공데이터포털 게이트웨이 오류 (<OpenAPI_ServiceResponse>):
      * 20: 접근 거부 (SERVICE_ACCESS_DENIED_ERROR)
      * 22: 일일 서비스 요청제한 횟수 초과 (LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR)
      * 30: 등록되지 않은 서비스키 (REGISTERED_KEY_ERROR)
      * 31: 활용기간 만료 (DEADLINE_HAS_EXPIRED_ERROR)
      * 10: 잘못된 요청 파라미터 (INVALID_REQUEST_PARAMETER_ERROR)
  - 비즈니스 XML (<wantedList>, <wantedDtl>):
      * resultCode == '0': 정상 (SUCCESS)
      * 기타 코드: 비즈니스 오류 메시지 기록

저장 구조:
  - 원본 XML: data/raw/bokjiro/list/page_XXXX.xml, data/raw/bokjiro/detail/<servId>.xml
  - 정제 JSONL: data/processed/bokjiro_welfare_services.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RAW_ROOT, get_logger, load_env_value, now_kst, write_run_log  # noqa: E402

PROCESSED_ROOT = BASE_DIR / "data" / "processed"
OUT_RAW = RAW_ROOT / "bokjiro"
OUT_PROCESSED_JSONL = PROCESSED_ROOT / "bokjiro_welfare_services.jsonl"

DEFAULT_LIST_URL = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
DEFAULT_DETAIL_URL = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001"

GATEWAY_ERROR_MAPPING = {
    "10": "잘못된 요청 파라미터 (INVALID_REQUEST_PARAMETER_ERROR)",
    "11": "필수 요청 파라미터 누락 (NO_MANDATORY_REQUEST_PARAMETERS_ERROR)",
    "12": "해당 오픈API 서비스 없음/폐기 (NO_OPENAPI_SERVICE_ERROR)",
    "20": "접근 거부 (SERVICE_ACCESS_DENIED_ERROR)",
    "21": "일시적 서비스 정지 (TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR)",
    "22": "일일 서비스 요청제한 횟수 초과 (LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR)",
    "30": "등록되지 않은 서비스키 (REGISTERED_KEY_ERROR)",
    "31": "활용기간 만료 (DEADLINE_HAS_EXPIRED_ERROR)",
    "32": "등록되지 않은 IP 주소 (UNREGISTERED_IP_ERROR)",
    "99": "기타 공공데이터포털 시스템 오류",
}


class BokjiroApiError(Exception):
    def __init__(self, message: str, code: Optional[str] = None, is_quota_exceeded: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.is_quota_exceeded = is_quota_exceeded


def get_api_urls() -> Tuple[str, str]:
    list_url = os.getenv("WELFARE_LIST_API_URL") or DEFAULT_LIST_URL
    detail_url = os.getenv("WELFARE_DETAIL_API_URL") or DEFAULT_DETAIL_URL
    return list_url.strip(), detail_url.strip()


def check_xml_response(root: ET.Element, context: str = "") -> None:
    """공공데이터포털 게이트웨이 에러 및 비즈니스 resultCode 검증."""
    if root.tag == "OpenAPI_ServiceResponse":
        reason_code = root.findtext(".//returnReasonCode") or ""
        auth_msg = root.findtext(".//returnAuthMsg") or ""
        err_msg = root.findtext(".//errMsg") or ""
        desc = GATEWAY_ERROR_MAPPING.get(reason_code, f"알 수 없는 게이트웨이 오류 ({reason_code})")
        full_msg = f"[{context}] 게이트웨이 오류코드 {reason_code}: {desc} (인증메시지: {auth_msg or err_msg})"
        is_quota = (reason_code == "22")
        raise BokjiroApiError(full_msg, code=reason_code, is_quota_exceeded=is_quota)

    result_code = root.findtext("resultCode")
    result_msg = root.findtext("resultMessage")
    if result_code is not None and str(result_code).strip() not in ("0", "SUCCESS", "00"):
        raise BokjiroApiError(f"[{context}] 비즈니스 resultCode={result_code}, message={result_msg}", code=result_code)


def make_api_request(url: str, params: Dict[str, Any], logger: logging.Logger, timeout: int = 30) -> bytes:
    """REST GET 요청 실행, HTTP 상태코드 및 응답 XML 에러 전수 검증."""
    try:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": "LifePolicyAI-BokjiroCollector/2.2"},
            timeout=timeout,
        )
    except Exception as e:
        raise BokjiroApiError(f"네트워크 통신 오류: {e}")

    try:
        root = ET.fromstring(r.content)
        check_xml_response(root, context=url.split("/")[-1])
    except ET.ParseError:
        if r.status_code != 200:
            raise BokjiroApiError(f"HTTP {r.status_code} 비XML 오류 응답: {r.text[:200]}", code=str(r.status_code))
        raise BokjiroApiError("응답 XML 파싱 실패")

    if r.status_code != 200:
        raise BokjiroApiError(f"HTTP {r.status_code}: {r.text[:200]}", code=str(r.status_code))

    return r.content


def fetch_list_page(
    list_url: str,
    key: str,
    page_no: int,
    num_of_rows: int,
    logger: logging.Logger,
) -> Tuple[bytes, int, List[Dict[str, str]]]:
    """목록 조회 (callTp=L) 실행 및 파싱."""
    params = {
        "serviceKey": key,
        "callTp": "L",
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "srchKeyCode": "003",
    }
    content = make_api_request(list_url, params, logger=logger, timeout=60)
    root = ET.fromstring(content)

    total_count = int(root.findtext("totalCount") or 0)
    items: List[Dict[str, str]] = []
    for it in root.findall("servList"):
        sid = (it.findtext("servId") or "").strip()
        snm = (it.findtext("servNm") or "").strip()
        if sid:
            items.append({
                "servId": sid,
                "servNm": snm,
                "jurMnofNm": (it.findtext("jurMnofNm") or "").strip(),
                "jurOrgNm": (it.findtext("jurOrgNm") or "").strip(),
                "inqplHmpgUrl": (it.findtext("inqplHmpgUrl") or "").strip(),
                "servDgst": (it.findtext("servDgst") or "").strip(),
                "servDtlLink": (it.findtext("servDtlLink") or "").strip(),
            })
    return content, total_count, items


def fetch_detail_xml(detail_url: str, key: str, serv_id: str, logger: logging.Logger) -> bytes:
    """상세 조회 (callTp=D, servId=...) 실행 및 검증."""
    params = {
        "serviceKey": key,
        "callTp": "D",
        "servId": serv_id,
    }
    content = make_api_request(detail_url, params, logger=logger, timeout=30)
    root = ET.fromstring(content)

    res_serv_id = root.findtext("servId")
    if not res_serv_id or res_serv_id.strip() != serv_id:
        raise BokjiroApiError(f"Detail servId mismatch: expected {serv_id}, got {res_serv_id}", code="ID_MISMATCH")
    return content


def parse_detail_to_refined_record(
    xml_bytes: bytes,
    policy_id: str,
    document_id: str,
    list_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    상세 XML에서 tgtrDtlCn, slctCritCn, alwServCn, applmetList, basfrmList, baslawList 등을
    누락 없이 추출하여 정제된 JSON 레코드로 변환한다.
    """
    root = ET.fromstring(xml_bytes)

    def text_of(tag: str) -> str:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    serv_id = text_of("servId")
    serv_nm = text_of("servNm") or (list_meta.get("servNm", "") if list_meta else "")
    jur_mnof_nm = text_of("jurMnofNm") or (list_meta.get("jurMnofNm", "") if list_meta else "")
    jur_org_nm = text_of("jurOrgNm") or (list_meta.get("jurOrgNm", "") if list_meta else "")

    # 필수 상세 필드 (가이드 v2.2)
    tgtr_dtl_cn = text_of("tgtrDtlCn")        # 지원대상 상세
    slct_crit_cn = text_of("slctCritCn")      # 선정기준
    alw_serv_cn = text_of("alwServCn")        # 급여서비스(지원내용)
    wlfare_info_outl_cn = text_of("wlfareInfoOutlCn")  # 사업개요

    # 리스트형 하위 필드들
    applmet_list: List[Dict[str, str]] = []   # 신청방법 및 링크
    for node in root.findall("applmetList"):
        nm = (node.findtext("servSeDetailNm") or "").strip()
        link = (node.findtext("servSeDetailLink") or "").strip()
        if nm or link:
            applmet_list.append({"name": nm, "link": link})

    basfrm_list: List[str] = []              # 구비서류
    for node in root.findall("basfrmList"):
        doc_nm = (node.findtext("servSeDetailNm") or "").strip()
        if doc_nm and doc_nm not in basfrm_list:
            basfrm_list.append(doc_nm)

    baslaw_list: List[str] = []              # 근거법령
    for node in root.findall("baslawList"):
        law_nm = (node.findtext("servSeDetailNm") or "").strip()
        if law_nm and law_nm not in baslaw_list:
            baslaw_list.append(law_nm)

    inqpl_list: List[Dict[str, str]] = []     # 문의처
    for node in root.findall("inqplCtadrList"):
        org = (node.findtext("servSeDetailNm") or "").strip()
        tel = (node.findtext("servSeDetailLink") or "").strip()
        if org or tel:
            inqpl_list.append({"organization": org, "contact": tel})

    life_array = text_of("lifeArray")
    trgter_indvdl_array = text_of("trgterIndvdlArray")
    intrs_thema_array = text_of("intrsThemaArray")
    srv_pvsn_nm = text_of("srvPvsnNm")
    sprt_cyc_nm = text_of("sprtCycNm")
    aply_mtd_nm = text_of("aplyMtdNm")
    enfc_bgng_ymd = text_of("enfcBgngYmd")
    enfc_end_ymd = text_of("enfcEndYmd")

    def fmt_ymd(ymd: str) -> Optional[str]:
        if len(ymd) == 8 and ymd.isdigit():
            if ymd == "99991231":
                return None
            return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        return None

    source_url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={serv_id}"
    online_apply_urls = [it["link"] for it in applmet_list if it.get("link", "").startswith("http")]
    official_apply_url = online_apply_urls[0] if online_apply_urls else source_url

    agency_full = f"{jur_mnof_nm} {jur_org_nm}".strip() if jur_org_nm else jur_mnof_nm
    sections = [
        f"[서비스명] {serv_nm} (서비스ID: {serv_id})",
        f"[소관부처 및 담당기관] {agency_full}",
        f"[생애주기 / 대상특성 / 관심분야] {life_array or '-'} / {trgter_indvdl_array or '-'} / {intrs_thema_array or '-'}",
        f"[제공유형 / 지원주기 / 신청방법] {srv_pvsn_nm or '-'} / {sprt_cyc_nm or '-'} / {aply_mtd_nm or '-'}",
        f"[사업개요(요약)]\n{wlfare_info_outl_cn}" if wlfare_info_outl_cn else "",
        f"[지원대상 상세 (tgtrDtlCn)]\n{tgtr_dtl_cn}" if tgtr_dtl_cn else "",
        f"[선정기준 (slctCritCn)]\n{slct_crit_cn}" if slct_crit_cn else "",
        f"[지원내용(급여서비스, alwServCn)]\n{alw_serv_cn}" if alw_serv_cn else "",
        f"[신청방법 및 절차 (applmetList)]\n" + "\n".join(f"- {it['name']}: {it['link']}" if it['link'] else f"- {it['name']}" for it in applmet_list) if applmet_list else "",
        f"[구비서류 (basfrmList)]\n" + "\n".join(f"- {doc}" for doc in basfrm_list) if basfrm_list else "",
        f"[근거법령 (baslawList)]\n" + ", ".join(baslaw_list) if baslaw_list else "",
        f"[문의처]\n" + "\n".join(f"- {it['organization']} ({it['contact']})" for it in inqpl_list) if inqpl_list else "",
        f"[출처 URL] {source_url}",
    ]
    full_text = "\n\n".join(s for s in sections if s)

    return {
        "policy_id": policy_id,
        "document_id": document_id,
        "official_id": serv_id,
        "policy_name": serv_nm,
        "service_name": serv_nm,
        "ministry": jur_mnof_nm,
        "organization": jur_org_nm,
        "agency": agency_full or "중앙부처",
        "regions": ["전국"],
        "region": "전국",
        "life_array": life_array,
        "target_individual_array": trgter_indvdl_array,
        "interest_theme_array": intrs_thema_array,
        "provision_type": srv_pvsn_nm,
        "support_cycle": sprt_cyc_nm,
        "application_method": aply_mtd_nm or "복지로 또는 관할 주민센터 신청",
        "summary": wlfare_info_outl_cn,
        "tgtrDtlCn": tgtr_dtl_cn,
        "target_detail": tgtr_dtl_cn,
        "slctCritCn": slct_crit_cn,
        "selection_criteria": slct_crit_cn,
        "alwServCn": alw_serv_cn,
        "benefit": alw_serv_cn,
        "applmetList": applmet_list,
        "application_methods": applmet_list,
        "basfrmList": basfrm_list,
        "required_documents": basfrm_list,
        "baslawList": baslaw_list,
        "basic_laws": baslaw_list,
        "inqplCtadrList": inqpl_list,
        "inquiries": inqpl_list,
        "application_start": fmt_ymd(enfc_bgng_ymd),
        "application_end": fmt_ymd(enfc_end_ymd),
        "official_apply_url": official_apply_url,
        "application_url": official_apply_url,
        "source_url": source_url,
        "local_raw_path": f"data/raw/bokjiro/detail/{serv_id}.xml",
        "text": full_text,
        "content": full_text,
        "content_hash": hashlib.sha256(xml_bytes).hexdigest(),
        "verification_status": "verified" if serv_id in {"WLF00004661", "WLF00004656", "WLF00004448", "WLF00001173", "WLF00001180", "WLF00004457", "WLF00000060", "WLF00000022", "WLF00000025", "WLF00001170", "WLF00000063"} else "extracted",
        "retrieved_at": now_kst(),
        "collected_at": now_kst(),
    }


def run_collection(
    page_rows: int = 100,
    fetch_details: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    한국사회보장정보원 중앙부처복지서비스 API v2.2 전수 수집 및 정제 실행.
    """
    raw_key = load_env_value("DATA_GO_KR_API_KEY", "BOKJIRO_API_KEY")
    if not raw_key:
        raise ValueError("환경변수 DATA_GO_KR_API_KEY 가 설정되지 않았습니다.")
    key = unquote(raw_key.strip())

    list_url, detail_url = get_api_urls()
    logger = get_logger("bokjiro", [key, raw_key])

    summary: Dict[str, Any] = {
        "source": "한국사회보장정보원 중앙부처복지서비스 API v2.2",
        "list_api_url": list_url,
        "detail_api_url": detail_url,
        "started_at": now_kst(),
        "total_count_reported": None,
        "list_records_collected": 0,
        "list_pages_fetched": 0,
        "detail_xml_collected": 0,
        "detail_cached": 0,
        "detail_failed": 0,
        "quota_status": "NORMAL",
        "refined_jsonl_written": 0,
        "errors": [],
    }

    raw_list_dir = OUT_RAW / "list"
    raw_detail_dir = OUT_RAW / "detail"
    raw_list_dir.mkdir(parents=True, exist_ok=True)
    raw_detail_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1. 목록 조회 (callTp=L) & 페이지네이션 & totalCount 비교
    # -------------------------------------------------------------------------
    logger.info("=== [Step 1] Fetching Welfare List (callTp=L) from %s ===", list_url)
    list_items_map: Dict[str, Dict[str, str]] = {}
    page = 1
    total_count = 0

    while True:
        cache_path = raw_list_dir / f"page_{page:04d}.xml"
        content: bytes
        if use_cache and cache_path.exists():
            content = cache_path.read_bytes()
            logger.info("Loaded cached list page %d from %s", page, cache_path.name)
            root = ET.fromstring(content)
            check_xml_response(root, context=f"Cached list page {page}")
            total_count = int(root.findtext("totalCount") or 0)
            page_items = []
            for it in root.findall("servList"):
                sid = (it.findtext("servId") or "").strip()
                if sid:
                    page_items.append({
                        "servId": sid,
                        "servNm": (it.findtext("servNm") or "").strip(),
                        "jurMnofNm": (it.findtext("jurMnofNm") or "").strip(),
                        "jurOrgNm": (it.findtext("jurOrgNm") or "").strip(),
                    })
        else:
            try:
                content, total_count, page_items = fetch_list_page(list_url, key, page, page_rows, logger)
                cache_path.write_bytes(content)
                logger.info("Fetched live list page %d: %d items (total: %d)", page, len(page_items), total_count)
                time.sleep(0.3)
            except BokjiroApiError as e:
                logger.error("List API error at page %d: %s", page, e)
                summary["errors"].append(f"List page {page} BokjiroApiError: {e}")
                if e.is_quota_exceeded:
                    summary["quota_status"] = "QUOTA_EXCEEDED_CODE_22"
                break
            except Exception as e:
                logger.error("Unexpected error at list page %d: %s", page, e)
                summary["errors"].append(f"List page {page} unexpected: {e}")
                break

        summary["list_pages_fetched"] += 1
        for it in page_items:
            list_items_map[it["servId"]] = it

        summary["total_count_reported"] = total_count
        summary["list_records_collected"] = len(list_items_map)

        if len(page_items) == 0 or len(list_items_map) >= total_count:
            break
        page += 1

    logger.info("List fetch complete: collected %d records vs totalCount reported %d",
                len(list_items_map), total_count)

    # -------------------------------------------------------------------------
    # 2. 상세 조회 (callTp=D) & 오류 코드 분기 처리
    # -------------------------------------------------------------------------
    if fetch_details and list_items_map:
        logger.info("=== [Step 2] Fetching Welfare Details (callTp=D) for %d services ===", len(list_items_map))
        for idx, (serv_id, meta) in enumerate(list_items_map.items(), start=1):
            detail_cache = raw_detail_dir / f"{serv_id}.xml"
            if use_cache and detail_cache.exists():
                # 이미 유효한 wantedDtl이 있으면 재사용
                raw_existing = detail_cache.read_bytes()
                if b"<wantedDtl>" in raw_existing:
                    summary["detail_cached"] += 1
                    continue

            try:
                xml_data = fetch_detail_xml(detail_url, key, serv_id, logger)
                detail_cache.write_bytes(xml_data)
                summary["detail_xml_collected"] += 1
                if idx % 20 == 0:
                    logger.info("Detail progress: %d / %d (newly fetched: %d)", idx, len(list_items_map), summary["detail_xml_collected"])
                time.sleep(0.3)
            except BokjiroApiError as e:
                summary["detail_failed"] += 1
                err_msg = f"Detail {serv_id} error [code={e.code}]: {e}"
                summary["errors"].append(err_msg)
                logger.warning(err_msg)
                if e.is_quota_exceeded:
                    summary["quota_status"] = "QUOTA_EXCEEDED_CODE_22"
                    logger.warning("Code 22: Limited number of requests exceeded. Stopping detail requests safely.")
                    break
                if e.code in ("20", "30", "31"):
                    logger.error("Authentication/Authorization error code %s. Stopping immediately.", e.code)
                    break
            except Exception as e:
                summary["detail_failed"] += 1
                summary["errors"].append(f"Detail {serv_id} unexpected: {str(e)[:150]}")

    # -------------------------------------------------------------------------
    # 3. 원본 XML 파싱 -> 정제 JSONL (bokjiro_welfare_services.jsonl) 생성
    # -------------------------------------------------------------------------
    logger.info("=== [Step 3] Refining XML details to JSONL (%s) ===", OUT_PROCESSED_JSONL)
    refined_records: List[Dict[str, Any]] = []

    pol_idx = 0
    for xml_file in sorted(raw_detail_dir.glob("WLF*.xml")):
        serv_id = xml_file.stem
        raw_bytes = xml_file.read_bytes()
        try:
            if b"<wantedDtl>" not in raw_bytes:
                continue
            pol_idx += 1
            pol_id = f"POL_BOK_{pol_idx:03d}"
            doc_id = f"DOC_BOK_{pol_idx:03d}"
            meta = list_items_map.get(serv_id)
            rec = parse_detail_to_refined_record(raw_bytes, policy_id=pol_id, document_id=doc_id, list_meta=meta)
            refined_records.append(rec)
        except Exception as e:
            logger.warning("Failed to parse detail XML %s: %s", xml_file.name, e)

    with open(OUT_PROCESSED_JSONL, "w", encoding="utf-8") as f:
        for r in refined_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary["refined_jsonl_written"] = len(refined_records)
    summary["finished_at"] = now_kst()
    write_run_log("bokjiro", summary)

    logger.info("=== Bokjiro Collector Finished ===")
    logger.info("Total reported: %s, Collected list: %d, Total valid detail XML: %d, Refined JSONL: %d",
                summary["total_count_reported"], summary["list_records_collected"],
                len(refined_records), summary["refined_jsonl_written"])
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="한국사회보장정보원 중앙부처복지서비스 API v2.2 수집기")
    parser.add_argument("--rows", type=int, default=100, help="한 페이지당 수집 건수 (기본 100)")
    parser.add_argument("--no-details", action="store_true", help="상세 수집 생략 여부")
    parser.add_argument("--no-cache", action="store_true", help="캐시 사용 안 함")
    args = parser.parse_args()

    res = run_collection(page_rows=args.rows, fetch_details=not args.no_details, use_cache=not args.no_cache)
    print(json.dumps(res, ensure_ascii=False, indent=2))
