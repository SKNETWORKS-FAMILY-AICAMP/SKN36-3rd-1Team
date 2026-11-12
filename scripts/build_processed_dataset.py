import argparse
import json
import csv
import hashlib
from pathlib import Path
from pypdf import PdfReader
from bs4 import BeautifulSoup

def read_file_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
        body = soup.find("div", class_="txt") or soup.find(id="container") or soup.find("body")
        return body.get_text("\n", strip=True) if body else ""


def build_dataset(raw_dir: Path, processed_dir: Path, metadata_dir: Path) -> None:
    NOW = "2026-11-04T16:25:00+09:00"

    def get_hash(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    # ==========================================
    # 1. POLICIES (policies.jsonl)
    # ==========================================
    policies = [
        {
            "policy_id": "POL_001",
            "official_id": "LH-2015122300019712",
            "policy_name": "[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고",
            "category": "주거·임대",
            "agency": "한국토지주택공사 서울지역본부",
            "regions": ["서울특별시"],
            "target_groups": ["신혼부부", "예비신혼부부", "신생아가구", "한부모가족", "유자녀가구"],
            "requirements": {
                "housing_rule": "homeless_household",
                "marriage_rule": "married_within_7_years_or_planning_within_year",
                "income_rule": "city_worker_monthly_avg_70pct_or_dual_90pct",
                "asset_rule": "total_asset_under_379m_and_car_under_37m",
                "min_age": 19,
                "max_age": None,
                "employment_status": "any"
            },
            "eligibility_review_status": "verified",
            "reception_status": "closed",
            "application_start": "2026-04-15",
            "application_end": "2026-04-17",
            "official_apply_url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            "source_document_ids": ["DOC_LH_001", "DOC_LH_002", "DOC_LH_003"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_002",
            "official_id": "MOLIT-HUG-NOTICE-1316",
            "policy_name": "기금대출 신혼부부 소득요건 합리화 개정 규정",
            "category": "주거·금융",
            "agency": "국토교통부 / 주택도시보증공사",
            "regions": ["전국"],
            "target_groups": ["신혼부부", "결혼예정자"],
            "requirements": {
                "housing_rule": "homeless_household_head",
                "marriage_rule": "married_within_7_years_or_planning_within_3_months",
                "income_rule": "single_income_fallback_when_joint_income_exceeds_threshold",
                "threshold_details": {
                    "버팀목_신혼부부전용": {"joint_exceeds": 75000000, "single_under": 50000000},
                    "디딤돌_신혼부부전용": {"joint_exceeds": 85000000, "single_under": 60000000},
                    "버팀목_일반신혼": {"joint_exceeds": 70000000, "single_under": 50000000},
                    "디딤돌_일반신혼": {"joint_exceeds": 85000000, "single_under": 60000000}
                },
                "contractor_rule": "applicant_must_be_contractor",
                "asset_rule": "joint_net_asset_under_345m_for_jeonse",
                "interest_rule": "highest_bracket_plus_0.3pct"
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": "2026-10-19",
            "application_end": None,
            "official_apply_url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            "source_document_ids": ["DOC_NHUF_001", "DOC_NHUF_002"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_003",
            "official_id": "NHUF-JEONSE-002",
            "policy_name": "주택도시기금 신혼부부전용 전세자금대출",
            "category": "주거·금융",
            "agency": "국토교통부 / 주택도시보증공사",
            "regions": ["전국"],
            "target_groups": ["신혼부부", "결혼예정자"],
            "requirements": {
                "housing_rule": "homeless_household_head",
                "marriage_rule": "married_within_7_years_or_planning_within_3_months",
                "income_rule": "joint_income_under_75m_or_single_under_50m_amendment",
                "asset_rule": "joint_net_asset_under_345m",
                "house_deposit_limit": 400000000,
                "house_area_limit_sqm": 85,
                "loan_limit_krw": 300000000,
                "interest_rate_range": "연 1.5% ~ 2.7%"
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            "source_document_ids": ["DOC_NHUF_003", "DOC_NHUF_004"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_004",
            "official_id": "NHUF-JEONSE-001",
            "policy_name": "주택도시기금 청년전용 버팀목전세자금대출",
            "category": "주거·금융",
            "agency": "국토교통부 / 주택도시보증공사",
            "regions": ["전국"],
            "target_groups": ["청년", "취업준비생", "단독세대주"],
            "requirements": {
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "homeless_single_household_head",
                "income_rule": "annual_income_under_50m_or_60m_special",
                "asset_rule": "net_asset_under_345m",
                "house_deposit_limit": 300000000,
                "house_area_limit_sqm": 85,
                "loan_limit_krw": 200000000,
                "interest_rate_range": "연 1.8% ~ 2.7%"
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            "source_document_ids": ["DOC_NHUF_005", "DOC_NHUF_006"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_005",
            "official_id": "MOEL-WORK24-CARD-001",
            "policy_name": "고용24 국민내일배움카드",
            "category": "일자리·교육·훈련",
            "agency": "고용노동부 / 한국고용정보원",
            "regions": ["전국"],
            "target_groups": ["구직자", "실직자", "재직자", "특수형태근로종사자", "자영업자"],
            "requirements": {
                "min_age": 15,
                "max_age": 74,
                "exclusion_criteria": [
                    "공무원",
                    "사립학교 교직원",
                    "만 75세 이상",
                    "연매출 4억원 이상 자영업자",
                    "특수형태근로종사자 월소득 500만원 이상",
                    "대규모기업 45세 미만 월소득 300만원 이상",
                    "졸업예정자 이외 대학생/대학원생"
                ],
                "benefit_limit_krw": 3000000,
                "benefit_max_krw": 5000000,
                "validity_years": 5
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            "source_document_ids": ["DOC_WORK24_001"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_006",
            "official_id": "NHUF-RENT-001",
            "policy_name": "주택도시기금 주거안정월세대출",
            "category": "주거·금융",
            "agency": "국토교통부 / 주택도시보증공사",
            "regions": ["전국"],
            "target_groups": ["취업준비생", "사회초년생", "근로장려금수급자", "저소득가구"],
            "requirements": {
                "housing_rule": "homeless_household_head",
                "eligible_types": "우대형(만 35세 이하 취준생 부모소득 6천만원 이하, 취업 후 5년 이내 소득 4천만원 이하 등), 일반형(연소득 5천만원 이하)",
                "house_deposit_limit": 100000000,
                "monthly_rent_limit": 600000,
                "house_area_limit_sqm": 85,
                "loan_limit_monthly_krw": 400000,
                "loan_total_limit_krw": 9600000,
                "interest_rate_range": "우대형 연 1.3%, 일반형 연 1.8%"
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
            "source_document_ids": ["DOC_NHUF_007"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_007",
            "official_id": "NHUF-RENT-002",
            "policy_name": "주택도시기금 청년전용 보증부월세대출",
            "category": "주거·금융",
            "agency": "국토교통부 / 주택도시보증공사",
            "regions": ["전국"],
            "target_groups": ["청년", "단독세대주", "사회초년생"],
            "requirements": {
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "homeless_single_household_head",
                "income_rule": "annual_income_under_50m",
                "asset_rule": "net_asset_under_345m",
                "house_deposit_limit": 65000000,
                "monthly_rent_limit": 700000,
                "deposit_loan_limit_krw": 35000000,
                "monthly_loan_limit_krw": 500000,
                "interest_rate_deposit": "연 1.3%",
                "interest_rate_rent": "연 0%(20만원 한도) ~ 1.0%"
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020303.jsp",
            "source_document_ids": ["DOC_NHUF_008"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_008",
            "official_id": "KEIS-YC-API-001",
            "policy_name": "온통청년 청년정책 Open API 서비스",
            "category": "플랫폼·연계",
            "agency": "국무조정실 / 한국고용정보원",
            "regions": ["전국"],
            "target_groups": ["청년", "지자체/중앙부처 정책 이용자"],
            "requirements": {
                "auth_type": "openApiVlak_after_admin_approval",
                "request_method": "GET_HTTPS",
                "endpoint": "https://www.youthcenter.go.kr/opi/youthPlcyList.do",
                "notes": "비인가 호출 시 302 리다이렉트(포트 8080) 차단됨. 회원가입 및 관리자 심사 승인 필수"
            },
            "eligibility_review_status": "verified",
            "reception_status": "pending_approval",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide",
            "source_document_ids": ["DOC_YC_001", "DOC_YC_002"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_009",
            "official_id": "MOLIT-YOUTH-RENT-001",
            "policy_name": "국토교통부 청년월세 한시 특별지원 (2차)",
            "category": "주거·복지",
            "agency": "국토교통부",
            "regions": ["전국"],
            "target_groups": ["청년독립가구", "취업준비생", "무주택청년"],
            "requirements": {
                "min_age": 19,
                "max_age": 34,
                "housing_rule": "homeless_independent_household",
                "income_rule": "youth_household_median_60pct_and_parent_household_median_100pct",
                "asset_rule": "youth_asset_under_122m_and_parent_asset_under_470m",
                "house_deposit_limit": 50000000,
                "monthly_rent_limit": 700000,
                "benefit_monthly_krw": 200000,
                "benefit_duration_months": 12
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": "2024-02-26",
            "application_end": "2025-02-25",
            "official_apply_url": "https://www.bokjiro.go.kr/",
            "source_document_ids": ["DOC_MOLIT_001"],
            "retrieved_at": NOW
        },
        {
            "policy_id": "POL_010",
            "official_id": "MOEL-KUA-001",
            "policy_name": "고용노동부 국민취업지원제도 (Ⅰ유형 청년특례 및 구직촉진수당)",
            "category": "일자리·소득지원",
            "agency": "고용노동부",
            "regions": ["전국"],
            "target_groups": ["구직청년", "취업준비생", "경력단절여성", "미취업자"],
            "requirements": {
                "min_age": 15,
                "max_age": 69,
                "youth_special_age": {"min": 18, "max": 34},
                "income_rule": "median_income_60pct_for_general_or_120pct_for_youth_special",
                "asset_rule": "net_asset_under_400m_or_500m_for_youth",
                "work_experience_rule": "exempt_for_youth_special",
                "benefit_monthly_krw": 500000,
                "benefit_duration_months": 6,
                "dependent_family_bonus_krw": 100000
            },
            "eligibility_review_status": "verified",
            "reception_status": "active",
            "application_start": None,
            "application_end": None,
            "official_apply_url": "https://www.work24.go.kr/",
            "source_document_ids": ["DOC_MOEL_001"],
            "retrieved_at": NOW
        }
    ]



    # ==========================================
    # 2. DOCUMENTS (documents.jsonl)
    # ==========================================
    # Read extracted texts
    lh_pdf = raw_dir / "lh" / "26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf"
    lh_reader = PdfReader(str(lh_pdf))
    lh_full_text = "\n\n".join([f"--- [Page {idx+1}] ---\n{page.extract_text()}" for idx, page in enumerate(lh_reader.pages)])

    nhuf_faq_pdf = raw_dir / "nhuf" / "신혼부부_소득요건_합리화_FAQ.pdf"
    nhuf_faq_reader = PdfReader(str(nhuf_faq_pdf))
    nhuf_faq_full_text = "\n\n".join([f"--- [Page {idx+1}] ---\n{page.extract_text()}" for idx, page in enumerate(nhuf_faq_reader.pages)])



    documents = [
        {
            "document_id": "DOC_LH_001",
            "policy_id": "POL_001",
            "title": "26년1차 신혼·신생아매입임대Ⅰ 입주자모집공고문(서울지역본부)(정정공고)",
            "source_type": "pdf",
            "source_url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            "local_raw_path": "data/raw/lh/26년1차_신혼·신생아매입임대Ⅰ_입주자모집공고문(서울지역본부)(정정공고).pdf",
            "published_at": "2026-03-31",
            "effective_at": "2026-04-08",
            "retrieved_at": NOW,
            "content_hash": get_hash(lh_full_text),
            "text": lh_full_text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_LH_002",
            "policy_id": "POL_001",
            "title": "서울지역본부 신혼신생아매입임대Ⅰ 신청시유의사항(정정공고)",
            "source_type": "pdf",
            "source_url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            "local_raw_path": "data/raw/lh/서울지역본부신혼신생아매입임대Ⅰ신청시유의사항(정정공고).pdf",
            "published_at": "2026-04-08",
            "effective_at": "2026-04-08",
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "lh" / "lh_notice_2015122300019712_seoul_newlywed.html"))[:1000]),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_001",
            "policy_id": "POL_002",
            "title": "기금대출 신혼부부 소득요건 합리화 관련 안내 공지",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            "local_raw_path": "data/raw/nhuf/nhuf_notice_1316_newlywed_income.html",
            "published_at": "2026-10-16",
            "effective_at": "2026-10-19",
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_notice_1316_newlywed_income.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_002",
            "policy_id": "POL_002",
            "title": "신혼부부 소득요건 합리화 FAQ 공식 첨부문서",
            "source_type": "pdf",
            "source_url": "https://nhuf.molit.go.kr/cms/cwboard/skin/download.jsp?id=3&fileId=1800",
            "local_raw_path": "data/raw/nhuf/신혼부부_소득요건_합리화_FAQ.pdf",
            "published_at": "2026-10-16",
            "effective_at": "2026-10-19",
            "retrieved_at": NOW,
            "content_hash": get_hash(nhuf_faq_full_text),
            "text": nhuf_faq_full_text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_003",
            "policy_id": "POL_003",
            "title": "신혼부부전용 전세자금 대출안내 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_newlywed_jeonse_guide.html",
            "published_at": None,
            "effective_at": "2026-10-19",
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_newlywed_jeonse_guide.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_004",
            "policy_id": "POL_003",
            "title": "신혼부부전용 전세자금 이용절차 및 제출서류 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020402.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_newlywed_jeonse_process.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_newlywed_jeonse_process.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_005",
            "policy_id": "POL_004",
            "title": "청년전용 버팀목전세자금 대출안내 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_youth_jeonse_guide.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_youth_jeonse_guide.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_006",
            "policy_id": "POL_004",
            "title": "청년전용 버팀목전세자금 이용절차 및 제출서류 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020302.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_youth_jeonse_process.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_youth_jeonse_process.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_WORK24_001",
            "policy_id": "POL_005",
            "title": "고용24 국민내일배움카드 발급안내 및 지원자격",
            "source_type": "html",
            "source_url": "https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            "local_raw_path": "data/raw/work24/work24_tomorrow_learning_card.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "work24" / "work24_tomorrow_learning_card.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_007",
            "policy_id": "POL_006",
            "title": "주거안정월세대출 대출안내 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_monthly_rent_guide.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_monthly_rent_guide.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_NHUF_008",
            "policy_id": "POL_007",
            "title": "청년전용 보증부월세대출 대출안내 상세",
            "source_type": "html",
            "source_url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020303.jsp",
            "local_raw_path": "data/raw/nhuf/nhuf_youth_rent_deposit_guide.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "nhuf" / "nhuf_youth_rent_deposit_guide.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_YC_001",
            "policy_id": "POL_008",
            "title": "온통청년 오픈 API 이용방법 안내 가이드",
            "source_type": "html",
            "source_url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide",
            "local_raw_path": "data/raw/youthcenter/youthcenter_openapi_guide.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "youthcenter" / "youthcenter_openapi_guide.html"))),
            "text": text,
            "review_status": "verified"
        },
        {
            "document_id": "DOC_YC_002",
            "policy_id": "POL_008",
            "title": "온통청년 청년정책 오픈 API 명세서",
            "source_type": "html",
            "source_url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc",
            "local_raw_path": "data/raw/youthcenter/youthcenter_openapi_doc.html",
            "published_at": None,
            "effective_at": None,
            "retrieved_at": NOW,
            "content_hash": get_hash((text := read_file_text(raw_dir / "youthcenter" / "youthcenter_openapi_doc.html"))),
            "text": text,
            "review_status": "verified"
        }
    ]

    processed_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    with open(processed_dir / "policies.jsonl", "w", encoding="utf-8") as f:
        for pol in policies:
            f.write(json.dumps(pol, ensure_ascii=False) + "\n")
    print(f"Wrote {len(policies)} policies to policies.jsonl")

    with open(processed_dir / "documents.jsonl", "w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Wrote {len(documents)} documents to documents.jsonl")

    # ==========================================
    # 3. CHUNKS (chunks.jsonl)
    # ==========================================
    # Chunking rule: 500-1000 characters per chunk, splitting along natural section/page boundaries
    chunks = []

    # (A) LH PDF Chunks by Page / Section
    for idx, page in enumerate(lh_reader.pages):
        p_text = page.extract_text() or ""
        if not p_text.strip():
            continue
        # Divide into 1 or 2 chunks per page
        p_num = idx + 1
        chunks.append({
            "chunk_id": f"DOC_LH_001_P{p_num:02d}_C01",
            "document_id": "DOC_LH_001",
            "policy_id": "POL_001",
            "chunk_index": len(chunks) + 1,
            "page_number": p_num,
            "section_title": f"LH 신혼·신생아 매입임대 공고문 제{p_num}쪽",
            "text": p_text[:1200].strip(),
            "source_url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13"
        })
        if len(p_text) > 1200:
            chunks.append({
                "chunk_id": f"DOC_LH_001_P{p_num:02d}_C02",
                "document_id": "DOC_LH_001",
                "policy_id": "POL_001",
                "chunk_index": len(chunks) + 1,
                "page_number": p_num,
                "section_title": f"LH 신혼·신생아 매입임대 공고문 제{p_num}쪽 (하단)",
                "text": p_text[1200:].strip(),
                "source_url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13"
            })

    # (B) NHUF FAQ PDF Chunks
    for idx, page in enumerate(nhuf_faq_reader.pages):
        p_text = page.extract_text() or ""
        p_num = idx + 1
        # Split by questions
        q_blocks = p_text.split("\n")
        cur_chunk = []
        c_sub = 1
        for line in q_blocks:
            cur_chunk.append(line)
            if len("\n".join(cur_chunk)) > 500:
                chunks.append({
                    "chunk_id": f"DOC_NHUF_002_P{p_num:02d}_C{c_sub:02d}",
                    "document_id": "DOC_NHUF_002",
                    "policy_id": "POL_002",
                    "chunk_index": len(chunks) + 1,
                    "page_number": p_num,
                    "section_title": f"신혼부부 소득요건 합리화 FAQ p.{p_num} ({c_sub})",
                    "text": "\n".join(cur_chunk).strip(),
                    "source_url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S"
                })
                cur_chunk = []
                c_sub += 1
        if cur_chunk:
            chunks.append({
                "chunk_id": f"DOC_NHUF_002_P{p_num:02d}_C{c_sub:02d}",
                "document_id": "DOC_NHUF_002",
                "policy_id": "POL_002",
                "chunk_index": len(chunks) + 1,
                "page_number": p_num,
                "section_title": f"신혼부부 소득요건 합리화 FAQ p.{p_num} ({c_sub})",
                "text": "\n".join(cur_chunk).strip(),
                "source_url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S"
            })

    # (C) NHUF Newlywed Jeonse & Youth Jeonse Chunks
    for doc_id, pol_id, raw_f, title, u in [
        ("DOC_NHUF_003", "POL_003", "nhuf/nhuf_newlywed_jeonse_guide.html", "신혼부부전용 전세자금 대출안내", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp"),
        ("DOC_NHUF_004", "POL_003", "nhuf/nhuf_newlywed_jeonse_process.html", "신혼부부전용 전세자금 제출서류", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020402.jsp"),
        ("DOC_NHUF_005", "POL_004", "nhuf/nhuf_youth_jeonse_guide.html", "청년전용 버팀목전세자금 대출안내", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp"),
        ("DOC_NHUF_006", "POL_004", "nhuf/nhuf_youth_jeonse_process.html", "청년전용 버팀목전세자금 제출서류", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020302.jsp"),
        ("DOC_WORK24_001", "POL_005", "work24/work24_tomorrow_learning_card.html", "국민내일배움카드 발급 및 자격", "https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do"),
        ("DOC_NHUF_007", "POL_006", "nhuf/nhuf_monthly_rent_guide.html", "주거안정월세대출 대출안내", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020201.jsp"),
        ("DOC_NHUF_008", "POL_007", "nhuf/nhuf_youth_rent_deposit_guide.html", "청년전용 보증부월세대출 대출안내", "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020303.jsp"),
        ("DOC_YC_001", "POL_008", "youthcenter/youthcenter_openapi_guide.html", "온통청년 오픈API 이용안내", "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide"),
        ("DOC_YC_002", "POL_008", "youthcenter/youthcenter_openapi_doc.html", "온통청년 오픈API 규격명세", "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc"),
    ]:
        t = read_file_text(raw_dir / raw_f)
        # split into 800-char chunks
        for i in range(0, len(t), 800):
            c_text = t[i:i+800].strip()
            if len(c_text) > 50:
                c_idx = (i // 800) + 1
                chunks.append({
                    "chunk_id": f"{doc_id}_C{c_idx:02d}",
                    "document_id": doc_id,
                    "policy_id": pol_id,
                    "chunk_index": len(chunks) + 1,
                    "page_number": None,
                    "section_title": f"{title} (단락 {c_idx})",
                    "text": c_text,
                    "source_url": u
                })

    with open(processed_dir / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Wrote {len(chunks)} chunks to chunks.jsonl")

    # ==========================================
    # 4. GRAPH EDGES (graph_edges.jsonl)
    # ==========================================
    edges = [
        # Agencies
        {"source_id": "POL_001", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_LH", "target_type": "Agency", "name": "한국토지주택공사", "evidence_document_id": "DOC_LH_001", "evidence_location": "p.1"},
        {"source_id": "POL_002", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_MOLIT_HUG", "target_type": "Agency", "name": "국토교통부 / 주택도시보증공사", "evidence_document_id": "DOC_NHUF_001", "evidence_location": "하단 담당기관"},
        {"source_id": "POL_003", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_MOLIT_HUG", "target_type": "Agency", "name": "국토교통부 / 주택도시보증공사", "evidence_document_id": "DOC_NHUF_003", "evidence_location": "개인상품 안내"},
        {"source_id": "POL_004", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_MOLIT_HUG", "target_type": "Agency", "name": "국토교통부 / 주택도시보증공사", "evidence_document_id": "DOC_NHUF_005", "evidence_location": "개인상품 안내"},
        {"source_id": "POL_005", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_MOEL", "target_type": "Agency", "name": "고용노동부 / 한국고용정보원", "evidence_document_id": "DOC_WORK24_001", "evidence_location": "기관안내"},
        {"source_id": "POL_008", "source_type": "Policy", "relation": "ADMINISTERED_BY", "target_id": "AGY_OPC_KEIS", "target_type": "Agency", "name": "국무조정실 / 한국고용정보원", "evidence_document_id": "DOC_YC_001", "evidence_location": "API 이용안내"},

        # Policy Amendments & Relationships
        {"source_id": "POL_002", "source_type": "Policy", "relation": "AMENDS", "target_id": "POL_003", "target_type": "Policy", "condition_group": "소득요건완화", "rule": "신혼부부 합산 소득 7.5천만원 초과 시 신청인 1인 5천만원 이하 요건 신설", "evidence_document_id": "DOC_NHUF_002", "evidence_location": "p.1 제2조 표"},

        # Target Groups
        {"source_id": "POL_001", "source_type": "Policy", "relation": "TARGETS", "target_id": "GRP_NEWLYWED", "target_type": "TargetGroup", "rule": "혼인 7년 이내 무주택", "evidence_document_id": "DOC_LH_001", "evidence_location": "p.1"},
        {"source_id": "POL_001", "source_type": "Policy", "relation": "TARGETS", "target_id": "GRP_NEWBORN", "target_type": "TargetGroup", "rule": "공고일 기준 2년 이내 출산/입양 가구", "evidence_document_id": "DOC_LH_001", "evidence_location": "p.1"},
        {"source_id": "POL_003", "source_type": "Policy", "relation": "TARGETS", "target_id": "GRP_NEWLYWED", "target_type": "TargetGroup", "rule": "혼인 7년 이내 또는 3개월 내 결혼예정자", "evidence_document_id": "DOC_NHUF_003", "evidence_location": "대출대상"},
        {"source_id": "POL_004", "source_type": "Policy", "relation": "TARGETS", "target_id": "GRP_YOUTH", "target_type": "TargetGroup", "rule": "만 19세 이상 34세 이하 단독세대주", "evidence_document_id": "DOC_NHUF_005", "evidence_location": "대출대상"},
        {"source_id": "POL_005", "source_type": "Policy", "relation": "TARGETS", "target_id": "GRP_JOBSEEKER", "target_type": "TargetGroup", "rule": "만 15세 이상 구직자/재직자", "evidence_document_id": "DOC_WORK24_001", "evidence_location": "지원자격"},

        # Requirements (AND/OR groups)
        {"source_id": "POL_001", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_LH_INCOME", "target_type": "Requirement", "logic": "AND", "rule": "도시근로자 가구당 월평균소득 70% 이하 (맞벌이 90% 이하)", "evidence_document_id": "DOC_LH_001", "evidence_location": "p.2"},
        {"source_id": "POL_001", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_LH_ASSET", "target_type": "Requirement", "logic": "AND", "rule": "총자산 3.79억원 이하 및 개별 자동차 3,708만원 이하", "evidence_document_id": "DOC_LH_001", "evidence_location": "p.3"},
        {"source_id": "POL_002", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_NHUF_SINGLE_INCOME", "target_type": "Requirement", "logic": "AND", "rule": "부부합산 소득기준 초과자 중 대출신청인 1인 소득 기준액(버팀목 5천만원/디딤돌 6천만원) 이하 충족", "evidence_document_id": "DOC_NHUF_002", "evidence_location": "p.1"},
        {"source_id": "POL_002", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_NHUF_CONTRACTOR", "target_type": "Requirement", "logic": "AND", "rule": "소득 심사자(1인)가 반드시 매매/임대차계약서상 계약체결자(차주)여야 함", "evidence_document_id": "DOC_NHUF_002", "evidence_location": "p.1 제3조"},
        {"source_id": "POL_003", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_NHUF_JEONSE_INCOME", "target_type": "Requirement", "logic": "OR", "rule": "부부합산 연소득 7.5천만원 이하 [기본] OR [개정] 합산 초과 시 신청인 1인 5천만원 이하", "evidence_document_id": "DOC_NHUF_003", "evidence_location": "대출대상"},
        {"source_id": "POL_004", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_NHUF_YOUTH_AGE", "target_type": "Requirement", "logic": "AND", "rule": "만 19세 이상 ~ 만 34세 이하", "evidence_document_id": "DOC_NHUF_005", "evidence_location": "대출대상"},
        {"source_id": "POL_004", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_NHUF_YOUTH_INCOME", "target_type": "Requirement", "logic": "AND", "rule": "연소득 5천만원 이하 (신혼가구/2자녀 이상 등 6천만원 이하)", "evidence_document_id": "DOC_NHUF_005", "evidence_location": "대출대상"},
        {"source_id": "POL_005", "source_type": "Policy", "relation": "HAS_REQUIREMENT", "target_id": "REQ_WORK24_EXCLUSION", "target_type": "Requirement", "logic": "NOR", "rule": "공무원, 사립교직원, 만 75세 이상, 연매출 4억 이상 자영업자 등 9대 제외조건에 해당하지 않을 것", "evidence_document_id": "DOC_WORK24_001", "evidence_location": "지원제외대상"},

        # Supporting Documents
        {"source_id": "POL_001", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_LH_001", "target_type": "SourceDocument", "evidence_document_id": "DOC_LH_001", "evidence_location": "전체"},
        {"source_id": "POL_002", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_NHUF_002", "target_type": "SourceDocument", "evidence_document_id": "DOC_NHUF_002", "evidence_location": "전체"},
        {"source_id": "POL_003", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_NHUF_003", "target_type": "SourceDocument", "evidence_document_id": "DOC_NHUF_003", "evidence_location": "전체"},
        {"source_id": "POL_004", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_NHUF_005", "target_type": "SourceDocument", "evidence_document_id": "DOC_NHUF_005", "evidence_location": "전체"},
        {"source_id": "POL_005", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_WORK24_001", "target_type": "SourceDocument", "evidence_document_id": "DOC_WORK24_001", "evidence_location": "전체"},
        {"source_id": "POL_008", "source_type": "Policy", "relation": "SUPPORTED_BY", "target_id": "DOC_YC_001", "target_type": "SourceDocument", "evidence_document_id": "DOC_YC_001", "evidence_location": "전체"}
    ]

    with open(processed_dir / "graph_edges.jsonl", "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"Wrote {len(edges)} graph edges to graph_edges.jsonl")

    # ==========================================
    # 5. METADATA (source_registry.csv)
    # ==========================================
    source_registry = [
        {
            "source_id": "SRC_LH_001",
            "agency": "한국토지주택공사",
            "title": "[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고",
            "url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?aisTpCd=26&ccrCnntSysDsCd=03&panId=2015122300019712&uppAisTpCd=13",
            "is_latest_or_revision": "정정공고 (최종)",
            "link_status": "200 OK",
            "data_format": "HTML / PDF",
            "collection_date": "2026-11-04",
            "effective_date": "2026-04-08",
            "reception_status": "closed (접수종료; 서류·자격요건 근거용)",
            "verification_status": "verified (원문 PDF 대조 완료)",
            "notes": "공고문 PDF(804KB) 및 유의사항 PDF 확보 완료. 신청기간 4.15~4.17 종료"
        },
        {
            "source_id": "SRC_NHUF_001",
            "agency": "국토교통부 / 주택도시보증공사",
            "title": "기금대출 신혼부부 소득요건 합리화 관련 안내 (소득요건 개정 공지)",
            "url": "https://nhuf.molit.go.kr/FP/FP08/FP0804/FP080402.jsp?articleId=1316&mode=S",
            "is_latest_or_revision": "최신 개정 규정",
            "link_status": "200 OK",
            "data_format": "HTML / PDF",
            "collection_date": "2026-11-04",
            "effective_date": "2026-10-19",
            "reception_status": "active (시행중)",
            "verification_status": "verified (원문 및 FAQ PDF 대조 완료)",
            "notes": "2026.10.16 등록, 2026.10.19 시행. 신혼부부 합산소득 초과 시 1인 소득기준 신설"
        },
        {
            "source_id": "SRC_NHUF_002",
            "agency": "국토교통부 / 주택도시보증공사",
            "title": "신혼부부전용 전세자금 대출안내 및 이용절차",
            "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020401.jsp",
            "is_latest_or_revision": "현행 기준",
            "link_status": "200 OK",
            "data_format": "HTML",
            "collection_date": "2026-11-04",
            "effective_date": "2026-10-19 (개정반영)",
            "reception_status": "active (상시신청)",
            "verification_status": "verified (기금포털 공식 웹페이지)",
            "notes": "금리 연 1.5%~2.7%, 수도권 최대 3억원, 순자산 3.45억원 이하"
        },
        {
            "source_id": "SRC_NHUF_003",
            "agency": "국토교통부 / 주택도시보증공사",
            "title": "청년전용 버팀목전세자금 대출안내 및 이용절차",
            "url": "https://nhuf.molit.go.kr/FP/FP05/FP0502/FP05020301.jsp",
            "is_latest_or_revision": "현행 기준",
            "link_status": "200 OK",
            "data_format": "HTML",
            "collection_date": "2026-11-04",
            "effective_date": "2026-01-01",
            "reception_status": "active (상시신청)",
            "verification_status": "verified (기금포털 공식 웹페이지)",
            "notes": "만 19~34세, 소득 5천만원 이하, 최대 2억원, 금리 연 1.8%~2.7%"
        },
        {
            "source_id": "SRC_WORK24_001",
            "agency": "고용노동부 / 한국고용정보원",
            "title": "고용24 국민내일배움카드 발급안내 및 가이드",
            "url": "https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do",
            "is_latest_or_revision": "현행 기준",
            "link_status": "200 OK",
            "data_format": "HTML",
            "collection_date": "2026-11-04",
            "effective_date": "2026-01-01",
            "reception_status": "active (상시신청)",
            "verification_status": "verified (고용24 공식 웹페이지)",
            "notes": "기본 300만원(최대 500만원), 5년 유효, 9대 제외조건 명시"
        },
        {
            "source_id": "SRC_YC_001",
            "agency": "국무조정실 / 한국고용정보원",
            "title": "온통청년 오픈 API 이용방법 및 규격명세",
            "url": "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide",
            "is_latest_or_revision": "현행 기준",
            "link_status": "200 OK (가이드/명세), 엔드포인트 비인가 302 차단",
            "data_format": "HTML / XML",
            "collection_date": "2026-11-04",
            "effective_date": "2026-01-01",
            "reception_status": "pending_approval (사용자 키 신청/승인 필요)",
            "verification_status": "verified (공식 가이드 및 호출 파라미터 구조 확인)",
            "notes": "회원가입 후 [마이페이지-오픈API] 신청 및 관리자 심사 승인 필요 (openApiVlak 필수)"
        }
    ]

    with open(metadata_dir / "source_registry.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "source_id", "agency", "title", "url", "is_latest_or_revision",
            "link_status", "data_format", "collection_date", "effective_date",
            "reception_status", "verification_status", "notes"
        ])
        writer.writeheader()
        writer.writerows(source_registry)
    print(f"Wrote {len(source_registry)} sources to source_registry.csv")

    print("\nAll processed datasets generated successfully!")


def main() -> None:
    data_dir = Path(__file__).resolve().parents[1] / "data"
    parser = argparse.ArgumentParser(description="초기 정책 데이터 정제")
    parser.add_argument("--raw-dir", type=Path, default=data_dir / "raw")
    parser.add_argument("--processed-dir", type=Path, default=data_dir / "processed")
    parser.add_argument("--metadata-dir", type=Path, default=data_dir / "metadata")
    args = parser.parse_args()
    try:
        build_dataset(args.raw_dir, args.processed_dir, args.metadata_dir)
    except FileNotFoundError as error:
        parser.error(f"필요한 입력 또는 출력 경로를 확인하세요: {error.filename}")


if __name__ == "__main__":
    main()
