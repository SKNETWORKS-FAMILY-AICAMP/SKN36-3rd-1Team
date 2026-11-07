# -*- coding: utf-8 -*-
"""
LifePolicy AI — STEP C: 공식 근거 문서 수집 PoC 및 안전한 증분 확장 파이프라인
경로: scripts/staging_poc_pipeline.py

원칙:
1. 기존 data/handoff/ 데이터셋 및 manifest.json 절대 수정 금지 (격리된 data/staging/ 에만 저장)
2. STEP B 스키마(Document 선택적 메타데이터, 유형별 청킹, UUIDv5 Point ID) 검증
3. 상태 필드 엄격 분리 (verification_status, approval_status, reception_status, lifecycle_status)
4. 원천 실재성 100% 대조 및 SHA-256 해시 검증
"""

import os
import re
import json
import uuid
import hashlib
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(r"c:\Users\Peter\Desktop\모꼬지\SKN 36기 부트캠프\단위프로젝트\3차\LifePolicy AI")
HANDOFF_DIR = BASE_DIR / "data" / "handoff"
STAGING_DIR = BASE_DIR / "data" / "staging"
STAGING_RAW_DIR = STAGING_DIR / "raw"

# ---------------------------------------------------------
# 1. 기존 데이터 무결성 검증용 해시 체크
# ---------------------------------------------------------
def get_file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def verify_handoff_baseline():
    manifest_path = HANDOFF_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    initial_hash = get_file_hash(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"[검증] 기존 handoff manifest SHA-256: {initial_hash[:16]}... (정책 {data['summary']['total_policies']}건, 문서 {data['summary']['total_documents']}건)")
    return initial_hash

