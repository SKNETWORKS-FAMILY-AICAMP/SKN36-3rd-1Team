# LifePolicy AI — 3차 데이터 수집 및 정제 최종 보고서 (data_collection_report.md)

> **작성자:** 데이터 수집/검증 담당 (장반석)  
> **기준일시:** 2026-11-04 (KST)  
> **상태:** 3차 데이터 전수 수집, 정제, 정합성 검증 100% 완료 (DB 담당자 인계 가능)

---

## 1. 2차 대비 3차 데이터셋 정량적 성과 요약

| 항목 | 2차 데이터셋 | 3차 정제 데이터셋 (현재) | 성과 및 변동 내역 |
|---|---:|---:|---|
| **정책 레코드 (Policies)** | 9건 | **237건** | **+228건 (+2,533%)** / 5대 분야 및 17개 시도 균형 수집 |
| **공식 근거 문서 (Documents)** | 14건 | **246건** | **+232건 (+1,657%)** / 원문 파일 디스크 실재 100% 확인 |
| **Vector 청크 (Chunks)** | 126건 | **555건** | **+429건 (+340%)** / 600~900자 의미 단위 청킹 |
| **Graph 관계 (Relationships)** | 37건 | **2,118건** | **+2,081건 (+5,624%)** / 소관기관·지역·대상군·자격조건 엣지 |
| **JSONL 파싱 오류** | 0건 | **0건** | 전 파일 유효한 JSONL 포맷 |
| **정책-문서 참조 무결성** | 0건 결함 | **0건 결함** | 100% 참조 무결성 보장 |
| **로컬 원본 파일 누락** | 2건 | **0건 (완전 해결)** | `DOC_MOLIT_001`, `DOC_MOEL_001` 원문 파일 복구 |
| **본문 추출 오류 및 잡음** | 2건 | **0건 (완전 해결)** | `DOC_NHUF_008`, `DOC_LH_003` 공식 원문 교체 및 정제 |

---

## 2. 공식 API 3종 실제 연동 및 수집 결과

1. **온통청년 Open API (`collectors/api/youthcenter_collector.py`):**
   - 최신 엔드포인트(`https://www.youthcenter.go.kr/go/ythip/getPlcy`) 및 공식 승인키(`apiKeyNm`) 적용
   - 공식 코드정의서(`data/metadata/youthcenter_code_info.xlsx` / `youthcenter_code_definitions.json`) 기반 8대 코드 그룹 100.0% 정규화
   - 전체 39개 페이지 전수 수집 완료 = **3,861건 원본 확보 (중복 0건, `lastMdfcnDt` 100% 보존)**
   - 자격 조건 불명확 건 1,079건에 대한 `needs_human_review` 정밀 플래그 관리
   - 5대 청년정책 분야(일자리, 주거, 교육, 복지·문화, 참여·권리) 대표 정책 **65건** 정규화 통합
   - 전수 정제 파일: `data/processed/youthcenter_policies.jsonl` (3,861건) 보관
2. **행정안전부 공공서비스 API (`collectors/api/gov24_collector.py`):**
   - 목록(`serviceList`), 상세(`serviceDetail`), 지원조건(`supportConditions`) 3종 엔드포인트 연동
   - 각각 11개 페이지 전수 수집 완료 = **10,942건 × 3종 = 총 32,826개 레코드 원본 확보**
   - 전국 및 17개 광역시·도별 핵심 공공서비스 **65건** 정규화 통합
3. **한국사회보장정보원 중앙부처복지서비스 API (`collectors/api/bokjiro_collector.py`):**
   - 목록(`NationalWelfarelistV001`) 461건 전수 수집 완료
   - 상세(`NationalWelfaredetailedV001`) XML **99건 수집** (국토교통부 청년월세 `WLF00004661.xml` 포함)
   - 상세 원문 보유 정책 **98건** 정규화 통합

---

## 3. P0 기존 데이터 오류 수정 상세

1. **`DOC_NHUF_008` (청년전용 보증부월세대출):**
   - 기존에 잘못 수집되어 있던 버팀목전세자금 FAQ(`FP05020303.jsp`)를 완전히 폐기
   - 공식 대출안내(`FP05020701.jsp`, 4,296자)로 전면 교체
   - 신규로 이용절차 및 제출서류(`FP05020702.jsp`, `DOC_NHUF_009`) 및 주거안정월세 서류(`DOC_NHUF_010`) 확충
2. **`DOC_MOLIT_001` & `DOC_MOEL_001`:**
   - 비존재 임의 요약 텍스트 파일(`data/raw/other/*.txt`) 참조를 제거
   - 복지로 상세 XML(`data/raw/bokjiro/detail/WLF00004661.xml`)과 정부24 공공서비스 원문(`data/raw/gov24/items/161300000099.json`, `149200005007.json`), 온통청년 원문(`data/raw/youthcenter/items/20260710005400113256.json`)을 디스크에 적재하고 실체적 원문으로 복구
3. **`DOC_LH_003`:**
   - 센스리더 스크린리더 안내, GNB 사이트맵 2회 중복, 자동로그아웃 모달, 빈 테이블(`조회된 데이터가 없습니다`)을 제거하고 순수 정정공고문 본문 639자 정제 완료
4. **`POL_001` ~ `POL_010` 수치 전수 교정:**
   - 모집공고문 및 기금 원문 수치(자산한도, 대출한도, 금리, 구직촉진수당 금액 등) 100% 일치 교정

---

## 4. 데이터베이스(Neo4j / Qdrant) 전달 산출물 목록

1. `data/processed/policies.jsonl` (237건) — Neo4j `Policy` 노드
2. `data/processed/documents.jsonl` (246건) — Neo4j `Document` 노드
3. `data/processed/chunks.jsonl` (555건) — Qdrant 컬렉션 적재
4. `data/processed/relationships.jsonl` / `graph_edges.jsonl` (2,118건) — Neo4j 엣지
5. `data/metadata/id_mapping.csv` — ID 변경 및 다중 API 중복 병합 매핑표
6. `data/metadata/dedup_candidates.json` — 중복 병합 상세 근거
7. `data/metadata/source_registry.csv` — 공식 출처 등록부
8. `data/metadata/api_registry.md` — 공식 API 연계 명세서
9. `data/processed/verification_result.json` — 10대 검사 전수 통과 검증 리포트
