# LifePolicy AI — 후보 릴리스 v1.4.0-candidate 변경 기록 (CHANGELOG)

- **릴리스 버전**: `v1.4.0-candidate`
- **배포 상태**: `CANDIDATE / PENDING_DB_REVIEW`
- **기준 베이스라인**: `v1.3.2-verified` (절대 무변경 보존)
- **최종 검증 일시**: 2026-11-06 18:13:01
- **책임자**: LifePolicy AI 데이터 아키텍처팀

---

## 1. 베이스라인 대비 실제 파일 집계 수치 (Actual Verified Counts)

| 데이터 항목 | v1.3.2-verified (기존) | v1.4.0-candidate (후보) | 증감 |
| :--- | ---: | ---: | ---: |
| 정책 (`Policy`) | 237 | 246 | +9 |
| 근거 문서 (`Document`) | 246 | 260 | +14 |
| • 신규 문서 `verified_official_api` | - | 8 | +8 |
| • 신규 문서 `verified_official_text` | - | 6 | +6 |
| 벡터 청크 (`Chunk`) | 557 | 590 | +33 |
| 그래프 노드 (전체) | 1034 | 1073 | +39 |
| • Organization | 116 | 123 | +7 |
| • ConditionGroup | 343 | 352 | +9 (루트 UNKNOWN) |
| 그래프 관계 (전체) | 3615 | 3702 | +87 |
| • ADMINISTERED_BY | 237 | 246 | +9 |
| • APPLIES_TO_LIFESTAGE | 419 | 437 | +18 |
| • AVAILABLE_IN | 238 | 247 | +9 |
| • HAS_CATEGORY | 237 | 246 | +9 |
| • HAS_ROOT_CONDITION_GROUP | 237 | 246 | +9 |
| • SUPPORTED_BY | 246 | 260 | +14 |
| • TARGETS | 424 | 443 | +19 |

## 1-1. 신규 문서 공식 원문 대조 (2026-11-06)

- 신규 문서는 공식 원문으로 확인한 것만 포함한다: 국가법령정보센터 조문 원문(`verified_official_text`), 정부24·온통청년 공식 API 응답(`verified_official_api`).
- 초기 후보의 요약 문서 22건 중 원문과 다르거나 원문을 확보하지 못한 문서는 제외했다. 문서별 판정과 사유: `data/staging/crosscheck_results.json`, `docs/data/official_source_crosscheck_report.md`.
- 신규 정책의 자격 조건 그룹은 모두 `UNKNOWN`이며 정책 노드 `verification_status`는 `extracted`이다. 실제 자격 판정에 쓰기 전 조건 구조화가 필요하다.

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
- 단순 접두어(`STARTS WITH 'POL_STG_'`)나 `doc_type != 'notice'` 삭제 방식을 폐기함.
- `release_delta_manifest.json`에 기록된 **정확한 9개 정책 ID, 14개 문서 ID, 7개 기관 ID, 9개 조건그룹 ID, 33개 Qdrant Point ID, 87개 관계 튜플**만을 타겟팅하여 삭제하도록 롤백 스크립트를 재설계함.
