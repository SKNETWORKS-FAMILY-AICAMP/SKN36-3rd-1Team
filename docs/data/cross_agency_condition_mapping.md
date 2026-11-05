# LifePolicy AI — 기관별 코드 및 지원조건 통합 매핑표 (cross_agency_condition_mapping.md)

> **기준일시:** 2026-11-05 (KST)  
> **대상 기관:** 한국사회보장정보원(복지로), 행정안전부(정부24), 고용정보원(온통청년)  
> **원칙:** 원본 코드/명칭과 내부 정규화 속성을 동시 보존하고, 비교 연산자·단위·검증 상태를 명시

---

## 1. 생애주기 및 연령 조건 통합 매핑

| 내부 정규화 속성 | 연산자 | 값 및 단위 | 복지로 원본 필드 | 정부24 원본 필드 (JA코드) | 온통청년 원본 필드 | 검증 및 처리 상태 |
|---|---|---|---|---|---|---|
| `life_cycle.영유아` | `IN` | `['영유아']` | `lifeArray: 영유아` | `JA0303(출산/입양)` | `sprtTrgtMinAge <= 5` | 공식 원문 대조 완료 |
| `life_cycle.아동` | `IN` | `['아동']` | `lifeArray: 아동` | `JA0317(초등학생)` | `sprtTrgtMinAge <= 12` | 공식 원문 대조 완료 |
| `life_cycle.청소년` | `IN` | `['청소년']` | `lifeArray: 청소년` | `JA0318(중학), JA0319(고등)` | `sprtTrgtMinAge <= 18` | 공식 원문 대조 완료 |
| `life_cycle.청년` | `IN` | `['청년']` | `lifeArray: 청년` | `JA0320(대학생/대학원생)` | 청년정책 전반 (`plcyNo`) | 공식 원문 대조 완료 |
| `life_cycle.중장년` | `IN` | `['중장년']` | `lifeArray: 중장년` | `JA0110 >= 40 AND <= 64` | `sprtTrgtMaxAge >= 40` | 공식 원문 대조 완료 |
| `life_cycle.노년` | `IN` | `['노년']` | `lifeArray: 노년` | `JA0110 >= 65` | `sprtTrgtMinAge >= 65` | 공식 원문 대조 완료 |
| `age_condition.min` | `>=` | 정수 (세) | `tgtrDtlCn` 텍스트 추출 | `JA0110` (대상연령 시작) | `sprtTrgtMinAge` | 0세 시 제한없음/오류 분리 |
| `age_condition.max` | `<=` | 정수 (세) | `tgtrDtlCn` 텍스트 추출 | `JA0111` (대상연령 종료) | `sprtTrgtMaxAge` | 0세 시 제한없음/오류 분리 |
| `age_condition.unlimited`| `IS` | `True/False` | `slctCritCn` (연령제한없음) | `JA0110/JA0111 IS NULL` | `sprtTrgtAgeLmtYn: N` | N은 제한없음으로 정규화 |

---

## 2. 소득 및 자산 조건 통합 매핑

| 내부 정규화 속성 | 연산자 | 값 및 단위 | 복지로 원본 필드 | 정부24 원본 필드 (JA코드) | 온통청년 원본 필드 | 검증 및 처리 상태 |
|---|---|---|---|---|---|---|
| `income_level.median_50` | `<=` | 50 (%) | `slctCritCn: 중위소득 50%` | `JA0201 (0~50%)` | `earnEtcCn` 텍스트 추출 | 자동 검증 완료 |
| `income_level.median_75` | `<=` | 75 (%) | `slctCritCn: 중위소득 75%` | `JA0202 (51~75%)` | `earnEtcCn` 텍스트 추출 | 자동 검증 완료 |
| `income_level.median_100`| `<=` | 100 (%) | `slctCritCn: 중위소득 100%` | `JA0203 (76~100%)` | `earnEtcCn` 텍스트 추출 | 자동 검증 완료 |
| `income_level.annual_amt`| `<=` | 원 (KRW) | `slctCritCn` 텍스트 추출 | - | `earnMaxAmt` (0043002) | 0원 시 기타설명 확인 필요 |
| `income_level.no_limit`  | `IS` | `True` | `slctCritCn: 소득기준 없음`| JA02 미기재 | `earnCndSeCd: 0043001` | 무관 100% 정상 매핑 |
| `asset_level.limit`      | `<=` | 원 (KRW) | `slctCritCn` 텍스트 추출 | 공고문 참조 | 공고문 참조 | 수동 검증 완료 |

