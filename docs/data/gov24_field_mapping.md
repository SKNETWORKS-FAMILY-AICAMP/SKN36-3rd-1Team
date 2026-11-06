# 행정안전부 공공서비스 API 원본 필드 — 정규화 필드 매핑표 (gov24_field_mapping.md)

> **근거 명세:** [공식 Swagger 명세](https://infuser.odcloud.kr/api/stages/44436/api-docs?1684891964110)  
> **적용 API:** `serviceList`, `serviceDetail`, `supportConditions`  
> **원칙:** 지원조건 API에 나타나지 않은 항목은 '조건 없음'이 아닌 '미확인(unconfirmed)'으로 명시  

---

## 1. 기본 정보 및 상세 설명 필드 매핑 (`serviceList` & `serviceDetail`)

| 원본 필드명 | 원본 출처 | 타입 | 정규화 필드명 | 설명 및 정규화 규칙 |
|---|---|---|---|---|
| `서비스ID` | 공통 | String | `official_id` / `service_id` | 공공서비스 고유 식별자 (PK) |
| `서비스명` | 공통 | String | `policy_name` / `service_name` | 정책 및 공공서비스 명칭 |
| `소관기관명` | 공통 | String | `agency` / `organization` | 소관 부처 또는 지자체명 |
| `부서명` | serviceList | String | `organization` (결합) | 소관 상세 부서 |
| `서비스분야` | serviceList | String | `category` / `service_field` | 복지, 주거, 고용 등 정책 분류 |
| `지원유형` | 공통 | String | `support_type` | 현금, 현물, 대출, 이용권 등 |
| `서비스목적요약` / `서비스목적` | 공통 | String | `purpose` / `summary` | 사업 목적 및 개요 |
| `지원대상` | 공통 | String | `target_audience` / `eligibility` | 지원 대상 텍스트 설명 |
| `선정기준` | 공통 | String | `selection_criteria` | 자격 요건 및 선정 기준 텍스트 |
| `지원내용` | 공통 | String | `benefit` | 지원 금액, 혜택, 기간 상세 |
| `신청기한` | 공통 | String | `application_period` | 신청 기간 또는 상시 여부 |
| `신청방법` | 공통 | String | `application_method` | 온라인/방문 등 신청 절차 |
| `구비서류` | serviceDetail | String | `required_documents` | 민원인 지참 제출 서류 목록 |
| `법령` / `자치법규` | serviceDetail | String | `basic_laws` | 정책의 근거 법령 및 조례 |
| `문의처` / `전화문의` | 공통 | String | `contact_info` | 담당 부서 및 문의 전화번호 |
| `온라인신청사이트URL` | serviceDetail | String/null | `official_apply_url` | 신청 주소만 저장, 없거나 안내 주소이면 null |
| `상세조회URL` | serviceList | String | `source_url` | 정부24 공식 상세 안내 웹페이지 |

---

## 2. 지원조건 JA 코드 매핑 (`supportConditions`)

> **JA 코드 해석 원칙:**
> - `Y`: `적용대상` (해당 지원조건이 자격요건으로 적용됨)
> - `None` / 부존재: `미확인` (지원조건 API에 명시되지 않은 것으로 공고문 본문 확인 필요)
> - `JA0110` / `JA0111`: 대상연령 시작 / 종료 정수값

| JA 코드 | Swagger 정의 | 정규화 그룹 | 정규화 속성 | 값 매핑 규칙 |
|---|---|---|---|---|
| `JA0101` | 남성 | 성별조건 (`gender_condition`) | `남성` | Y → 적용대상, None → 미확인 |
| `JA0102` | 여성 | 성별조건 (`gender_condition`) | `여성` | Y → 적용대상, None → 미확인 |
| `JA0110` | 대상연령(시작) | 연령조건 (`age_condition`) | `대상연령_시작` | 정수값 (예: 19), None → 미확인 |
| `JA0111` | 대상연령(종료) | 연령조건 (`age_condition`) | `대상연령_종료` | 정수값 (예: 34), None → 미확인 |
| `JA0201` | 중위소득 0~50% | 소득구간조건 (`income_brackets`) | `중위소득 0~50%` | Y → 적용대상, None → 미확인 |
| `JA0202` | 중위소득 51~75% | 소득구간조건 (`income_brackets`) | `중위소득 51~75%` | Y → 적용대상, None → 미확인 |
| `JA0203` | 중위소득 76~100% | 소득구간조건 (`income_brackets`) | `중위소득 76~100%` | Y → 적용대상, None → 미확인 |
| `JA0204` | 중위소득 101~200% | 소득구간조건 (`income_brackets`) | `중위소득 101~200%` | Y → 적용대상, None → 미확인 |
| `JA0205` | 중위소득 200% 초과 | 소득구간조건 (`income_brackets`) | `중위소득 200% 초과` | Y → 적용대상, None → 미확인 |
| `JA0301` | 예비부모/난임 | 생애주기조건 (`lifecycle_conditions`) | `예비부모/난임` | Y → 적용대상, None → 미확인 |
| `JA0302` | 임산부 | 생애주기조건 (`lifecycle_conditions`) | `임산부` | Y → 적용대상, None → 미확인 |
| `JA0303` | 출산/입양 | 생애주기조건 (`lifecycle_conditions`) | `출산/입양` | Y → 적용대상, None → 미확인 |
| `JA0313` | 농업인 | 취업활동조건 (`employment_conditions`) | `농업인` | Y → 적용대상, None → 미확인 |
| `JA0314` | 어업인 | 취업활동조건 (`employment_conditions`) | `어업인` | Y → 적용대상, None → 미확인 |
| `JA0315` | 축산업인 | 취업활동조건 (`employment_conditions`) | `축산업인` | Y → 적용대상, None → 미확인 |
| `JA0316` | 임업인 | 취업활동조건 (`employment_conditions`) | `임업인` | Y → 적용대상, None → 미확인 |
| `JA0317` | 초등학생 | 학업조건 (`education_conditions`) | `초등학생` | Y → 적용대상, None → 미확인 |
| `JA0318` | 중학생 | 학업조건 (`education_conditions`) | `중학생` | Y → 적용대상, None → 미확인 |
| `JA0319` | 고등학생 | 학업조건 (`education_conditions`) | `고등학생` | Y → 적용대상, None → 미확인 |
| `JA0320` | 대학생/대학원생 | 학업조건 (`education_conditions`) | `대학생/대학원생` | Y → 적용대상, None → 미확인 |
| `JA0326` | 근로자/직장인 | 취업활동조건 (`employment_conditions`) | `근로자/직장인` | Y → 적용대상, None → 미확인 |
| `JA0327` | 구직자/실업자 | 취업활동조건 (`employment_conditions`) | `구직자/실업자` | Y → 적용대상, None → 미확인 |
| `JA0328` | 장애인 | 특수대상조건 (`special_conditions`) | `장애인` | Y → 적용대상, None → 미확인 |
| `JA0329` | 국가보훈대상자 | 특수대상조건 (`special_conditions`) | `국가보훈대상자` | Y → 적용대상, None → 미확인 |
| `JA0330` | 질병/질환자 | 특수대상조건 (`special_conditions`) | `질병/질환자` | Y → 적용대상, None → 미확인 |
| `JA0401` | 다문화가족 | 가구주거조건 (`household_conditions`) | `다문화가족` | Y → 적용대상, None → 미확인 |
| `JA0402` | 북한이탈주민 | 가구주거조건 (`household_conditions`) | `북한이탈주민` | Y → 적용대상, None → 미확인 |
| `JA0403` | 한부모가정/조손가정 | 가구주거조건 (`household_conditions`) | `한부모가정/조손가정` | Y → 적용대상, None → 미확인 |
| `JA0404` | 1인가구 | 가구주거조건 (`household_conditions`) | `1인가구` | Y → 적용대상, None → 미확인 |
| `JA0411` | 다자녀가구 | 가구주거조건 (`household_conditions`) | `다자녀가구` | Y → 적용대상, None → 미확인 |
| `JA0412` | 무주택세대 | 가구주거조건 (`household_conditions`) | `무주택세대` | Y → 적용대상, None → 미확인 |
| `JA0413` | 신규전입 | 가구주거조건 (`household_conditions`) | `신규전입` | Y → 적용대상, None → 미확인 |
| `JA0414` | 확대가족 | 가구주거조건 (`household_conditions`) | `확대가족` | Y → 적용대상, None → 미확인 |
| `JA1101` | 예비창업자 | 소상공인조건 (`business_conditions`) | `예비창업자` | Y → 적용대상, None → 미확인 |
| `JA1102` | 영업중 | 소상공인조건 (`business_conditions`) | `영업중` | Y → 적용대상, None → 미확인 |
| `JA1103` | 생계곤란/폐업예정자 | 소상공인조건 (`business_conditions`) | `생계곤란/폐업예정자` | Y → 적용대상, None → 미확인 |
| `JA2101` | 중소기업 | 기업조건 (`corporate_conditions`) | `중소기업` | Y → 적용대상, None → 미확인 |
| `JA2102` | 사회복지시설 | 기업조건 (`corporate_conditions`) | `사회복지시설` | Y → 적용대상, None → 미확인 |
| `JA2103` | 기관/단체 | 기업조건 (`corporate_conditions`) | `기관/단체` | Y → 적용대상, None → 미확인 |
