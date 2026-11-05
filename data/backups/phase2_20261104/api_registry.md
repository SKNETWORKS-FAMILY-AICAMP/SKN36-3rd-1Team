# LifePolicy AI — 공식 API 연계 및 등록 명세서 (api_registry.md)

> **기준일시:** 2026-11-04 (KST)  
> **작성자:** LifePolicy AI 데이터 수집/검증 담당 (장반석)  
> **상태:** 공공데이터포털 API 2종(행안부, 사회보장정보원) 연동 모듈 구축 완료 / 온통청년 관리자 심사 대기

---

## 1. 개요 및 연계 원칙

LifePolicy AI 서비스의 대한민국 정부 및 지자체 지원 정책 추천 및 공식 근거 기반 질의응답을 위해 조사·검증된 공식 API의 명세, 인증 절차, 엔드포인트 구조, 호출 제한 및 현재 상태를 기록합니다.

> **보안 및 규정 준수 원칙:**
> - 비인가 인증키 생성 또는 우회 시도를 일절 금지하며, 공식 관리자 승인 절차를 거친 키만 사용합니다.
> - API 키, 토큰, 비밀번호는 절대 Git 저장소에 커밋하지 않으며, `.env` 및 환경변수(`DATA_GO_KR_API_KEY`)를 통해 안전하게 주입합니다.
> - 무분별한 대량 호출을 방지하고 요청 간 적정 지연(0.3초) 및 로컬 캐싱을 적용합니다.

---

## 2. API별 상세 명세

### 2.1 행정안전부 공공서비스(혜택) API (API ①)

- **제공 기관:** 행정안전부
- **공식 등록번호:** 15113968
- **공식 안내 URL:** https://www.data.go.kr/data/15113968/openapi.do
- **수집 모듈:** `collectors/api/gov24_collector.py`
- **엔드포인트:**
  - 목록 조회: `https://api.odcloud.kr/api/gov24/v3/serviceList`
  - 상세 조회: `https://api.odcloud.kr/api/gov24/v3/serviceDetail`
  - 지원조건 조회: `https://api.odcloud.kr/api/gov24/v3/supportConditions`
- **통신 프로토콜 / Method:** HTTPS / `GET`
- **응답 형식:** JSON
- **인증 파라미터:** `serviceKey` (공공데이터포털 일반인증키)
- **일일 트래픽:** 개발계정 일 10,000건
- **수집 대상 정보:** 중앙부처 및 지자체 공공서비스 목록, 서비스명, 지원대상, 선정기준, 지원내용, 신청방법, 접수기관, 문의처 등

### 2.2 한국사회보장정보원 중앙부처복지서비스 API (API ②)

- **제공 기관:** 한국사회보장정보원
- **공식 등록번호:** 15090532
- **공식 안내 URL:** https://www.data.go.kr/data/15090532/openapi.do
- **수집 모듈:** `collectors/api/bokjiro_collector.py`
- **엔드포인트:**
  - 목록 조회: `http://apis.data.go.kr/B554287/NationalWelfareInformations/nationalWelfaresList`
  - 상세 조회: `http://apis.data.go.kr/B554287/NationalWelfareInformations/nationalWelfareDetail`
- **통신 프로토콜 / Method:** HTTP / `GET`
- **응답 형식:** XML (ElementTree 파서로 JSON 변환)
- **인증 파라미터:** `serviceKey` (공공데이터포털 일반인증키 - URL 디코딩 적용)
- **주요 요청 인자:** `callTp` (L: 목록, D: 상세), `pageNo`, `numOfRows`, `servId` (상세 조회 시)
- **수집 대상 정보:** 생애주기별(임신·출산, 영유아, 아동·청소년, 청년, 중장년, 노년) 복지서비스, 지원주기, 제공유형, 담당부처, 대상자 선정기준, 구비서류, 온라인 신청 URL

### 2.3 온통청년 청년정책 Open API (API ③)

- **제공 기관:** 국무조정실 / 한국고용정보원
- **공식 안내 URL:** https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiGuide
- **명세서 URL:** https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc
- **수집 모듈:** `collectors/api/youthcenter_collector.py`
- **요청 엔드포인트:** `https://www.youthcenter.go.kr/opi/youthPlcyList.do`
- **통신 프로토콜 / Method:** HTTPS / `GET`
- **응답 형식:** XML / JSON
- **인증 파라미터:** `openApiVlak` (신청 승인 후 발급)
- **현재 상태 (2026-11-04):** **신청 완료 / 심사 및 발급 대기**
  - 비인가 키 호출 시 302 리다이렉트(포트 8080 타임아웃) 발생 확인.
  - 심사 승인 완료 후 `.env`에 `YOUTH_POLICY_API_KEY`를 설정하면 즉시 수집 가능하도록 수집기 준비 완료.

---

## 3. API 실행 및 키 설정 방법

1. 프로젝트 루트에 `.env` 파일을 생성하거나 환경변수를 설정합니다:
   ```env
   DATA_GO_KR_API_KEY=발급받은_공공데이터포털_일반인증키
   YOUTH_POLICY_API_KEY=온통청년_승인키(대기중)
   ```
2. 수집 실행 명령어:
   ```powershell
   # 행안부 공공서비스 API 수집
   python collectors/api/gov24_collector.py --limit 20

   # 한국사회보장정보원 복지서비스 API 수집
   python collectors/api/bokjiro_collector.py --limit 20

   # 온통청년 API 수집 (키 승인 후)
   python collectors/api/youthcenter_collector.py --page 1 --display 20
   ```
