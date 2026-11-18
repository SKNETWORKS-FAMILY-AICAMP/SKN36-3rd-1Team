# docs/api/mois — 행정안전부(정부24) 공공서비스 연계 안내

> **위치**: `LifePolicy AI/docs/api/mois/`  
> **역할**: 행정안전부 정부24 공공서비스 등록정보 API 데이터 연계 및 필드 매핑 규격 관리  
> **상위 가이드**: [docs/api 인덱스](../README.md) | [docs 전체 인덱스](../../README.md)

---

## 1. 개요 및 수집 방식

행정안전부의 정부24(Gov24) 공공서비스 등록정보는 중앙부처 및 지자체의 대민 지원 혜택을 포괄하는 핵심 공공 데이터셋입니다.

- **데이터 출처**: 공공데이터포털(data.go.kr) "행정안전부_공공서비스 목록 및 상세 등록정보"
- **연계 방식**: 공공데이터포털 Open API 및 파일 다운로드 배치 수집
- **수집 주기**: 마일스톤 정기 갱신 및 증분 파이프라인 적용

---

## 2. 관련 핵심 규격 문서

- [gov24_field_mapping.md](../../data/gov24_field_mapping.md): **[핵심 매핑표]** 행안부 API 원본 필드(`svcId`, `svcNm`, `trgterInfo`, `slctnCritCn`, `alwAmt`, `reqstMthdCn` 등)와 당사 표준 데이터 계약(`policy_id`, `policy_name`, `target_group`, `condition`, `benefit_amount`, `apply_method` 등) 간의 1:1 변환 규격.
- [cross_agency_condition_mapping.md](../../data/cross_agency_condition_mapping.md): 행안부 지원조건 코드를 복지부 및 온통청년 표준 자격 체계와 상호 호환되도록 통합 매핑한 규격서.

---

## 3. 주요 연계 필드 요약

| 행안부 원본 필드 | 표준 계약 필드 | 매핑 및 처리 규칙 |
|---|---|---|
| `svcId` | `policy_id` | 고유 식별자 접두어 `GOV24_` 부여 (예: `GOV24_123400000000`) |
| `svcNm` | `policy_name` | 정책 명칭 정규화 (특수문자 및 불필요한 기관 태그 정제) |
| `trgterInfo` | `target_summary` | 지원대상 요약 텍스트 정제 |
| `slctnCritCn` | `conditions` | 선정기준 원문에서 연령, 소득, 가구 조건 정규식 추출 |
| `reqstMthdCn` | `apply_method` | `온라인만`, `오프라인만`, `둘 다`, `직권(신청 없음)`으로 표준화 |
| `refrncNm` | `supervising_agency`| 소관 기관명 매핑 |
| `svcPruposeCn` | `description` | 서비스 목적 및 혜택 상세 설명 |
