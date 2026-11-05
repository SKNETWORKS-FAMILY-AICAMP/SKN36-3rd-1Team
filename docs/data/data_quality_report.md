# LifePolicy AI — 3차 데이터 품질 및 정합성 검증 보고서 (data_quality_report.md)

> **기준일시:** 2026-11-04 (KST)  
> **검증 대상:** `data/processed/` 디렉터리 내 JSONL 5종 전수 검증  
> **검증 결과:** **10대 전수 검사 항목 10/10 100% 통과 (결함 0건)**

---

## 1. 2차 데이터 품질 문제점 조치 및 검증 결과

| 대상 ID | 2차 문제점 | 원인 분석 | 3차 조치 내역 | 검증 결과 |
|---|---|---|---|---|
| **DOC_NHUF_008** | 청년전용 보증부월세대출 문서에 버팀목전세자금 FAQ 혼입 | 원본 수집 시 청년전용 버팀목 FAQ URL(`FP05020303.jsp`)이 오기입됨 | 공식 대출안내(`FP05020701.jsp`, `nhuf_youth_monthly_rent_deposit_guide.html`)로 전면 교체하고, 이용절차/서류(`FP05020702.jsp`, `DOC_NHUF_009`) 신규 구축 | **정상 (4,296자 공식 원문 일치)** |
| **DOC_MOLIT_001** | `data/raw/other/molit_youth_rent_official.txt` 원본 파일 누락 | 5줄짜리 임의 가상 텍스트 요약본 참조 | 실제 공식 API 원문인 복지로 중앙부처복지서비스(`data/raw/bokjiro/detail/WLF00004661.xml`) 및 정부24(`data/raw/gov24/items/161300000099.json`, `DOC_MOLIT_002`)로 교체 | **정상 (실제 원문 파일 기반 verified)** |
| **DOC_MOEL_001** | `data/raw/other/moel_kua_official.txt` 원본 파일 누락 | 5줄짜리 임의 가상 텍스트 요약본 참조 | 실제 공식 API 원문인 정부24(`data/raw/gov24/items/149200005007.json`) 및 온통청년(`data/raw/youthcenter/items/20260710005400113256.json`, `DOC_MOEL_002`)으로 교체 | **정상 (실제 원문 파일 기반 verified)** |
| **DOC_LH_003** | 센스리더 안내, GNB 사이트맵 2회 중복, 자동로그아웃 모달, 빈 테이블混入 | `#container` 셀렉터 사용으로 웹페이지 전역 UI 컴포넌트까지 통째로 추출됨 | `#sub_container > section` 타겟팅 및 `.lyPop`, `.btns`, `.sns_box`, 빈 테이블(`조회된 데이터가 없습니다`) 제거 파서 적용 | **정상 (순수 공고문 639자 정제)** |
| **POL_008** | 온통청년 API 서비스 안내 레코드가 정책 목록에 혼재 | 메타데이터와 정책 레코드 분리 미흡 | 정책 목록(`policies.jsonl`)에서 완전 배제하고 `data/metadata/api_metadata.json`으로 이관 | **정상 (정책 0건 분리 완료)** |
| **POL_001~010** | 자산·소득·대출한도·금리·지원기간 불일치 | 원문 PDF/HTML 개정 사항 반영 지연 | `POL_001` 총자산 3.37억/3.54억, `POL_003` 수도권 2.5억/1.6억, `POL_004` 1.5억 및 2.2~3.3%, `POL_006` 최대 1,440만원(월 60만원), `POL_007` 보증금 4,500만원/월세 50만원/60㎡, `POL_009` 24개월/480만원, `POL_010` 구직촉진수당 월 50만~90만원 대조 교정 | **정상 (전수 일치)** |

---

## 2. 10대 검사 기준 자동화 검증 결과 (`scripts/verify_data_quality.py`)

```
=================================================================
      LifePolicy AI 3차 데이터 정합성 및 품질 전수 검증        
=================================================================

[Check 1/10] JSONL Parsing & Schema Loading Check
  - policies       :   237 records successfully loaded
  - documents      :   246 records successfully loaded
  - chunks         :   555 records successfully loaded
  - relationships  :  2118 records successfully loaded
  - graph_edges    :  2118 records successfully loaded

[Check 2/10] Required Fields Completeness Check
  - Missing field violations: 0

[Check 3/10] Policy ID Uniqueness Check
  - Total policies: 237, Unique IDs: 237, Duplicate IDs: 0

[Check 4/10] Document ID Uniqueness & Policy->Document Reference Integrity
  - Total documents: 246, Unique: 246, Broken references: 0

[Check 5/10] Vector Chunk -> Document & Policy Reference Integrity
  - Total chunks: 555, Duplicate chunk IDs: 0, Broken references: 0

[Check 6/10] Graph Relationships Target & Evidence Integrity
  - Total relationships: 2118, Relation integrity violations: 0

[Check 7/10] Local Raw File Physical Existence Check (local_raw_path)
  - Missing raw files: 0 (전체 로컬 파일 디스크 상 실재 확인)

[Check 8/10] HTML Login/Session/Error Page Contamination Check
  - Contaminated documents: 0

[Check 9/10] Meaningless Short Text (< 100 chars) Check
  - Short text violations: 0

[Check 10/10] Source URL, Timestamps & Semantic Title-Content Consistency
  - URL/Timestamp/Semantic errors: 0

=================================================================
  전수 검증 결과: 10대 검사항목 중 10개 통과 / 0개 실패 (PASSED)
=================================================================
```

---

## 3. 검증 상태(Verification Status) 분류 체계 및 관리 기준

| 상태값 | 정의 | 건수 |
|---|---|---:|
| **verified** | 공식 원문(PDF/HTML/API)과 1:1 대조 및 수치 정합성 수동·의미 검증이 완료된 고품질 정책·문서 | 정책 21건 / 문서 30건 |
| **extracted** | 공식 공공데이터 API 원문 XML/JSON에서 파싱 및 스키마 정규화가 완료된 정책·문서 | 정책 216건 / 문서 216건 |
| **합계 (Total)** | 통합 데이터셋 전수 레코드 | **정책 237건 / 문서 246건** |
| **downloaded** | 원본 파일 다운로드가 완료되었으나 파싱 전 상태 (해당사항 없음) | 0건 |
| **failed** | 수집 실패 또는 본문 추출 오류 발생 (해당사항 없음 - 전원 복구) | 0건 |
| **needs_review** | 원문 확인 불가 또는 수기 검토 대기 (해당사항 없음) | 0건 |

---

## 4. 기준 데이터셋(v1) 및 매니페스트 관리 현황

- **v1 매니페스트:** `data/metadata/manifest_v1.json` 및 `data/releases/v1/manifest.json`
- **v1 불변 스냅샷 보관:** `data/releases/v1/`
- **파일명 표준화 및 호환성 조치:**
  - 표준 정제 파일명: `data/processed/gov24_public_services.jsonl` (10,942건)
  - 하위 호환성 지원: `data/processed/gov24_merged_services.jsonl` (동일 내용 동기화 완료)
- **보안 검증:** API 키(`DATA_GO_KR_API_KEY`, `YOUTH_POLICY_API_KEY` 등) 미포함 전수 검증 통과 (0건)
