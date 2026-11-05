# LifePolicy AI — 전처리 및 정규화 규칙 (3차 확정)

> **담당:** 데이터 수집/검증 담당 (장반석)  
> **상태:** **3차 연동 및 전수 검증 완료**  
> **근거:** [PRD §4.1](../product/PRD.md), [CODING_STYLE §5](../development/CODING_STYLE.md), 3차 작업 지시서 §6

---

## 1. 원본 보존 및 안전한 로컬 저장 (Raw Layer)

- 모든 수집 원본은 `data/raw/<provider>/` 디렉터리에 변형 없이 영구 보존합니다.
  - `data/raw/nhuf/`: 주택도시기금 HTML/PDF (보증부월세대출 신규 HTML 포함)
  - `data/raw/lh/`: LH 공고문/유의사항 PDF 및 정제된 HTML
  - `data/raw/work24/`: 고용24 내일배움카드 HTML
  - `data/raw/gov24/`: 정부24 공공서비스 API 응답(목록·상세·조건 33개 JSON + 개별 아이템 65건)
  - `data/raw/youthcenter/`: 온통청년 API 39개 페이지 JSON + 개별 아이템 65건
  - `data/raw/bokjiro/`: 한국사회보장정보원 목록 XML 1건 + 상세 XML 99건
- 로컬 경로는 반드시 프로젝트 루트 기준 상대 경로 및 정방향 슬래시(`/`)를 적용합니다.

---

## 2. 본문 정제 및 노이즈 제거 규칙 (`collectors/preprocessing/normalize.py`)

1. **HTML 정제:**
   - `<script>`, `<style>`, `<nav>`, `<button>` 태그 및 `.lnb`, `.location`, `.btn_wrap`, `.sns_wrap` 등 내비게이션·버튼 영역 분해(`decompose`).
   - 주택도시기금: `.contents` 또는 `#contArea` 셀렉터로 본문 격리 후 탭/공유/자동로그아웃 텍스트 제거.
   - LH 청약플러스: `#sub_container > section` 타겟팅 및 스크린리더(센스리더) 가이드, 자동로그아웃 모달(`.lyPop`), 빈 테이블(`조회된 데이터가 없습니다`) 완전 배제.
   - 고용24: `#content .cont_wrap` 본문 영역만 추출.
2. **PDF 텍스트 추출:**
   - `pypdf.PdfReader`를 사용하여 페이지 단위로 원형 텍스트 추출 (`--- [Page N] ---`).
   - 표 구조, 자격 조건 문장, 부정문(단, 제외 등) 손실 없이 보존.
3. **Open API XML/JSON 정규화:**
   - 복지로 XML: `wlfareInfoOutlCn`, `tgtrDtlCn`, `slctCritCn`, `alwServCn`, `basfrmList`(구비서류)를 구조적 텍스트로 합성.
   - 정부24 JSON: `지원대상`, `선정기준`, `지원내용`, `구비서류`, `신청기한`을 통합 포맷팅.
   - 온통청년 JSON: `plcyExplnCn`, `plcySprtCn`, `addAplyQlfcCndCn`, `earnEtcCn`, `sbmsnDcmntCn` 결합.

---

## 3. 중복 탐지 및 병합 규칙

1. 동일 정책 판별 기준:
   - 공식 사업명, 소관 부처/운영기관, 지원 대상군, 지원 내용(월세/구직수당/훈련비), 공식 신청 URL 일치 여부 비교.
2. 중복 처리 방식:
   - 다중 출처(복지로/정부24/온통청년)에 동시 존재하는 정책은 **대표 policy_id로 단일화** (`POL_004`, `POL_005`, `POL_006`, `POL_009`, `POL_010`).
   - 각 출처의 상세 문서는 상호 보완적인 근거 문서(`DOC_MOLIT_001`, `DOC_MOLIT_002`, `DOC_MOEL_001`, `DOC_MOEL_002`)로 연계.
   - 모든 병합 및 매핑 이력은 `data/metadata/id_mapping.csv` 및 `data/metadata/dedup_candidates.json`에 기록.

---

## 4. Vector 청킹 및 Graph 엣지 생성 규칙

1. **청킹 규칙 (`chunks.jsonl`):**
   - PDF 문서는 1페이지 내외 기준 분할.
   - 긴 HTML/API 문서는 600~900자 단위 분할, 80자 오버랩 적용.
   - 각 청크에 `chunk_id`, `document_id`, `policy_id`, `policy_name`, `category`, `region`, `target_group` 메타데이터 부여.
2. **그래프 엣지 생성 규칙 (`relationships.jsonl` / `graph_edges.jsonl`):**
   - `ADMINISTERED_BY`: 정책 -> 소관기관
   - `HAS_CATEGORY`: 정책 -> 5대 분야 카테고리
   - `AVAILABLE_IN`: 정책 -> 지역 (전국 또는 17개 시도)
   - `TARGETS`: 정책 -> 생애주기/계층 대상군 (청년, 신혼부부, 취약계층 등)
   - `SUPPORTED_BY`: 정책 -> 공식 근거 문서
   - `MODIFIES_CONDITION_OF`: 개정/우대 규정 -> 대상 정책
