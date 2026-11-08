# LifePolicy AI — 최종 통합 데이터 계약서 (data_contract.md)

> **기준일시:** 2026-11-05 (KST)  
> **버전:** `v1.3.2-verified`  
> **개정 (2026-11-06):** `Policy.official_apply_url`을 선택(Nullable)으로 변경 — 신청 주소가 확인된 경우에만 값이 있다. `Document`의 `doc_type`·`law_article`·`effective_date`·`version` 등 선택 속성 명시. v1.3.2 데이터 자체는 변경 없음(기존 값은 모두 채워져 있어 호환).  
> **당사자:** 데이터 엔지니어링 파이프라인(생성) ↔ Neo4j Graph DB & Qdrant Vector DB(소비)  
> **목적:** 데이터 파이프라인과 다운스트림 데이터베이스(Graph/Vector) 간의 무결성, 필드 타입, 제약조건 및 생명주기를 규정하는 공식 법적/기술적 계약

---

## 1. 식별자(ID) 체계 및 상호 참조 계약

모든 식별자는 대소문자를 구분(Case-sensitive)하며, 시스템 전역에서 유일(Globally Unique)해야 합니다.

| 식별자 명칭 | 정규식 패턴 | 설명 및 예시 | 소유 및 참조 주체 |
|---|---|---|---|
| **`policy_id`** | `^POL_[A-Z0-9_]+$` | 프로젝트 전역 정책 기본 키 (PK)<br>예: `POL_001`, `POL_GOV_001`, `POL_YOUTH_001` | Neo4j `(:Policy)` PK, Qdrant Payload FK |
| **`official_id`** | `^[A-Za-z0-9_-]+$` | 원천 기관/공식 API의 원본 서비스 식별자<br>예: `161300000099`, `WLF00004656`, `20261103005400214707` | 원천 기관 대조 및 실시간 갱신용 |
| **`document_id`** | `^DOC_[A-Z0-9_]+$` | 공식 근거 문서 기본 키 (PK)<br>예: `DOC_LH_001`, `DOC_GOV_001`, `DOC_YOUTH_001` | Neo4j `(:Document)` PK, Qdrant Payload FK |
| **`chunk_id`** | `^(DOC_[A-Za-z0-9_]+_(P[0-9]+_)?C[0-9]+|CHK_[A-Za-z0-9_]+)$` | Qdrant 벡터 검색용 청크 기본 키 (PK)<br>예: `DOC_LH_001_P01_C01`, `DOC_GOV_001_C01` | Qdrant Payload 식별자 |
| **`point_id`** | `^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` | Qdrant 컬렉션 고유 Point ID (RFC 4122 규격 표준 UUIDv5)<br>생성 규칙: `UUIDv5(NAMESPACE_DNS, chunk_id)` | Qdrant 벡터 포인트 PK (결정론적 1:1 매핑) |
| **`agency_id`** | `^AGENCY_[A-Za-z0-9_가-힣]+$` | 소관기관/지자체 기본 키 (PK)<br>예: `AGENCY_한국토지주택공사_서울지역본부`, `AGENCY_보건복지부` | Neo4j `(:Organization)` PK |
| **`category_id`** | `^CAT_[A-Z0-9_]+$` | 정책 대분류 기본 키 (PK)<br>예: `CAT_HOUSING_FINANCE`, `CAT_YOUTH_JOB_ASSET` | Neo4j `(:Category)` PK |
| **`region_id`** | `^REGION_[A-Z0-9_]+$` | 지역 범위 기본 키 (PK)<br>예: `REGION_SEOUL`, `REGION_NATIONWIDE` | Neo4j `(:Region)` PK |
| **`stage_id`** | `^LIFESTAGE_[가-힣]+$` | 6대 표준 생애단계 기본 키 (PK)<br>예: `LIFESTAGE_청년`, `LIFESTAGE_중장년` | Neo4j `(:LifeStage)` PK |
| **`target_group_id`** | `^GROUP_[A-Z0-9_]+$` | 대상 집단 기본 키 (PK)<br>예: `GROUP_NEWLYWED`, `GROUP_LOW_INCOME` | Neo4j `(:TargetGroup)` PK |
| **`group_id`** | `^GRP_[A-Za-z0-9_]+$` | 복합 조건 그룹 기본 키 (PK)<br>예: `GRP_POL_001_ROOT`, `GRP_POL_GOV_001_OR_INC` | Neo4j `(:ConditionGroup)` PK |
| **`condition_id`** | `^COND_[A-Z0-9_가-힣]+$` | 원자 자격 조건 기본 키 (PK)<br>예: `COND_AGE_19_35`, `COND_HH_무주택세대`, `COND_INC_중위소득_0_50_` | Neo4j `(:EligibilityCondition)` PK |

