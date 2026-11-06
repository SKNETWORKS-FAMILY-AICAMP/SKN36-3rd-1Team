# Issue #21 최종 검증 결과

- 확인일: 2026-11-07, 브랜치 `feature/21-eligibility-data-fix`, HEAD `a77c13c` + 미커밋 생성 코드
- 배포 경로: `data/candidates/issue21-v1.4.0-rag/` (코드 커밋 후 생성해 커밋). 검증용 로컬 생성본 `-20261107-rag`(+`-rag-replay` 동일), `-20261107-rag-v2`(문서 본문·정책 메타데이터 추가본)
- 입력: `data/releases/v1.4.0-candidate/` (Git 추적, 수정 없음)
- 이전 후보 `final`·`scoped`·`rootfix`와 그 보고서는 과거 결과로 보존한다.
- DB 접근·적재 없음, commit/push/merge 없음. RAG 사용법은 `ISSUE21_RAG_HANDOFF_20261107.md`.

## 1. 수정한 결함과 근거

| 문제 | 수정 | 근거 | 테스트 |
|---|---|---|---|
| 소득 5구간 OR, 첫 구간만 속성 기록, 무주택 필수 AND, JA0410·취업 JA 누락 (1차 수정 `0645f90`) | 유지·검증 | 코드 결함 | `test_eligibility_generation` |
| JA0322 누락 | 보존 (28개) | 공공데이터포털 항목설명 "해당사항없음" | `SourceCodePreservationTests` |
| 조건 하나가 검토 대기면 루트 전체 UNKNOWN | 확인된 조건이 있으면 루트 AND, 보류 조건은 `UNKNOWN_REVIEW` | `db_schema.md` 3값 논리 | `RootSemanticsTests` |
| 확인된 조건만으로 TRUE가 나올 수 있음 | 원문 문장·연령 값·지역 상세 검토를 `UNKNOWN_UNSTRUCTURED`로 표시 | 원문 미구조화 | `EvidenceRuleTests` |
| 시군구 노드로 `AVAILABLE_IN` 교체 (rootfix) → 시도 검색 22개·청크 40개 누락, 공백 ID | 시도 관계·`region` 유지, `region_detail` 추가 | `preprocessing_rules.md` 17개 시도 계약 | `RegionContractTests` |
| 온통청년 소득 `0043001` 무관도 검토 대기 조건으로 생성 (52개) | 조건 관계 없음, 원값 보존 | 공식 `API코드정보.xlsx` earnCndSeCd 0043001=무관 | `EvidenceRuleTests` |
| 연령 원문 일치 여부 미반영 | 코드값과 독립 원문이 같은 범위 하나만 말하면 확인 조건 (14개) | 정부24 지원대상·선정기준 원문, 온통청년 API 자유서술 필드 | `EvidenceRuleTests` |
| K-패스 19~120세 vs 19~34세 | 19세 이상 확정, 19~34세는 청년 환급 유형 | 정책 원문 "만 19세 이상의 국민 / 청년층: 만19~34세" | `test_official_age_review_file_decisions` |
| 서울 청년수당 official_id 원천 부재 | official_id 유지, 신청기간·지원내용이 정확히 같은 문서 원천(213994)으로만 조건 복원 | 211516은 `staging_poc_pipeline.py` 수기 입력값, 원천 3,861건에 없음 | `test_conflicting_source_id…` |
| `source_commit` 고정값, CRLF 기준 생성기 해시 | 실제 HEAD, LF 기준, 미커밋 파일 목록 기록 | 재현성 | `test_provenance…` |
| CRLF 체크아웃으로 증거·manifest 해시 불일치 | Git 원본 바이트로 복원, `data/staging/policies.jsonl -text` | blob 해시 = 기록 해시 | `test_review_is_repeatable…` 통과 |

## 2. 최종 후보 수량과 검증

| | v1.4.0 | 최종 |
|---|---:|---:|
| 정책 / 문서 / 청크 | 246 / 261 / 599 | 동일 |
| 노드 | 1,074 | 1,185 (ConditionGroup 352→434, EligibilityCondition 52→81) |
| 관계 | 3,703 | 4,427 (REQUIRES/CONTAINS_CONDITION 735→1,056, CONTAINS_GROUP 106→188) |
| Region / AVAILABLE_IN | 18 / 247 | 동일 (바이트·관계 동일) |

- 불변: 문서 노드 바이트, 청크 ID·본문·출처, 신청 URL(null 224 포함)·출처 URL, 정책 노드의 다른 모든 속성.
- 변경: 정책 노드 `eligibility_review_status`(246), `region_detail`(22개 추가), 청크 `eligibility_review_status`·`income_level`·`region_detail`.
- 계약 검증기 PASS, 참조·DAG·중복 ID PASS, manifest 19개 파일 SHA-256 PASS, 보호 파일 157개 생성 전후 동일.

