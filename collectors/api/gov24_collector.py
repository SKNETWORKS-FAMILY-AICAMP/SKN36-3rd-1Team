"""
행정안전부 공공서비스(혜택) API v3 수집 및 정제 모듈 (gov24_collector.py).

근거:
  - 행정안전부 공공서비스 API 공식 Swagger 명세 (https://infuser.odcloud.kr/api/stages/44436/api-docs?1684891964110)
  - 공공데이터포털 공공서비스(혜택) 등록번호 15113968

설정:
  - 목록 URL: MOIS_SERVICE_LIST_API_URL (기본: https://api.odcloud.kr/api/gov24/v3/serviceList)
  - 상세 URL: MOIS_SERVICE_DETAIL_API_URL (기본: https://api.odcloud.kr/api/gov24/v3/serviceDetail)
  - 조건 URL: MOIS_SUPPORT_CONDITIONS_API_URL (기본: https://api.odcloud.kr/api/gov24/v3/supportConditions)
  - 공통 인증키: DATA_GO_KR_API_KEY (.env 공통 키)

처리 흐름:
  1. serviceList 목록 조회 (page, perPage)
  2. 서비스ID 확보
  3. serviceDetail 상세 정보 조회 (cond[서비스ID::EQ] 또는 벌크 페이지)
  4. supportConditions 지원조건 조회 (cond[서비스ID::EQ] 또는 벌크 페이지)
  5. 서비스 ID 기준으로 3종 응답 결합
  6. 원본 JSON 저장 (data/raw/gov24/items/<서비스ID>.json)
  7. JA 코드 Swagger 해석 및 연령·소득·취업·가구유형 구조화 (미확인 원칙 준수)
  8. 정제 JSONL (data/processed/gov24_public_services.jsonl) 생성
  9. 원본-정규화 필드 매핑표 (data/metadata/gov24_field_mapping.md) 생성
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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RAW_ROOT, get_logger, load_env_value, now_kst, write_run_log  # noqa: E402
from collectors.preprocessing.application_urls import application_url_or_none

PROCESSED_ROOT = BASE_DIR / "data" / "processed"
METADATA_ROOT = BASE_DIR / "data" / "metadata"
OUT_RAW = RAW_ROOT / "gov24"
OUT_PROCESSED_JSONL = PROCESSED_ROOT / "gov24_public_services.jsonl"
OUT_MAPPING_MD = METADATA_ROOT / "gov24_field_mapping.md"

DEFAULT_LIST_URL = "https://api.odcloud.kr/api/gov24/v3/serviceList"
DEFAULT_DETAIL_URL = "https://api.odcloud.kr/api/gov24/v3/serviceDetail"
DEFAULT_COND_URL = "https://api.odcloud.kr/api/gov24/v3/supportConditions"

# Swagger supportConditions_model 정의에 따른 JA 코드 한글 명칭
JA_CODE_DEFINITIONS: Dict[str, Tuple[str, str, str]] = {
    # 코드: (카테고리, 속성키, 한글명칭)
    "JA0101": ("gender", "male", "남성"),
    "JA0102": ("gender", "female", "여성"),
    "JA0110": ("age", "min_age", "대상연령(시작)"),
    "JA0111": ("age", "max_age", "대상연령(종료)"),
    "JA0201": ("income", "median_0_50", "중위소득 0~50%"),
    "JA0202": ("income", "median_51_75", "중위소득 51~75%"),
    "JA0203": ("income", "median_76_100", "중위소득 76~100%"),
    "JA0204": ("income", "median_101_200", "중위소득 101~200%"),
    "JA0205": ("income", "median_over_200", "중위소득 200% 초과"),
    "JA0301": ("lifecycle", "pre_parent_infertility", "예비부모/난임"),
    "JA0302": ("lifecycle", "pregnant", "임산부"),
    "JA0303": ("lifecycle", "birth_adoption", "출산/입양"),
    "JA0313": ("employment", "farmer", "농업인"),
    "JA0314": ("employment", "fisher", "어업인"),
    "JA0315": ("employment", "livestock", "축산업인"),
    "JA0316": ("employment", "forester", "임업인"),
    "JA0317": ("education", "elementary_student", "초등학생"),
    "JA0318": ("education", "middle_student", "중학생"),
    "JA0319": ("education", "high_student", "고등학생"),
    "JA0320": ("education", "college_student", "대학생/대학원생"),
    "JA0322": ("special", "not_applicable", "해당사항없음"),
    "JA0326": ("employment", "employed_worker", "근로자/직장인"),
    "JA0327": ("employment", "job_seeker", "구직자/실업자"),
    "JA0328": ("special", "disabled", "장애인"),
    "JA0329": ("special", "national_merit", "국가보훈대상자"),
    "JA0330": ("special", "patient", "질병/질환자"),
    "JA0401": ("household", "multicultural", "다문화가족"),
    "JA0402": ("household", "north_korean_defector", "북한이탈주민"),
    "JA0403": ("household", "single_parent_grandparent", "한부모가정/조손가정"),
    "JA0404": ("household", "single_person", "1인가구"),
    "JA0410": ("household", "not_applicable", "해당사항없음"),
    "JA0411": ("household", "multi_child", "다자녀가구"),
    "JA0412": ("household", "homeless_household", "무주택세대"),
    "JA0413": ("household", "new_resident", "신규전입"),
    "JA0414": ("household", "extended_family", "확대가족"),
    "JA1101": ("business", "pre_entrepreneur", "예비창업자"),
    "JA1102": ("business", "operating_business", "영업중(소상공인/자영업)"),
    "JA1103": ("business", "closing_business", "생계곤란/폐업예정자"),
    "JA1201": ("business_sector", "restaurant", "음식점업"),
    "JA1202": ("business_sector", "manufacturing_small", "제조업(소상공인)"),
    "JA1299": ("business_sector", "other_sector_small", "기타업종(소상공인)"),
    "JA2101": ("corporate", "sme", "중소기업"),
    "JA2102": ("corporate", "welfare_facility", "사회복지시설"),
    "JA2103": ("corporate", "organization", "기관/단체"),
    "JA2201": ("corporate_sector", "manufacturing", "제조업"),
    "JA2202": ("corporate_sector", "agriculture_fishery", "농업,임업 및 어업"),
    "JA2203": ("corporate_sector", "it_communications", "정보통신업"),
    "JA2299": ("corporate_sector", "other_sector", "기타업종"),
}


def get_gov24_urls() -> Tuple[str, str, str]:
    list_url = os.getenv("MOIS_SERVICE_LIST_API_URL") or os.getenv("MOIS_BENEFITS_API_URL") or DEFAULT_LIST_URL
    detail_url = os.getenv("MOIS_SERVICE_DETAIL_API_URL") or DEFAULT_DETAIL_URL
    cond_url = os.getenv("MOIS_SUPPORT_CONDITIONS_API_URL") or DEFAULT_COND_URL
    return list_url.strip(), detail_url.strip(), cond_url.strip()


def fetch_api_json(url: str, params: Dict[str, Any], logger: logging.Logger, timeout: int = 45) -> Dict[str, Any]:
    """공공데이터포털 odcloud.kr REST GET 호출 및 JSON 파싱."""
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "LifePolicyAI-Gov24Collector/3.0"}, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"Network error while calling {url}: {e}")

    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} error from {url}: {r.text[:200]}")

    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Non-JSON response from {url}: {r.text[:200]}") from e

    return data


def fetch_single_service(
    service_id: str,
    key: str,
    list_url: str,
    detail_url: str,
    cond_url: str,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """
    단일 서비스 ID에 대해 cond[서비스ID::EQ] 조건으로 목록, 상세, 지원조건을 실시간 호출 결합.
    """
    # 1. Detail 조회
    d_resp = fetch_api_json(detail_url, {"serviceKey": key, "cond[서비스ID::EQ]": service_id}, logger)
    detail_data = d_resp.get("data", [{}])[0] if d_resp.get("data") else {}

    # 2. Conditions 조회
    c_resp = fetch_api_json(cond_url, {"serviceKey": key, "cond[서비스ID::EQ]": service_id}, logger)
    cond_data = c_resp.get("data", [{}])[0] if c_resp.get("data") else {}

    # 3. List 조회
    l_resp = fetch_api_json(list_url, {"serviceKey": key, "cond[서비스ID::EQ]": service_id}, logger)
    list_data = l_resp.get("data", [{}])[0] if l_resp.get("data") else {}

    # 통합 결합
    combined = {}
    combined.update(list_data)
    combined.update(detail_data)
    combined["_support_conditions_raw"] = cond_data
    return combined


def structure_ja_conditions(cond_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Swagger 명세에 따라 JA 코드 필드를 체계적으로 구조화한다.
    - 'Y': '적용대상'
    - 'None'/부존재: '미확인' (조건 없음으로 단정하지 않음)
    - 연령 필드(JA0110, JA0111): integer 또는 None
    """
    gender_cond: Dict[str, str] = {}
    income_cond: Dict[str, str] = {}
    employment_cond: Dict[str, str] = {}
    household_cond: Dict[str, str] = {}
    lifecycle_cond: Dict[str, str] = {}
    business_cond: Dict[str, str] = {}

    def val_to_status(val: Any) -> str:
        if val == "Y":
            return "적용대상"
        if val == "N":
            return "제외대상"
        return "미확인"

    # 1. 성별
    m_val = cond_dict.get("JA0101")
    f_val = cond_dict.get("JA0102")
    gender_cond = {
        "남성": val_to_status(m_val),
        "여성": val_to_status(f_val),
        "상태": "확인" if m_val == "Y" or f_val == "Y" else "미확인(공고문 본문 참조)",
    }

    # 2. 연령
    min_age = cond_dict.get("JA0110")
    max_age = cond_dict.get("JA0111")
    age_cond = {
        "대상연령_시작": int(min_age) if isinstance(min_age, int) and min_age > 0 else (int(min_age) if str(min_age).isdigit() and int(min_age) > 0 else None),
        "대상연령_종료": int(max_age) if isinstance(max_age, int) and max_age > 0 else (int(max_age) if str(max_age).isdigit() and int(max_age) > 0 else None),
        "상태": "확인" if min_age or max_age else "미확인(공고문 본문 참조)",
    }

    # 3. 소득구간
    for code in ["JA0201", "JA0202", "JA0203", "JA0204", "JA0205"]:
        name = JA_CODE_DEFINITIONS[code][2]
        income_cond[name] = val_to_status(cond_dict.get(code))
    income_has_y = any(v == "적용대상" for v in income_cond.values())
    income_cond["상태"] = "확인" if income_has_y else "미확인(공고문 본문 참조)"

    # 4. 취업상태 및 경제활동
    for code in ["JA0326", "JA0327", "JA1101", "JA1102", "JA1103", "JA0313", "JA0314", "JA0315", "JA0316"]:
        name = JA_CODE_DEFINITIONS[code][2]
        employment_cond[name] = val_to_status(cond_dict.get(code))
    emp_has_y = any(v == "적용대상" for v in employment_cond.values())
    employment_cond["상태"] = "확인" if emp_has_y else "미확인(공고문 본문 참조)"

    # 5. 가구유형 및 주거
    for code in ["JA0404", "JA0410", "JA0411", "JA0403", "JA0401", "JA0402", "JA0412", "JA0413", "JA0414"]:
        name = JA_CODE_DEFINITIONS[code][2]
        household_cond[name] = val_to_status(cond_dict.get(code))
    hh_has_y = any(v == "적용대상" for v in household_cond.values())
    household_cond["상태"] = "확인" if hh_has_y else "미확인(공고문 본문 참조)"

    # 6. 생애주기 / 학업 / 건강·특수
    for code in ["JA0301", "JA0302", "JA0303", "JA0317", "JA0318", "JA0319", "JA0320", "JA0328", "JA0329", "JA0330"]:
        name = JA_CODE_DEFINITIONS[code][2]
        lifecycle_cond[name] = val_to_status(cond_dict.get(code))
    lc_has_y = any(v == "적용대상" for v in lifecycle_cond.values())
    lifecycle_cond["상태"] = "확인" if lc_has_y else "미확인(공고문 본문 참조)"

    # 7. 기업 및 소상공인
    for code in ["JA2101", "JA2102", "JA2103", "JA1201", "JA1202", "JA2201", "JA2202", "JA2203"]:
        name = JA_CODE_DEFINITIONS[code][2]
        business_cond[name] = val_to_status(cond_dict.get(code))
    biz_has_y = any(v == "적용대상" for v in business_cond.values())
    business_cond["상태"] = "확인" if biz_has_y else "미확인(공고문 본문 참조)"

    return {
        "성별조건": gender_cond,
        "연령조건": age_cond,
        "소득구간조건": income_cond,
        "취업경제활동조건": employment_cond,
        "가구주거조건": household_cond,
        "생애주기특수조건": lifecycle_cond,
        "기업소상공인조건": business_cond,
    }


