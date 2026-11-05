# LifePolicy AI — 공식 API 연계 및 등록 명세서 (api_registry.md)

> **기준일시:** 2026-11-04 (KST)  
> **상태:** 공식 API 3종(행정안전부 공공서비스, 온통청년, 한국사회보장정보원 중앙부처복지서비스 v2.2) 연동 및 정제 완료  
> **총 수집 건수:** 행정안전부 10,942건 / 온통청년 3,861건 / 한국사회보장정보원 461건 (상세 99건 원문 및 99건 정제 JSONL)

---

## 1. 개요 및 연계 원칙

LifePolicy AI 서비스의 대한민국 정부 및 지자체 지원 정책 추천 및 공식 근거 기반 질의응답을 위해 발급된 공식 공공데이터 API 3종의 명세, 인증 파라미터, 엔드포인트 구조, 오류 코드 처리 및 실제 수집 결과를 기록합니다.

> **보안 및 규정 준수 원칙:**
> - API 키, 인증 토큰, 비밀번호는 절대 Git 저장소, 소스코드, 로그에 출력하거나 커밋하지 않으며 `.env`를 통해서만 안전하게 주입합니다.
> - 무분별한 대량 호출 방지를 위해 요청 간 지연(0.2~0.3초) 및 로컬 캐싱 디렉터리(`data/raw/{gov24,youthcenter,bokjiro}`)를 운용합니다.
> - 지수 백오프 재시도 및 트래픽 초과(HTTP 429 / 게이트웨이 오류코드 22) 시 즉시 안전 종료 정책을 준수합니다.

---

## 2. API별 상세 명세 및 수집 결과

### 2.1 한국사회보장정보원 중앙부처복지서비스 API v2.2 (API ③)

- **제공 기관:** 한국사회보장정보원
- **공식 등록번호:** 15090532
- **활용가이드 버전:** v2.2
- **공식 안내 URL:** https://www.data.go.kr/data/15090532/openapi.do
- **수집 모듈:** `collectors/api/bokjiro_collector.py`
- **환경변수 설정:**
  - 목록 조회 URL: `WELFARE_LIST_API_URL` (`https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001`)
  - 상세 조회 URL: `WELFARE_DETAIL_API_URL` (`https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001`)
  - 인증키: `DATA_GO_KR_API_KEY` (URL 디코딩 적용)
- **통신 프로토콜 / Method:** HTTPS / `GET` (XML 응답)
- **요청 파라미터:**
  - 목록 조회: `callTp=L`, `pageNo`, `numOfRows`, `srchKeyCode=003`
  - 상세 조회: `callTp=D`, `servId` (목록 조회 결과에서 추출)
- **오류 코드 및 예외 처리 정책:**
  - `resultCode == '0'`: 정상 응답 (SUCCESS)
  - 게이트웨이 오류 코드 (`<OpenAPI_ServiceResponse>`):
    * `20`: 접근 거부 (SERVICE_ACCESS_DENIED_ERROR) → 즉시 종료 및 키 권한 확인
    * `22`: 일일 서비스 요청제한 횟수 초과 (LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR) → 즉시 안전 중단 및 캐시 보존
    * `30`: 등록되지 않은 서비스키 (REGISTERED_KEY_ERROR) → 즉시 종료 및 키 재확인
    * `31`: 활용기간 만료 (DEADLINE_HAS_EXPIRED_ERROR) → 즉시 종료 및 갱신 안내
    * `10`: 잘못된 요청 파라미터 (INVALID_REQUEST_PARAMETER_ERROR) → 요청 파라미터 점검
- **상세 응답 필수 보존 필드:**
  - `tgtrDtlCn`: 지원대상 상세 (자격 요건 원문)
  - `slctCritCn`: 선정기준 (소득·재산·가구 기준)
  - `alwServCn`: 급여서비스 (실제 지원 혜택 및 금액)
  - `applmetList`: 신청방법 및 온라인 신청 링크
  - `basfrmList`: 구비서류 목록
  - `baslawList`: 근거법령 목록
  - `inqplCtadrList`: 문의처 및 연락처
  - `lifeArray`, `trgterIndvdlArray`, `intrsThemaArray`: 생애주기, 대상특성, 관심주제
- **저장 분리 구조:**
  - **원본 XML:** `data/raw/bokjiro/list/page_XXXX.xml` 및 `data/raw/bokjiro/detail/<servId>.xml`
  - **정제 JSONL:** `data/processed/bokjiro_welfare_services.jsonl` (99건)
- **실제 호출 및 수집 실적:**
  - **목록 조회:** API 응답 `totalCount=461`건 전수 수집 완료 (수집 건수 461건, 일치율 100%)
  - **상세 조회:** 99건 원문 XML 및 정제 JSONL 적재 완료 (100건 개발계정 쿼터 도달 시 오류코드 22 감지 후 안전 중단)

---

### 2.2 온통청년 청년정책 Open API (API ①)

