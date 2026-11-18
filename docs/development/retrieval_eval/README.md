# docs/development/retrieval_eval — 검색 성능 벤치마크 평가 데이터셋

> **위치**: `LifePolicy AI/docs/development/retrieval_eval/`  
> **역할**: 하이브리드 RAG 검색 엔진 및 임베딩 모델의 품질 회귀(Regression)를 방지하기 위한 골든 벤치마크 데이터셋 관리  
> **상위 가이드**: [docs/development 인덱스](../README.md) | [docs 전체 인덱스](../../README.md)

---

## 1. 벤치마크 평가셋 개요

본 디렉터리는 다양한 생애주기별(사회초년생, 신혼부부, 취업준비생, 저소득 가구 등) 사용자 자연어 질의에 대해, 시스템이 실제로 정확한 정책을 추천하는지 검증하기 위한 **공식 골든 테스트셋(Golden Benchmark Dataset)**을 포함합니다.

---

## 2. 파일 구성 및 설명

| 파일명 | 역할 | 설명 |
|---|---|---|
| [questions_candidates_v0.jsonl](questions_candidates_v0.jsonl) | **평가 질의 및 정답 세트** | 10개 대표 생애주기 시나리오별 사용자 질문과 기대 추천 정책 후보군(`ground_truth_policy_ids`) 매핑 데이터 |
| [questions_candidates_v0.source.json](questions_candidates_v0.source.json) | **평가셋 메타데이터** | 벤치마크 평가 질의 생성 출처, 설계 의도 및 검증자 정보 |
| [retrieval_eval_v0.freeze.json](retrieval_eval_v0.freeze.json) | **고정 기준선 (Baseline)** | 3차 MVP 기준 공식 고정 성능 스코어 (MRR, Hit@1, Hit@3, Hit@5)로 모델 변경 시 성능 비교 기준점 제공 |

---

## 3. 벤치마크 재현 및 평가 실행 방법

백엔드 및 DB 헬스체크 통과 후, 아래 명령어를 실행하여 현재 검색 파이프라인의 벤치마크 스코어를 실측하고 기준선(`retrieval_eval_v0.freeze.json`) 대비 회귀가 없는지 검증합니다.

```bash
# 루트 디렉터리(LifePolicy AI)에서 실행
python database/retrieval_v140_eval.py
```

### 주요 평가 지표
- **Hit@1 (Top-1 Accuracy)**: 검색 결과 1위에 정답 정책이 포함될 확률
- **Hit@3 / Hit@5**: 상위 3개 또는 5개 추천 카드 내에 정답 정책이 포함될 확률
- **MRR (Mean Reciprocal Rank)**: 정답 정책의 순위 역수 평균
- **Constraint Pass Rate**: 연령/소득/지역 하드 필터링 제약 조건 통과율