---

## 2. 공통 필드 및 자료형 계약

### 2.1 필수 필드와 선택 필드 구분
- **정책(`Policy`):**
  - **필수(NOT NULL):** `policy_id`, `official_id`, `policy_name`, `source_type`, `category`, `agency`, `organization`, `regions`, `life_stages`, `eligibility_attributes`, `requirements`, `eligibility`, `benefit`, `application_period`, `application_method`, `required_documents`, `source_url`, `source_document_ids`, `verification_status`, `reception_status`, `collected_at`
  - **선택(Nullable):** `application_start`, `application_end`, `lastMdfcnDt`, `review_flags`, `official_apply_url`
    - `official_apply_url`: **실제 신청 주소가 확인된 경우에만 값이 있다.** 법령·서비스 안내(정부24·복지로 상세 등) 페이지만 있으면 `null`이며, 해당 주소는 `source_url`에 둔다. 소비 측(UI)은 값이 있을 때만 '신청하기'를 노출한다. (2026-11-06 개정)
- **근거 문서(`Document`):**
  - **필수(NOT NULL):** `document_id`, `policy_id`, `title`, `source_type`, `source_url`, `local_raw_path`, `content_hash`, `text`, `verification_status`, `collected_at`
  - **선택(Nullable):** `published_at`, `effective_at`, `doc_type`, `law_article`, `effective_date`, `version`, `extracted_text_path`, `official_id`
    - `law_article`·`effective_date`·`version`은 법령·조례·공고에만 의미가 있어 서비스 안내·FAQ 문서는 `null`이다. (2026-11-06 개정, v1.4.0-candidate 신규 문서 속성)
- **벡터 청크(`Chunk`):**
  - **필수(NOT NULL):** `chunk_id`, `policy_id`, `document_id`, `policy_name`, `category`, `region`, `life_stages`, `section_title`, `text`, `source_url`, `verification_status`

---

## 3. Null 및 미확인(Unknown) 값 처리 원칙

1. **임의 추측 금지:**
   - 공식 문서나 원천 API에 명시되지 않은 조건(소득 상한, 자산 기준, 성별 등)은 `null` 또는 `미확인`으로 명시하며, 임의로 '조건 없음'이나 가상의 숫자로 채우지 않는다.
2. **0~0세 이상치 처리 계약:**
   - 원천 API에 연령이 `0~0세`로 기재된 경우:
     * 연령제한여부(`age_limit_yn`)가 'N'이면: `min_age: null, max_age: null, age_limit: false` (연령제한없음)
     * 연령제한여부가 'Y'이면: `min_age: null, max_age: null, age_limit: true, age_review_needed: true` (입력 오류 및 공고문 확인 대상)
3. **3값 논리(3-Valued Logic) 준수:**
   - 조건 평가 시 `TRUE`(충족), `FALSE`(미충족), `UNKNOWN`(원문 미기재로 심사 필요)의 3가지 상태를 엄격히 구분한다.

---

## 4. 원천 출처 정보 및 메타데이터 계약

1. **기관별 출처 표기 표준:**
   - `bokjiro_api`: 한국사회보장정보원 중앙부처복지서비스 API v2.2
   - `gov24_api`: 행정안전부 공공서비스(혜택) Open API
   - `youthcenter_api`: 국무조정실/한국고용정보원 온통청년 청년정책 API
   - `nhuf_html` / `lh_pdf`: 국토교통부 주택도시기금 포털 및 LH 한국토지주택공사 공고문
2. **로컬 원본 파일 보존 (`local_raw_path`):**
   - 모든 문서는 디스크 상의 원본 실재 파일(`data/raw/...`)을 1:1로 가리켜야 하며, 파일이 누락되어서는 안 된다.