def normalize_gov24_record(
    service_id: str,
    combined_raw: Dict[str, Any],
    policy_id: Optional[str] = None,
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """통합된 Gov24 원본 레코드를 스키마에 맞춰 정규화."""
    pol_id = policy_id or f"POL_GOV_{service_id}"
    doc_id = document_id or f"DOC_GOV_{service_id}"

    svc_nm = (combined_raw.get("서비스명") or "").strip()
    org_nm = (combined_raw.get("소관기관명") or combined_raw.get("접수기관명") or "행정기관").strip()
    dept_nm = (combined_raw.get("부서명") or "").strip()
    full_org = f"{org_nm} {dept_nm}".strip() if dept_nm and dept_nm not in org_nm else org_nm

    field_nm = combined_raw.get("서비스분야", "기타")
    support_type = combined_raw.get("지원유형", "기타")
    purpose = combined_raw.get("서비스목적요약") or combined_raw.get("서비스목적") or ""
    target_raw = combined_raw.get("지원대상") or ""
    criteria_raw = combined_raw.get("선정기준") or ""
    benefit_raw = combined_raw.get("지원내용") or ""
    apply_method = combined_raw.get("신청방법") or ""
    apply_period = combined_raw.get("신청기한") or ""
    req_docs = combined_raw.get("구비서류") or ""
    reception_org = combined_raw.get("접수기관명") or combined_raw.get("접수기관") or ""
    contact = combined_raw.get("문의처") or combined_raw.get("전화문의") or ""
    detail_url = combined_raw.get("상세조회URL") or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{service_id}"
    apply_url = application_url_or_none(combined_raw.get("온라인신청사이트URL"), detail_url)

    laws = ", ".join(filter(None, [combined_raw.get("법령"), combined_raw.get("자치법규"), combined_raw.get("행정규칙")]))

    # 지원조건 파싱
    cond_raw = combined_raw.get("_support_conditions_raw") or {}
    structured_conditions = structure_ja_conditions(cond_raw)

    # 본문 텍스트 생성
    sections = [
        f"[서비스명] {svc_nm} (서비스ID: {service_id})",
        f"[소관기관 / 서비스분야 / 지원유형] {full_org} / {field_nm} / {support_type}",
        f"[서비스목적]\n{purpose}" if purpose else "",
        f"[지원대상]\n{target_raw}" if target_raw else "",
        f"[선정기준]\n{criteria_raw}" if criteria_raw else "",
        f"[지원내용]\n{benefit_raw}" if benefit_raw else "",
        f"[구조화 지원조건 요약]\n"
        f"- 대상연령: {structured_conditions['연령조건']['대상연령_시작'] or '미확인'}세 ~ {structured_conditions['연령조건']['대상연령_종료'] or '미확인'}세\n"
        f"- 소득구간: " + ", ".join(k for k, v in structured_conditions['소득구간조건'].items() if v == '적용대상') if any(v == '적용대상' for v in structured_conditions['소득구간조건'].values()) else "- 소득구간: 미확인(공고문 참조)\n"
        f"- 가구·주거: " + ", ".join(k for k, v in structured_conditions['가구주거조건'].items() if v == '적용대상') if any(v == '적용대상' for v in structured_conditions['가구주거조건'].values()) else "- 가구·주거: 미확인(공고문 참조)",
        f"[신청기한 및 신청방법]\n- 신청기한: {apply_period or '상시/별도공고'}\n- 신청방법: {apply_method or '-'}\n- 접수기관: {reception_org or '-'}",
        f"[구비서류]\n{req_docs}" if req_docs else "",
        f"[근거법령]\n{laws}" if laws else "",
        f"[문의처] {contact}" if contact else "",
        f"[출처 URL] {detail_url}",
    ]
    full_text = "\n\n".join(s for s in sections if s)

    return {
        "policy_id": pol_id,
        "document_id": doc_id,
        "official_id": service_id,
        "service_id": service_id,
        "policy_name": svc_nm,
        "service_name": svc_nm,
        "agency": full_org,
        "organization": full_org,
        "service_field": field_nm,
        "support_type": support_type,
        "purpose": purpose,
        "target_audience": target_raw,
        "selection_criteria": criteria_raw,
        "benefit": benefit_raw,
        "application_method": apply_method,
        "application_period": apply_period,
        "required_documents": req_docs,
        "reception_organization": reception_org,
        "contact_info": contact,
        "basic_laws": laws,
        "official_apply_url": apply_url,
        "application_url": apply_url,
        "source_url": detail_url,
        "structured_support_conditions": structured_conditions,
        "full_text": full_text,
        "text": full_text,
        "content": full_text,
        "content_hash": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        "verification_status": "verified" if service_id in {"161300000099", "149200005007", "149200000026", "105100000001"} else "extracted",
        "retrieved_at": now_kst(),
        "collected_at": now_kst(),
    }


def write_mapping_table_markdown() -> Path:
    """Swagger 정의와 정규화 필드의 1:1 매핑표를 생성."""
    lines = [
        "# 행정안전부 공공서비스 API 원본 필드 — 정규화 필드 매핑표 (gov24_field_mapping.md)",
        "",
        "> **근거 명세:** [공식 Swagger 명세](https://infuser.odcloud.kr/api/stages/44436/api-docs?1684891964110)  ",
        "> **적용 API:** `serviceList`, `serviceDetail`, `supportConditions`  ",
        "> **원칙:** 지원조건 API에 나타나지 않은 항목은 '조건 없음'이 아닌 '미확인(unconfirmed)'으로 명시  ",
        "",
        "---",
        "",
        "## 1. 기본 정보 및 상세 설명 필드 매핑 (`serviceList` & `serviceDetail`)",
        "",
        "| 원본 필드명 | 원본 출처 | 타입 | 정규화 필드명 | 설명 및 정규화 규칙 |",
        "|---|---|---|---|---|",
        "| `서비스ID` | 공통 | String | `official_id` / `service_id` | 공공서비스 고유 식별자 (PK) |",
        "| `서비스명` | 공통 | String | `policy_name` / `service_name` | 정책 및 공공서비스 명칭 |",
        "| `소관기관명` | 공통 | String | `agency` / `organization` | 소관 부처 또는 지자체명 |",
        "| `부서명` | serviceList | String | `organization` (결합) | 소관 상세 부서 |",
        "| `서비스분야` | serviceList | String | `category` / `service_field` | 복지, 주거, 고용 등 정책 분류 |",
        "| `지원유형` | 공통 | String | `support_type` | 현금, 현물, 대출, 이용권 등 |",
        "| `서비스목적요약` / `서비스목적` | 공통 | String | `purpose` / `summary` | 사업 목적 및 개요 |",
        "| `지원대상` | 공통 | String | `target_audience` / `eligibility` | 지원 대상 텍스트 설명 |",
        "| `선정기준` | 공통 | String | `selection_criteria` | 자격 요건 및 선정 기준 텍스트 |",
        "| `지원내용` | 공통 | String | `benefit` | 지원 금액, 혜택, 기간 상세 |",
        "| `신청기한` | 공통 | String | `application_period` | 신청 기간 또는 상시 여부 |",
        "| `신청방법` | 공통 | String | `application_method` | 온라인/방문 등 신청 절차 |",
        "| `구비서류` | serviceDetail | String | `required_documents` | 민원인 지참 제출 서류 목록 |",
        "| `법령` / `자치법규` | serviceDetail | String | `basic_laws` | 정책의 근거 법령 및 조례 |",
        "| `문의처` / `전화문의` | 공통 | String | `contact_info` | 담당 부서 및 문의 전화번호 |",
        "| `온라인신청사이트URL` | serviceDetail | String/null | `official_apply_url` | 신청 주소만 저장, 없거나 안내 주소이면 null |",
        "| `상세조회URL` | serviceList | String | `source_url` | 정부24 공식 상세 안내 웹페이지 |",
        "",
        "---",
        "",
        "## 2. 지원조건 JA 코드 매핑 (`supportConditions`)",
        "",
        "> **JA 코드 해석 원칙:**",
        "> - `Y`: `적용대상` (해당 지원조건이 자격요건으로 적용됨)",
        "> - `None` / 부존재: `미확인` (지원조건 API에 명시되지 않은 것으로 공고문 본문 확인 필요)",
        "> - `JA0110` / `JA0111`: 대상연령 시작 / 종료 정수값",
        "",
        "| JA 코드 | Swagger 정의 | 정규화 그룹 | 정규화 속성 | 값 매핑 규칙 |",
        "|---|---|---|---|---|",
        "| `JA0101` | 남성 | 성별조건 (`gender_condition`) | `남성` | Y → 적용대상, None → 미확인 |",
        "| `JA0102` | 여성 | 성별조건 (`gender_condition`) | `여성` | Y → 적용대상, None → 미확인 |",
        "| `JA0110` | 대상연령(시작) | 연령조건 (`age_condition`) | `대상연령_시작` | 정수값 (예: 19), None → 미확인 |",
        "| `JA0111` | 대상연령(종료) | 연령조건 (`age_condition`) | `대상연령_종료` | 정수값 (예: 34), None → 미확인 |",
        "| `JA0201` | 중위소득 0~50% | 소득구간조건 (`income_brackets`) | `중위소득 0~50%` | Y → 적용대상, None → 미확인 |",
        "| `JA0202` | 중위소득 51~75% | 소득구간조건 (`income_brackets`) | `중위소득 51~75%` | Y → 적용대상, None → 미확인 |",
        "| `JA0203` | 중위소득 76~100% | 소득구간조건 (`income_brackets`) | `중위소득 76~100%` | Y → 적용대상, None → 미확인 |",
        "| `JA0204` | 중위소득 101~200% | 소득구간조건 (`income_brackets`) | `중위소득 101~200%` | Y → 적용대상, None → 미확인 |",
        "| `JA0205` | 중위소득 200% 초과 | 소득구간조건 (`income_brackets`) | `중위소득 200% 초과` | Y → 적용대상, None → 미확인 |",
        "| `JA0301` | 예비부모/난임 | 생애주기조건 (`lifecycle_conditions`) | `예비부모/난임` | Y → 적용대상, None → 미확인 |",
        "| `JA0302` | 임산부 | 생애주기조건 (`lifecycle_conditions`) | `임산부` | Y → 적용대상, None → 미확인 |",
        "| `JA0303` | 출산/입양 | 생애주기조건 (`lifecycle_conditions`) | `출산/입양` | Y → 적용대상, None → 미확인 |",
        "| `JA0313` | 농업인 | 취업활동조건 (`employment_conditions`) | `농업인` | Y → 적용대상, None → 미확인 |",
        "| `JA0314` | 어업인 | 취업활동조건 (`employment_conditions`) | `어업인` | Y → 적용대상, None → 미확인 |",
        "| `JA0315` | 축산업인 | 취업활동조건 (`employment_conditions`) | `축산업인` | Y → 적용대상, None → 미확인 |",
        "| `JA0316` | 임업인 | 취업활동조건 (`employment_conditions`) | `임업인` | Y → 적용대상, None → 미확인 |",
        "| `JA0317` | 초등학생 | 학업조건 (`education_conditions`) | `초등학생` | Y → 적용대상, None → 미확인 |",
        "| `JA0318` | 중학생 | 학업조건 (`education_conditions`) | `중학생` | Y → 적용대상, None → 미확인 |",
        "| `JA0319` | 고등학생 | 학업조건 (`education_conditions`) | `고등학생` | Y → 적용대상, None → 미확인 |",
        "| `JA0320` | 대학생/대학원생 | 학업조건 (`education_conditions`) | `대학생/대학원생` | Y → 적용대상, None → 미확인 |",
        "| `JA0326` | 근로자/직장인 | 취업활동조건 (`employment_conditions`) | `근로자/직장인` | Y → 적용대상, None → 미확인 |",
        "| `JA0327` | 구직자/실업자 | 취업활동조건 (`employment_conditions`) | `구직자/실업자` | Y → 적용대상, None → 미확인 |",
        "| `JA0328` | 장애인 | 특수대상조건 (`special_conditions`) | `장애인` | Y → 적용대상, None → 미확인 |",
        "| `JA0329` | 국가보훈대상자 | 특수대상조건 (`special_conditions`) | `국가보훈대상자` | Y → 적용대상, None → 미확인 |",
        "| `JA0330` | 질병/질환자 | 특수대상조건 (`special_conditions`) | `질병/질환자` | Y → 적용대상, None → 미확인 |",
        "| `JA0401` | 다문화가족 | 가구주거조건 (`household_conditions`) | `다문화가족` | Y → 적용대상, None → 미확인 |",
        "| `JA0402` | 북한이탈주민 | 가구주거조건 (`household_conditions`) | `북한이탈주민` | Y → 적용대상, None → 미확인 |",
        "| `JA0403` | 한부모가정/조손가정 | 가구주거조건 (`household_conditions`) | `한부모가정/조손가정` | Y → 적용대상, None → 미확인 |",
        "| `JA0404` | 1인가구 | 가구주거조건 (`household_conditions`) | `1인가구` | Y → 적용대상, None → 미확인 |",
        "| `JA0411` | 다자녀가구 | 가구주거조건 (`household_conditions`) | `다자녀가구` | Y → 적용대상, None → 미확인 |",
        "| `JA0412` | 무주택세대 | 가구주거조건 (`household_conditions`) | `무주택세대` | Y → 적용대상, None → 미확인 |",
        "| `JA0413` | 신규전입 | 가구주거조건 (`household_conditions`) | `신규전입` | Y → 적용대상, None → 미확인 |",
        "| `JA0414` | 확대가족 | 가구주거조건 (`household_conditions`) | `확대가족` | Y → 적용대상, None → 미확인 |",
        "| `JA1101` | 예비창업자 | 소상공인조건 (`business_conditions`) | `예비창업자` | Y → 적용대상, None → 미확인 |",
        "| `JA1102` | 영업중 | 소상공인조건 (`business_conditions`) | `영업중` | Y → 적용대상, None → 미확인 |",
        "| `JA1103` | 생계곤란/폐업예정자 | 소상공인조건 (`business_conditions`) | `생계곤란/폐업예정자` | Y → 적용대상, None → 미확인 |",
        "| `JA2101` | 중소기업 | 기업조건 (`corporate_conditions`) | `중소기업` | Y → 적용대상, None → 미확인 |",
        "| `JA2102` | 사회복지시설 | 기업조건 (`corporate_conditions`) | `사회복지시설` | Y → 적용대상, None → 미확인 |",
        "| `JA2103` | 기관/단체 | 기업조건 (`corporate_conditions`) | `기관/단체` | Y → 적용대상, None → 미확인 |",
    ]
    content = "\n".join(lines) + "\n"
    OUT_MAPPING_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MAPPING_MD.write_text(content, encoding="utf-8")
    docs_copy = BASE_DIR / "docs" / "data" / "gov24_field_mapping.md"
    docs_copy.parent.mkdir(parents=True, exist_ok=True)
    docs_copy.write_text(content, encoding="utf-8")
    return OUT_MAPPING_MD


def run_collection(per_page: int = 1000, max_pages: Optional[int] = None, use_cache: bool = True) -> Dict[str, Any]:
    """행정안전부 공공서비스 API 3종 전수 수집 및 결합 실행."""
    raw_key = load_env_value("DATA_GO_KR_API_KEY")
    if not raw_key:
        raise ValueError("환경변수 DATA_GO_KR_API_KEY 가 설정되지 않았습니다.")
    key = unquote(raw_key.strip())

    list_url, detail_url, cond_url = get_gov24_urls()
    logger = get_logger("gov24", [key, raw_key])

    summary: Dict[str, Any] = {
        "source": "행정안전부 공공서비스(혜택) API v3",
        "list_api_url": list_url,
        "detail_api_url": detail_url,
        "support_conditions_api_url": cond_url,
        "started_at": now_kst(),
        "endpoints": {},
        "merge_summary": {
            "service_list_count": 0,
            "service_detail_count": 0,
            "support_conditions_count": 0,
            "combined_success_count": 0,
            "missing_detail_count": 0,
            "missing_conditions_count": 0,
        },
        "refined_jsonl_written": 0,
        "errors": [],
    }

    # 1. 3개 엔드포인트 수집 / 캐시 로드
    endpoints_map = {
        "serviceList": list_url,
        "serviceDetail": detail_url,
        "supportConditions": cond_url,
    }

    all_endpoint_data: Dict[str, Dict[str, Dict[str, Any]]] = {
        "serviceList": {},
        "serviceDetail": {},
        "supportConditions": {},
    }

    for ep_name, ep_url in endpoints_map.items():
        ep_dir = OUT_RAW / ep_name
        ep_dir.mkdir(parents=True, exist_ok=True)
        st = {"pages_ok": 0, "records": 0, "match_count": None}
        page = 1

        while not (max_pages and page > max_pages):
            cache_file = ep_dir / f"page_{page:04d}.json"
            j: Dict[str, Any]
            if use_cache and cache_file.exists():
                j = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                try:
                    j = fetch_api_json(ep_url, {"page": page, "perPage": per_page, "returnType": "JSON", "serviceKey": key}, logger)
                    cache_file.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
                    time.sleep(0.3)
                except Exception as e:
                    summary["errors"].append(f"{ep_name} page {page}: {e}")
                    logger.error("%s page %d failed: %s", ep_name, page, e)
                    break

            records = j.get("data", [])
            st["match_count"] = j.get("matchCount")
            st["pages_ok"] += 1
            st["records"] += len(records)

            for item in records:
                sid = item.get("서비스ID")
                if sid:
                    all_endpoint_data[ep_name][sid] = item

            if len(records) == 0 or st["records"] >= (st["match_count"] or 0):
                break
            page += 1

        summary["endpoints"][ep_name] = st
        logger.info("%s collected %d records (reported matchCount: %s)", ep_name, len(all_endpoint_data[ep_name]), st["match_count"])

    # 2. 서비스 ID 기준 3종 데이터 결합 (Merge)
    list_sids = set(all_endpoint_data["serviceList"].keys())
    detail_sids = set(all_endpoint_data["serviceDetail"].keys())
    cond_sids = set(all_endpoint_data["supportConditions"].keys())

    all_sids = list_sids | detail_sids | cond_sids
    common_sids = list_sids & detail_sids & cond_sids

    summary["merge_summary"]["service_list_count"] = len(list_sids)
    summary["merge_summary"]["service_detail_count"] = len(detail_sids)
    summary["merge_summary"]["support_conditions_count"] = len(cond_sids)
    summary["merge_summary"]["combined_success_count"] = len(common_sids)
    summary["merge_summary"]["missing_detail_count"] = len(list_sids - detail_sids)
    summary["merge_summary"]["missing_conditions_count"] = len(list_sids - cond_sids)

    logger.info("Merge statistics: %d services common to all 3 endpoints (Missing detail: %d, Missing cond: %d)",
                len(common_sids), summary["merge_summary"]["missing_detail_count"], summary["merge_summary"]["missing_conditions_count"])

    # 3. 원본 JSON 저장 및 정규화 JSONL 생성
    items_dir = OUT_RAW / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)

    refined_records: List[Dict[str, Any]] = []

    for sid in sorted(common_sids):
        l_item = all_endpoint_data["serviceList"].get(sid, {})
        d_item = all_endpoint_data["serviceDetail"].get(sid, {})
        c_item = all_endpoint_data["supportConditions"].get(sid, {})

        combined_item: Dict[str, Any] = {}
        combined_item.update(l_item)
        combined_item.update(d_item)
        combined_item["_support_conditions_raw"] = c_item

        # 원본 개별 아이템 JSON 저장 (상위 500개 및 주요 정책 우선 디스크 보존)
        if len(refined_records) < 500 or sid in {"161300000099", "149200005007", "149200000026", "105100000001"}:
            (items_dir / f"{sid}.json").write_text(json.dumps(combined_item, ensure_ascii=False, indent=2), encoding="utf-8")

        normalized = normalize_gov24_record(sid, combined_item)
        refined_records.append(normalized)

    # 4. JSONL 파일 저장
    with open(OUT_PROCESSED_JSONL, "w", encoding="utf-8") as f:
        for r in refined_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary["refined_jsonl_written"] = len(refined_records)

    # 5. 매핑표 Markdown 생성
    mapping_path = write_mapping_table_markdown()
    summary["field_mapping_markdown"] = str(mapping_path)

    summary["finished_at"] = now_kst()
    write_run_log("gov24", summary)

    logger.info("Gov24 collection complete: %d records written to %s", len(refined_records), OUT_PROCESSED_JSONL)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="행정안전부 공공서비스 API 수집 및 정제 모듈")
    parser.add_argument("--per-page", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    res = run_collection(per_page=args.per_page, max_pages=args.max_pages, use_cache=not args.no_cache)
    print(json.dumps(res, ensure_ascii=False, indent=2))
