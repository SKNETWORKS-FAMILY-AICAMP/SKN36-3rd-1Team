# #39 자녀 월령 — 실제 R3D DB 읽기 전용 검증 결과

> 이 파일은 검증 스크립트(`r3d_issue39.py` v2, 저장소 밖)가 실제 실행 결과로 자동 생성한 부분과,
> 그 아래 사람이 정리한 해석 절로 나뉜다. 자동 생성 부분의 값은 손으로 바꾸지 않는다.

## 1. 실행 환경 (자동 생성)

- 실행 시각: 2026-11-27T10:12:46+09:00
- 코드: `feature/39-child-age-api-pipeline` HEAD `1a41973` + 미커밋 변경(계약 문서·테스트·이 보고서, 판정 코드 변경 없음)
- DB: R3D verify — Qdrant 127.0.0.1:56756, Neo4j 127.0.0.1:56787
- 인증: 로컬 DPAPI 인증 파일을 현재 Windows 사용자로 복호화해 실행 프로세스 환경 변수로만 전달(값 미출력·미기록, 실행 후 삭제)
- 접근 방식: Neo4j READ 세션 count·조회, Qdrant count·scroll·검색만. 쓰기·재적재·스키마 변경 없음
- 검색 프로필 `b2_06b_v140`, 컬렉션(별칭) `exp_b2_qwen3emb06b_v140_v1`
- 실행 전 Neo4j {'policies': 249, 'nodes': 1147, 'relationships': 2702} / Qdrant {'exp_b2_qwen3emb06b_v140_v1__tmp_4ab3e799968e06bfc7142d2ce41ca22a': 1249, 'exp_b3_qwen3emb06b_v140_v1__tmp_0c323833068aac41f45f7c00e7a177ae': 1338}
- 실행 후 Neo4j {'policies': 249, 'nodes': 1147, 'relationships': 2702} / Qdrant {'exp_b2_qwen3emb06b_v140_v1__tmp_4ab3e799968e06bfc7142d2ce41ca22a': 1249, 'exp_b3_qwen3emb06b_v140_v1__tmp_0c323833068aac41f45f7c00e7a177ae': 1338} → **건수 불변**
- Generator는 Mock(외부 LLM 호출 없음)

## 2. 실제 R3D 정책 데이터 (자동 생성)

- Neo4j `POL_GOV_050` REQUIRES_CONDITION: `[{'id': 'COND_CHILD_AGE_LE_24M', 'type': 'SPECIAL_TARGET', 'op': 'LTE', 'value': '24', 'unit': '개월', 'name': '대상 자녀 연령: 24개월 이하'}]`
- Neo4j 루트 그룹: `[{'group_id': 'GRP_POL_GOV_050_ROOT', 'logic': 'AND', 'id': 'COND_CHILD_AGE_LE_24M'}]`
- Neo4j Policy.eligibility(수집 요약 문구): `○ 24개월 미만 아이를 양육하면서, 신청일 현재 전주시 관내에 주민등록을 두고 거주하는 장애인`
- Qdrant `POL_GOV_050` 청크: `[('CHK_B2_DOC_GOV_050_C001', 'DOC_GOV_050'), ('CHK_B2_DOC_GOV_050_C002', 'DOC_GOV_050'), ('CHK_B2_DOC_GOV_050_C003', 'DOC_GOV_050')]`, 원문 표현 출현 횟수: `{'24개월 미만': 2, '24개월 이하': 1, '생후 24개월까지': 1}`
- 검색 결과 data_quality: hold_auto_eligibility=True, hold_reasons=['MARRIAGE_CHILD_NOT_MODELED', 'SEARCH_REGION_REQUIRES_REVIEW', 'SUSPECTED_REGION_GRANULARITY_LOST'], affected_attributes=['children', 'region']
- 정적 플래그 파일(neo4j_semantic_flags_v1_4_0) `POL_GOV_050`: [('S10_MARRIAGE_CHILD_NOT_MODELED', 'MARRIAGE_CHILD_NOT_MODELED', ['children'])]

## 3. 자녀 월령 판정 결과 (자동 생성, 상태는 `자녀 월령 조건 / 정책 종합`)