3. **무결성 검증 (`content_hash`):**
   - 모든 문서 본문 텍스트는 SHA-256 해시값을 보유하여 원문 변조 여부를 상시 검증할 수 있어야 한다.

---

## 5. 생애단계(LifeStage) 분류 표준 계약

Neo4j 노드 레이블 및 Qdrant 필터 필드는 **`LifeStage`**로 단일화하며 다음 6대 표준 구간을 사용합니다:

| 생애단계 코드 | 표준 명칭 | 연령 정의 | 주요 판별 규칙 |
|---|---|---|---|
| `LIFESTAGE_영유아` | `영유아` | 만 0세 ~ 5세 | 취학 전 아동, 보육, 신생아, 첫만남이용권 등 |
| `LIFESTAGE_아동` | `아동` | 만 6세 ~ 12세 | 초등학생 연령, 아동수당, 방과후 돌봄 등 |
| `LIFESTAGE_청소년` | `청소년` | 만 13세 ~ 18세 (확장 9~24세) | 중·고등학생 연령, 청소년기본법 지원 등 |
| `LIFESTAGE_청년` | `청년` | 만 19세 ~ 39세 | 청년기본법(19~34) 및 지자체 청년조례(19~39) |
| **`LIFESTAGE_중장년`** | **`중장년`** | **만 40세 ~ 64세** | **고용상 연령차별금지법, 고용노동부·통계청 중장년 일자리 기준** |
| `LIFESTAGE_노년` | `노년` | 만 65세 이상 | 노인복지법, 기초연금, 노인맞춤돌봄 등 |
| `unknown` | `unknown` | 미확정 | 원문 연령 근거 부재 시 임의 배정 금지 |

---

## 6. 자격 조건 자료형 및 비교 연산자 계약

`EligibilityCondition` 노드 및 `requirements` 객체에서 지원하는 공식 연산자 규격입니다.

| 조건 분류 | 비교 연산자 | 값 단위 | 예시 |
|---|---|---|---|
| **연령 (`AGE`)** | `>=`, `<=`, `BETWEEN` | 정수 (세) | `AGE BETWEEN 19 AND 34` |
| **소득 비율 (`INCOME_MEDIAN`)** | `<=`, `>` | 백분율 (%) | `INCOME_MEDIAN <= 50` |
| **소득 금액 (`INCOME_AMOUNT`)** | `<=`, `>` | 원 (KRW) | `INCOME_AMOUNT <= 50000000` |
| **혼인 상태 (`MARRIAGE`)** | `==`, `IN` | 코드/명칭 | `MARRIAGE IN ['기혼', '신혼부부']` |
| **취업 상태 (`EMPLOYMENT`)** | `==`, `IN` | 코드/명칭 | `EMPLOYMENT == '구직자·취업준비생'` |
| **주택 소유 (`HOUSING`)** | `==` | 상태명 | `HOUSING == '무주택자'` |
| **특수 자격 (`SPECIAL`)** | `==`, `IN` | 특수대상명 | `SPECIAL == '장애인'` |
| **배제 조건 (`EXCLUSION`)** | `NOT IN`, `!=` | 제외대상명 | `NOT IN ['타 디딤돌 대출 기수혜자']` |

---

## 7. 중복 정책 식별 및 병합 계약

1. **병합 기준:**
   - 서로 다른 기관(예: 복지로, 정부24, 온통청년)에서 수집되었더라도 소관 부처, 사업 목적, 지원 내용이 동일한 경우(예: 청년월세 한시 특별지원, 국민취업지원제도, 국민내일배움카드) 단일 `policy_id`로 통합한다.
   - 단, 단순 정책명의 유사성만으로는 병합하지 않으며 소관부처 및 지원대상 기준이 일치할 때만 병합한다.
2. **근거 문서 보존:**
   - 정책은 하나로 병합되더라도 각 기관의 원본 문서는 각각 고유한 `document_id`로 생성되어 `[:SUPPORTED_BY]` 관계로 다중 연결된다.
3. **매핑 추적성:**
   - 모든 병합 이력은 `data/metadata/id_mapping.csv` 및 `data/metadata/dedup_candidates.json`에 영구 보존된다.
