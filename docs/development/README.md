# docs/development — 개발 규칙·이슈별 작업 기록

팀 공통 코딩 규칙과, 이슈·PR 단위로 남긴 실행 가이드·검증 기록입니다. 실험 원본 데이터는
[`experiments/`](experiments/)·[`retrieval_eval/`](retrieval_eval/)에 그대로 둡니다
(스크립트가 이 경로를 고정 참조하므로 옮기지 않습니다).

## 팀 공통 규칙

| 파일 | 내용 |
| --- | --- |
| [`CODING_STYLE.md`](CODING_STYLE.md) | 팀 공통 코딩 스타일 |
| [`recommendation_safety_rules.md`](recommendation_safety_rules.md) | 정책 추천·필터링 엔진 안전장치 규칙 |

## Issue #41 — 임베딩 모델 A/B 비교

| 파일 | 내용 |
| --- | --- |
| [`ISSUE41_AB_EXECUTION_GUIDE.md`](ISSUE41_AB_EXECUTION_GUIDE.md) | A/B 평가 실행 가이드 |
| [`ISSUE41_EMBEDDING_AB_EVAL.md`](ISSUE41_EMBEDDING_AB_EVAL.md) | 예비 A/B 실험 결과 |
| [`ISSUE41_AB_RUN03_ANALYSIS.md`](ISSUE41_AB_RUN03_ANALYSIS.md) | Qwen3 0.6B/4B A/B 결과 분석 및 PR 범위 |
| [`ISSUE41_RUNPOD_GUIDE.md`](ISSUE41_RUNPOD_GUIDE.md) | RunPod 벡터 전송 가이드 |

## Issue #43 — 최종 DB 복원·재적재

| 파일 | 내용 |
| --- | --- |
| [`ISSUE43_RESTORE_GUIDE.md`](ISSUE43_RESTORE_GUIDE.md) | 팀원용 최종 DB 복원·검증 가이드 |
| [`FINAL_DB_RELOAD_20261110.md`](FINAL_DB_RELOAD_20261110.md) | 최종 DB 재적재 및 독립 인계 검증(2026-11-10) |
| [`issue43_restore.compose.yaml`](issue43_restore.compose.yaml) | 복원용 docker-compose 설정 |

## Issue #5 — RAG 최종 인계 / PR #45 리뷰

| 파일 | 내용 |
| --- | --- |
| [`ISSUE5_V140_RAG_HANDOFF_20261107.md`](ISSUE5_V140_RAG_HANDOFF_20261107.md) | RAG 최종 인계(PR #35·#37 반영) |
| [`PR45_REVIEW_VALIDATION_20261110.md`](PR45_REVIEW_VALIDATION_20261110.md) | PR #45 리뷰 검증 기록 |

## DB 인계

| 파일 | 내용 |
| --- | --- |
| [`SSOK_주민혁_DB인계서_20261105.md`](SSOK_주민혁_DB인계서_20261105.md) | 주민혁 님 DB 구축·적재 기술 인계서(적재 순서·검증 절차 포함) |

## 실험 원본 (고정 경로, 옮기지 않음)

| 경로 | 내용 |
| --- | --- |
| [`experiments/desktop_20261106/`](experiments/desktop_20261106/) | C1 청킹 LLM 제안 원본(`database/full_index.py`가 참조) |
| [`experiments/issue41_ab_run02/`](experiments/issue41_ab_run02/), [`issue41_ab_run03/`](experiments/issue41_ab_run03/) | 임베딩 A/B 실행 원본 |
| [`retrieval_eval/`](retrieval_eval/) | 검색 평가 질문·동결 결과 원본 |
