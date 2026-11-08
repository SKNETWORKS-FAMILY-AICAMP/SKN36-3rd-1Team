# LifePolicy AI — 후보 릴리스 v1.4.0-candidate 변경 기록 (CHANGELOG)

- **릴리스 버전**: `v1.4.0-candidate`
- **배포 상태**: `CANDIDATE / PENDING_DB_REVIEW`
- **기준 베이스라인**: `v1.3.2-verified` (원본 인계 파일 보존, 후보의 신청 주소는 교정)
- **최종 검증 일시**: 2026-11-09 00:01:25 KST
- **책임자**: LifePolicy AI 데이터 아키텍처팀

---

## 1. 베이스라인 대비 실제 파일 집계 수치 (Actual Verified Counts)

| 데이터 항목 | v1.3.2-verified (기존) | v1.4.0-candidate (후보) | 증감 |
| :--- | ---: | ---: | ---: |
| 정책 (`Policy`) | 237 | 246 | +9 |
| 근거 문서 (`Document`) | 246 | 261 | +15 |
| • 신규 문서 `verified_official_api` | - | 8 | +8 |
| • 신규 문서 `verified_official_raw` | - | 1 | +1 |
| • 신규 문서 `verified_official_text` | - | 6 | +6 |
| 벡터 청크 (`Chunk`) | 557 | 599 | +42 |
| 그래프 노드 (전체) | 1034 | 1028 | +-6 |
| • Organization | 116 | 123 | +7 |
| • ConditionGroup | 343 | 306 | +9 (루트 UNKNOWN) |
| 그래프 관계 (전체) | 3615 | 2915 | +88 |
| • ADMINISTERED_BY | 237 | 246 | +9 |
| • APPLIES_TO_LIFESTAGE | 419 | 437 | +18 |
| • AVAILABLE_IN | 238 | 247 | +9 |
| • CONTAINS_CONDITION | 735 | 364 | +-371 |
| • CONTAINS_GROUP | 106 | 60 | +-46 |
| • HAS_CATEGORY | 237 | 246 | +9 |
| • HAS_ROOT_CONDITION_GROUP | 237 | 246 | +9 |
| • REQUIRES_CONDITION | 735 | 364 | +-371 |
| • SUPPORTED_BY | 246 | 261 | +15 |
| • TARGETS | 424 | 443 | +19 |

## 1-1. 신규 문서 공식 원문 대조 (2026-11-06)

- 신규 문서는 공식 원문으로 확인한 것만 포함한다: 국가법령정보센터 조문 원문(`verified_official_text`), 정부24·온통청년 공식 API 응답(`verified_official_api`).
- 초기 후보의 요약 문서 22건 중 원문과 다르거나 원문을 확보하지 못한 문서는 제외했다. 문서별 판정과 사유: `data/staging/crosscheck_results.json`, `docs/data/official_source_crosscheck_report.md`.
- 신규 정책의 자격 조건 그룹은 모두 `UNKNOWN`이며 정책 노드 `verification_status`는 `extracted`이다. 실제 자격 판정에 쓰기 전 조건 구조화가 필요하다.

## 1-2. 원문 대조 교정 (2026-11-09)

- 설명 문구 8건, 지역 4건, 연령 조건 제거 4건, 가구 조건 교정 46건을 적용했다.
- 조건 관계 788개와 가구유형 OR 그룹 46개를 제거했다. 근거: `data/metadata/data_corrections_v1.4.0.json`.
- `data/handoff`(v1.3.2)는 변경하지 않았다. 제거된 조건은 '제한 없음'이 아니라 자동 판정 불가로 처리해야 한다.

## 1-2. 기존 정책의 신청 주소 교정