## 3. 판정 구조

- 루트 AND 30개 (온통청년 26, 정부24·staging 4): 확인된 조건으로 FALSE 가능, TRUE 불가.
- 루트 UNKNOWN 216개: 조건 없음 126 (기타 108, 온통청년 18), 모든 조건 검토 대기 90 (정부24 68, 온통청년 22).
- 실제 정책 246개 전체 검사(`RealCandidatePartialEvaluationTests`): AND 루트는 확인 조건 불충족이면 FALSE, 검토 조건 값과 무관. UNKNOWN 루트는 어떤 값에서도 UNKNOWN.
- 정책 `eligibility_review_status`는 246개 모두 `needs_review`(모든 정책에 UNKNOWN 그룹 존재). 자동 적격 판정 0.

## 4. 테스트 (84개 중 82 통과, 1 실패, 1 오류)

| 테스트 | 결과 | 원인과 근거 |
|---|---|---|
| `test_review_is_repeatable_from_captured_evidence` | **통과** (이전 오류) | CRLF 체크아웃 → Git 원본 바이트 복원 |
| `test_chunk_texts_and_document_hashes_match_saved_bodies` | 실패 | 원본 255건이 `.gitignore`(용량·라이선스) 대상이라 이 PC에 없음. 본문 해시 불일치 0, 청크 불일치 0. 재수집 파일은 원본이 아니므로 만들지 않음 |
| `test_frozen_manifests_match_actual_files` | 오류 | handoff 19/19는 통과. `data/releases/v1` manifest가 gitignore된 110MB 파일 2개(`gov24_public_services.jsonl`, `gov24_merged_services.jsonl`)를 포함. Git에 없고 이 PC에도 없음. 동결 manifest는 수정하지 않음 |

해결 조건: 데이터 담당 PC의 `data/raw/`와 v1 원천 덤프 2개를 같은 경로에 두면 두 테스트를 그대로 재실행할 수 있다.

## 5. 공식 근거 조사 결과

| 항목 | 확인한 자료 | 결론 |
|---|---|---|
| JA0410·JA0322 | 공공데이터포털 15113968 항목설명 | 둘 다 "해당사항없음". 평가 의미는 명세 없음 |
| JA 전체 Y·카테고리 결합 | 같은 페이지, 저장소 `gov24_field_mapping.md` | Y/결합 의미 명세 없음 → JA 그룹은 UNKNOWN 유지 |
| `sprtTrgtAgeLmtYn` | 저장소 API 명세 xlsx("지원대상연령제한여부"), 원천 3,861건 분포 | Y: 0~0 998·범위 736, N: 범위 2,057·0~0 70로 방향을 정할 수 없음 → 플래그 해석 안 함, 원문 일치로만 판정 |
| `0043001` | 공식 `API코드정보.xlsx` | "무관" 확정 → 적용 |
| 기타 소득 14건 | 원천 `earnEtcCn` | 기준이 제각각 → 원문 보존, 판정 불가 |
| GOV_011 | 정부24 원문, 법무부 청소년자립생활관 안내 | 확정 근거 없음 → `issue21_official_age_review.json`에 unresolved 기록 |
| 공식 API 재조회 | `.env`의 `YOUTH_POLICY_API_KEY`·`DATA_GO_KR_API_KEY` 값 없음 | 호출하지 않음 |

## 6. 커밋 대상

| 구분 | 파일 |
|---|---|
| 생성 규칙 | `collectors/api/gov24_collector.py`, `scripts/build_clean_dataset.py`, `scripts/build_handoff_package.py` |
| 재생성·검증 도구 | `scripts/rebuild_eligibility_candidate.py`, `scripts/eligibility_3vl.py`, `scripts/verify_candidate_issue5_compat.py` |
| 근거 메타데이터 | `data/metadata/issue21_official_age_review.json` |
| 계약·설정 | `docs/data/data_contract.md` §8, `.gitattributes` |
| 테스트 | `tests/unit/test_issue21_{consumer_compatibility,unknown_scope,root_semantics,candidate_rebuild}.py` |
| 문서 | 본 문서, `ISSUE21_RAG_HANDOFF_20261107.md`, `ISSUE21_REVIEW_REQUEST_20261107.md`, 과거 표시를 추가한 Issue #21 보고서 3개 |
| 데이터 (별도 커밋) | 코드 커밋 후 생성한 `data/candidates/issue21-v1.4.0-rag/` 하나 (8.8MB, LF 고정) |
| 제외 | 그 외 `data/candidates/` 폴더·안내 md 전체, `docs/development/experiments/`(기존 미추적) |

커밋 후 `rebuild_eligibility_candidate.py`로 새 폴더에 다시 생성하면 `source_commit`이 실제 커밋과 일치하고 `generator_uncommitted`가 비어야 한다.