---

## 3. 혼인 및 가족 상태 통합 매핑

| 내부 정규화 속성 | 연산자 | 값 및 단위 | 복지로 원본 필드 | 정부24 원본 필드 (JA코드) | 온통청년 원본 필드 | 검증 및 처리 상태 |
|---|---|---|---|---|---|---|
| `family_status.신혼부부` | `==` | 혼인 7년 이내 | `trgterIndvdlArray: 신혼부부` | `JA0301 (예비부모/난임)` | `mrgSttsCd: 0055001 (기혼)` | 공고문 혼인기간 대조 |
| `family_status.미혼`     | `==` | 미혼 | `tgtrDtlCn` 텍스트 | - | `mrgSttsCd: 0055002 (미혼)` | 코드 매핑 완료 |
| `family_status.임신출산` | `IN` | 임산부/영아 | `lifeArray: 임신·출산` | `JA0302(임산부), JA0303(출산)`| `sbizCd: 여성` 연계 | 자동 검증 완료 |
| `family_status.한부모`   | `IN` | 한부모/조손 | `trgterIndvdlArray: 한부모·조손`| `JA0403 (한부모/조손)` | `sbizCd: 한부모` | 코드 매핑 완료 |
| `family_status.1인가구`  | `==` | 단독가구 | `tgtrDtlCn` 텍스트 | `JA0404 (1인가구)` | `ptcpPrpTrgtCn` 등 참조 | 자동 검증 완료 |
| `family_status.다자녀`   | `>=` | 2자녀 이상 | `trgterIndvdlArray: 다자녀` | 공고문 참조 | `sbizCd: 다자녀` | 자동 검증 완료 |

---

## 4. 취업 및 경제활동 상태 통합 매핑

| 내부 정규화 속성 | 연산자 | 값 및 단위 | 복지로 원본 필드 | 정부24 원본 필드 (JA코드) | 온통청년 원본 필드 (jobCd) | 검증 및 처리 상태 |
|---|---|---|---|---|---|---|
| `employment.unemployed`  | `==` | 미취업/구직자 | `tgtrDtlCn: 구직자` | `JA0327 (구직자/실업자)` | `0013001 (미취업자)` | 코드 매핑 완료 |
| `employment.employed`    | `==` | 재직자/근로자 | `tgtrDtlCn: 근로자` | `JA0326 (근로자/직장인)` | `0013002 (재직자)` | 코드 매핑 완료 |
| `employment.entrepreneur`| `==` | 창업자/소상공인| `tgtrDtlCn: 창업자` | - | `0013003 (창업자)` | 코드 매핑 완료 |
| `employment.freelancer`  | `==` | 프리랜서 | `tgtrDtlCn` 텍스트 | - | `0013004 (프리랜서)` | 코드 매핑 완료 |
| `employment.agriculture` | `IN` | 농·어·임·축산 | `tgtrDtlCn: 농어업인` | `JA0313~JA0316` | `sbizCd: 농어업인` | 코드 매핑 완료 |
| `employment.no_limit`    | `IS` | `True` | 제한없음 | 제한없음 | `0013010 (제한없음)` | 코드 매핑 완료 |

---

## 5. 특수대상 및 취약계층 통합 매핑

| 내부 정규화 속성 | 연산자 | 값 및 단위 | 복지로 원본 필드 | 정부24 원본 필드 (JA코드) | 온통청년 원본 필드 (sbizCd) | 검증 및 처리 상태 |
|---|---|---|---|---|---|---|
| `special_target.장애인`  | `==` | 등록장애인 | `trgterIndvdlArray: 장애인` | `JA0328 (장애인)` | `0014004 (장애인)` | 코드 매핑 완료 |
| `special_target.보훈`    | `==` | 보훈대상자 | `trgterIndvdlArray: 보훈대상자` | `JA0329 (국가보훈대상자)` | - | 코드 매핑 완료 |
| `special_target.다문화`  | `==` | 이주배경/다문화| `trgterIndvdlArray: 다문화·탈북민`| `JA0401(다문화), JA0402(탈북민)` | `0014006 (다문화)` | 코드 매핑 완료 |

