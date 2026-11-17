# docs/data — 데이터 설계·검증 문서

데이터 수집·전처리·스키마의 세부 근거 자료입니다. 요약은 [산출물 01](../reports/ssok-deliverables/01%20·%20수집%20데이터%20및%20전처리.md)을 먼저 보고, 구체적인 수치·판정 근거가 필요할 때 아래에서 찾습니다.

## 지금도 보는 계약·스키마

| 파일 | 내용 |
| --- | --- |
| [`data_contract.md`](data_contract.md) | 최종 통합 데이터 계약서 — 필드·null 규칙 |
| [`db_schema.md`](db_schema.md) | 최종 통합 DB 스키마 설계 |
| [`data_schema.md`](data_schema.md) | 데이터 스키마 명세(3차 확정) |
| [`preprocessing_rules.md`](preprocessing_rules.md) | 전처리·정규화 규칙(3차 확정) |
| [`gov24_field_mapping.md`](gov24_field_mapping.md) | 정부24 원본→정규화 필드 매핑표 (수집기가 `data/metadata/`에도 자동 생성) |
| [`cross_agency_condition_mapping.md`](cross_agency_condition_mapping.md) | 기관별 코드·지원조건 통합 매핑표 |
| [`db_schema_change_proposal.md`](db_schema_change_proposal.md) | DB 스키마 변경 이력 안내 |
| [`data_contract_change_proposal.md`](data_contract_change_proposal.md) | 데이터 계약 변경 제안 |

## 수집·품질 분석 보고서 (산출물 01의 세부 근거)

| 파일 | 내용 |
| --- | --- |
| [`data_collection_report.md`](data_collection_report.md) | 3차 데이터 수집 및 정제 최종 보고서 |
| [`data_quality_report.md`](data_quality_report.md) | 3차 데이터 품질·정합성 검증(문서 ID별 수정 내역) |
| [`lifecycle_distribution_report.md`](lifecycle_distribution_report.md) | 생애주기별 정책 분포(법적 근거·정책 ID 포함) |
| [`target_attributes_distribution_report.md`](target_attributes_distribution_report.md) | 대상자 속성(가족·소득·취업 등)별 정책 분포 |
| [`lifecycle_improvement_comparison.md`](lifecycle_improvement_comparison.md) | 생애주기 분류 개선 전후 비교 |
| [`official_source_crosscheck_report.md`](official_source_crosscheck_report.md) | v1.4.0 후보 신규 문서 공식 원문 대조 |
| [`pr16_application_url_review.md`](pr16_application_url_review.md) | PR #16 신청 주소 교정·검증 |
| [`needs_review_policies_report.md`](needs_review_policies_report.md) | 검토 필요 정책 목록·관리 지침 |

## v1.4.0 후보 릴리스 검증

| 파일 | 내용 |
| --- | --- |
| [`release_candidate_v1.4.0_audit.md`](release_candidate_v1.4.0_audit.md) | 후보 릴리스 v1.4.0-candidate 품질 감사 |
| [`v1.4.0_candidate_fix_report.md`](v1.4.0_candidate_fix_report.md) | 후보 릴리스 결함 수정·안정화 |
| [`data_corrections_v1.4.0_review.md`](data_corrections_v1.4.0_review.md) | v1.4.0 후보 원문 대조 교정 |
| [`document_expansion_poc_report.md`](document_expansion_poc_report.md) | 공식 근거 문서 수집 PoC·품질 검증 |
| [`schema_extension_compatibility.md`](schema_extension_compatibility.md) | 스키마 확장 적합성 분석 |
| [`qdrant_document_expansion_plan.md`](qdrant_document_expansion_plan.md) | Qdrant 문서 확장·벡터 페이로드 설계 |

## 정책 선별 알고리즘(STEP A~D)

| 파일 | 내용 |
| --- | --- |
| [`policy_discovery_audit.md`](policy_discovery_audit.md) | 정책 누락 원인 분석·숨은 정책 발굴 |
| [`policy_selection_v2_design.md`](policy_selection_v2_design.md) | 정량 스코어링 기반 정책 선별 알고리즘 v2 설계(STEP A) |
| [`policy_candidate_validation.md`](policy_candidate_validation.md) | 발굴 정책 30건 재검증·선별 코드 감사(STEP A) |
| [`incremental_pipeline_test_report.md`](incremental_pipeline_test_report.md) | 증분 파이프라인 시험 |
| [`step_a_to_d_final_review.md`](step_a_to_d_final_review.md) | STEP A~D 종합 품질 검증·최종 안정화 |

## 검색 품질 평가

| 파일 | 내용 |
| --- | --- |
| [`graphrag_retrieval_evaluation.md`](graphrag_retrieval_evaluation.md) | GraphRAG 검색 성능 평가·최종 검색 아키텍처 |

## 기타

| 파일 | 내용 |
| --- | --- |
| [`ANTIGRAVITY_CROSSCHECK_PROMPT.md`](ANTIGRAVITY_CROSSCHECK_PROMPT.md) | 외부 AI(Antigravity)로 독립 수집·크로스체크할 때 쓴 작업 지시문 |

> `cross_agency_condition_mapping.md`·`gov24_field_mapping.md`는 `data/metadata/`에도 같은(또는 과거) 내용이 있습니다.
> `data/`는 장반석 님 담당 영역이라, 어느 쪽을 기준으로 둘지는 확인 후에 정리합니다(삭제 보류).
