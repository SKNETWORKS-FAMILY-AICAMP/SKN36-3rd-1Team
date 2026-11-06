# LifePolicy AI — 후보 릴리스 v1.4.0-candidate 변경 기록 (CHANGELOG)

- **릴리스 버전**: `v1.4.0-candidate`
- **배포 상태**: `CANDIDATE / PENDING_DB_REVIEW`
- **기준 베이스라인**: `v1.3.2-verified` (절대 무변경 보존)
- **최종 검증 일시**: 2026-11-06 17:27:44
- **책임자**: LifePolicy AI 데이터 아키텍처팀

---

## 1. 베이스라인 대비 실제 파일 집계 수치 (Actual Verified Counts)

| 데이터 항목 | v1.3.2-verified (기존) | v1.4.0-candidate (후보) | 증감 내역 (Delta) | 호환성 및 무결성 판정 |
| :--- | :---: | :---: | :---: | :---: |
| **통합 정책 (`Policy`)** | 237건 | **246건** | **+9건 신규** (기존 1건 병합) | 충돌 0건 (100% PASS) |
| **근거 문서 (`Document`)** | 246건 | **268건** | **+22건 공식 근거** | 4단계 검증 분류 적용 |
| • *공식 원본 직보유 (`verified_official_raw`)* | - | 2건 | +2건 | LH 모집공고문(PDF) / FAQ(HWP) |
| • *법령·조례 직추출 (`verified_extracted`)* | - | 8건 | +8건 | 법률 제49조, 제30조, 조례 등 |
| • *공식 포털 정제본 (`reference_restructured`)* | - | 12건 | +12건 | 정부24/포털 지침·FAQ 정제본 |
| **벡터 청크 (`Chunk`)** | 557건 | **593건** | **+36건 맞춤 청크** | UUIDv5 충돌 0건 (100% PASS) |
| **그래프 노드 (`Total Nodes`)** | 1,034개 | **1,082개** | **+48개 노드 증분** | 고유 제약조건 100% PASS |
| • *Policy 노드* | 237개 | 246개 | +9개 | PK 유일 |
| • *Document 노드* | 246개 | 268개 | +22개 | PK 유일 |
| • *Organization 노드* | 116개 | 124개 | +8개 | PK 유일 (신규 부처 8개) |
| • *ConditionGroup 노드* | 343개 | 352개 | +9개 (루트 UNKNOWN) | PK 유일 |
| • *Category / LifeStage / Region / TargetGroup / Condition* | 92개 | 92개 | 0개 (기존 100% 재사용) | 불변 |
| **그래프 관계 (`Relationship`)** | 3,615건 | **3,710건** | **+95건 관계 증분** | **Broken References = 0건** |
| • *SUPPORTED_BY* | 246건 | 268건 | +22건 | 출처 추적 100% |
| • *ADMINISTERED_BY* | 237건 | 246건 | +9건 | 소관기관 매핑 100% |
| • *HAS_CATEGORY* | 237건 | 246건 | +9건 | 5대 표준 카테고리 100% |
| • *AVAILABLE_IN* | 238건 | 247건 | +9건 | 18대 표준 광역지역 100% |
| • *APPLIES_TO_LIFESTAGE* | 419건 | 437건 | +18건 | 6대 표준 생애단계 100% |
| • *TARGETS* | 424건 | 443건 | +19건 | 11대 표준 대상집단 100% |
| • *HAS_ROOT_CONDITION_GROUP* | 237건 | 246건 | +9건 | 루트 조건 그룹 연결 100% |
| • *기타 조건 DAG 관계* | 1,577건 | 1,577건 | 0건 (기존 보존) | 무변경 보존 |

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
- [`release_delta_manifest.json`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/data/releases/v1.4.0-candidate/release_delta_manifest.json)에 기록된 **정확한 9개 정책 ID, 22개 문서 ID, 8개 기관 ID, 9개 조건그룹 ID, 36개 Qdrant Point ID, 95개 관계 튜플**만을 타겟팅하여 삭제하도록 롤백 스크립트를 재설계함.