- L1 = 실제 조건 노드의 구조화 비교(Checker 조건 단위 평가, 보류 적용 전)
- L2 = 실제 데이터 그대로(데이터 품질 보류 포함)
- L3 = 같은 실제 정책 객체에서 보류만 메모리상 끈 사본(DB 변경 없음)

| child_age_months | L1 비교 | L2 Checker | L2 Pipeline | L2 API | L3 Checker | L3 Pipeline | L3 API |
|---|---|---|---|---|---|---|---|
| 0 | eligible | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | eligible / needs_check | eligible / needs_check | eligible / needs_check |
| 10 | eligible | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | eligible / needs_check | eligible / needs_check | eligible / needs_check |
| 23 | eligible | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | eligible / needs_check | eligible / needs_check | eligible / needs_check |
| **24** | eligible | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | eligible / needs_check | eligible / needs_check | eligible / needs_check |
| **25** | ineligible | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | ineligible / ineligible | ineligible / ineligible | ineligible / ineligible |
| None | needs_check | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check | needs_check / needs_check |
| omitted | - | - | - | needs_check / needs_check | - | - | needs_check / needs_check |

### API 응답 사유·exclusion_reasons (자동 생성)

| child_age_months | L2 API 사유 | L2 exclusion_reasons | L3 API 사유 | L3 exclusion_reasons |
|---|---|---|---|---|
| 0 | 자녀 월령 24개월 이하 기준에 입력한 0개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령 24개월 이하 기준에 입력한 0개월은 포함됩니다. | null |
| 10 | 자녀 월령 24개월 이하 기준에 입력한 10개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령 24개월 이하 기준에 입력한 10개월은 포함됩니다. | null |
| 23 | 자녀 월령 24개월 이하 기준에 입력한 23개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령 24개월 이하 기준에 입력한 23개월은 포함됩니다. | null |
| 24 | 자녀 월령 24개월 이하 기준에 입력한 24개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령 24개월 이하 기준에 입력한 24개월은 포함됩니다. | null |
| 25 | 자녀 월령 24개월 이하 기준에 입력한 25개월은 포함되지 않습니다. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령 24개월 이하 기준에 입력한 25개월은 포함되지 않습니다. | null |
| None | 자녀 월령(개월 수)을 확인해야 합니다. 기준: 24개월 이하. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령(개월 수)을 확인해야 합니다. 기준: 24개월 이하. | null |
| omitted | 자녀 월령(개월 수)을 확인해야 합니다. 기준: 24개월 이하. 데이터 품질 경고로 자동 판정을 보류합니다. | null | 자녀 월령(개월 수)을 확인해야 합니다. 기준: 24개월 이하. | null |

### API 응답 conditions 전체 행 — 24·25개월 (자동 생성)

- L2 24개월:
  - `children` = needs_check — 자녀 월령 24개월 이하 기준에 입력한 24개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다.
  - `eligibility` = needs_check — 모든 조건을 충족해야 합니다(종합 결과). 데이터 품질 경고로 자동 판정을 보류합니다.
  - `region` = needs_check — 데이터 품질 경고로 해당 자격 요건의 확인이 필요합니다. 데이터 품질 경고로 자동 판정을 보류합니다.
  - `housing` = needs_check — 가구 요건의 공식 원문 기준을 추가 확인해야 합니다. 확인할 항목: 장애인가정 해당 여부 원문 검토 메모: 장애인가정 요건이 있으나 그래프에 해당 가구 유형이 없음 가구 관련 추가 정보를 답변 본문에서 확인해 주세요.
- L2 25개월:
  - `children` = needs_check — 자녀 월령 24개월 이하 기준에 입력한 25개월은 포함되지 않습니다. 데이터 품질 경고로 자동 판정을 보류합니다.
  - `eligibility` = needs_check — 모든 조건을 충족해야 합니다(종합 결과). 데이터 품질 경고로 자동 판정을 보류합니다.
  - `region` = needs_check — 데이터 품질 경고로 해당 자격 요건의 확인이 필요합니다. 데이터 품질 경고로 자동 판정을 보류합니다.
  - `housing` = needs_check — 가구 요건의 공식 원문 기준을 추가 확인해야 합니다. 확인할 항목: 장애인가정 해당 여부 원문 검토 메모: 장애인가정 요건이 있으나 그래프에 해당 가구 유형이 없음 가구 관련 추가 정보를 답변 본문에서 확인해 주세요.