- **제공 기관:** 국무조정실 / 한국고용정보원
- **수집 모듈:** `collectors/api/youthcenter_collector.py`
- **최신 엔드포인트:** `https://www.youthcenter.go.kr/go/ythip/getPlcy`
- **공식 코드정의서:** `https://www.youthcenter.go.kr/downloadform/API%EC%BD%94%EB%93%9C%EC%A0%95%EB%B3%B4.xlsx` (로컬 저장: `data/metadata/youthcenter_code_info.xlsx`, 파싱 매핑표: `data/metadata/youthcenter_code_definitions.json`)
- **인증 파라미터:** `apiKeyNm` (`YOUTH_POLICY_API_KEY`)
- **요청 파라미터:**
  - `apiKeyNm`: 인증키 (필수)
  - `pageNum`: 페이지 번호 (1부터 시작)
  - `pageSize`: 페이지당 건수 (기본 100, 최대 100)
  - `pageType`: 조회 유형 (`1`: 목록 조회, `2`: 정책번호 기반 상세 조회)
  - `plcyNo`: 정책번호 (`pageType=2`일 때 대상 정책 ID 지정)
  - `rtnType`: 반환 형식 (`json` 또는 `xml`, 기본값 `json` 적용)
  - `zipCd`: 지역코드 (선택)
  - `lclsfNm`, `mclsfNm`: 대분류/중분류 정책 분야 (선택)
- **조회 유형별 실측 및 검증 결과:**
  - `pageType=1` (목록): 100건 단위 페이징 호출 시 정책 기본정보뿐 아니라 연령, 소득, 혼인, 취업, 학력, 전공, 신청방법, 제출서류 등 모든 상세 필드가 손실 없이 일괄 반환됨을 확인.
  - `pageType=2` (상세): `plcyNo`를 지정하여 단건 호출 시 `pageType=1`의 해당 레코드와 100% 동일한 상세 필드가 반환됨을 확인.
- **코드 정의 매핑 (8대 핵심 코드 그룹 100.0% 정상 변환):**
  1. `mrgSttsCd` (혼인상태): `0055001`(기혼), `0055002`(미혼), `0055003`(제한없음)
  2. `earnCndSeCd` (소득조건): `0043001`(무관), `0043002`(연소득), `0043003`(기타)
  3. `jobCd` (취업상태, 다중코드): `0013001`(미취업자) ~ `0013010`(제한없음)
  4. `schoolCd` (학력, 다중코드): `0049001`(고졸 미만) ~ `0049010`(제한없음)
  5. `plcyMajorCd` (전공, 다중코드): `0011001`(인문) ~ `0011009`(제한없음)
  6. `sbizCd` (특화분야, 다중코드): `0014001`(중소기업) ~ `0014010`(해당없음)
  7. `pvsnInstGroupCd` (제공기관구분): `0054001`(중앙부처), `0054002`(지자체)
  8. `plcyPvsnMthdCd` (지급방식): `0042001`(현금지급), `0042005`(바우처) ~ `0042013`(기타)
- **모호 조건 플래그 정책 (`needs_review`):**
  - 공식 원문에 명시되지 않거나 모순되는 조건(예: 최소/최대 연령이 0세로 입력된 경우, 소득 범위 역전 등)은 자의적으로 단정하지 않고 `age_review_status`, `income_review_status`, `marriage_review_status`, `needs_human_review: true` 플래그를 설정하여 관리.
- **저장 분리 구조:**
  - **원본 API 응답 (JSON):** `data/raw/youthcenter/api/page_0001.json` ~ `page_0039.json` (39개 파일, 전 필드 무손실 보존)
  - **정제 JSONL (전수 정규화):** `data/processed/youthcenter_policies.jsonl` (3,861건, `plcyNo` 중복 0건, `lastMdfcnDt` 100% 보존, 원본 코드 및 한국어 레이블 동시 보존)
  - **통합 정제 정책셋:** `data/processed/policies.jsonl` 및 `documents.jsonl` (대표 65건 통합)
  - **Graph 관계:** `data/processed/relationships.jsonl` (`REQUIRES_AGE`, `REQUIRES_MARRIAGE`, `REQUIRES_JOB_STATUS`, `REQUIRES_EDUCATION` 등)
  - **Vector 청크:** `data/processed/chunks.jsonl` (정책설명, 지원내용, 신청절차, 제출서류)
- **실제 수집 실적:** **전체 39개 페이지 전수 수집 완료 = 총 3,861건 (중복 0건, 고유 정책 3,861건)**

---

### 2.3 행정안전부 공공서비스(혜택) API (API ②)

- **제공 기관:** 행정안전부 (등록번호 15113968)
- **수집 모듈:** `collectors/api/gov24_collector.py`
- **엔드포인트:** `https://api.odcloud.kr/api/gov24/v3/{serviceList,serviceDetail,supportConditions}`
- **인증 파라미터:** `serviceKey` (`DATA_GO_KR_API_KEY`)
- **실제 수집 결과:** **목록, 상세, 지원조건 각각 10,942건 전수 수집 완료 (총 32,826개 레코드 결합)**
- **저장 위치:** `data/raw/gov24/serviceList/`, `serviceDetail/`, `supportConditions/`
