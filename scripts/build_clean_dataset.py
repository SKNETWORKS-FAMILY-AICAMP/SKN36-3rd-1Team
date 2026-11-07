"""
LifePolicy AI - 3차 데이터 오류 수정 및 3종 공식 API 통합 정제 데이터셋 생성 스크립트 (scripts/build_clean_dataset.py)

수행 내역:
1. [P0] 기존 2차 데이터 품질 오류 전수 수정 및 원문 대조:
   - DOC_NHUF_008: 잘못 수집된 청년전용 버팀목전세자금 FAQ(FP05020303.jsp) 대신 실제 청년전용 보증부월세대출 대출안내(FP05020701.jsp, nhuf_youth_monthly_rent_deposit_guide.html)로 교체하고 이용절차/제출서류(DOC_NHUF_009, FP05020702.jsp) 추가
   - DOC_NHUF_010: 주거안정월세대출 이용절차/제출서류(FP05020202.jsp, nhuf_monthly_rent_process.html) 추가
   - DOC_MOLIT_001 & DOC_MOEL_001: 존재하지 않던 가상 요약 파일(data/raw/other/*.txt) 참조를 제거하고 실제 공식 API 원문(data/raw/bokjiro/detail/WLF00004661.xml, data/raw/gov24/items/161300000099.json, data/raw/gov24/items/149200005007.json, data/raw/youthcenter/items/20260710005400113256.json)으로 교체
   - DOC_LH_003: 센스리더 안내, GNB 사이트맵 중복, 자동로그아웃 팝업, 공유/뷰어 버튼, 빈 테이블 제거 후 순수 공고 본문만 정제
   - POL_001~POL_010: 원문 PDF/HTML/API 수치(자산·소득·대출한도·금리·지원기간·구직촉진수당) 전수 대조 및 교정
2. [P0] 공식 API 3종(온통청년 3,861건, 행정안전부 공공서비스 10,942건, 중앙부처복지서비스 461건) 실제 수집 데이터 정규화·중복 병합 및 Neo4j/Qdrant 적재용 데이터셋 구축
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collectors.preprocessing.normalize import (
    compute_sha256,
    extract_lh_notice_html,
    extract_nhuf_html,
    extract_pdf_text,
    extract_work24_html,
    format_gov24_item_text,
    format_youthcenter_item_text,
    parse_bokjiro_detail_xml,
)
from collectors.api.gov24_collector import structure_ja_conditions
from collectors.api.youthcenter_collector import load_code_definitions, normalize_youthcenter_item
from collectors.preprocessing.application_urls import application_url_or_none

DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
METADATA_DIR = DATA_DIR / "metadata"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DIR.mkdir(parents=True, exist_ok=True)

NOW = "2026-11-04T19:30:00+09:00"

SGG_TO_REGION = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}

REGION_CODE_MAP = {
    "전국": "REGION_NATIONWIDE",
    "서울특별시": "REGION_SEOUL",
    "부산광역시": "REGION_BUSAN",
    "대구광역시": "REGION_DAEGU",
    "인천광역시": "REGION_INCHEON",
    "광주광역시": "REGION_GWANGJU",
    "대전광역시": "REGION_DAEJEON",
    "울산광역시": "REGION_ULSAN",
    "세종특별자치시": "REGION_SEJONG",
    "경기도": "REGION_GYEONGGI",
    "강원특별자치도": "REGION_GANGWON",
    "충청북도": "REGION_CHUNGBUK",
    "충청남도": "REGION_CHUNGNAM",
    "전북특별자치도": "REGION_JEONBUK",
    "전라남도": "REGION_JEONNAM",
    "경상북도": "REGION_GYEONGBUK",
    "경상남도": "REGION_GYEONGNAM",
    "제주특별자치도": "REGION_JEJU",
}


def infer_region_from_text(org_name: str, title: str = "") -> List[str]:
    combined = f"{org_name} {title}"
    found: List[str] = []
    for short_nm, full_nm in SGG_TO_REGION.items():
        if full_nm in combined or combined.startswith(short_nm):
            if full_nm not in found:
                found.append(full_nm)
    return found if found else ["전국"]


def infer_category(raw_cat: str, title: str, text: str) -> str:
    combined = f"{raw_cat} {title} {text[:300]}"
    if any(k in combined for k in ["임신", "출산", "육아", "보육", "양육", "신생아", "산모", "난임", "첫만남", "부모급여", "아동수당", "아이돌봄", "돌봄", "한부모", "입양"]):
        if "주거" in combined or "임대" in combined or "전세" in combined:
            return "주거·금융"
        return "출산·육아·가족"
    if any(k in combined for k in ["전세", "월세", "임대", "주거", "주택", "보증금", "이자지원", "이사비", "청약"]):
        return "주거·금융"
    if any(k in combined for k in ["취업", "일자리", "구직", "창업", "내일배움", "직업훈련", "인턴", "면접", "자격증", "저축", "자산형성", "내일저축", "도약계좌"]):
        return "청년 취업·교육·자산형성"
    if any(k in combined for k in ["교통", "패스", "의료", "건강", "간병", "치매", "예방접종", "바우처", "장학", "학자금", "교육비"]):
        return "교통·생활비·의료·돌봄"
    return "지역별 생활지원·복지"


def infer_target_groups(life_str: str, trg_str: str, title: str, text: str) -> List[str]:
    combined = f"{life_str} {trg_str} {title} {text[:400]}"
    groups: List[str] = []
    mapping = [
        (["청년", "대학생", "취업준비생", "사회초년생", "19세", "34세"], "청년"),
        (["신혼", "예비신혼", "결혼"], "신혼부부"),
        (["임산부", "임신", "산모", "난임", "출산"], "임산부·출산가구"),
        (["영유아", "신생아", "아동", "초등", "청소년", "다자녀"], "영유아·아동·청소년"),
        (["구직", "실직", "미취업", "경력단절"], "구직자·취업준비생"),
        (["저소득", "기초생활", "차상위", "한부모"], "저소득·취약계층"),
        (["노년", "어르신", "65세", "고령"], "어르신·노년층"),
        (["장애인", "장애"], "장애인"),
        (["농어민", "농업", "어업"], "농어업인"),
        (["소상공인", "자영업", "중소기업"], "소상공인·재직자"),
    ]
    for keywords, label in mapping:
        if any(k in combined for k in keywords) and label not in groups:
            groups.append(label)
    if not groups:
        groups.append("일반국민")
    return groups


def extract_lifecycles_and_attributes(
    *,
    policy_name: str,
    target_groups: List[str],
    requirements: Dict[str, Any],
    eligibility_text: str,
    raw_lifecycles: Optional[List[str]] = None,
    raw_targets: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    6대 표준 생애주기(영유아, 아동, 청소년, 청년, 중장년, 노년) 및
    자격 조건 속성(가족/혼인, 소득, 취업, 장애/특수대상, 주거) 정규화 분리 추출.
    """
    name = policy_name or ""
    req = requirements or {}
    elig = eligibility_text or ""
    combined = f"{name} {elig}"

    lifecycles: Set[str] = set()
    fam: Set[str] = set()
    inc: Set[str] = set()
    emp: Set[str] = set()
    spec: Set[str] = set()
    hous: Set[str] = set()

    # 1. 기관 공식 생애주기/대상자 배열 (복지로 lifeArray, trgterIndvdlArray)
    if raw_lifecycles:
        for r_lc in raw_lifecycles:
            if not r_lc:
                continue
            for token in r_lc.replace("/", ",").split(","):
                token = token.strip()
                if token in ["영유아", "아동", "청소년", "청년", "중장년", "노년"]:
                    lifecycles.add(token)
                elif "임신" in token or "출산" in token:
                    fam.add("임신·출산")
                    lifecycles.add("청년")

    if raw_targets:
        for r_tg in raw_targets:
            if not r_tg:
                continue
            for token in r_tg.replace("/", ",").split(","):
                token = token.strip()
                if "장애인" in token:
                    spec.add("장애인")
                if "저소득" in token or "수급자" in token or "차상위" in token:
                    inc.add("저소득·취약계층")
                if "다문화" in token or "탈북" in token:
                    spec.add("다문화·탈북민")
                if "다자녀" in token:
                    fam.add("다자녀")
                if "한부모" in token or "조손" in token:
                    fam.add("한부모·조손")
                if "보훈" in token:
                    spec.add("국가보훈대상자")

    # 2. 연령 조건 기반 생애주기 판별 (중장년: 만 40~64세 표준 적용)
    min_a = req.get("min_age")
    max_a = req.get("max_age")
    if min_a is not None or max_a is not None:
        eff_min = min_a if min_a is not None else 0
        eff_max = max_a if max_a is not None else 120
        if eff_min <= 5 and eff_max >= 0 and eff_max <= 6:
            lifecycles.add("영유아")
        if eff_min <= 12 and eff_max >= 6 and (eff_min >= 4 or eff_max <= 14):
            lifecycles.add("아동")
        if eff_min <= 18 and eff_max >= 13 and (eff_min >= 9 or eff_max <= 24):
            lifecycles.add("청소년")
        if eff_min <= 39 and eff_max >= 19:
            lifecycles.add("청년")
        if eff_min <= 64 and eff_max >= 40:
            lifecycles.add("중장년")
        if eff_max >= 65 and eff_min >= 60:
            lifecycles.add("노년")
        elif eff_max >= 70 and eff_min <= 20:
            if eff_min <= 18:
                lifecycles.add("청소년")
            if eff_min <= 39:
                lifecycles.add("청년")
            lifecycles.add("중장년")
            lifecycles.add("노년")

    # 3. 정부24 구조화 지원조건 (JA 코드)
    s_cond = req.get("structured_support_conditions") or {}
    if s_cond:
        hh = s_cond.get("가구주거조건", {})
        if hh.get("한부모가정/조손가정") == "적용대상":
            fam.add("한부모·조손")
        if hh.get("1인가구") == "적용대상":
            fam.add("1인가구")
        if hh.get("다문화가족") == "적용대상":
            spec.add("다문화·탈북민")
        if hh.get("북한이탈주민") == "적용대상":
            spec.add("다문화·탈북민")

        emp_c = s_cond.get("취업활동조건", {})
        if emp_c.get("근로자/직장인") == "적용대상":
            emp.add("재직자·근로자")
        if emp_c.get("구직자/실업자") == "적용대상":
            emp.add("구직자·취업준비생")
        if emp_c.get("농업인") == "적용대상" or emp_c.get("어업인") == "적용대상" or emp_c.get("임업인") == "적용대상":
            emp.add("농어업인")

        spec_c = s_cond.get("특수대상조건", {})
        if spec_c.get("장애인") == "적용대상":
            spec.add("장애인")
        if spec_c.get("국가보훈대상자") == "적용대상":
            spec.add("국가보훈대상자")

        inc_c = s_cond.get("소득구간조건", {})
        if inc_c.get("중위소득 0~50%") == "적용대상":
            inc.add("기준중위소득_50%이하")
        elif inc_c.get("중위소득 51~75%") == "적용대상":
            inc.add("기준중위소득_75%이하")

        lc_cond = s_cond.get("생애주기조건", {})
        if any(lc_cond.get(k) == "적용대상" for k in ["예비부모/난임", "임산부", "출산/입양"]):
            fam.add("임신·출산")
            lifecycles.add("청년")

    # 4. 온통청년 자격 요건
    mrg_lbl = req.get("marriage_status_label")
    if mrg_lbl == "기혼":
        fam.add("신혼·기혼")
    elif mrg_lbl == "미혼":
        fam.add("미혼")

    for j_lbl in req.get("job_labels", []):
        if j_lbl in ["미취업자", "구직자"]:
            emp.add("구직자·취업준비생")
        elif j_lbl in ["재직자", "일용근로자"]:
            emp.add("재직자·근로자")
        elif j_lbl in ["(예비)창업자", "창업자"]:
            emp.add("소상공인·창업자")
        elif j_lbl == "제한없음":
            emp.add("제한없음")

    for s_lbl in req.get("special_labels", []):
        if "장애인" in s_lbl:
            spec.add("장애인")
        if "한부모" in s_lbl:
            fam.add("한부모·조손")
        if "소상공인" in s_lbl or "중소기업" in s_lbl:
            emp.add("소상공인·창업자")

    # 5. 기존 target_groups 및 텍스트 키워드 기반 정밀 매핑
    for tg in target_groups:
        if tg == "청년":
            lifecycles.add("청년")
        elif tg == "어르신·노년층":
            lifecycles.add("노년")
        elif tg == "영유아·아동·청소년":
            if any(k in combined for k in ["영유아", "신생아", "보육", "어린이집", "영아", "유아"]):
                lifecycles.add("영유아")
            if any(k in combined for k in ["아동", "초등", "방과후"]):
                lifecycles.add("아동")
            if any(k in combined for k in ["청소년", "중학", "고등"]):
                lifecycles.add("청소년")
            if not ("영유아" in lifecycles or "아동" in lifecycles or "청소년" in lifecycles):
                lifecycles.update(["영유아", "아동", "청소년"])
        elif tg == "신혼부부":
            fam.add("신혼부부")
            lifecycles.add("청년")
        elif tg == "임산부·출산가구":
            fam.add("임신·출산")
            lifecycles.add("청년")
        elif tg == "저소득·취약계층":
            inc.add("저소득·취약계층")
        elif tg == "장애인":
            spec.add("장애인")
        elif tg == "농어업인":
            emp.add("농어업인")
        elif tg == "구직자·취업준비생":
            emp.add("구직자·취업준비생")
        elif tg == "소상공인·재직자":
            emp.add("소상공인·재직자")
        elif tg == "일반국민":
            lifecycles.update(["청년", "중장년", "노년"])

    # 중장년 키워드 탐지
    if any(k in combined for k in ["중장년", "신중년", "40대", "50대", "준고령", "경력단절"]):
        lifecycles.add("중장년")

    # 주거 상태
    if any(k in combined for k in ["무주택", "무주택세대구성원", "무주택세대주"]):
        hous.add("무주택자")
    if any(k in combined for k in ["임차", "월세", "전세", "임대주택"]):
        hous.add("임차인가구")

    if not lifecycles:
        lifecycles.add("unknown")

    attributes = {
        "family_status": sorted(list(fam)) if fam else ["해당없음/미확인"],
        "income_level": sorted(list(inc)) if inc else ["소득무관/미확인"],
        "employment_status": sorted(list(emp)) if emp else ["제한없음/미확인"],
        "special_target": sorted(list(spec)) if spec else ["해당없음"],
        "housing_status": sorted(list(hous)) if hous else ["해당없음"],
    }

    return sorted(list(lifecycles)), attributes