- L3 24개월:
  - `children` = eligible — 자녀 월령 24개월 이하 기준에 입력한 24개월은 포함됩니다.
  - `eligibility` = needs_check — 모든 조건을 충족해야 합니다(종합 결과). 소득·가구 원문 확인이 남아 종합 판정을 보류합니다.
  - `housing` = needs_check — 가구 요건의 공식 원문 기준을 추가 확인해야 합니다. 확인할 항목: 장애인가정 해당 여부 원문 검토 메모: 장애인가정 요건이 있으나 그래프에 해당 가구 유형이 없음 가구 관련 추가 정보를 답변 본문에서 확인해 주세요.
- L3 25개월:
  - `children` = ineligible — 자녀 월령 24개월 이하 기준에 입력한 25개월은 포함되지 않습니다.
  - `eligibility` = ineligible — 모든 조건을 충족해야 합니다(종합 결과).
  - `housing` = needs_check — 가구 요건의 공식 원문 기준을 추가 확인해야 합니다. 확인할 항목: 장애인가정 해당 여부 원문 검토 메모: 장애인가정 요건이 있으나 그래프에 해당 가구 유형이 없음 가구 관련 추가 정보를 답변 본문에서 확인해 주세요.

## 4. 전체 점검 항목 (자동 생성)