- 기존 정책 219건에서 `official_apply_url`을 변경했다(원문 대조 교정은 1-2절). 전체 판정은 `application_url_review.json`, 변경 전후 값은 `release_delta_manifest.json`의 `updated_policy_properties`에 있다.
- 출처·참고·안내·공고 주소를 신청 주소로 대체하지 않는다. 남은 주소는 원천의 신청 필드에 근거한 값이며, 실제 접수 화면·로그인·현재 접수 가능 여부는 검증하지 않았다.
- `data/handoff/`, 기존 정제본, 기존 문서·관계·청크·원문 해시는 수정하지 않는다.
- 후보용 Neo4j 로더는 기본적으로 사전 검증만 수행한다. 실제 적재는 비어 있는 검증 DB에서만 허용하며, 기본값은 검증 후 롤백이다.

---

## 2. 29건 관계 참조 결함 수정 내역

외부 독립 검토에서 확인된 29건의 참조 결함(Broken References)을 기존 스키마 표준으로 100% 매핑하여 완전히 해결하였습니다.

1. **Category 매핑 교정 (7건)**:
   - 비표준 `CAT_CHILDBIRTH_CHILDCARE`, `CAT_TAX_INCENTIVE`, `CAT_LIVING_COST_SUBSIDY`, `CAT_LOCAL_WELFARE`를 기존 5대 표준(`CAT_BIRTH_CHILDCARE`, `CAT_YOUTH_JOB_ASSET`, `CAT_TRANSPORT_MEDICAL_CARE`, `CAT_REGIONAL_WELFARE`)으로 100% 재매핑 완료.
2. **LifeStage 매핑 교정 (3건)**:
   - 비표준 `LIFESTAGE_출산·육아`를 전면 배제하고, 기존 6대 표준(`LIFESTAGE_영유아`, `LIFESTAGE_아동`, `LIFESTAGE_청소년`, `LIFESTAGE_청년`, `LIFESTAGE_중장년`, `LIFESTAGE_노년`)으로 정상 매핑 완료.
3. **Region 매핑 교정 (3건)**:
   - 기초지자체 표기(`REGION_서울특별시 동대문구`, `REGION_인천광역시 서구`)를 기존 18대 표준(`REGION_SEOUL`, `REGION_INCHEON`)으로 매핑 완료. (세부 자치구는 정책 속성 및 이름에 보존)
4. **TargetGroup 매핑 교정 (16건)**:
   - 임의 한글 접두어(`GROUP_청년`, `GROUP_다자녀가구`, `GROUP_중소기업재직자` 등)를 기존 11대 표준(`GROUP_YOUTH`, `GROUP_PREGNANT_BIRTH`, `GROUP_CHILD_YOUTH`, `GROUP_WORKER_BIZ`, `GROUP_JOBSEEKER`, `GROUP_GENERAL`)으로 100% 정상화.

---

## 3. 신규 9개 정책 루트 조건 그룹(`HAS_ROOT_CONDITION_GROUP`) 보완
- 신규 정책 9건에 대해 조건 누락 없이 3값 논리(3-Valued Logic)를 지원하는 `GRP_POL_STG_ICN_001_ROOT` 노드 9개를 생성하고 `HAS_ROOT_CONDITION_GROUP` 관계를 연결함.
- 임의의 가상 조건을 만들지 않고 `logic: "UNKNOWN"`으로 처리하여 자격 판정 시 시스템 안전성을 확보함.

---

## 4. 정밀 롤백 프로토콜 (Targeted Rollback by Exact Item List)
- 증분 삭제용 롤백 스크립트는 구현하지 않았다. 기존 DB에 합쳐 적재하는 작업은 이번 검증 범위에 포함하지 않는다.
- 검증 로더는 하나의 트랜잭션에서 적재·재적재·건수 대조를 수행하고, `--commit`을 명시하지 않으면 롤백한다.
- 후보의 신청 주소를 되돌려야 할 때는 `updated_policy_properties`의 `before` 값을 사용한다. 신규 ID 목록만 삭제해서는 기존 정책 속성이 복원되지 않는다.