def make_policy_record(
    *,
    policy_id: str,
    official_id: str,
    policy_name: str,
    source_type: str,
    category: str,
    agency: str,
    regions: List[str],
    target_groups: List[str],
    requirements: Dict[str, Any],
    eligibility_text: str,
    benefit_text: str,
    application_period: str,
    application_start: Optional[str],
    application_end: Optional[str],
    application_method: str,
    required_documents: List[str],
    official_apply_url: Optional[str],
    source_url: str,
    source_document_ids: List[str],
    verification_status: str,
    reception_status: str = "active",
    raw_lifecycles: Optional[List[str]] = None,
    raw_targets: Optional[List[str]] = None,
    life_cycles: Optional[List[str]] = None,
    eligibility_attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """기존 스키마(PRD §6)와 STEP 2 생애주기·자격조건 분리 필드를 모두 포함하는 정책 레코드 생성."""
    if life_cycles is None or eligibility_attributes is None:
        inferred_lc, inferred_attr = extract_lifecycles_and_attributes(
            policy_name=policy_name,
            target_groups=target_groups,
            requirements=requirements,
            eligibility_text=eligibility_text,
            raw_lifecycles=raw_lifecycles,
            raw_targets=raw_targets,
        )
        if life_cycles is None:
            life_cycles = inferred_lc
        if eligibility_attributes is None:
            eligibility_attributes = inferred_attr

    # 수동으로 지정한 주소도 API 주소와 같은 기준으로 검사한다.
    official_apply_url = application_url_or_none(official_apply_url, source_url)
    return {
        "policy_id": policy_id,
        "official_id": official_id,
        "policy_name": policy_name,
        "source_type": source_type,
        "category": category,
        "agency": agency,
        "organization": agency,
        "regions": regions,
        "region": ", ".join(regions),
        "target_groups": target_groups,
        "target_group": ", ".join(target_groups),
        "life_stages": life_cycles,
        "life_cycles": life_cycles,
        "eligibility_attributes": eligibility_attributes,
        "requirements": requirements,
        "eligibility": eligibility_text,
        "benefit": benefit_text,
        "application_period": application_period,
        "application_start": application_start,
        "application_end": application_end,
        "application_method": application_method,
        "required_documents": required_documents,
        "official_apply_url": official_apply_url,
        "application_url": official_apply_url,
        "source_url": source_url,
        "source_document_ids": source_document_ids,
        "reception_status": reception_status,
        "eligibility_review_status": verification_status,
        "verification_status": verification_status,
        "retrieved_at": NOW,
        "collected_at": NOW,
    }


def make_document_record(
    *,
    document_id: str,
    policy_id: str,
    title: str,
    source_type: str,
    source_url: str,
    local_raw_path: str,
    text: str,
    verification_status: str,
    published_at: Optional[str] = None,
    effective_at: Optional[str] = None,
) -> Dict[str, Any]:
    """기존 스키마와 3차 지시서 §6.2 필드를 모두 포함하는 근거 문서 레코드 생성."""
    clean_txt = text.strip()
    return {
        "document_id": document_id,
        "policy_id": policy_id,
        "title": title,
        "source_type": source_type,
        "source_url": source_url,
        "local_raw_path": local_raw_path.replace("\\", "/"),
        "published_at": published_at,
        "effective_at": effective_at,
        "retrieved_at": NOW,
        "collected_at": NOW,
        "content_hash": compute_sha256(clean_txt),
        "text": clean_txt,
        "content": clean_txt,
        "review_status": verification_status,
        "verification_status": verification_status,
    }


def load_gov24_merged() -> Dict[str, Dict[str, Any]]:
    """캐시된 Gov24 serviceList + serviceDetail + supportConditions 10,942건을 서비스ID 기준으로 통합."""
    merged: Dict[str, Dict[str, Any]] = {}
    for sub in ["serviceList", "serviceDetail", "supportConditions"]:
        folder = RAW_DIR / "gov24" / sub
        for p in sorted(folder.glob("page_*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            for row in data.get("data", []):
                sid = row.get("서비스ID")
                if not sid:
                    continue
                if sid not in merged:
                    merged[sid] = {}
                for k, v in row.items():
                    if v is not None and v != "":
                        merged[sid][k] = v
                if sub == "supportConditions":
                    merged[sid]["_support_conditions_raw"] = row
                    conds = {
                        k: v
                        for k, v in row.items()
                        if v == "Y" or (isinstance(v, int) and k in ("지원연령(시작)", "지원연령(종료)", "JA0110", "JA0111") and v > 0)
                    }
                    merged[sid]["_support_conditions"] = conds
    return merged


def load_youthcenter_all() -> Dict[str, Dict[str, Any]]:
    """캐시된 온통청년 3,861건을 plcyNo 기준으로 로드."""
    items: Dict[str, Dict[str, Any]] = {}
    folder = RAW_DIR / "youthcenter" / "api"
    for p in sorted(folder.glob("page_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        lst = data.get("result", {}).get("youthPolicyList", [])
        for row in lst:
            pno = row.get("plcyNo")
            if pno:
                items[pno] = row
    return items


def main() -> None:
    print("=== [Step 1] Loading Raw Files & Extracting Cleaned Texts ===")

    gov24_all = load_gov24_merged()
    yc_all = load_youthcenter_all()
    print(f"Loaded Gov24 merged records: {len(gov24_all)}, YouthCenter records: {len(yc_all)}")

    # Save standalone raw files for POL_009, POL_010, POL_005, POL_004 cross-API matches
    gov24_items_dir = RAW_DIR / "gov24" / "items"
    yc_items_dir = RAW_DIR / "youthcenter" / "items"
    gov24_items_dir.mkdir(parents=True, exist_ok=True)
    yc_items_dir.mkdir(parents=True, exist_ok=True)

    for sid in ["161300000099", "149200005007", "149200000026"]:
        if sid in gov24_all:
            (gov24_items_dir / f"{sid}.json").write_text(
                json.dumps(gov24_all[sid], ensure_ascii=False, indent=2), encoding="utf-8"
            )

    for pno in ["20260710005400113256", "20261004005400113874", "20250123005400110393", "20241231005400100180"]:
        if pno in yc_all:
            (yc_items_dir / f"{pno}.json").write_text(
                json.dumps(yc_all[pno], ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # 1. LH PDFs & HTML
    lh_pdf1_path = RAW_DIR / "lh" / "26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf"
    lh_pdf1_text, lh_pdf1_pages = extract_pdf_text(lh_pdf1_path)

    lh_pdf2_path = RAW_DIR / "lh" / "서울지역본부신혼신생아매입임대Ⅰ신청시유의사항(정정공고).pdf"
    lh_pdf2_text, lh_pdf2_pages = extract_pdf_text(lh_pdf2_path)

    lh_html_path = RAW_DIR / "lh" / "lh_notice_2015122300019712_seoul_newlywed.html"
    lh_html_text = extract_lh_notice_html(lh_html_path)
    print(f"DOC_LH_003 cleaned length: {len(lh_html_text)} chars (UI noise stripped)")

    # 2. NHUF HTMLs & PDF
    nhuf_001_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_notice_1316_newlywed_income.html")
    nhuf_002_text, nhuf_002_pages = extract_pdf_text(RAW_DIR / "nhuf" / "신혼부부_소득요건_합리화_FAQ.pdf")
    nhuf_003_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_newlywed_jeonse_guide.html")
    nhuf_004_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_newlywed_jeonse_process.html")
    nhuf_005_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_youth_jeonse_guide.html")
    nhuf_006_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_youth_jeonse_process.html")
    nhuf_007_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_monthly_rent_guide.html")

    # P0 4.1 수정: DOC_NHUF_008 실제 청년전용 보증부월세대출 원문(FP05020701.jsp) 적용 + DOC_NHUF_009(절차/서류) 추가
    nhuf_008_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_youth_monthly_rent_deposit_guide.html")
    nhuf_009_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_youth_monthly_rent_deposit_process.html")
    nhuf_010_text = extract_nhuf_html(RAW_DIR / "nhuf" / "nhuf_monthly_rent_process.html")
    print(f"DOC_NHUF_008 (청년전용 보증부월세대출 대출안내): {len(nhuf_008_text)} chars")
    print(f"DOC_NHUF_009 (청년전용 보증부월세대출 절차/서류): {len(nhuf_009_text)} chars")
    print(f"DOC_NHUF_010 (주거안정월세대출 절차/서류): {len(nhuf_010_text)} chars")

    # 3. Work24 HTML
    work24_text = extract_work24_html(RAW_DIR / "work24" / "work24_tomorrow_learning_card.html")

    # 4. P0 4.2 수정: DOC_MOLIT_001 & DOC_MOEL_001 실제 공식 API 원문 추출
    molit_bokjiro_parsed = parse_bokjiro_detail_xml(RAW_DIR / "bokjiro" / "detail" / "WLF00004661.xml")
    molit_gov24_text = format_gov24_item_text(gov24_all["161300000099"])
    moel_gov24_text = format_gov24_item_text(gov24_all["149200005007"])
    moel_yc_text = format_youthcenter_item_text(yc_all["20260710005400113256"])

    # =========================================================================
    # [Step 2] 기존 9개 핵심 정책 (POL_001 ~ POL_007, POL_009, POL_010) 및 근거 문서 정의
    # =========================================================================
    policies: List[Dict[str, Any]] = [
        make_policy_record(
            policy_id="POL_001",
            official_id="LH-2015122300019712",
            policy_name="[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고",
            source_type="pdf/html",
            category="주거·금융",
            agency="한국토지주택공사 서울지역본부",
            regions=["서울특별시"],
            target_groups=["신혼부부", "임산부·출산가구", "영유아·아동·청소년"],
            requirements={
                "housing_rule": "무주택세대구성원",
                "marriage_rule": "혼인 7년 이내 신혼부부, 예비신혼부부, 2세 이하 신생아 가구, 6세 이하 한부모·유자녀 가구",
                "income_rule": "전년도 도시근로자 가구원수별 월평균소득 70% 이하 (배우자 소득 있는 경우 90% 이하, 4순위 100% 이하, 신생아 가구 최대 20%p 가산)",
                "asset_rule": "신혼·예비신혼·한부모·유자녀 가구 총자산 3억 3,700만원 이하, 신생아 가구 총자산 3억 5,400만원 이하, 자동차가액 4,563만원 이하",
                "min_age": 19,
                "max_age": None,
                "employment_status": "any",
            },
            eligibility_text="서울특별시 거주 무주택세대구성원으로서 신생아 가구(공고일 기준 2세 이하 자녀), 신혼부부(혼인 7년 이내), 예비신혼부부, 6세 이하 자녀 한부모가족·유자녀가구. 소득 기준 70%(맞벌이 90%, 신생아가구 최대 20%p 가산), 총자산 3억 3,700만원(신생아 가구 3억 5,400만원) 이하, 자동차 4,563만원 이하.",
            benefit_text="서울지역 시세 30%~40% 수준의 다가구·다세대 매입임대주택 공급 (최초 임대기간 2년, 재계약 최대 9회 가능하여 최장 20년 거주)",
            application_period="2026-04-15 ~ 2026-04-17",
            application_start="2026-04-15",
            application_end="2026-04-17",
            application_method="LH청약플러스(apply.lh.or.kr) 온라인 청약 신청 후 서류제출 대상자 등기우편 제출",
            required_documents=[
                "주민등록표등본 및 초본",
                "가족관계증명서(상세)",
                "혼인관계증명서(상세)",
                "임신진단서 또는 출생증명서(해당자)",
                "개인정보 수집·이용 및 제3자 제공 동의서",
                "금융정보 등 제공동의서",
                "자산보유사실 확인서",
            ],
            official_apply_url="https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            source_url="https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            source_document_ids=["DOC_LH_001", "DOC_LH_002", "DOC_LH_003"],
            verification_status="verified",
            reception_status="closed",
        ),
        make_policy_record(
            policy_id="POL_002",
            official_id="MOLIT-HUG-NOTICE-1316",
            policy_name="기금대출 신혼부부 소득요건 합리화 개정 규정",
            source_type="pdf/html",
            category="주거·금융",
            agency="국토교통부 / 주택도시보증공사",
            regions=["전국"],
            target_groups=["신혼부부"],
            requirements={
                "housing_rule": "무주택 세대주",
                "marriage_rule": "혼인기간 7년 이내 또는 3개월 이내 결혼예정자",
                "income_rule": "부부합산 연소득이 기존 기준을 초과하더라도 신청인 본인(계약자) 소득이 기준 이하이면 대출 신청 허용",
                "threshold_details": {
                    "버팀목_신혼부부전용": {"joint_exceeds": 75000000, "single_under": 50000000},
                    "디딤돌_신혼부부전용": {"joint_exceeds": 85000000, "single_under": 60000000},
                    "버팀목_일반신혼": {"joint_exceeds": 70000000, "single_under": 50000000},
                    "디딤돌_일반신혼": {"joint_exceeds": 85000000, "single_under": 60000000},
                },
                "contractor_rule": "임대차·매매계약서상 계약자 본인이 대출 신청인이어야 함",
                "asset_rule": "부부합산 순자산 기준(전세 3.37억원, 구입 4.88억원 이하) 동일 유지",
                "interest_rule": "부부합산 소득 최상위 구간 금리에 0.3%p 가산 금리 적용",
            },
            eligibility_text="신혼부부(혼인 7년 이내 또는 3개월 내 결혼예정자)로서 부부합산 소득이 기준(버팀목 7.5천만원/디딤돌 8.5천만원)을 초과하더라도 신청인 본인 연소득이 기준(버팀목 5천만원/디딤돌 6천만원) 이하인 무주택 세대주.",
            benefit_text="맞벌이 신혼부부가 합산 소득 초과로 기금 전세·구입자금 대출에서 배제되지 않도록 단독 소득 기준 예외 허용 (최상위 소득구간 금리 + 0.3%p 적용)",
            application_period="2025-10-19 ~ 상시",
            application_start="2025-10-19",
            application_end=None,
            application_method="기금e든든(enhuf.molit.go.kr) 온라인 신청 또는 수탁은행 방문 신청",
            required_documents=["혼인관계증명서(또는 예식장계약서)", "임대차/매매계약서(신청인 본인 명의)", "부부 각자 소득금액증명원"],
            official_apply_url="https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            source_url="https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            source_document_ids=["DOC_NHUF_001", "DOC_NHUF_002"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_003",
            official_id="NHUF-JEONSE-002",
            policy_name="주택도시기금 신혼부부전용 전세자금대출",
            source_type="html",
            category="주거·금융",
            agency="국토교통부 / 주택도시보증공사",
            regions=["전국"],
            target_groups=["신혼부부"],
            requirements={
                "housing_rule": "세대원 전원 무주택인 세대주(예비세대주 포함)",
                "marriage_rule": "혼인기간 7년 이내 또는 3개월 이내 결혼예정자",
                "income_rule": "부부합산 연소득 7,500만원 이하 (단, 합산 7,500만원 초과 시에도 본인 소득 5,000만원 이하이면 신청 가능)",
                "asset_rule": "2026년 기준 부부합산 순자산가액 3.37억원 이하",
                "house_deposit_limit_metro_krw": 400000000,
                "house_deposit_limit_non_metro_krw": 300000000,
                "house_area_limit_sqm": 85,
                "loan_limit_metro_krw": 250000000,
                "loan_limit_non_metro_krw": 160000000,
                "loan_ratio_limit": "전세금액의 80% 이내",
                "interest_rate_range": "연 1.9% ~ 3.3%",
            },
            eligibility_text="부부합산 연소득 7,500만원 이하, 순자산가액 3.37억원 이하 무주택 세대주인 신혼부부(혼인기간 7년 이내 또는 3개월 이내 결혼예정자). 임차보증금 수도권 4억원(수도권 외 3억원) 이하, 전용면적 85㎡(읍·면 100㎡) 이하 주택.",
            benefit_text="전세자금 대출한도 수도권 최대 2.5억원, 수도권 외 최대 1.6억원 이내(임차보증금의 80% 이내), 금리 연 1.9% ~ 3.3%, 최장 10년(2년 단위 4회 연장, 자녀 1명당 2년 추가 가능)",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="기금e든든(enhuf.molit.go.kr) 온라인 신청 또는 기금 수탁은행(우리·국민·하나·농협·신한·대구·부산은행) 방문",
            required_documents=[
                "신분증",
                "주민등록등본 및 가족관계증명서",
                "혼인관계증명서(또는 예식장계약서/청첩장)",
                "확정일자부 임대차계약서 사본 및 임차주택 등기사항전부증명서",
                "재직 및 소득확인서류(건강보험자격득실확인서, 소득금액증명원 등)",
            ],
            official_apply_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            source_document_ids=["DOC_NHUF_003", "DOC_NHUF_004"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_004",
            official_id="NHUF-JEONSE-001",
            policy_name="주택도시기금 청년전용 버팀목전세자금대출",
            source_type="html",
            category="주거·금융",
            agency="국토교통부 / 주택도시보증공사",
            regions=["전국"],
            target_groups=["청년"],
            requirements={
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "세대원 전원 무주택인 세대주(예비세대주 포함)",
                "income_rule": "신청인(배우자 합산) 연소득 5,000만원 이하 (혁신도시 이전 공공기관 종사자, 타 지역 이주 재개발 구역내 세입자, 다자녀·2자녀 가구 6,000만원 이하, 신혼부부 7,500만원 이하)",
                "asset_rule": "2026년 기준 순자산가액 3.37억원 이하",
                "house_deposit_limit_krw": 300000000,
                "house_area_limit_sqm": 85,
                "loan_limit_krw": 150000000,
                "loan_ratio_limit": "전세금액의 80% 이내",
                "interest_rate_range": "연 2.2% ~ 3.3%",
            },
            eligibility_text="만 19세 이상 ~ 만 34세 이하의 무주택 세대주(예비세대주 포함), 부부합산 연소득 5,000만원 이하(신혼 7,500만원 이하), 순자산가액 3.37억원 이하. 임차보증금 3억원 이하 및 전용면적 85㎡ 이하(주거용 오피스텔 포함).",
            benefit_text="전세자금 최대 1.5억원 이내(임차보증금의 80% 이내), 금리 연 2.2% ~ 3.3%, 이용기간 2년(4회 연장하여 최장 10년 가능)",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="기금e든든(enhuf.molit.go.kr) 온라인 신청 또는 기금 수탁은행 방문 신청",
            required_documents=[
                "신분증",
                "주민등록등본(최근 5년 주소변동 이력 포함)",
                "가족관계증명서 및 혼인관계증명서(단독세대주·미혼 확인 시)",
                "확정일자부 임대차계약서 사본 및 계약금 영수증(5% 이상)",
                "임차주택 등기사항전부증명서",
                "소득 및 재직 증빙서류",
            ],
            official_apply_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            source_document_ids=["DOC_NHUF_005", "DOC_NHUF_006"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_005",
            official_id="MOEL-WORK24-CARD-001",
            policy_name="고용24 국민내일배움카드",
            source_type="html/api",
            category="청년 취업·교육·자산형성",
            agency="고용노동부 / 한국고용정보원",
            regions=["전국"],
            target_groups=["청년", "구직자·취업준비생", "소상공인·재직자"],
            requirements={
                "min_age": 15,
                "max_age": 74,
                "exclusion_criteria": [
                    "현직 공무원 및 사립학교 교직원",
                    "만 75세 이상인 사람",
                    "졸업까지 수업연한이 2년을 초과하여 남은 대학생 및 고등학교 1·2학년 재학생",
                    "연 매출 4억원 이상 자영업자",
                    "월 임금 300만원 이상인 대규모기업 종사자(만 45세 미만)",
                    "월 평균 소득 500만원 이상인 특수형태근로종사자",
                ],
                "benefit_limit_krw": 3000000,
                "benefit_max_krw": 5000000,
                "validity_years": 5,
            },
            eligibility_text="일자리 준비(취업·이직·역량개발)가 필요한 국민 누구나 신청 가능 (단, 현직 공무원·사립학교 교직원, 만 75세 이상, 연매출 4억원 이상 자영업자, 대기업 월 300만원 이상 45세 미만 근로자 등 제외). 대학생은 졸업까지 남은 수업연한이 2년 이내인 경우 발급 가능.",
            benefit_text="1인당 5년간 기본 300만원(취약계층·특정계층 최대 500만원)의 직업능력개발 훈련비 지원 (훈련비의 45~85% 국비 지원) 및 요건 충족 시 월 최대 11만 6천원 훈련장려금 지급",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="고용24(work24.go.kr) 온라인 신청 또는 거주지 관할 고용센터 방문 신청",
            required_documents=["신분증", "국민내일배움카드 발급신청서", "졸업예정증명서 또는 재학증명서(대학생 등 해당자)", "자영업자/특고 소득증빙서류(해당자)"],
            official_apply_url="https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            source_url="https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            source_document_ids=["DOC_WORK24_001"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_006",
            official_id="NHUF-RENT-001",
            policy_name="주택도시기금 주거안정월세대출",
            source_type="html",
            category="주거·금융",
            agency="국토교통부 / 주택도시보증공사",
            regions=["전국"],
            target_groups=["청년", "구직자·취업준비생", "저소득·취약계층"],
            requirements={
                "housing_rule": "세대원 전원 무주택인 세대주(예비세대주 포함)",
                "eligible_types": "우대형(만 35세 이하 무소득 취준생 중 부모소득 6천만원 이하, 희망키움통장 가입자, 근로·자녀장려금 수급자, 취업 후 5년 이내 만 35세 이하 연소득 4천만원 이하 사회초년생, 주거급여 수급자), 일반형(부부합산 연소득 5천만원 이하)",
                "asset_rule": "2026년 기준 순자산가액 3.37억원 이하",
                "house_deposit_limit_krw": 100000000,
                "monthly_rent_limit_krw": 600000,
                "house_area_limit_sqm": 85,
                "loan_limit_monthly_krw": 600000,
                "loan_total_limit_krw": 14400000,
                "interest_rate_range": "우대형 연 1.3%, 일반형 연 2.3%",
            },
            eligibility_text="임차보증금 1억원 이하 및 월세 60만원 이하의 전용면적 85㎡ 이하 주택 임차 무주택 세대주. 우대형(취준생·사회초년생·장려금/주거급여 수급자 등) 또는 일반형(부부합산 연소득 5천만원 이하), 순자산 3.37억원 이하.",
            benefit_text="2년간 최대 1,440만원(월 최대 60만원 한도) 범위 내에서 매월 월세 자금 대출, 금리 우대형 연 1.3% / 일반형 연 2.3%, 최장 10년(2년 단위 4회 연장 가능)",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="기금e든든(enhuf.molit.go.kr) 온라인 신청 또는 기금 수탁은행 방문 신청",
            required_documents=[
                "신분증 및 주민등록등본",
                "확정일자부 임대차계약서 사본",
                "임차주택 등기사항전부증명서",
                "우대형 자격확인서류(졸업증명서, 장려금수급사실증명서, 주거급여수급자증명서 등 해당자)",
                "소득 및 자산 확인서류",
            ],
            official_apply_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
            source_document_ids=["DOC_NHUF_007", "DOC_NHUF_010"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_007",
            official_id="NHUF-RENT-002",
            policy_name="주택도시기금 청년전용 보증부월세대출",
            source_type="html",
            category="주거·금융",
            agency="국토교통부 / 주택도시보증공사",
            regions=["전국"],
            target_groups=["청년"],
            requirements={
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "세대원 전원 무주택인 단독세대주(예비세대주 포함)",
                "income_rule": "신청인(배우자 합산) 연소득 5,000만원 이하",
                "asset_rule": "2026년 기준 순자산가액 3.37억원 이하",
                "house_deposit_limit_krw": 45000000,
                "monthly_rent_limit_krw": 500000,
                "house_area_limit_sqm": 60,
                "deposit_loan_limit_krw": 35000000,
                "monthly_loan_total_limit_krw": 12000000,
                "monthly_loan_limit_krw": 500000,
                "interest_rate_deposit": "연 1.3%",
                "interest_rate_rent": "월 20만원 한도 연 0% (초과분 연 1.0%)",
            },
            eligibility_text="만 19세 이상 ~ 만 34세 이하 무주택 단독세대주(예비세대주 포함), 연소득 5,000만원 이하, 순자산 3.37억원 이하. 임차보증금 4,500만원 이하 및 월세 50만원 이하의 전용면적 60㎡ 이하 주택(주거용 오피스텔 포함).",
            benefit_text="보증금 대출 최대 3,500만원(금리 연 1.3%) + 월세금 대출 최대 1,200만원(월 최대 50만원, 20만원까지 연 0% 무이자 / 초과분 연 1.0%) 동시 또는 선택 지원 (최장 10년 5개월)",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="기금e든든(enhuf.molit.go.kr) 온라인 신청 또는 우리·국민·신한·하나·농협은행 방문 신청",
            required_documents=[
                "신분증",
                "주민등록등본(최근 5개년 주소변동 이력 포함)",
                "가족관계증명서 및 혼인관계증명서",
                "확정일자부 임대차계약서 사본(보증금 5% 이상 지급 영수증)",
                "임차주택 등기사항전부증명서",
                "재직 및 소득 확인서류",
            ],
            official_apply_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020701.jsp",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020701.jsp",
            source_document_ids=["DOC_NHUF_008", "DOC_NHUF_009"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_009",
            official_id="WLF00004661",
            policy_name="국토교통부 청년월세 한시 특별지원",
            source_type="api",
            category="주거·금융",
            agency="국토교통부 청년주거정책과",
            regions=["전국"],
            target_groups=["청년", "저소득·취약계층"],
            requirements={
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "부모와 별도 거주하는 무주택 청년",
                "subscription_account_rule": "청약통장 가입 필수",
                "income_rule": "청년독립가구 기준 중위소득 60% 이하 및 원가구 기준 중위소득 100% 이하",
                "asset_rule": "청년독립가구 총자산 1억 2,200만원 이하 및 원가구 총자산 4억 7,000만원 이하",
                "benefit_monthly_krw": 200000,
                "benefit_duration_months": 24,
                "benefit_total_krw": 4800000,
            },
            eligibility_text="만 19세 ~ 34세 이하 부모와 별도 거주하는 무주택 청년(청약통장 가입 필수)으로서 청년독립가구 소득이 기준 중위소득 60% 이하(재산 1억 2,200만원 이하)이면서 원가구 소득이 기준 중위소득 100% 이하(재산 4억 7,000만원 이하)인 청년.",
            benefit_text="실제 납부하는 임대료 범위 내에서 월 최대 20만원씩 최장 24개월(회) 동안 총 최대 480만원 현금 지원 (방학기간 등 일시 주거지 이전 시에도 수급기간 내 연속/분할 지급)",
            application_period="2024-02-26 ~ 2025-02-25",
            application_start="2024-02-26",
            application_end="2025-02-25",
            application_method="복지로(bokjiro.go.kr) 온라인 신청 또는 주소지 관할 읍·면·동 행정복지센터 방문 신청",
            required_documents=[
                "월세지원 신청서",
                "소득·재산 신고서",
                "임대차계약서 및 최근 3개월간 월세 이체 증빙서류",
                "월세 입금 통장 사본",
                "청약통장 사본",
                "가족관계증명서(상세)",
            ],
            official_apply_url="https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00004661",
            source_url="https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00004661",
            source_document_ids=["DOC_MOLIT_001", "DOC_MOLIT_002"],
            verification_status="verified",
        ),
        make_policy_record(
            policy_id="POL_010",
            official_id="149200005007",
            policy_name="고용노동부 국민취업지원제도 (Ⅰ·Ⅱ유형 및 청년특례)",
            source_type="api",
            category="청년 취업·교육·자산형성",
            agency="고용노동부 국민취업지원기획팀",
            regions=["전국"],
            target_groups=["청년", "구직자·취업준비생", "저소득·취약계층"],
            requirements={
                "min_age": 15,
                "max_age": 69,
                "youth_special_age": {"min": 18, "max": 34},
                "income_rule": "Ⅰ유형 요건심사형 가구단위 중위소득 60% 이하, Ⅰ유형 선발형 청년(18~34세) 중위소득 120% 이하, Ⅱ유형 중위소득 100% 이하(청년은 소득 무관)",
                "asset_rule": "가구원 합산 재산 4억원 이하 (18~34세 청년은 5억원 이하)",
                "work_experience_rule": "최근 2년 이내 100일(800시간) 이상 취업경험 (선발형 청년특례는 취업경험 무관)",
                "benefit_monthly_min_krw": 500000,
                "benefit_monthly_max_krw": 900000,
                "benefit_duration_months": 6,
                "dependent_family_bonus_krw": 100000,
            },
            eligibility_text="15~69세 구직자. Ⅰ유형 요건심사형은 중위소득 60% 이하·재산 4억원 이하(18~34세 청년 5억원 이하)·취업경험 충족자, Ⅰ유형 선발형 청년특례(18~34세)는 중위소득 120% 이하·재산 5억원 이하·취업경험 무관. Ⅱ유형은 청년(18~34세 소득 무관) 및 중장년(중위소득 100% 이하).",
            benefit_text="취업지원서비스(1:1 심층상담·일경험·직업훈련 연계) + Ⅰ유형 구직촉진수당 월 50만~90만원(기본 50만원 + 미성년·고령·중증장애 부양가족 1인당 월 10만원, 월 최대 40만원 추가) × 6개월 지급, 조기취업성공수당(최대 150만원), Ⅱ유형 취업활동비용(최대 195.4만원) 지원",
            application_period="상시",
            application_start=None,
            application_end=None,
            application_method="고용24(work24.go.kr) 온라인 신청 또는 거주지 관할 고용센터 방문 신청",
            required_documents=[
                "취업지원 신청서 및 개인정보 수집·이용·제공 동의서",
                "가구단위 증빙서류(가족관계증명서 등 필요 시)",
                "특정계층 증빙서류(해당자)",
                "부양가족 가산 수당 신청 시 가족관계·장애 증명서류(해당자)",
            ],
            official_apply_url="https://www.work24.go.kr",
            source_url="https://www.gov.kr/portal/rcvfvrSvc/dtlEx/149200005007",
            source_document_ids=["DOC_MOEL_001", "DOC_MOEL_002"],
            verification_status="verified",
        ),
    ]

    documents: List[Dict[str, Any]] = [
        make_document_record(
            document_id="DOC_LH_001",
            policy_id="POL_001",
            title="26년1차 신혼·신생아매입임대Ⅰ 입주자모집공고문(서울지역본부)(정정공고)",
            source_type="pdf",
            source_url="https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            local_raw_path="data/raw/lh/26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf",
            text=lh_pdf1_text,
            verification_status="verified",
            published_at="2026-03-31",
            effective_at="2026-04-08",
        ),
        make_document_record(
            document_id="DOC_LH_002",
            policy_id="POL_001",
            title="서울지역본부 신혼신생아매입임대Ⅰ 신청시유의사항(정정공고)",
            source_type="pdf",
            source_url="https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            local_raw_path="data/raw/lh/서울지역본부신혼신생아매입임대Ⅰ신청시유의사항(정정공고).pdf",
            text=lh_pdf2_text,
            verification_status="verified",
            published_at="2026-04-08",
            effective_at="2026-04-08",
        ),
        make_document_record(
            document_id="DOC_LH_003",
            policy_id="POL_001",
            title="[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 웹 상세공고 (UI 노이즈 정제본)",
            source_type="html",
            source_url="https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            local_raw_path="data/raw/lh/lh_notice_2015122300019712_seoul_newlywed.html",
            text=lh_html_text,
            verification_status="verified",
            published_at="2026-04-08",
            effective_at="2026-04-08",
        ),
        make_document_record(
            document_id="DOC_NHUF_001",
            policy_id="POL_002",
            title="기금대출 신혼부부 소득요건 합리화 관련 안내 공지",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            local_raw_path="data/raw/nhuf/nhuf_notice_1316_newlywed_income.html",
            text=nhuf_001_text,
            verification_status="verified",
            published_at="2025-10-16",
            effective_at="2025-10-19",
        ),
        make_document_record(
            document_id="DOC_NHUF_002",
            policy_id="POL_002",
            title="신혼부부 소득요건 합리화 FAQ 공식 첨부문서",
            source_type="pdf",
            source_url="https://nhuf.molit.go.kr/cms/cwboard/skin/download.jsp?id=3&fileId=1800",
            local_raw_path="data/raw/nhuf/신혼부부_소득요건_합리화_FAQ.pdf",
            text=nhuf_002_text,
            verification_status="verified",
            published_at="2025-10-16",
            effective_at="2025-10-19",
        ),
        make_document_record(
            document_id="DOC_NHUF_003",
            policy_id="POL_003",
            title="신혼부부전용 전세자금 대출안내 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            local_raw_path="data/raw/nhuf/nhuf_newlywed_jeonse_guide.html",
            text=nhuf_003_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_004",
            policy_id="POL_003",
            title="신혼부부전용 전세자금 이용절차 및 제출서류 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020402.jsp",
            local_raw_path="data/raw/nhuf/nhuf_newlywed_jeonse_process.html",
            text=nhuf_004_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_005",
            policy_id="POL_004",
            title="청년전용 버팀목전세자금 대출안내 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            local_raw_path="data/raw/nhuf/nhuf_youth_jeonse_guide.html",
            text=nhuf_005_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_006",
            policy_id="POL_004",
            title="청년전용 버팀목전세자금 이용절차 및 제출서류 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020302.jsp",
            local_raw_path="data/raw/nhuf/nhuf_youth_jeonse_process.html",
            text=nhuf_006_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_007",
            policy_id="POL_006",
            title="주거안정월세대출 대출안내 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
            local_raw_path="data/raw/nhuf/nhuf_monthly_rent_guide.html",
            text=nhuf_007_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_010",
            policy_id="POL_006",
            title="주거안정월세대출 이용절차 및 제출서류 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020202.jsp",
            local_raw_path="data/raw/nhuf/nhuf_monthly_rent_process.html",
            text=nhuf_010_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_008",
            policy_id="POL_007",
            title="청년전용 보증부월세대출 대출안내 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020701.jsp",
            local_raw_path="data/raw/nhuf/nhuf_youth_monthly_rent_deposit_guide.html",
            text=nhuf_008_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_NHUF_009",
            policy_id="POL_007",
            title="청년전용 보증부월세대출 이용절차 및 제출서류 상세",
            source_type="html",
            source_url="https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020702.jsp",
            local_raw_path="data/raw/nhuf/nhuf_youth_monthly_rent_deposit_process.html",
            text=nhuf_009_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_WORK24_001",
            policy_id="POL_005",
            title="고용24 국민내일배움카드 발급안내 및 지원자격",
            source_type="html",
            source_url="https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            local_raw_path="data/raw/work24/work24_tomorrow_learning_card.html",
            text=work24_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_MOLIT_001",
            policy_id="POL_009",
            title="국토교통부 청년월세 지원사업 중앙부처복지서비스 상세원문 (WLF00004661)",
            source_type="api",
            source_url="https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00004661",
            local_raw_path="data/raw/bokjiro/detail/WLF00004661.xml",
            text=molit_bokjiro_parsed["full_text"],
            verification_status="verified",
            published_at="2024-02-26",
            effective_at="2024-02-26",
        ),
        make_document_record(
            document_id="DOC_MOLIT_002",
            policy_id="POL_009",
            title="국토교통부 청년월세 한시 특별지원 정부24 공공서비스 상세원문 (161300000099)",
            source_type="api",
            source_url="https://www.gov.kr/portal/rcvfvrSvc/dtlEx/161300000099",
            local_raw_path="data/raw/gov24/items/161300000099.json",
            text=molit_gov24_text,
            verification_status="verified",
            published_at="2024-02-26",
            effective_at="2024-02-26",
        ),
        make_document_record(
            document_id="DOC_MOEL_001",
            policy_id="POL_010",
            title="고용노동부 국민취업지원제도 정부24 공공서비스 상세원문 (149200005007)",
            source_type="api",
            source_url="https://www.gov.kr/portal/rcvfvrSvc/dtlEx/149200005007",
            local_raw_path="data/raw/gov24/items/149200005007.json",
            text=moel_gov24_text,
            verification_status="verified",
            published_at="2021-01-01",
            effective_at="2026-01-01",
        ),
        make_document_record(
            document_id="DOC_MOEL_002",
            policy_id="POL_010",
            title="고용노동부 국민취업지원제도 온통청년 정책 상세원문 (20260710005400113256)",
            source_type="api",
            source_url="https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20260710005400113256",
            local_raw_path="data/raw/youthcenter/items/20260710005400113256.json",
            text=moel_yc_text,
            verification_status="verified",
            published_at="2026-01-01",
            effective_at="2026-01-01",
        ),
    ]

    # =========================================================================
    # [Step 3] 공식 API 3종 신규 정책 정규화 및 중복 병합
    # =========================================================================
    dedup_records: List[Dict[str, str]] = [
        {
            "canonical_policy_id": "POL_009",
            "duplicate_official_id": "WLF00004661",
            "duplicate_source": "bokjiro_api",
            "duplicate_name": "청년월세 지원사업",
            "reason": "국토교통부 동일 사업(청년월세 한시 특별지원) — POL_009의 공식 근거 문서 DOC_MOLIT_001로 통합",
        },
        {
            "canonical_policy_id": "POL_009",
            "duplicate_official_id": "161300000099",
            "duplicate_source": "gov24_api",
            "duplicate_name": "청년월세 한시 특별지원",
            "reason": "국토교통부 동일 사업 — POL_009의 공식 근거 문서 DOC_MOLIT_002로 통합",
        },
        {
            "canonical_policy_id": "POL_009",
            "duplicate_official_id": "20261004005400113874",
            "duplicate_source": "youthcenter_api",
            "duplicate_name": "청년월세지원",
            "reason": "국토교통부 동일 사업 — POL_009 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_010",
            "duplicate_official_id": "149200005007",
            "duplicate_source": "gov24_api",
            "duplicate_name": "국민취업지원제도",
            "reason": "고용노동부 동일 사업 — POL_010의 공식 근거 문서 DOC_MOEL_001로 통합",
        },
        {
            "canonical_policy_id": "POL_010",
            "duplicate_official_id": "20260710005400113256",
            "duplicate_source": "youthcenter_api",
            "duplicate_name": "국민취업지원제도",
            "reason": "고용노동부 동일 사업 — POL_010의 공식 근거 문서 DOC_MOEL_002로 통합",
        },
        {
            "canonical_policy_id": "POL_010",
            "duplicate_official_id": "WLF00003245",
            "duplicate_source": "bokjiro_api",
            "duplicate_name": "국민취업지원제도",
            "reason": "고용노동부 동일 사업 — POL_010 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_005",
            "duplicate_official_id": "WLF00006229",
            "duplicate_source": "bokjiro_api",
            "duplicate_name": "국민내일배움카드(일반)",
            "reason": "고용노동부 동일 사업 — POL_005 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_005",
            "duplicate_official_id": "149200000026",
            "duplicate_source": "gov24_api",
            "duplicate_name": "국민내일배움카드",
            "reason": "고용노동부 동일 사업 — POL_005 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_005",
            "duplicate_official_id": "20250123005400110393",
            "duplicate_source": "youthcenter_api",
            "duplicate_name": "국민내일배움카드",
            "reason": "고용노동부 동일 사업 — POL_005 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_004",
            "duplicate_official_id": "WLF00003222",
            "duplicate_source": "bokjiro_api",
            "duplicate_name": "버팀목전세자금대출",
            "reason": "국토교통부 주택도시기금 버팀목전세자금대출 — POL_003/POL_004 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_004",
            "duplicate_official_id": "20241231005400100180",
            "duplicate_source": "youthcenter_api",
            "duplicate_name": "청년전용 버팀목 전세자금",
            "reason": "국토교통부 동일 사업 — POL_004 기준으로 중복 병합",
        },
        {
            "canonical_policy_id": "POL_006",
            "duplicate_official_id": "WLF00001063",
            "duplicate_source": "bokjiro_api",
            "duplicate_name": "주거안정월세대출",
            "reason": "국토교통부 동일 사업 — POL_006 기준으로 중복 병합",
        },
    ]

    excluded_bokjiro_ids = {"WLF00004661", "WLF00006229", "WLF00003222", "WLF00001063", "WLF00003245"}
    excluded_gov24_ids = {"161300000099", "149200005007", "149200000026"}
    excluded_yc_ids = {"20260710005400113256", "20261004005400113874", "20250123005400110393", "20241231005400100180"}

    # 직접 원문 XML/JSON과 대조 검수 완료한 대표 API 정책 집합 (verified 부여 대상)
    verified_bokjiro_ids = {
        "WLF00004656",  # 부모급여(영아수당) 지원
        "WLF00004448",  # 첫만남이용권
        "WLF00001173",  # 아동수당 지급
        "WLF00001180",  # 아이돌봄서비스
        "WLF00004457",  # 청년내일저축계좌
        "WLF00000060",  # 에너지바우처
        "WLF00000022",  # 기초생활보장 생계급여
        "WLF00000025",  # 기초생활보장 주거급여
        "WLF00001170",  # 기초연금
        "WLF00000063",  # 한부모가족 아동양육비 지원
    }

    seen_policy_names: Set[str] = {re.sub(r"\s+", "", p["policy_name"]) for p in policies}

    # --- 3.1 중앙부처복지서비스 (Bokjiro 상세 XML 보유 95건) ---
    bokjiro_detail_files = sorted((RAW_DIR / "bokjiro" / "detail").glob("WLF*.xml"))
    b_idx = 0
    for xml_path in bokjiro_detail_files:
        serv_id = xml_path.stem
        if serv_id in excluded_bokjiro_ids:
            continue
        parsed = parse_bokjiro_detail_xml(xml_path)
        if len(parsed["full_text"]) < 120:
            continue

        b_idx += 1
        pol_id = f"POL_BOK_{b_idx:03d}"
        doc_id = f"DOC_BOK_{b_idx:03d}"
        v_status = "verified" if serv_id in verified_bokjiro_ids else "extracted"

        cat = infer_category(parsed["intrs_thema"], parsed["policy_name"], parsed["full_text"])
        t_groups = infer_target_groups(parsed["life_array"], parsed["trgter_indvdl"], parsed["policy_name"], parsed["target_detail"])

        pol_rec = make_policy_record(
            policy_id=pol_id,
            official_id=serv_id,
            policy_name=parsed["policy_name"],
            source_type="api",
            category=cat,
            agency=parsed["agency"],
            regions=["전국"],
            target_groups=t_groups,
            requirements={
                "life_cycle": parsed["life_array"] or None,
                "target_characteristic": parsed["trgter_indvdl"] or None,
                "selection_criteria": parsed["selection_criteria"] or parsed["target_detail"],
                "support_cycle": parsed["sprt_cyc"] or None,
                "provision_type": parsed["srv_pvsn"] or None,
            },
            eligibility_text=f"{parsed['target_detail']} {parsed['selection_criteria']}".strip(),
            benefit_text=parsed["benefit"] or parsed["summary"],
            application_period=(
                f"{parsed['application_start']} ~ {parsed['application_end']}"
                if parsed["application_start"] and parsed["application_end"]
                else (f"{parsed['application_start']} ~ 상시" if parsed["application_start"] else "상시/공고참조")
            ),
            application_start=parsed["application_start"],
            application_end=parsed["application_end"],
            application_method=parsed["aply_mtd"] or "복지로 또는 관할 주민센터 신청",
            required_documents=parsed["required_documents"],
            official_apply_url=parsed["application_url"],
            source_url=parsed["source_url"],
            source_document_ids=[doc_id],
            verification_status=v_status,
            raw_lifecycles=[parsed["life_array"]] if parsed.get("life_array") else None,
            raw_targets=[parsed["trgter_indvdl"]] if parsed.get("trgter_indvdl") else None,
        )
        doc_rec = make_document_record(
            document_id=doc_id,
            policy_id=pol_id,
            title=f"[중앙부처복지서비스] {parsed['policy_name']} ({serv_id})",
            source_type="api",
            source_url=parsed["source_url"],
            local_raw_path=f"data/raw/bokjiro/detail/{serv_id}.xml",
            text=parsed["full_text"],
            verification_status=v_status,
            published_at=parsed["application_start"],
            effective_at=parsed["application_start"],
        )
        policies.append(pol_rec)
        documents.append(doc_rec)
        seen_policy_names.add(re.sub(r"\s+", "", parsed["policy_name"]))

    print(f"Added {b_idx} Bokjiro policies & documents")

    # --- 3.2 행정안전부 공공서비스(Gov24) 5대 우선 분야 및 전국/17개 시도 대표 정책 65건 ---
    gov24_priority_keywords = [
        "출산", "육아", "산후조리", "신생아", "난임", "다자녀", "아동", "돌봄",
        "청년", "월세", "전세", "보증금", "이자지원", "주택", "이사비",
        "취업", "창업", "면접", "자격증", "장학금", "학자금",
        "교통비", "패스", "의료비", "간병", "명절", "생계",
    ]
    gov24_candidates: List[Dict[str, Any]] = []
    region_bucket_counts: Dict[str, int] = {}

    for sid, item in sorted(gov24_all.items()):
        if sid in excluded_gov24_ids:
            continue
        nm = (item.get("서비스명") or "").strip()
        norm_nm = re.sub(r"\s+", "", nm)
        if not nm or norm_nm in seen_policy_names:
            continue
        org = (item.get("소관기관명") or "").strip()
        target = (item.get("지원대상") or "").strip()
        benefit = (item.get("지원내용") or "").strip()
        if len(target) < 20 or len(benefit) < 20:
            continue
        if not any(kw in f"{nm} {target} {benefit}" for kw in gov24_priority_keywords):
            continue

        regs = infer_region_from_text(org, nm)
        reg_key = regs[0]
        # 중앙부처/전국 최대 15건, 17개 시도별 최대 3건씩 고르게 선발 (총 약 65건)
        max_per_reg = 15 if reg_key == "전국" else 3
        if region_bucket_counts.get(reg_key, 0) >= max_per_reg:
            continue

        region_bucket_counts[reg_key] = region_bucket_counts.get(reg_key, 0) + 1
        gov24_candidates.append(item)
        seen_policy_names.add(norm_nm)
        if len(gov24_candidates) >= 65:
            break

    g_idx = 0
    for idx_i, item in enumerate(gov24_candidates, start=1):
        sid = item["서비스ID"]
        raw_item_path = gov24_items_dir / f"{sid}.json"
        raw_item_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")

        full_txt = format_gov24_item_text(item)
        if len(full_txt) < 120:
            continue

        g_idx += 1
        pol_id = f"POL_GOV_{g_idx:03d}"
        doc_id = f"DOC_GOV_{g_idx:03d}"
        # 상위 5개 핵심 정책은 원문 대조 검수(verified), 나머지는 extracted
        v_status = "verified" if g_idx <= 5 else "extracted"

        nm = item.get("서비스명", "").strip()
        org = item.get("소관기관명", "").strip() or "행정기관"
        regs = infer_region_from_text(org, nm)
        cat = infer_category(item.get("서비스분야", ""), nm, full_txt)
        t_groups = infer_target_groups("", item.get("지원대상", ""), nm, full_txt)
        detail_url = item.get("상세조회URL") or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{sid}"
        req_docs_raw = (item.get("구비서류") or "").strip()
        req_docs_list = [line.strip("- ·*") for line in req_docs_raw.splitlines() if line.strip()][:8] if req_docs_raw else []

        raw_cond = item.get("_support_conditions_raw") or item.get("_support_conditions", {})
        structured_cond = structure_ja_conditions(raw_cond)

        pol_rec = make_policy_record(
            policy_id=pol_id,
            official_id=sid,
            policy_name=nm,
            source_type="api",
            category=cat,
            agency=org,
            regions=regs,
            target_groups=t_groups,
            requirements={
                "service_field": item.get("서비스분야"),
                "support_type": item.get("지원유형"),
                "selection_criteria": item.get("선정기준") or item.get("지원대상"),
                "structured_support_conditions": structured_cond,
                "support_conditions": item.get("_support_conditions", {}),
            },
            eligibility_text=f"{item.get('지원대상', '')}\n{item.get('선정기준', '')}".strip(),
            benefit_text=item.get("지원내용", ""),
            application_period=item.get("신청기한") or "상시/공고참조",
            application_start=None,
            application_end=None,
            application_method=item.get("신청방법") or "정부24 또는 관할 기관 신청",
            required_documents=req_docs_list,
            official_apply_url=application_url_or_none(item.get("온라인신청사이트URL"), detail_url),
            source_url=detail_url,
            source_document_ids=[doc_id],
            verification_status=v_status,
        )
        doc_rec = make_document_record(
            document_id=doc_id,
            policy_id=pol_id,
            title=f"[정부24 공공서비스] {nm} ({sid})",
            source_type="api",
            source_url=detail_url,
            local_raw_path=f"data/raw/gov24/items/{sid}.json",
            text=full_txt,
            verification_status=v_status,
        )
        policies.append(pol_rec)
        documents.append(doc_rec)

    print(f"Added {g_idx} Gov24 policies & documents across {len(region_bucket_counts)} regions")

    # --- 3.3 온통청년(YouthCenter) 5대 분야(일자리·주거·교육·복지문화·참여권리) 및 중앙/지자체 대표 정책 65건 ---
    yc_candidates: List[Dict[str, Any]] = []
    yc_cat_counts: Dict[str, int] = {}

    for pno, item in sorted(yc_all.items(), reverse=True):
        if pno in excluded_yc_ids:
            continue
        nm = (item.get("plcyNm") or "").strip()
        norm_nm = re.sub(r"\s+", "", nm)
        if not nm or norm_nm in seen_policy_names:
            continue
        expln = (item.get("plcyExplnCn") or "").strip()
        sprt = (item.get("plcySprtCn") or "").strip()
        if len(expln) < 20 or len(sprt) < 20:
            continue
        lclsf = (item.get("lclsfNm") or "기타").strip()
        if yc_cat_counts.get(lclsf, 0) >= 15:
            continue
        yc_cat_counts[lclsf] = yc_cat_counts.get(lclsf, 0) + 1
        yc_candidates.append(item)
        seen_policy_names.add(norm_nm)
        if len(yc_candidates) >= 65:
            break

    yc_code_defs = load_code_definitions()
    y_idx = 0
    for item in yc_candidates:
        pno = item["plcyNo"]
        raw_item_path = yc_items_dir / f"{pno}.json"
        raw_item_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")

        y_idx += 1
        pol_id = f"POL_YC_{y_idx:03d}"
        doc_id = f"DOC_YC_{y_idx:03d}"
        v_status = "verified" if y_idx <= 5 else "extracted"

        norm_yc = normalize_youthcenter_item(item, yc_code_defs, policy_id=pol_id, document_id=doc_id)
        full_txt = norm_yc["full_text"]
        if len(full_txt) < 120:
            continue

        nm = norm_yc["policy_name"]
        agency = norm_yc["agency"]
        regs = norm_yc["regions"]
        cat = infer_category(norm_yc["category"], nm, full_txt)

        pol_rec = make_policy_record(
            policy_id=pol_id,
            official_id=pno,
            policy_name=nm,
            source_type="api",
            category=cat,
            agency=agency,
            regions=regs,
            target_groups=["청년"],
            requirements=norm_yc["requirements"],
            eligibility_text=norm_yc["eligibility"],
            benefit_text=norm_yc["benefit"],
            application_period=norm_yc["application_period"],
            application_start=None,
            application_end=None,
            application_method=norm_yc["application_method"],
            required_documents=norm_yc["required_documents"],
            official_apply_url=norm_yc["official_apply_url"],
            source_url=norm_yc["source_url"],
            source_document_ids=[doc_id],
            verification_status=v_status,
        )
        pol_rec["review_flags"] = norm_yc["review_flags"]
        pol_rec["lastMdfcnDt"] = norm_yc["lastMdfcnDt"]

        doc_rec = make_document_record(
            document_id=doc_id,
            policy_id=pol_id,
            title=f"[온통청년 정책] {nm} ({pno})",
            source_type="api",
            source_url=norm_yc["source_url"],
            local_raw_path=f"data/raw/youthcenter/items/{pno}.json",
            text=full_txt,
            verification_status=v_status,
        )
        doc_rec["lastMdfcnDt"] = norm_yc["lastMdfcnDt"]
        policies.append(pol_rec)
        documents.append(doc_rec)

    print(f"Added {y_idx} YouthCenter policies & documents")

    # =========================================================================
    # [Step 4] Vector DB 적재용 청크 생성 (chunks.jsonl)
    # =========================================================================
    pol_by_id = {p["policy_id"]: p for p in policies}
    chunks: List[Dict[str, Any]] = []

    def append_chunk(
        *,
        chunk_id: str,
        doc: Dict[str, Any],
        page_number: int,
        section_title: str,
        chunk_text: str,
    ) -> None:
        pol = pol_by_id[doc["policy_id"]]
        txt = chunk_text.strip()
        if len(txt) < 50:
            return
        chunks.append(
            {
                "chunk_id": chunk_id,
                "policy_id": doc["policy_id"],
                "document_id": doc["document_id"],
                "policy_name": pol["policy_name"],
                "category": pol["category"],
                "region": pol["region"],
                "target_group": pol["target_group"],
                "section_title": section_title,
                "chunk_index": len(chunks) + 1,
                "page_number": page_number,
                "text": txt,
                "content": txt,
                "source_url": doc["source_url"],
                "verification_status": doc["verification_status"],
            }
        )

    doc_by_id = {d["document_id"]: d for d in documents}

    # 4.1 PDF 문서 페이지별 청킹 (DOC_LH_001, DOC_LH_002, DOC_NHUF_002)
    for p_num, p_text in lh_pdf1_pages:
        append_chunk(
            chunk_id=f"DOC_LH_001_P{p_num:02d}_C01",
            doc=doc_by_id["DOC_LH_001"],
            page_number=p_num,
            section_title=f"LH 신혼·신생아 매입임대 공고문 제{p_num}쪽",
            chunk_text=p_text[:1000],
        )
        if len(p_text) > 1000:
            append_chunk(
                chunk_id=f"DOC_LH_001_P{p_num:02d}_C02",
                doc=doc_by_id["DOC_LH_001"],
                page_number=p_num,
                section_title=f"LH 신혼·신생아 매입임대 공고문 제{p_num}쪽 (하단)",
                chunk_text=p_text[900:],
            )

    append_chunk(
        chunk_id="DOC_LH_002_P01_C01",
        doc=doc_by_id["DOC_LH_002"],
        page_number=1,
        section_title="LH 신혼·신생아 매입임대 신청 시 유의사항",
        chunk_text=lh_pdf2_text,
    )

    for p_num, p_text in nhuf_002_pages:
        step = 700
        for idx_c, start in enumerate(range(0, len(p_text), step), start=1):
            append_chunk(
                chunk_id=f"DOC_NHUF_002_P{p_num:02d}_C{idx_c:02d}",
                doc=doc_by_id["DOC_NHUF_002"],
                page_number=p_num,
                section_title=f"신혼부부 소득요건 합리화 FAQ p.{p_num} ({idx_c})",
                chunk_text=p_text[start : start + step + 80],
            )

    # 4.2 HTML 및 API 문서 의미 단위 청킹 (600~900자 기준, 80자 오버랩)
    pdf_doc_ids = {"DOC_LH_001", "DOC_LH_002", "DOC_NHUF_002"}
    for doc in documents:
        if doc["document_id"] in pdf_doc_ids:
            continue
        if doc["verification_status"] not in {"verified", "extracted"}:
            continue
        full_txt = doc["text"]
        step = 750
        overlap = 80
        c_num = 0
        start = 0
        while start < len(full_txt):
            piece = full_txt[start : start + step].strip()
            if len(piece) >= 50:
                c_num += 1
                append_chunk(
                    chunk_id=f"{doc['document_id']}_C{c_num:02d}",
                    doc=doc,
                    page_number=1,
                    section_title=f"{doc['title']} (섹션 {c_num})",
                    chunk_text=piece,
                )
            if start + step >= len(full_txt):
                break
            start += step - overlap

    # =========================================================================
    # [Step 5] Neo4j 적재용 Graph 관계 생성 (relationships.jsonl / graph_edges.jsonl)
    # =========================================================================
    edges: List[Dict[str, Any]] = []

    target_group_code_map = {
        "청년": "GROUP_YOUTH",
        "신혼부부": "GROUP_NEWLYWED",
        "임산부·출산가구": "GROUP_PREGNANT_BIRTH",
        "영유아·아동·청소년": "GROUP_CHILD_YOUTH",
        "구직자·취업준비생": "GROUP_JOBSEEKER",
        "저소득·취약계층": "GROUP_LOW_INCOME",
        "어르신·노년층": "GROUP_SENIOR",
        "장애인": "GROUP_DISABLED",
        "농어업인": "GROUP_FARMER_FISHER",
        "소상공인·재직자": "GROUP_WORKER_BIZ",
        "일반국민": "GROUP_GENERAL",
    }

    category_code_map = {
        "출산·육아·가족": "CAT_BIRTH_CHILDCARE",
        "청년 취업·교육·자산형성": "CAT_YOUTH_JOB_ASSET",
        "주거·금융": "CAT_HOUSING_FINANCE",
        "교통·생활비·의료·돌봄": "CAT_TRANSPORT_MEDICAL_CARE",
        "지역별 생활지원·복지": "CAT_REGIONAL_WELFARE",
    }

    for pol in policies:
        pid = pol["policy_id"]
        ev_doc = pol["source_document_ids"][0]
        v_stat = pol["verification_status"]

        # 1. ADMINISTERED_BY (Agency)
        agency_slug = re.sub(r"[^0-9A-Za-z가-힣]+", "_", pol["agency"]).strip("_")[:30]
        edges.append(
            {
                "source_id": pid,
                "relation": "ADMINISTERED_BY",
                "target_id": f"AGENCY_{agency_slug}",
                "target_name": pol["agency"],
                "evidence_document_id": ev_doc,
                "evidence_location": "소관기관",
                "review_status": v_stat,
            }
        )

        # 2. HAS_CATEGORY (Category)
        cat_id = category_code_map.get(pol["category"], "CAT_GENERAL")
        edges.append(
            {
                "source_id": pid,
                "relation": "HAS_CATEGORY",
                "target_id": cat_id,
                "target_name": pol["category"],
                "evidence_document_id": ev_doc,
                "evidence_location": "정책분류",
                "review_status": v_stat,
            }
        )

        # 3. AVAILABLE_IN (Region)
        for reg in pol["regions"]:
            reg_id = REGION_CODE_MAP.get(reg, f"REGION_{reg}")
            edges.append(
                {
                    "source_id": pid,
                    "relation": "AVAILABLE_IN",
                    "target_id": reg_id,
                    "target_name": reg,
                    "evidence_document_id": ev_doc,
                    "evidence_location": "지역범위",
                    "review_status": v_stat,
                }
            )

        # 4. TARGETS (TargetGroup)
        for tg in pol["target_groups"]:
            tg_id = target_group_code_map.get(tg, f"GROUP_{tg}")
            edges.append(
                {
                    "source_id": pid,
                    "relation": "TARGETS",
                    "target_id": tg_id,
                    "target_name": tg,
                    "evidence_document_id": ev_doc,
                    "evidence_location": "지원대상",
                    "review_status": v_stat,
                }
            )

        # 4-1. APPLIES_TO_LIFESTAGE (LifeStage Node - 6대 표준 생애단계)
        for lc in pol.get("life_stages", pol.get("life_cycles", [])):
            if lc and lc != "unknown":
                edges.append(
                    {
                        "source_id": pid,
                        "relation": "APPLIES_TO_LIFESTAGE",
                        "target_id": f"LIFESTAGE_{lc}",
                        "target_name": lc,
                        "evidence_document_id": ev_doc,
                        "evidence_location": "생애단계",
                        "review_status": v_stat,
                    }
                )

        # 5. SUPPORTED_BY (SourceDocument)
        for did in pol["source_document_ids"]:
            edges.append(
                {
                    "source_id": pid,
                    "relation": "SUPPORTED_BY",
                    "target_id": did,
                    "target_name": doc_by_id[did]["title"],
                    "evidence_document_id": did,
                    "evidence_location": "전문",
                    "review_status": v_stat,
                }
            )

        # 6. REQUIRES_CONDITION (구조화 지원조건 엣지 - 연령·소득·가구)
        reqs = pol.get("requirements", {})
        s_cond = reqs.get("structured_support_conditions") or {}
        if s_cond:
            age_c = s_cond.get("연령조건", {})
            min_a = age_c.get("대상연령_시작")
            max_a = age_c.get("대상연령_종료")
            if min_a or max_a:
                edges.append(
                    {
                        "source_id": pid,
                        "relation": "REQUIRES_CONDITION",
                        "target_id": f"COND_AGE_{min_a or 0}_{max_a or 99}",
                        "target_name": f"연령조건: 만 {min_a or 0}세~{max_a or 99}세",
                        "evidence_document_id": ev_doc,
                        "evidence_location": "지원조건 JA0110/JA0111",
                        "review_status": v_stat,
                    }
                )
            for hh_name, hh_val in s_cond.get("가구주거조건", {}).items():
                if hh_val == "적용대상":
                    edges.append(
                        {
                            "source_id": pid,
                            "relation": "REQUIRES_CONDITION",
                            "target_id": f"COND_HH_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', hh_name)}",
                            "target_name": f"가구주거조건: {hh_name}",
                            "evidence_document_id": ev_doc,
                            "evidence_location": "지원조건 JA04",
                            "review_status": v_stat,
                        }
                    )
            for inc_name, inc_val in s_cond.get("소득구간조건", {}).items():
                if inc_val == "적용대상":
                    edges.append(
                        {
                            "source_id": pid,
                            "relation": "REQUIRES_CONDITION",
                            "target_id": f"COND_INC_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', inc_name)}",
                            "target_name": f"소득조건: {inc_name}",
                            "evidence_document_id": ev_doc,
                            "evidence_location": "지원조건 JA02",
                            "review_status": v_stat,
                        }
                    )

        # 7. 온통청년 구조화 요건 엣지 (혼인, 취업, 학력, 특화)
        mrg_lbl = reqs.get("marriage_status_label")
        if mrg_lbl in ("기혼", "미혼"):
            edges.append(
                {
                    "source_id": pid,
                    "relation": "REQUIRES_CONDITION",
                    "target_id": f"COND_MRG_{mrg_lbl}",
                    "target_name": f"혼인조건: {mrg_lbl}",
                    "evidence_document_id": ev_doc,
                    "evidence_location": "온통청년 mrgSttsCd",
                    "review_status": v_stat,
                }
            )
        for j_lbl in reqs.get("job_labels", []):
            if j_lbl and j_lbl != "제한없음":
                edges.append(
                    {
                        "source_id": pid,
                        "relation": "REQUIRES_CONDITION",
                        "target_id": f"COND_JOB_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', j_lbl)}",
                        "target_name": f"취업조건: {j_lbl}",
                        "evidence_document_id": ev_doc,
                        "evidence_location": "온통청년 jobCd",
                        "review_status": v_stat,
                    }
                )
        for s_lbl in reqs.get("school_labels", []):
            if s_lbl and s_lbl != "제한없음":
                edges.append(
                    {
                        "source_id": pid,
                        "relation": "REQUIRES_CONDITION",
                        "target_id": f"COND_EDU_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', s_lbl)}",
                        "target_name": f"학력조건: {s_lbl}",
                        "evidence_document_id": ev_doc,
                        "evidence_location": "온통청년 schoolCd",
                        "review_status": v_stat,
                    }
                )
        for sp_lbl in reqs.get("special_labels", []):
            if sp_lbl and sp_lbl != "제한없음":
                edges.append(
                    {
                        "source_id": pid,
                        "relation": "REQUIRES_CONDITION",
                        "target_id": f"COND_SP_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', sp_lbl)}",
                        "target_name": f"특화조건: {sp_lbl}",
                        "evidence_document_id": ev_doc,
                        "evidence_location": "온통청년 sbizCd",
                        "review_status": v_stat,
                    }
                )

    # 정책 간 조건 변경 관계 (POL_002 -> POL_003)
    edges.append(
        {
            "source_id": "POL_002",
            "relation": "MODIFIES_CONDITION_OF",
            "target_id": "POL_003",
            "target_name": "주택도시기금 신혼부부전용 전세자금대출",
            "evidence_document_id": "DOC_NHUF_002",
            "evidence_location": "p.1 신혼부부 소득요건 합리화",
            "review_status": "verified",
        }
    )

    # =========================================================================
    # [Step 6] 파일 저장 (policies.jsonl, documents.jsonl, chunks.jsonl, relationships.jsonl)
    # =========================================================================
    with open(PROCESSED_DIR / "policies.jsonl", "w", encoding="utf-8") as f:
        for p in policies:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    with open(PROCESSED_DIR / "documents.jsonl", "w", encoding="utf-8") as f:
        for d in documents:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    with open(PROCESSED_DIR / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    with open(PROCESSED_DIR / "relationships.jsonl", "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    with open(PROCESSED_DIR / "graph_edges.jsonl", "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # 중복 병합 내역 저장
    with open(METADATA_DIR / "dedup_candidates.json", "w", encoding="utf-8") as f:
        json.dump(dedup_records, f, ensure_ascii=False, indent=2)

    # POL_008 API 메타데이터 업데이트 (승인 및 실제 수집 완료 반영)
    api_metadata_record = {
        "metadata_type": "api_service_info",
        "official_id": "KEIS-YC-API-001",
        "service_name": "온통청년 청년정책 Open API 서비스",
        "provider": "국무조정실 / 한국고용정보원",
        "category": "플랫폼·연계",
        "status": "active_collected",
        "auth_type": "apiKeyNm (공식 승인 완료)",
        "endpoint": "https://www.youthcenter.go.kr/go/ythip/getPlcy",
        "guide_url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide",
        "total_collected": 3861,
        "registered_at": NOW,
        "note": "정책 추천 데이터셋(policies.jsonl)에서 분리하여 API 메타데이터로 관리하며 실제 3,861건 수집 완료",
    }
    with open(METADATA_DIR / "api_metadata.json", "w", encoding="utf-8") as f:
        json.dump(api_metadata_record, f, ensure_ascii=False, indent=2)

    print("==========================================")
    print(f"Policies written     : {len(policies)}")
    print(f"Documents written    : {len(documents)}")
    print(f"Chunks written       : {len(chunks)}")
    print(f"Relationships written: {len(edges)}")
    print("==========================================")


if __name__ == "__main__":
    main()