| 층 | 항목 | 기대 | 실제 | 결과 |
|---|---|---|---|---|
| DB | neo4j_connected_read_session | null | {"policies": 249, "nodes": 1147, "relationships": 2702} | PASS |
| DB | neo4j_condition_is_LTE_24_months | ["LTE", "24", "개월"] | ["LTE", "24", "개월"] | PASS |
| DB | neo4j_requires_only_child_age_condition | ["COND_CHILD_AGE_LE_24M"] | ["COND_CHILD_AGE_LE_24M"] | PASS |
| DB | neo4j_root_group_contains_child_condition | [["AND", "COND_CHILD_AGE_LE_24M"]] | [["AND", "COND_CHILD_AGE_LE_24M"]] | PASS |
| DB | qdrant_connected | null | {"exp_b2_qwen3emb06b_v140_v1__tmp_4ab3e799968e06bfc7142d2ce41ca22a": 1249, "exp_ | PASS |
| DB | qdrant_payload_chunks_for_policy | "DOC_GOV_050 chunks" | [["CHK_B2_DOC_GOV_050_C001", "DOC_GOV_050"], ["CHK_B2_DOC_GOV_050_C002", "DOC_GO | PASS |
| DB | hybrid_search_returns_policy | ["POL_GOV_050"] | ["POL_GOV_050"] | PASS |
| DB | search_graph_condition_matches_neo4j | [["COND_CHILD_AGE_LE_24M", "LTE", "24", "개월"]] | [["COND_CHILD_AGE_LE_24M", "LTE", "24", "개월"]] | PASS |
| DQ | search_data_quality_observed | null | {"hold": true, "hold_reasons": ["MARRIAGE_CHILD_NOT_MODELED", "SEARCH_REGION_REQ | PASS |
| DQ | static_flags_applied_to_search_result | {"codes⊆": ["MARRIAGE_CHILD_NOT_MODELED"], "affects⊆": ["children"]} | {"codes": ["MARRIAGE_CHILD_NOT_MODELED", "SEARCH_REGION_REQUIRES_REVIEW", "SUSPE | PASS |
| DQ | children_hold_source | ["MARRIAGE_CHILD_NOT_MODELED"] | ["MARRIAGE_CHILD_NOT_MODELED"] | PASS |
| L1 | structured_comparison_0 | "eligible" | "eligible" | PASS |
| L1 | structured_comparison_10 | "eligible" | "eligible" | PASS |
| L1 | structured_comparison_23 | "eligible" | "eligible" | PASS |
| L1 | structured_comparison_24 | "eligible" | "eligible" | PASS |
| L1 | structured_comparison_25 | "ineligible" | "ineligible" | PASS |
| L1 | structured_comparison_None | "needs_check" | "needs_check" | PASS |
| L1 | applicant_age_0_not_used_as_child_months | "needs_check" | "needs_check" | PASS |
| L1 | applicant_age_1_not_used_as_child_months | "needs_check" | "needs_check" | PASS |
| L1 | applicant_age_2_not_used_as_child_months | "needs_check" | "needs_check" | PASS |
| L1 | applicant_age_24_not_used_as_child_months | "needs_check" | "needs_check" | PASS |
| L2 | checker_0 | "needs_check" | "needs_check" | PASS |
| L2 | checker_10 | "needs_check" | "needs_check" | PASS |
| L2 | checker_23 | "needs_check" | "needs_check" | PASS |
| L2 | checker_24 | "needs_check" | "needs_check" | PASS |
| L2 | checker_25 | "needs_check" | "needs_check" | PASS |
| L2 | checker_None | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_0 | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_10 | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_23 | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_24 | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_25 | "needs_check" | "needs_check" | PASS |
| L2 | pipeline_None | "needs_check" | "needs_check" | PASS |
| L2 | api_0 | "needs_check" | "needs_check" | PASS |
| L2 | api_10 | "needs_check" | "needs_check" | PASS |
| L2 | api_23 | "needs_check" | "needs_check" | PASS |
| L2 | api_24 | "needs_check" | "needs_check" | PASS |
| L2 | api_25 | "needs_check" | "needs_check" | PASS |
| L2 | api_None | "needs_check" | "needs_check" | PASS |
| L2 | api_omitted | "needs_check" | "needs_check" | PASS |
| API | 422_int_-1 | 422 | 422 | PASS |
| API | 422_float_24.5 | 422 | 422 | PASS |
| API | 422_str_'24' | 422 | 422 | PASS |
| API | 422_bool_True | 422 | 422 | PASS |
| API | 422_bool_False | 422 | 422 | PASS |
| API | 422_list_[24] | 422 | 422 | PASS |
| API | 422_dict_{'m': 24} | 422 | 422 | PASS |
| API | child_months_not_search_filter | "same candidates" | [["POL_GOV_050", "POL_GOV_051", "POL_BOK_061", "POL_BOK_066", "POL_BOK_063"]] | PASS |
| L3 | checker_0 | "eligible" | "eligible" | PASS |
| L3 | checker_10 | "eligible" | "eligible" | PASS |
| L3 | checker_23 | "eligible" | "eligible" | PASS |
| L3 | checker_24 | "eligible" | "eligible" | PASS |
| L3 | checker_25 | "ineligible" | "ineligible" | PASS |
| L3 | checker_None | "needs_check" | "needs_check" | PASS |
| L3 | pipeline_0 | "eligible" | "eligible" | PASS |
| L3 | pipeline_10 | "eligible" | "eligible" | PASS |
| L3 | pipeline_23 | "eligible" | "eligible" | PASS |
| L3 | pipeline_24 | "eligible" | "eligible" | PASS |
| L3 | pipeline_25 | "ineligible" | "ineligible" | PASS |
| L3 | pipeline_None | "needs_check" | "needs_check" | PASS |
| L3 | api_0 | "eligible" | "eligible" | PASS |
| L3 | api_10 | "eligible" | "eligible" | PASS |
| L3 | api_23 | "eligible" | "eligible" | PASS |
| L3 | api_24 | "eligible" | "eligible" | PASS |
| L3 | api_25 | "ineligible" | "ineligible" | PASS |
| L3 | api_None | "needs_check" | "needs_check" | PASS |
| L3 | api_omitted | "needs_check" | "needs_check" | PASS |
| DB | counts_unchanged_before_after | [{"policies": 249, "nodes": 1147, "relationships": 2702}, {"exp_b2_qwen3emb06b_v | [{"policies": 249, "nodes": 1147, "relationships": 2702}, {"exp_b2_qwen3emb06b_v | PASS |

**요약: pass=68, fail=0**

---

> 아래 절(5~13)은 위 자동 생성 결과와 저장소·공식 원문 조회를 바탕으로 사람이 정리한 해석이다. 2026-11-27 작성.

## 5. 검증 목적

PR #139 리뷰 요청: 실제 R3D DB 기준으로 자녀 월령 24개월·25개월 조건 판정이 정확히 구분되는지, 그리고 그 결과가 Checker → RAG Pipeline → FastAPI까지 그대로 전달되는지 확인한다. #39(월령 조건 비교)와 #102(데이터 품질 보류)를 분리해서 본다.

## 6. 이전 실행 `fail=15`의 원인 (재확인 결과)

- 2026-11-27 09:32 첫 실행은 `pass=23, fail=15`였다. 실패 15건은 Checker·Pipeline·API의 0/10/23/24/25개월이었고, 모두 `needs_check`가 나왔다.
- 원인은 **검증 스크립트의 기대값 오류**였다. 스크립트가 데이터 품질 보류를 고려하지 않고 "보류 없음" 기대값(eligible/ineligible)을 최종 상태에 그대로 요구했다. #139 코드 결함이 아니다.
- 재확인 근거(이번 실행, 실제 데이터):
  - 검색 결과의 `data_quality.hold_auto_eligibility=True`, `affected_attributes=["children","region"]`.
  - `children`을 막는 경고는 `MARRIAGE_CHILD_NOT_MODELED` 하나뿐이다(`children_hold_source` PASS). 이 경고는 정적 플래그 파일 `neo4j_semantic_flags_v1_4_0`의 S10 항목에서 온다.
  - `region` 보류는 그래프 계산 경고 `SEARCH_REGION_REQUIRES_REVIEW`·`SUSPECTED_REGION_GRANULARITY_LOST`에서 온다. 이것은 오프라인 분석 때 몰랐던 부분이다. 이전 설명("S10 하나가 원인")은 `children`에 대해서만 맞다.
  - 보류를 메모리상 끈 사본(L3)에서는 같은 실제 데이터로 24 → eligible, 25 → ineligible이 나온다. 그러므로 needs_check의 원인은 월령 비교가 아니라 보류다.
- 인증 오류 기록: 이전에 `Import-Clixml ... qdrant.clix 파일을 찾을 수 없습니다`가 난 것은 확장자 오기다. 실제 파일은 `restore-bundle\secrets-01\qdrant.clixml`·`neo4j.clixml`(DPAPI SecureString, 현재 Windows 사용자로 복호화 가능)이다. 자동 탐색 목록에 이 폴더가 없어서 `secret files not found`가 났다. 실행 스크립트에 이 경로를 추가했고, 이번에는 `-KeyFile`/`-PwFile`로 정확한 경로를 넘겨 실행했다. 인증 파일은 읽기만 했고 수정·복사하지 않았다.

## 7. 24개월·25개월 비교 결과 (실제 R3D)

| 층 | 24개월 | 25개월 | 비고 |
|---|---|---|---|
| Neo4j 조건 | `COND_CHILD_AGE_LE_24M` LTE "24" 개월 | 같음 | 루트 AND 그룹의 유일한 조건 |
| L1 구조화 비교(조건 단위, 보류 전) | **eligible** | **ineligible** | Checker `_condition()` 반환 `status` |
| L2 실제 데이터 Checker | needs_check (사유 "…24개월은 포함됩니다. 데이터 품질 경고로 자동 판정을 보류합니다.") | needs_check (사유 "…25개월은 포함되지 않습니다. …보류합니다.") | 보류 |
| L2 Pipeline / API | Checker와 같음, `exclusion_reasons: null` | 같음 | HTTP 200, 인용 `DOC_GOV_050` |
| L3 보류 제거 사본 Checker / Pipeline / API | `children` **eligible**, 종합 needs_check | `children` **ineligible**, 종합 **ineligible** | 종합 needs_check는 `housing` 원문 확인 행("장애인가정 요건이 그래프에 없음") 때문 |

결론: 실제 R3D 조건으로 24개월은 충족, 25개월은 미충족으로 정확히 구분된다. 실제 데이터에서는 #102 보류 때문에 최종 상태가 둘 다 `needs_check`다. 비교 결과는 사유 문구에 남고, Checker → Pipeline → API에서 바뀌지 않는다.

**구조화 비교의 한계:** 공개 API 응답에는 보류 적용 전 비교 결과를 담는 구조화 필드가 없다. 최종 `status`와 사유 문구만 있다. 그래서 L1은 Checker 내부 함수 `_condition()`을 직접 호출해 확인했다. L2의 Pipeline·API에서는 사유 문구(보조 검증)와 L3의 구조화 `status`로 확인했다.

## 8. #102 데이터 품질 보류의 영향, 정상 데이터와 보류 데이터의 차이

| 입력 | 보류 없음(L3, 자녀 조건) | 보류 있음(L2, 자녀 조건) | 보류 있음 사유에 남는 비교 결과 |
|---|---|---|---|
| 0개월 | eligible | needs_check | 포함됩니다 |
| 10개월 | eligible | needs_check | 포함됩니다 |
| 23개월 | eligible | needs_check | 포함됩니다 |
| 24개월 | eligible | needs_check | 포함됩니다 |
| 25개월 | ineligible | needs_check | 포함되지 않습니다 |
| 미입력(null·생략) | needs_check | needs_check | "자녀 월령(개월 수)을 확인해야 합니다" |

- 보류는 확정 제외가 아니다. L2 전 구간에서 `exclusion_reasons`는 `null`이었다.
- 0개월과 미입력은 보류 상태에서도 사유로 구분된다. 신청자 `age` 0·1·2·24는 자녀 월령을 대신하지 않는다(L1 PASS).
- 422 7종(-1, 24.5, "24", true, false, [24], {..})은 실제 서버 경로에서도 422였고, 입력값을 응답에 노출하지 않았다.

## 9. 정책 근거 재확인 (24개월 "미만" vs "이하")

| 출처 | 표현 | 확인 방법 |
|---|---|---|
| Neo4j `Policy.eligibility`(수집 요약) | "24개월 미만 아이를 양육하면서…" | R3D 조회 |
| Qdrant `POL_GOV_050` 청크(정부24 원문) | "24개월 미만" 2회, "24개월 이하" 1회("24개월 이하의 영아를 양육하는 장애인가정"), "생후 24개월까지(최대2년지원)" 1회 | R3D scroll. 원문 자체에 두 표현이 섞여 있다 |
| 「전주시 장애인가정 출산 및 양육 지원 조례」 [시행 2023.11.9.] [전주시조례 제4072호, 일부개정] | 제2조(정의) 제4호·제4조(지원대상) 제2호·제5조(지원액) 제2항 모두 "24개월 이하". 조례 본문에 "24개월 미만"은 없음 | 2026-11-27 국가법령정보센터(law.go.kr 자치법규) 본문 조회 |
| 프로젝트 결정 | `data/metadata/data_corrections_v1.4.0.json` age_condition_replacements: COND_AGE_0_1 → COND_CHILD_AGE_LE_24M, "조례 문구를 따른다(반석 확정 2026-11-20, #39 A안)" | 저장소 |

→ LTE 24는 근거 법령(조례)과 정부24 지원대상 상세 문구에 맞다. 정부24 요약·`Policy.eligibility`의 "미만"은 근거 법령과 다른 요약 표현이다. 이번 PR에서 데이터는 바꾸지 않았다.

## 10. 전체 PASS/FAIL

- 실제 R3D 검증(최종 실행 2026-11-27T10:12:46+09:00): **pass=68, fail=0**, python exit code 0. DB 건수 실행 전후 불변(Neo4j 249/1147/2702, Qdrant 1249/1338).
- 재현 실행(2026-11-27T10:41:24+09:00, 커밋 `3ba6ece` 기준, 같은 스크립트 v2·같은 인증 방식): **pass=68, fail=0**, exit code 0, DB 건수 불변. 위 1~4절 표와 값이 같다(L1 24 eligible / 25 ineligible, L2 모두 needs_check·`exclusion_reasons` null, L3 24 `children` eligible·종합 needs_check / 25 `children`·종합 ineligible). 이 실행은 위 자동 생성 절을 덮어쓰지 않도록 보고서를 저장소 밖에 출력했다.
- 이전 실행: 09:32 `pass=23 fail=15`(기대값 오류), 같은 날 v2 첫 실행(시각 미기록)은 스크립트 결함(TestClient 종료 시 검색 세션이 닫혀 L3에서 중단, DQ 검사가 그래프 경고를 고려하지 않음)으로 미완료. 두 결함을 고친 뒤 위 결과를 얻었다. 판정 기대값(L1 고정값, 보류 시 needs_check)은 바꾸지 않았다.
- 오프라인 테스트: `tests/integration/test_issue39_child_age_api.py` 59 passed. 전체 unit+integration 결과는 PR 댓글·완료 보고에 적는다.

## 11. #139 코드 문제 여부

- API 스키마·Pipeline 전달·422·0개월/미입력 구분·신청자 나이 독립성·검색 필터 미사용 모두 실제 R3D에서 기대대로 동작했다. **#139 판정 코드는 수정이 필요 없다.**
- 이번 변경은 계약 문서(T25 보류 동작 기록)·테스트(실제 R3D와 같은 보류 상태 시나리오)·이 보고서뿐이다.
- 후속 보완(2026-11-27, 다자녀 안내 합의 반영): `child_age_months`는 신청 여부를 확인하려는 자녀 한 명의 월령이고, 자녀가 여러 명이면 자녀별로 각각 확인하도록 답변 본문에서 안내한다. 계약 문서(§3.2·§5.1·T26)와 프롬프트에 이 규칙을 넣었다. 또 월령이 입력된 상태에서 Generator가 "자녀가 여러 명이면 자녀별 월령을 각각 알려 주세요" 같은 안내를 중복 입력 요청으로 지우던 문제를 고쳤다(`backend/llm/generator.py`의 `_is_redundant_request`, 같은 자녀 월령 재요청은 기존대로 제거). Checker·Pipeline 판정 코드와 `ask_info` 허용 필드는 바꾸지 않았다.

## 12. 미해결 사항과 DB(데이터) 담당 확인 사항 — 이번 PR 범위 밖

1. `POL_GOV_050`의 S10 정적 플래그 `MARRIAGE_CHILD_NOT_MODELED`(children: incomplete) 유지 여부. 플래그 근거는 두 가지다.
   - 원문에서 자녀 나이를 하나로 추출하지 못함(`child_age_check: no_text_age`, 원문에 미만/이하 혼재)
   - "장애인가정" 가구 유형 미모델(`missing_details.family_type`)
   - 조례로 LTE 24가 확인됐으므로 월령 부분은 해소 근거가 있다. 다만 가구 유형 미모델은 남는다. 해제하면 `children`은 24 eligible / 25 ineligible로 확정되고, 종합은 `housing` 원문 확인 행 때문에 24개월에서도 needs_check로 남을 가능성이 높다(L3 결과).
2. `region` 그래프 경고(`SEARCH_REGION_REQUIRES_REVIEW`, `SUSPECTED_REGION_GRANULARITY_LOST`): 전주시 정책이 시·도 단위로 저장된 문제. #39와 무관하다.
3. `Policy.eligibility`의 "24개월 미만" 요약 문구를 조례 기준으로 교정할지 여부. 데이터 변경이므로 담당 확인이 필요하다.
4. 미검증: React 화면 표시, 실제 LLM 답변 본문(Generator는 Mock).

## 13. Merge 의견

리뷰 요청(실제 R3D 24/25 구분 확인)은 충족됐다. 판정 코드 변경 없이 계약·테스트·검증 보고서와 다자녀 안내 보완(프롬프트 1줄, Generator 중복 요청 예외)만 추가되므로 PR #139 머지를 막을 사유는 없다고 판단한다. 실제 LLM 답변에서 다자녀 안내가 나오는지는 Mock 테스트만 했고 미검증이다. 위 12번은 별도 데이터 이슈(#102 후속)로 분리를 제안한다.
