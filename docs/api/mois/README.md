# docs/api/mois — 행정안전부(정부24) 공공서비스 연계 안내

> **위치**: `LifePolicy AI/docs/api/mois/`  
> **역할**: 행정안전부 정부24 공공서비스 등록정보 API 데이터 연계 및 필드 매핑 규격 관리  
> **상위 가이드**: [docs/api 인덱스](../README.md) | [docs 전체 인덱스](../../README.md)

---

## 1. 개요 및 수집 방식

행정안전부의 정부24(Gov24) 공공서비스 등록정보는 중앙부처 및 지자체의 대민 지원 혜택을 포괄하는 핵심 공공 데이터셋입니다.

- **데이터 출처**: 공공데이터포털 행정안전부 공공서비스 Open API ([Swagger 명세](https://infuser.odcloud.kr/api/stages/44436/api-docs?1684891964110))
- **사용 API**: `serviceList`(목록), `serviceDetail`(상세), `supportConditions`(지원조건 JA 코드). 수집기: `collectors/api/gov24_collector.py`
- **원본 보관**: `data/raw/gov24/` (원본 필드명은 한글 키 그대로 저장)

---

## 2. 관련 핵심 규격 문서

- [gov24_field_mapping.md](../../data/gov24_field_mapping.md): **[핵심 매핑표]** 행안부 API 원본 필드(`서비스ID`, `서비스명`, `지원대상`, `선정기준`, `지원내용`, `신청방법` 등 한글 키)와 정규화 필드(`official_id`, `policy_name`, `target_audience`, `selection_criteria`, `benefit`, `application_method` 등)의 변환표. 지원조건 JA 코드 매핑도 이 문서에 있다.
- [cross_agency_condition_mapping.md](../../data/cross_agency_condition_mapping.md): 행안부 지원조건 코드를 복지부 및 온통청년 표준 자격 체계와 상호 호환되도록 통합 매핑한 규격서.

---

## 3. 주요 연계 필드 요약

[gov24_field_mapping.md](../../data/gov24_field_mapping.md) 1절에서 자주 쓰는 필드만 옮겼다. 전체 목록과 지원조건(JA 코드) 매핑은 원문 표를 본다.

| 행안부 원본 필드 | 정규화 필드 | 설명 |
|---|---|---|
| `서비스ID` | `official_id` / `service_id` | 공공서비스 고유 식별자. 원본 대조와 갱신 추적에 쓴다 |
| (프로젝트 부여) | `policy_id` | 프로젝트 정책 ID. 정부24에서 고른 정책은 `POL_GOV_001` 형식(`scripts/build_clean_dataset.py`), 4차 추가 정책은 `POL_STG_…` 형식 |
| `서비스명` | `policy_name` / `service_name` | 정책 및 공공서비스 명칭 |
| `소관기관명` | `agency` / `organization` | 소관 부처 또는 지자체명 (`부서명`은 `organization`에 결합) |
| `지원대상` | `target_audience` / `eligibility` | 지원 대상 텍스트 |
| `선정기준` | `selection_criteria` | 자격 요건 및 선정 기준 텍스트 |
| `지원내용` | `benefit` | 지원 금액, 혜택, 기간 |
| `신청기한` | `application_period` | 신청 기간 또는 상시 여부 |
| `신청방법` | `application_method` | 온라인/방문 등 신청 절차 (원문 그대로 저장) |
| `서비스목적요약` / `서비스목적` | `purpose` / `summary` | 사업 목적 및 개요 |
| `온라인신청사이트URL` | `official_apply_url` | 신청 주소만 저장, 없거나 안내 주소이면 null |
| `상세조회URL` | `source_url` | 정부24 공식 상세 안내 웹페이지 |

- 나이·소득·가구 등 자격 조건은 본문 문장이 아니라 `supportConditions`의 JA 코드에서 만든다. 지원조건 API에 없는 항목은 '조건 없음'이 아니라 '미확인'이다.