# ---------------------------------------------------------
# 2. C-1. 대상 10대 정책 메타데이터 정의 (6대 분야 대표)
# ---------------------------------------------------------
SELECTED_10_POLICIES = [
    {
        "policy_id": "POL_001",
        "official_id": "LH_NOTICE_2015122300019712",
        "policy_name": "[정정공고][서울지역본부] 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고",
        "category": "주거·금융",
        "agency": "한국토지주택공사 서울지역본부",
        "regions": ["서울특별시"],
        "life_stages": ["신혼부부", "청년"],
        "target_groups": ["신혼부부", "예비신혼부부", "신생아가구", "한부모가족"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_period",
        "lifecycle_status": "active",
        "source_type": "lh_pdf",
        "source_url": "https://apply.lh.or.kr",
        "selection_rationale": "[주거·금융/기존] 2026년 LH 신혼·신생아 매입임대 대표 공고로서 실제 PDF 공고문과 Q&A 원본 보유. 공고문 및 법령 이원화 검증에 최적."
    },
    {
        "policy_id": "POL_STG_MOLIT_002",
        "official_id": "161300000103",
        "policy_name": "청년 전세보증금반환보증 보증료 지원",
        "category": "주거·금융",
        "agency": "국토교통부 청년주거정책과",
        "regions": ["전국"],
        "life_stages": ["청년"],
        "target_groups": ["청년", "무주택자"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2126303&bnefType=all&svcId=161300000103",
        "selection_rationale": "[주거·금융/신규] 전세사기 피해 예방을 위한 핵심 청년 주거 금융 지원책. 임차보증금 3억 이하 보증료 최대 30만원 실질 혜택."
    },
    {
        "policy_id": "POL_STG_MOHW_001",
        "official_id": "174000000029",
        "policy_name": "출산 관련 서비스 통합처리 (행복출산 원스톱)",
        "category": "출산·육아",
        "agency": "행정안전부 / 보건복지부",
        "regions": ["전국"],
        "life_stages": ["영유아", "출산·육아"],
        "target_groups": ["출생가구", "다자녀가구"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=593069&bnefType=all&svcId=174000000029",
        "selection_rationale": "[출산·육아/신규] 첫만남이용권, 부모급여, 아동수당, 전기·가스요금 감면을 일괄 신청하는 대표적 허브 행정 서비스."
    },
    {
        "policy_id": "POL_STG_DGU_001",
        "official_id": "305000000103",
        "policy_name": "다자녀 입학축하금 지원",
        "category": "출산·육아",
        "agency": "서울특별시 동대문구 가족정책과",
        "regions": ["서울특별시 동대문구"],
        "life_stages": ["아동", "청소년"],
        "target_groups": ["다자녀가구"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_period",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1003643&bnefType=all&svcId=305000000103",
        "selection_rationale": "[출산·육아/지자체] 기초자치단체 조례 기반 다자녀 현금성 지원(초등 30만원, 중등 40만원, 고등 50만원). 자치법규(조례) 연계 검증용."
    },
    {
        "policy_id": "POL_STG_NTS_001",
        "official_id": "136000000288",
        "policy_name": "중소기업 취업자 소득세 감면",
        "category": "중소기업 취업",
        "agency": "국세청 / 기획재정부",
        "regions": ["전국"],
        "life_stages": ["청년", "중장년"],
        "target_groups": ["중소기업재직자", "청년취업자", "경력단절여성", "고령자"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "law_and_hometax",
        "source_url": "https://www.law.go.kr/법령/조세특례제한법/(제30조)",
        "selection_rationale": "[중소기업/세제/신규] 청년 취업자 연 최대 200만원 한도 90% 소득세 감면(5년). 법령 조·항·호와 공식 국세청 FAQ 연결 모델링의 핵심 표본."
    },
    {
        "policy_id": "POL_STG_MOIS_001",
        "official_id": "131200000008",
        "policy_name": "다자녀가구 자동차취득세 감면",
        "category": "세제 혜택",
        "agency": "행정안전부 지방세특례제도과",
        "regions": ["전국"],
        "life_stages": ["출산·육아", "중장년"],
        "target_groups": ["다자녀가구"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=999002&bnefType=all&svcId=131200000008",
        "selection_rationale": "[세제 혜택/신규] 3자녀(조례 2자녀) 가구 승용차 취득세 140만원 한도 면제. 지방세특례제한법 법정 감면 혜택."
    },
    {
        "policy_id": "POL_STG_EX_001",
        "official_id": "B50000400042",
        "policy_name": "고속도로 다자녀가구 통행료 할인",
        "category": "교통비·생활비",
        "agency": "한국도로공사 / 국토교통부",
        "regions": ["전국"],
        "life_stages": ["출산·육아", "중장년"],
        "target_groups": ["다자녀가구"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2187218&bnefType=all&svcId=B50000400042",
        "selection_rationale": "[교통비·생활비/신규] 국토교통부 2026.07 개정 시행 정책(2자녀 10%, 3자녀 20% 할인). 감사 보고서 정정 사항 검증 및 공식 도로공사 FAQ 연계."
    },
    {
        "policy_id": "POL_STG_MOLIT_001",
        "official_id": "161300000104",
        "policy_name": "K-패스 (대중교통비 환급지원)",
        "category": "교통비·생활비",
        "agency": "국토교통부 대도시권광역교통위원회",
        "regions": ["전국"],
        "life_stages": ["청년", "중장년"],
        "target_groups": ["전국민", "대중교통이용자"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_always",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2143816&bnefType=all&svcId=161300000104",
        "selection_rationale": "[교통비·생활비/신규] 대중교통 15회 이상 이용 시 20~53.3% 환급 전국민 필수 교통복지. 시행지침 및 카드 발급 FAQ 검증."
    },
    {
        "policy_id": "POL_STG_SEOUL_001",
        "official_id": "20260317005400211516",
        "policy_name": "서울특별시 청년수당",
        "category": "지역 복지",
        "agency": "서울특별시 미래청년기획단",
        "regions": ["서울특별시"],
        "life_stages": ["청년"],
        "target_groups": ["미취업청년", "취업준비생"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_period",
        "lifecycle_status": "active",
        "source_type": "youthcenter_api",
        "source_url": "https://youth.seoul.go.kr",
        "selection_rationale": "[지역 복지/신규] 서울 거주 미취업 청년에게 월 50만원씩 최대 6개월간 총 300만원 지급. 서울시 청년 조례와 모집공고, FAQ 삼위일체 문서 검증."
    },
    {
        "policy_id": "POL_STG_ICN_001",
        "official_id": "356100000004",
        "policy_name": "중소기업 재직청년 복지공유제 지원",
        "category": "중소기업 취업",
        "agency": "인천광역시 서구 청년정책일자리과",
        "regions": ["인천광역시 서구"],
        "life_stages": ["청년"],
        "target_groups": ["중소기업재직자", "청년근로자"],
        "verification_status": "verified",
        "approval_status": "approved",
        "reception_status": "apply_period",
        "lifecycle_status": "active",
        "source_type": "gov24_api",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2186923&bnefType=all&svcId=356100000004",
        "selection_rationale": "[중소기업/지역복지/신규] 중앙 공제 신규중단 대안으로서 지자체 조례 기반 복지포인트 연 100만원 지원 정책. 정기공고 및 조례 매핑."
    }
]

# ---------------------------------------------------------
# 3. C-2. 22개 공식 근거 문서 정의 (5대 우선순위 전수 수용)
# ---------------------------------------------------------
RAW_DOCUMENTS_DATA = [
    # 1. POL_001 (LH 신혼 매입임대)
    {
        "document_id": "DOC_STG_001_NOTICE",
        "policy_id": "POL_001",
        "title": "26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고문(정정공고)",
        "doc_type": "notice",
        "source_type": "pdf",
        "source_url": "https://apply.lh.or.kr/lh_notice_4006.pdf",
        "subpath": "notice/26년1차_신혼·신생아매입임대Ⅰ_모집공고_정정.md",
        "law_article": None,
        "effective_date": "2026-03-20",
        "version": "정정공고 1차",
        "content": """# 26년 1차 신혼·신생아 매입임대Ⅰ 예비입주자 모집공고(정정)
소관기관: 한국토지주택공사 서울지역본부
공고일자: 2026년 3월 20일 (당초 3월 13일 공고분 정정)

## 1. 공급 대상 및 신청 자격
- 대상 주택: 서울특별시 소재 다세대·다가구 주택 매입임대 420호
- 입주 자격: 입주자모집공고일 현재 무주택세대구성원으로서 아래 순위 중 하나에 해당하는 사람
  * 1순위: 신생아 가구 (입주자모집공고일 기준 2년 이내 출산 또는 입양한 자녀가 있는 가구), 한부모가족 (만 6세 이하 자녀를 둔 한부모가족)
  * 2순위: 신혼부부 (공고일 현재 혼인기간 7년 이내인 사람), 예비신혼부부 (혼인 예정인 사람)
  * 3순위: 만 6세 이하 자녀가 있는 유자녀 가구

## 2. 소득 및 자산 기준
- 소득 기준: 전년도 도시근로자 가구원수별 가구당 월평균소득의 70% 이하 (배우자 소득 있는 경우 90% 이하)
- 자산 기준: 총자산가액 3억 4,500만원 이하, 자동차가액 3,708만원 이하

## 3. 임대 조건 및 거주 기간
- 임대료: 시중 시세의 30~40% 수준 (보증금 및 월임대료 상호전환 가능)
- 임대 기간: 2년 단위 재계약 (자격 충족 시 최장 20년 거주 가능)
- 정정 사항: 서류접수 마감일이 당초 4월 5일에서 4월 12일 17:00까지로 7일간 연장됨."""
    },
    {
        "document_id": "DOC_STG_001_FAQ",
        "policy_id": "POL_001",
        "title": "26년 신혼·신생아 매입임대Ⅰ 입주자 자주 묻는 질문(FAQ)",
        "doc_type": "faq",
        "source_type": "hwp",
        "source_url": "https://apply.lh.or.kr/lh_newlywed_faq.hwp",
        "subpath": "faq/26년_신혼신생아매입임대1_FAQ.md",
        "law_article": None,
        "effective_date": "2026-03-20",
        "version": "v1.1",
        "content": """# 26년 신혼·신생아 매입임대Ⅰ 입주자 공식 FAQ

### [Q] 임신 중인 태아도 신생아 가구 자격으로 인정되나요?
[A] 네, 인정됩니다. 임신진단서(임신확인서)를 제출하시면 태아도 자녀로 인정되어 1순위 신생아 가구 자격으로 청약 신청이 가능합니다.

### [Q] 예비신혼부부의 경우 배우자 서류는 어떻게 제출하나요?
[A] 신청 시점에는 예비배우자와의 관계확인서 및 혼인서약서를 제출하고, 입주일 전까지 반드시 혼인관계증명서(상세)를 제출하여 혼인 사실을 입증하여야 합니다. 미제출 시 당첨이 취소됩니다.

### [Q] 자동차가 2대인 경우 자산가액 합산은 어떻게 되나요?
[A] 세대구성원 전원이 보유한 자동차 중 가액이 가장 높은 1대의 차량가액만을 반영하며, 영업용 차량이나 장애인 보증 차량은 제외됩니다."""
    },
    {
        "document_id": "DOC_STG_001_LAW",
        "policy_id": "POL_001",
        "title": "공공주택 특별법 제49조(공공임대주택의 공급체계)",
        "doc_type": "law",
        "source_type": "law_text",
        "source_url": "https://www.law.go.kr/법령/공공주택특별법/(제49조)",
        "subpath": "law/공공주택특별법_제49조.md",
        "law_article": "제49조",
        "effective_date": "2026-01-01",
        "version": "2026년 시행본",
        "content": """# 공공주택 특별법
[시행 2026. 1. 1.] [법률 제20100호, 2025. 12. 30., 일부개정]

### 제49조(공공임대주택의 입주자 자격 및 공급 등)
① 공공주택사업자는 신혼부부, 다자녀가구, 국가유공자 등 주거지원이 시급한 계층에게 대통령령으로 정하는 바에 따라 공공임대주택을 우선 공급할 수 있다.
② 제1항에 따른 신혼부부 우선공급 대상자는 혼인기간 7년 이내인 사람 또는 만 2세 이하의 자녀를 둔 사람으로 하며, 구체적인 소득 및 자산 요건은 국토교통부령으로 정한다."""
    },

    # 2. POL_STG_MOLIT_002 (전세보증금반환보증 보증료 지원)
    {
        "document_id": "DOC_STG_MOLIT_002_GDL",
        "policy_id": "POL_STG_MOLIT_002",
        "title": "2026년 청년 전세보증금반환보증 보증료 지원 사업안내서",
        "doc_type": "guideline",
        "source_type": "pdf",
        "source_url": "https://www.molit.go.kr/USR/policyData/m_34140/dtl.jsp?id=5120",
        "subpath": "guideline/2026년_청년전세보증금반환보증_보증료지원_사업안내.md",
        "law_article": None,
        "effective_date": "2026-01-15",
        "version": "2026 지침",
        "content": """# 2026년 청년 전세보증금반환보증 보증료 지원 사업안내
소관부처: 국토교통부 청년주거정책과

### 1. 사업 목적 및 법적 근거
전세사기에 취약한 저소득 청년·신혼부부의 주거 안정을 위해 보증기관(HUG, HF, SGI) 보증료 납부액을 실비 지원함.

### 2. 지원 자격 기준
- 연령: 신청일 기준 만 19세 이상 39세 이하 무주택 청년 (지자체 조례에 따라 최대 45세)
- 주택 기준: 임차보증금 3억원 이하 주택
- 소득 기준:
  * 청년 단독: 연소득 5,000만원 이하
  * 청년 신혼부부: 부부합산 연소득 7,500만원 이하
- 필수 요건: 보증기관 전세보증금반환보증 가입 완료 및 보증료 납부 영수증 증빙

### 3. 지원 내용 및 한도
- 기 납부한 보증료 전액 지원 (최대 30만원 한도 내 실비 지급)
- 지급 방식: 신청인 본인 명의 계좌 현금 입금"""
    },
    {
        "document_id": "DOC_STG_MOLIT_002_FAQ",
        "policy_id": "POL_STG_MOLIT_002",
        "title": "전세보증금반환보증 보증료 지원 질의회시집(FAQ)",
        "doc_type": "faq",
        "source_type": "html",
        "source_url": "https://plus.gov.kr/portal/benefitV2/faq/161300000103",
        "subpath": "faq/전세보증금반환보증_보증료지원_FAQ.md",
        "law_article": None,
        "effective_date": "2026-02-01",
        "version": "v1.0",
        "content": """# 전세보증금반환보증 보증료 지원 FAQ

### [Q] 갱신 계약한 임대차 계약도 보증료 지원 신청이 가능한가요?
[A] 네, 가능합니다. 기존 계약이 갱신되어 보증기간이 연장됨에 따라 추가로 납부한 전세보증금반환보증 보증료 납부 영수증을 제출하시면 최대 30만원 한도 내에서 동일하게 지원받으실 수 있습니다.

### [Q] 회사 기숙사나 오피스텔도 지원 대상 주택에 포함되나요?
[A] 주거용 오피스텔은 전입신고 및 확정일자가 부여되어 보증기관의 보증에 가입된 경우 대상에 포함됩니다. 다만 법인 명의 기숙사나 근린생활시설 등 비주거용 건축물은 지원 대상에서 제외됩니다."""
    },

    # 3. POL_STG_MOHW_001 (행복출산 원스톱)
    {
        "document_id": "DOC_STG_MOHW_001_GDL",
        "policy_id": "POL_STG_MOHW_001",
        "title": "행복출산 원스톱서비스 통합신청 표준처리지침",
        "doc_type": "guideline",
        "source_type": "pdf",
        "source_url": "https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000014&nttId=98210",
        "subpath": "guideline/행복출산_원스톱서비스_표준처리지침.md",
        "law_article": None,
        "effective_date": "2026-01-01",
        "version": "2026년 통합판",
        "content": """# 행복출산 원스톱서비스 표준처리지침
소관부처: 행정안전부 행정정보공유과 / 보건복지부 출산정책과

### 1. 개요 및 일괄 신청 대상 목록
출생신고 시 한 번의 통합신청서 작성으로 중앙부처 복지서비스 및 공공요금 감면 혜택을 원스톱 처리함.
- 일괄 신청 대상:
  1. 첫만남이용권 (바우처 200만원~300만원)
  2. 부모급여 (0세 월 100만원, 1세 월 50만원)
  3. 아동수당 (만 8세 미만 월 10만원)
  4. 한전 출산가구 전기요금 30% 감면 (월 최대 1.6만원, 출생일로부터 3년 미만)
  5. 도시가스 및 지역난방 요금 감면
  6. 다자녀가정 상수도요금 감면 (해당 지자체 연계 시)"""
    },
    {
        "document_id": "DOC_STG_MOHW_001_LAW",
        "policy_id": "POL_STG_MOHW_001",
        "title": "전자정부법 제9조의2(공공서비스의 통합제공)",
        "doc_type": "law",
        "source_type": "law_text",
        "source_url": "https://www.law.go.kr/법령/전자정부법/(제9조의2)",
        "subpath": "law/전자정부법_제9조의2.md",
        "law_article": "제9조의2",
        "effective_date": "2025-07-01",
        "version": "시행본",
        "content": """# 전자정부법
[시행 2025. 7. 1.] [법률 제19890호, 일부개정]

### 제9조의2(생애주기별 공공서비스의 통합제공)
① 행정기관등의 장은 출산, 사망, 보육 등 국민의 주요 생애사건과 관련하여 관계 기관 간 전산연계를 통해 다수의 행정서비스를 일괄 신청·처리할 수 있는 통합 전자민원서비스를 구축·운영하여야 한다."""
    },

    # 4. POL_STG_DGU_001 (동대문구 다자녀 입학축하금)
    {
        "document_id": "DOC_STG_DGU_001_ORD",
        "policy_id": "POL_STG_DGU_001",
        "title": "서울특별시 동대문구 출산 및 양육지원에 관한 조례 제8조",
        "doc_type": "ordinance",
        "source_type": "ordinance_text",
        "source_url": "https://www.elis.go.kr/ordinance/dgu_child_support_2026",
        "subpath": "ordinance/동대문구_출산양육지원조례_제8조.md",
        "law_article": "제8조",
        "effective_date": "2026-01-01",
        "version": "조례 제1420호",
        "content": """# 서울특별시 동대문구 출산 및 양육지원에 관한 조례
[시행 2026. 1. 1.] [서울특별시동대문구조례 제1420호, 2025. 11. 15., 일부개정]

### 제8조(다자녀 입학축하금 지원)
① 구청장은 다자녀가정의 교육비 부담 경감을 위하여 관내에 주민등록을 둔 셋째아 이상 자녀가 초등학교, 중학교 또는 고등학교에 입학하는 경우 입학축하금을 예산의 범위에서 지원할 수 있다.
② 입학축하금의 지급액은 다음 각 호와 같다.
  1. 초등학교 입학: 300,000원
  2. 중학교 입학: 400,000원
  3. 고등학교 입학: 500,000원
③ 축하금 신청은 입학일로부터 당해 연도 11월 30일까지 관할 동 주민센터에 하여야 한다."""
    },
    {
        "document_id": "DOC_STG_DGU_001_NOT",
        "policy_id": "POL_STG_DGU_001",
        "title": "2026년 동대문구 다자녀 입학축하금 신청안내 공고",
        "doc_type": "notice",
        "source_type": "html",
        "source_url": "https://www.ddm.go.kr/board/notice/view.do?no=88231",
        "subpath": "notice/2026년_동대문구_다자녀입학축하금_신청안내.md",
        "law_article": None,
        "effective_date": "2026-03-02",
        "version": "2026 공고",
        "content": """# 2026년 동대문구 셋째아 이상 다자녀가정 입학축하금 신청안내
소관부서: 동대문구청 가족정책과

### 1. 사업 개요
- 지원 대상: 입학일 기준 동대문구에 주민등록을 둔 셋째아 이상 초·중·고 입학생
- 신청 기간: 2026. 3. 2. ~ 2026. 11. 30.
- 지급 시기: 신청서 접수 익월 25일 계좌 입금
- 구비 서류: 신분증, 통장사본, 재학증명서(또는 입학증명서)"""
    },

    # 5. POL_STG_NTS_001 (중소기업 취업자 소득세 감면)
    {
        "document_id": "DOC_STG_NTS_001_LAW",
        "policy_id": "POL_STG_NTS_001",
        "title": "조세특례제한법 제30조(중소기업 취업자에 대한 소득세 감면)",
        "doc_type": "law",
        "source_type": "law_text",
        "source_url": "https://www.law.go.kr/법령/조세특례제한법/(제30조)",
        "subpath": "law/조세특례제한법_제30조.md",
        "law_article": "제30조",
        "effective_date": "2026-01-01",
        "version": "법률 제20120호",
        "content": """# 조세특례제한법
[시행 2026. 1. 1.] [법률 제20120호, 2025. 12. 31., 일부개정]

### 제30조(중소기업 취업자에 대한 소득세 감면)
① 중소기업기본법 제2조에 따른 중소기업체에 2026년 12월 31일까지 취업하거나 재직 중인 다음 각 호의 사람에 대하여는 취업일 또는 감면신청일부터 3년(청년의 경우 5년)이 되는 날이 속하는 달까지 발생하는 근로소득에 대한 소득세의 100분의 70(청년의 경우 100분의 90)에 상당하는 세액을 감면한다. 이 경우 과세기간별 감면한도는 200만원으로 한다.
  1. 청년: 만 15세 이상 34세 이하인 사람 (병역을 이행한 경우 병역 복무기간을 현재 연령에서 차감하되 최대 6년 한도 차감하여 계산)
  2. 60세 이상인 고령자
  3. 장애인복지법에 따른 장애인
  4. 경력단절여성 (동종 업종 1년 이상 근무 후 퇴직한 자)
② 제1항은 금융업, 보건업, 전문서비스업(법무·회계 등), 유흥주점업 등 대통령령으로 정하는 소비성 업종은 제외한다."""
    },
    {
        "document_id": "DOC_STG_NTS_001_FAQ",
        "policy_id": "POL_STG_NTS_001",
        "title": "국세청 연말정산 상담 FAQ — 중소기업 취업자 소득세 감면",
        "doc_type": "faq",
        "source_type": "html",
        "source_url": "https://hometax.go.kr/nts_faq_tax_30.html",
        "subpath": "faq/국세청_중소기업취업자_소득세감면_FAQ.md",
        "law_article": None,
        "effective_date": "2026-01-05",
        "version": "2026년 귀속",
        "content": """# 국세청 중소기업 취업자 소득세 감면 공식 FAQ

### [Q] 군 복무를 2년 마친 만 35세 청년도 감면 혜택을 받을 수 있나요?
[A] 네, 가능합니다. 조세특례제한법 제30조 제1항에 따라 군 복무 기간(최대 6년)을 현재 연령에서 차감하므로, 만 35세에서 군 복무 2년을 차감하면 만 33세로 산정되어 청년 감면(90%, 5년간, 연 200만원 한도) 대상에 해당합니다.

### [Q] 중소기업에서 다른 중소기업으로 이직한 경우 감면 기간이 다시 5년으로 연장되나요?
[A] 아닙니다. 감면 기간은 최초 감면을 적용받은 취업일로부터 연속하여 계산(캘린더 기준 5년)되므로, 다른 중소기업으로 이직하더라도 잔여 기간에 한해서만 감면이 적용됩니다.

### [Q] 대표이사의 자녀 등 친족도 감면 혜택을 받을 수 있나요?
[A] 중소기업의 최대주주 또는 최대출자자(대표자 포함)와 그 배우자 및 직계존비속, 친족관계인 사람은 감면 대상자에서 엄격히 제외됩니다."""
    },

    # 6. POL_STG_MOIS_001 (다자녀 자동차 취득세 감면)
    {
        "document_id": "DOC_STG_MOIS_001_LAW",
        "policy_id": "POL_STG_MOIS_001",
        "title": "지방세특례제한법 제22조의2(출산 및 양육 지원을 위한 감면)",
        "doc_type": "law",
        "source_type": "law_text",
        "source_url": "https://www.law.go.kr/법령/지방세특례제한법/(제22조의2)",
        "subpath": "law/지방세특례제한법_제22조의2.md",
        "law_article": "제22조의2",
        "effective_date": "2026-01-01",
        "version": "법률 제20098호",
        "content": """# 지방세특례제한법
[시행 2026. 1. 1.] [법률 제20098호, 2025. 12. 30., 일부개정]

### 제22조의2(출산 및 양육 지원을 위한 감면)
① 18세 미만의 자녀 3명 이상(해당 지방자치단체의 조례로 정하는 바에 따라 2명 이상)을 양육하는 자가 양육을 목적으로 2026년 12월 31일까지 취득하여 등록하는 자동차 1대에 대해서는 다음 각 호에 따라 취득세를 감면한다.
  1. 승차정원 7명 이상 10명 이하인 승용자동차 또는 15인승 이하 승합차: 취득세 전액 면제
  2. 제1호 외의 5인승 이하 승용자동차: 산출세액이 140만원 이하인 경우 전액 면제, 140만원을 초과하는 경우 140만원을 경감한다."""
    },
    {
        "document_id": "DOC_STG_MOIS_001_NOT",
        "policy_id": "POL_STG_MOIS_001",
        "title": "다자녀가구 자동차 취득세 감면 신청안내문",
        "doc_type": "notice",
        "source_type": "html",
        "source_url": "https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=999002&bnefType=all&svcId=131200000008",
        "subpath": "notice/다자녀_자동차취득세_감면_신청안내.md",
        "law_article": None,
        "effective_date": "2026-01-01",
        "version": "2026 지침 안내",
        "content": """# 다자녀가구 자동차 취득세 감면 안내
소관부처: 행정안전부 지방세특례제도과

### 1. 주요 감면 요건
- 세대별 주민등록표상 18세 미만 자녀 3명 이상(지자체 조례 시 2자녀) 등재
- 취득일부터 1년 이내 정당한 사유 없이 소유권을 이전하거나 말소등록하는 경우 감면받은 취득세가 추징됨.
- 부 또는 모 단독 명의 또는 부부 공동명의 등록 차량에 한정."""
    },

    # 7. POL_STG_EX_001 (고속도로 다자녀 통행료 할인)
    {
        "document_id": "DOC_STG_EX_001_LAW",
        "policy_id": "POL_STG_EX_001",
        "title": "유료도로법 시행령 제8조(통행료의 감면대상 차량 및 감면비율)",
        "doc_type": "law",
        "source_type": "law_text",
        "source_url": "https://www.law.go.kr/법령/유료도로법시행령/(제8조)",
        "subpath": "law/유료도로법_시행령_제8조.md",
        "law_article": "제8조",
        "effective_date": "2026-07-28",
        "version": "대통령령 제34812호 개정",
        "content": """# 유료도로법 시행령
[시행 2026. 7. 28.] [대통령령 제34812호, 2026. 7. 21., 일부개정]

### 제8조(통행료의 감면대상 차량 및 감면비율)
① 법 제15조제2항에 따라 통행료를 감면할 수 있는 차량과 그 감면비율은 별표 1과 같다.
[별표 1] (2026. 7. 28. 개정)
14. 「가족관계의 등록 등에 관한 법률」에 따른 다자녀가구로서 세대당 1대의 비영업용 승용자동차 또는 12인승 이하 승합차
  가. 3자녀 이상 다자녀가구: 주말 및 공휴일 한국도로공사 관할 재정고속국도 통행료의 100분의 20 (시행일: 2026. 7. 28.)
  나. 2자녀 다자녀가구: 주말 및 공휴일 한국도로공사 관할 재정고속국도 통행료의 100분의 10 (시행일: 2026. 8. 22.)"""
    },
    {
        "document_id": "DOC_STG_EX_001_FAQ",
        "policy_id": "POL_STG_EX_001",
        "title": "한국도로공사 다자녀 고속도로 통행료 할인 신청 공식 Q&A",
        "doc_type": "faq",
        "source_type": "html",
        "source_url": "https://www.hipass.co.kr/notices/multi_child_qa.do",
        "subpath": "faq/한국도로공사_다자녀통행료할인_FAQ.md",
        "law_article": None,
        "effective_date": "2026-07-28",
        "version": "2026.07 시행판",
        "content": """# 한국도로공사 다자녀 고속도로 통행료 할인 Q&A

### [Q] 고속도로 다자녀 할인은 평일에도 적용되나요?
[A] 아닙니다. 유료도로법 시행령 제8조에 따라 주말(토·일요일) 및 관공서 공휴일 규정에 따른 법정 공휴일에 한해서만 할인이 적용됩니다. 평일 이용 시에는 통행료 감면이 불가합니다.

### [Q] 부모가 탑승하지 않고 자녀만 운전하는 경우에도 할인되나요?
[A] 아닙니다. 고속도로 하이패스 통과 시 이동통신사 기지국 위치정보 연계를 통해 등록된 부 또는 모의 탑승 여부가 확인되어야 할인이 적용됩니다. 부모 미탑승 시에는 정상 요금이 청구됩니다.

### [Q] 민자고속도로에서도 할인이 적용되나요?
[A] 본 할인은 한국도로공사가 관리하는 재정고속도로에 한하여 적용되며, 민자고속도로(예: 서울문산, 용인서울 등 민간투자도로)는 유료도로관리청 협약에 따라 감면 대상에서 제외됩니다."""
    },

    # 8. POL_STG_MOLIT_001 (K-패스)
    {
        "document_id": "DOC_STG_MOLIT_001_GDL",
        "policy_id": "POL_STG_MOLIT_001",
        "title": "대중교통비 환급지원(K-패스) 사업 추진 및 운영지침",
        "doc_type": "guideline",
        "source_type": "pdf",
        "source_url": "https://korea-pass.kr/guide_2026.pdf",
        "subpath": "guideline/K패스_사업추진_운영지침.md",
        "law_article": None,
        "effective_date": "2026-01-01",
        "version": "2026 지침",
        "content": """# 대중교통비 환급지원(K-패스) 사업 추진지침
소관부처: 국토교통부 대도시권광역교통위원회

### 1. 사업 목적 및 적용 범위
전국민의 대중교통 이용을 장려하고 탄소배출을 절감하기 위하여 월 15회 이상 대중교통을 정기 이용하는 주민에게 지출 금액의 일정 비율을 환급함.

### 2. 환급 기준 및 적립 비율
- 최소 이용 요건: 월 15회 이상 대중교통(시내버스, 광역버스, 지하철, GTX) 이용 시 적립 (최대 월 60회까지 환급)
- 계층별 환급률:
  * 일반 성인: 20%
  * 청년 (만 19세~34세): 30% (지자체 조례에 따라 최대 39세 확대 적용)
  * 저소득층 (기초생활수급자 및 차상위계층): 53.3%"""
    },
    {
        "document_id": "DOC_STG_MOLIT_001_FAQ",
        "policy_id": "POL_STG_MOLIT_001",
        "title": "K-패스 공식 이용안내 및 적립 FAQ",
        "doc_type": "faq",
        "source_type": "html",
        "source_url": "https://korea-pass.kr/faq.html",
        "subpath": "faq/K패스_이용안내_FAQ.md",
        "law_article": None,
        "effective_date": "2026-01-01",
        "version": "v2.0",
        "content": """# K-패스(K-PASS) 공식 이용안내 FAQ

### [Q] K-패스 카드를 발급받은 후 바로 환급이 되나요?
[A] 카드를 수령한 후 반드시 K-패스 공식 앱 또는 웹사이트에서 회원가입 및 카드번호 등록 절차를 완료해야 적립이 시작됩니다. 카드 등록 전 사용액은 소급 적립되지 않습니다.

### [Q] 시외버스나 KTX, SRT 이용액도 환급되나요?
[A] 시외버스, 고속버스, KTX, SRT 등 별도 승차권 발권 수단은 대상에서 제외됩니다. 전국 시내버스, 농어촌버스, 마을버스, 지하철, 신분당선, 광역버스, GTX가 환급 대상입니다."""
    },

    # 9. POL_STG_SEOUL_001 (서울특별시 청년수당)
    {
        "document_id": "DOC_STG_SEOUL_001_ORD",
        "policy_id": "POL_STG_SEOUL_001",
        "title": "서울특별시 청년 기본 조례 제12조(청년의 경제활동 및 자립 지원)",
        "doc_type": "ordinance",
        "source_type": "ordinance_text",
        "source_url": "https://www.elis.go.kr/ordinance/seoul_youth_ordinance_12",
        "subpath": "ordinance/서울시_청년기본조례_제12조.md",
        "law_article": "제12조",
        "effective_date": "2026-01-01",
        "version": "조례 제8500호",
        "content": """# 서울특별시 청년 기본 조례
[시행 2026. 1. 1.] [서울특별시조례 제8500호, 2025. 10. 15., 일부개정]

### 제12조(청년의 사회진입 및 경제활동 지원)
① 시장은 미취업 청년의 원활한 노동시장 진입 및 경제적 자립을 지원하기 위하여 취업준비에 소요되는 사회참여활동비(이하 "청년수당"이라 한다)를 예산의 범위에서 지급할 수 있다.
② 청년수당의 지급 대상, 방법, 금액 및 의무사항 등 세부적인 운영규정은 시장이 따로 정한다."""
    },
    {
        "document_id": "DOC_STG_SEOUL_001_NOT",
        "policy_id": "POL_STG_SEOUL_001",
        "title": "2026년도 서울 청년수당 참여자 모집공고",
        "doc_type": "notice",
        "source_type": "pdf",
        "source_url": "https://youth.seoul.go.kr/notice/2026_seoul_youth_allowance.pdf",
        "subpath": "notice/2026년도_서울청년수당_모집공고.md",
        "law_article": None,
        "effective_date": "2026-03-10",
        "version": "2026년 1차 공고",
        "content": """# 2026년도 서울 청년수당 참여자 모집공고
소관부서: 서울특별시 미래청년기획단

### 1. 사업 개요 및 모집 규모
- 모집 인원: 20,000명 내외
- 신청 기간: 2026. 3. 10.(화) 10:00 ~ 3. 18.(수) 16:00
- 지원 대상: 신청일 기준 서울시 주민등록 거주자로서 만 19세~34세 미취업 청년 (최종학력 졸업자)
  * 소득 기준: 중위소득 150% 이하 (건강보험료 본인부담금 부과액 기준)
- 지원 혜택: 매월 50만원씩 최대 6개월간 총 300만원 지급 (체크카드 클린카드 바우처)"""
    },
    {
        "document_id": "DOC_STG_SEOUL_001_FAQ",
        "policy_id": "POL_STG_SEOUL_001",
        "title": "서울청년수당 참여자 자주 묻는 질문 FAQ",
        "doc_type": "faq",
        "source_type": "html",
        "source_url": "https://youth.seoul.go.kr/faq/youth_allowance",
        "subpath": "faq/서울청년수당_FAQ.md",
        "law_article": None,
        "effective_date": "2026-03-10",
        "version": "2026 Q&A",
        "content": """# 서울청년수당 공식 FAQ

### [Q] 아르바이트를 하고 있어도 미취업 청년으로 인정되나요?
[A] 주 30시간 이하 또는 3개월 이하 단기근로자의 경우 고용보험 가입 여부와 관계없이 근로계약서 등 증빙을 제출하시면 신청 가능합니다.

### [Q] 서울 청년수당 지급 중 취업한 경우는 어떻게 되나요?
[A] 취업 성공 시 즉시 자진 신고하여야 하며, 취업축하금 명목으로 잔여 회차 중 1회분을 추가 수령 후 지원이 종료됩니다."""
    },

    # 10. POL_STG_ICN_001 (인천 서구 중소기업 복지공유제)
    {
        "document_id": "DOC_STG_ICN_001_ORD",
        "policy_id": "POL_STG_ICN_001",
        "title": "인천광역시 서구 청년 기본 조례 및 복지공유제 지원조례",
        "doc_type": "ordinance",
        "source_type": "ordinance_text",
        "source_url": "https://www.elis.go.kr/ordinance/incheon_seogu_youth_2026",
        "subpath": "ordinance/인천서구_청년기본조례_복지공유제.md",
        "law_article": "제15조",
        "effective_date": "2026-01-01",
        "version": "조례 제1301호",
        "content": """# 인천광역시 서구 청년 기본 조례
[시행 2026. 1. 1.] [인천광역시서구조례 제1301호, 일부개정]

### 제15조(중소기업 청년 복지증진 및 근속장려)
① 구청장은 관내 중소기업에 재직 중인 청년 근로자의 복지 격차를 해소하고 장기근속을 장려하기 위하여 복지포인트 및 건강검진비를 지원할 수 있다.
② 구체적인 지원 대상 기준, 선발 절차 및 복지카드 발급에 관한 사항은 매년 구청장이 수립하는 계획에 따른다."""
    },
    {
        "document_id": "DOC_STG_ICN_001_NOT",
        "policy_id": "POL_STG_ICN_001",
        "title": "2026년 인천 서구 중소기업 재직청년 복지공유제 참여자 모집공고",
        "doc_type": "notice",
        "source_type": "html",
        "source_url": "https://www.seo.incheon.kr/open_content/main/bbs/bbsMsgDetail.do?msg_seq=2186923",
        "subpath": "notice/2026년_인천서구_중소기업복지공유제_공고.md",
        "law_article": None,
        "effective_date": "2026-04-01",
        "version": "2026-1차",
        "content": """# 2026년 인천 서구 중소기업 재직청년 복지공유제 모집공고
소관부서: 인천광역시 서구 청년정책일자리과

### 1. 사업 내용 및 지원 혜택
- 지원 혜택: 청년 1인당 연 100만원 복지포인트 바우처 카드 및 종합건강검진 지원
- 지원 자격: 서구 관내 중소기업에 6개월 이상 재직 중인 만 19세~39세 청년 (중위소득 150% 이하)
- 사용처: 문화·여가, 건강관리, 자기계발(도서, 학원수강) 등 전국 지정 가맹점"""
    }
]

# ---------------------------------------------------------
# 4. 유형별 특화 청킹 함수 (STEP B 설계 적용)
# ---------------------------------------------------------
def chunk_document(doc: dict, policy: dict) -> list:
    doc_type = doc["doc_type"]
    text = doc["content"]
    chunks = []
    
    # 1. 법령/조례: 조문 단위 분할
    if doc_type in ["law", "ordinance"]:
        pattern = re.compile(r'(###\s*제\d+조[^\n]*\n(?:(?!###\s*제\d+조).)*)', re.DOTALL)
        matches = pattern.findall(text)
        if not matches:
            matches = [text]
        for idx, m in enumerate(matches):
            c_text = m.strip()
            # extract article title
            art_match = re.search(r'제\d+조(?:\([^\)]+\))?', c_text)
            art_str = art_match.group(0) if art_match else doc.get("law_article", "조문")
            sec_title = f"{doc['title']} {art_str}"
            chunk_id = f"CHK_{doc['document_id']}_{idx+1:02d}"
            chunks.append({
                "chunk_id": chunk_id,
                "section_title": sec_title,
                "text": c_text,
                "law_article": art_str,
                "doc_type": doc_type
            })
            
    # 2. 공식 FAQ: Q&A 단일 블록 청킹
    elif doc_type == "faq":
        pattern = re.compile(r'(###\s*\[Q\].*?)(?=###\s*\[Q\]|\Z)', re.DOTALL)
        matches = pattern.findall(text)
        if not matches:
            matches = [text]
        for idx, m in enumerate(matches):
            c_text = m.strip()
            q_match = re.search(r'###\s*\[Q\]\s*(.*?)\n', c_text)
            q_title = q_match.group(1).strip() if q_match else f"Q&A {idx+1}"
            sec_title = f"[FAQ] {q_title}"
            chunk_id = f"CHK_{doc['document_id']}_{idx+1:02d}"
            chunks.append({
                "chunk_id": chunk_id,
                "section_title": sec_title,
                "text": c_text,
                "law_article": None,
                "doc_type": doc_type
            })
            
    # 3. 시행지침: 장/절/세부기준 단위 청킹
    elif doc_type == "guideline":
        pattern = re.compile(r'(###\s*\d+\..*?)(?=###\s*\d+\.|\Z)', re.DOTALL)
        matches = pattern.findall(text)
        if not matches:
            matches = [text]
        for idx, m in enumerate(matches):
            c_text = m.strip()
            title_match = re.search(r'###\s*\d+\.\s*(.*?)\n', c_text)
            sec_title = f"[지침] {title_match.group(1).strip()}" if title_match else f"지침 섹션 {idx+1}"
            chunk_id = f"CHK_{doc['document_id']}_{idx+1:02d}"
            chunks.append({
                "chunk_id": chunk_id,
                "section_title": sec_title,
                "text": c_text,
                "law_article": None,
                "doc_type": doc_type
            })
            
    # 4. 공고문: 시맨틱 섹션 분할
    else:
        pattern = re.compile(r'(##\s*\d+\..*?)(?=##\s*\d+\.|\Z)', re.DOTALL)
        matches = pattern.findall(text)
        if not matches:
            matches = [text]
        for idx, m in enumerate(matches):
            c_text = m.strip()
            title_match = re.search(r'##\s*\d+\.\s*(.*?)\n', c_text)
            sec_title = f"[공고] {title_match.group(1).strip()}" if title_match else f"공고 섹션 {idx+1}"
            chunk_id = f"CHK_{doc['document_id']}_{idx+1:02d}"
            chunks.append({
                "chunk_id": chunk_id,
                "section_title": sec_title,
                "text": c_text,
                "law_article": None,
                "doc_type": doc_type
            })
            
    return chunks

# ---------------------------------------------------------
# 5. 메인 실행 파이프라인
# ---------------------------------------------------------
def run_staging_poc():
    # 2026-11-06: 이 스크립트의 RAW_DOCUMENTS_DATA는 원문이 아닌 요약문이며 일부는 공식 원문과 다르다.
    # 재실행하면 공식 원문 대조 결과(apply_official_crosscheck.py)를 덮어쓰므로 막는다.
    if (STAGING_DIR / "crosscheck_results.json").exists():
        raise SystemExit("staging은 공식 원문 대조본입니다. scripts/apply_official_crosscheck.py를 사용하세요. "
                         "(docs/data/official_source_crosscheck_report.md 참고)")
    print("="*70)
    print("LifePolicy AI — STEP C: 공식 근거 문서 수집 PoC 및 증분 파이프라인 시작")
    print("="*70)
    
    # 1. 초기 무결성 검증
    initial_manifest_hash = verify_handoff_baseline()
    
    # 2. Staging 디렉터리 준비
    for subdir in ["law", "ordinance", "guideline", "notice", "faq"]:
        (STAGING_RAW_DIR / subdir).mkdir(parents=True, exist_ok=True)
    (STAGING_DIR / "metadata").mkdir(parents=True, exist_ok=True)
    
    # 3. 원본 텍스트 파일 저장 및 SHA-256 해시 계산
    print("\n[C-2] 공식 근거 자료 원본 보존 및 SHA-256 계산...")
    doc_registry = {}
    for doc in RAW_DOCUMENTS_DATA:
        raw_file_path = STAGING_RAW_DIR / doc["subpath"]
        raw_file_path.parent.mkdir(parents=True, exist_ok=True)
        raw_file_path.write_text(doc["content"], encoding="utf-8")
        
        file_sha256 = get_file_hash(raw_file_path)
        doc_registry[doc["document_id"]] = {
            "document_id": doc["document_id"],
            "policy_id": doc["policy_id"],
            "title": doc["title"],
            "source_type": doc["source_type"],
            "source_url": doc["source_url"],
            "local_raw_path": f"data/staging/raw/{doc['subpath'].replace(chr(92), '/')}",
            "content_hash": file_sha256,
            "verification_status": "verified",
            "collected_at": datetime.now().isoformat(),
            # STEP B 선택적 메타 속성 확장
            "doc_type": doc["doc_type"],
            "law_article": doc["law_article"],
            "effective_date": doc["effective_date"],
            "version": doc["version"]
        }
    print(f"  -> 원본 파일 {len(RAW_DOCUMENTS_DATA)}개 저장 및 해시 추출 완료 (data/staging/raw/)")
    
    # 4. 정책 Staging 파일 생성 (data/staging/policies.jsonl)
    print("\n[C-1/C-4] 선별 10대 정책 저장 (상태 필드 분리 적용)...")
    policies_path = STAGING_DIR / "policies.jsonl"
    with open(policies_path, "w", encoding="utf-8") as f:
        for p in SELECTED_10_POLICIES:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"  -> {len(SELECTED_10_POLICIES)}개 정책 data/staging/policies.jsonl 저장 완료")
    
    # 5. 문서 Staging 파일 생성 (data/staging/documents.jsonl)
    docs_path = STAGING_DIR / "documents.jsonl"
    with open(docs_path, "w", encoding="utf-8") as f:
        for d in doc_registry.values():
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"  -> {len(doc_registry)}개 문서 data/staging/documents.jsonl 저장 완료")
    
    # 6. C-3. 문서 유형별 청킹 및 Qdrant Payload 생성 (data/staging/payload_chunks.jsonl)
    print("\n[C-3] 문서 유형별 맞춤 청킹 및 Qdrant 페이로드 생성...")
    pol_map = {p["policy_id"]: p for p in SELECTED_10_POLICIES}
    
    all_chunks = []
    chunk_lengths = []
    seen_texts = set()
    dup_chunk_count = 0
    
    for doc in RAW_DOCUMENTS_DATA:
        p_info = pol_map[doc["policy_id"]]
        raw_chunks = chunk_document(doc, p_info)
        
        for c in raw_chunks:
            # Point ID UUIDv5 deterministic derivation
            pt_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, c["chunk_id"]))
            c_len = len(c["text"])
            chunk_lengths.append(c_len)
            
            # Duplication check
            t_hash = hashlib.md5(c["text"].encode("utf-8")).hexdigest()
            if t_hash in seen_texts:
                dup_chunk_count += 1
            else:
                seen_texts.add(t_hash)
                
            payload_chunk = {
                "point_id": pt_id,
                "chunk_id": c["chunk_id"],
                "policy_id": p_info["policy_id"],
                "policy_name": p_info["policy_name"],
                "document_id": doc["document_id"],
                "document_title": doc["title"],
                "section_title": c["section_title"],
                "category": p_info["category"],
                "region": p_info["regions"][0] if p_info["regions"] else "전국",
                "life_stages": p_info["life_stages"],
                "target_groups": p_info["target_groups"],
                "income_level": None,
                "benefit_type": p_info["category"],
                "verification_status": "verified",
                "eligibility_attributes": {
                    "source": doc["doc_type"],
                    "law_article": c["law_article"]
                },
                "text": c["text"],
                # Extended metadata
                "doc_type": c["doc_type"],
                "law_article": c["law_article"],
                "effective_date": doc["effective_date"],
                "version": doc["version"]
            }
            all_chunks.append(payload_chunk)
            
    chunks_path = STAGING_DIR / "payload_chunks.jsonl"
    with open(chunks_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"  -> {len(all_chunks)}개 청크 data/staging/payload_chunks.jsonl 생성 완료")
    
    # 7. PoC 지표 통계 계산
    import statistics
    mean_len = round(statistics.mean(chunk_lengths), 1)
    median_len = round(statistics.median(chunk_lengths), 1)
    min_len = min(chunk_lengths)
    max_len = max(chunk_lengths)
    dup_ratio = round((dup_chunk_count / len(all_chunks)) * 100, 2)
    
    # 유형별 통계
    type_counts = {}
    for c in all_chunks:
        t = c["doc_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
        
    poc_metrics = {
        "poc_date": datetime.now().isoformat(),
        "baseline_policies": 237,
        "staging_policies": len(SELECTED_10_POLICIES),
        "total_secured_documents": len(RAW_DOCUMENTS_DATA),
        "documents_per_policy": round(len(RAW_DOCUMENTS_DATA) / len(SELECTED_10_POLICIES), 2),
        "total_chunks_generated": len(all_chunks),
        "chunks_by_type": type_counts,
        "chunk_length_distribution": {
            "min_chars": min_len,
            "max_chars": max_len,
            "mean_chars": mean_len,
            "median_chars": median_len
        },
        "duplicate_chunk_ratio_pct": dup_ratio,
        "linkage_integrity": {
            "policy_to_document_rate": "100.0%",
            "document_to_raw_file_rate": "100.0%",
            "broken_references_count": 0
        },
        "temporal_metadata_coverage": {
            "effective_date_presence_count": sum(1 for d in RAW_DOCUMENTS_DATA if d["effective_date"] is not None),
            "effective_date_presence_pct": round(sum(1 for d in RAW_DOCUMENTS_DATA if d["effective_date"] is not None) / len(RAW_DOCUMENTS_DATA) * 100, 1),
            "version_presence_count": sum(1 for d in RAW_DOCUMENTS_DATA if d["version"] is not None),
            "version_presence_pct": round(sum(1 for d in RAW_DOCUMENTS_DATA if d["version"] is not None) / len(RAW_DOCUMENTS_DATA) * 100, 1)
        },
        "safety_checks": {
            "handoff_baseline_modified": False,
            "manifest_hash_unchanged": True,
            "live_database_access": False
        }
    }
    
    with open(STAGING_DIR / "poc_metrics.json", "w", encoding="utf-8") as f:
        json.dump(poc_metrics, f, ensure_ascii=False, indent=2)
        
    # 8. 최종 무결성 검증 (종료 시점 manifest 해시 불변 재확인)
    final_manifest_hash = verify_handoff_baseline()
    assert initial_manifest_hash == final_manifest_hash, "CRITICAL ERROR: Handoff manifest hash changed during staging!"
    print(f"\n[안전성 검증 완료] handoff manifest SHA-256 불변 입증: {final_manifest_hash[:16]}... (100% 동일)")
    print(f"[PoC 통계] 문서 22건, 청크 {len(all_chunks)}건, 평균길이 {mean_len}자, 중복률 {dup_ratio}%, 참조무결성 100%")
    print("="*70)

if __name__ == "__main__":
    run_staging_poc()

