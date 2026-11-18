# docs/development/experiments — 모델 및 청킹 실험 데이터 아카이브

> **위치**: `LifePolicy AI/docs/development/experiments/`  
> **역할**: 시맨틱 청킹(Semantic Chunking) 및 대규모 임베딩 모델 A/B 테스트의 세부 실행 결과 로그 보관  
> **상위 가이드**: [docs/development 인덱스](../README.md) | [docs 전체 인덱스](../../README.md)

---

## 1. 실험 아카이브 개요

본 디렉터리는 RAG 검색 파이프라인의 정확도, 시맨틱 분할 품질, 임베딩 모델 성능을 최적화하기 위해 수행된 실험 데이터와 실행 산출물(JSON)을 보관합니다.

---

## 2. 세부 실험 디렉터리별 설명

### 1) [desktop_20261106/](desktop_20261106/) — 시맨틱 청킹 윈도우 분할 실험
- **수행일**: 2026-11-06
- **목적**: 복잡한 공공 공고문(LH 신혼·신생아 매입임대, 주택도시기금 버팀목 전세자금 대출 등)을 분할할 때, 문맥 유실을 최소화하는 최적의 윈도우 크기(Window Size `w0`~`w6`) 도출.
- **실험 데이터**:
  - `c1_openai_DOC_LH_001_w0.json` ~ `w6.json`: LH 공고문 대상 윈도우별 청크 분할 결과
  - `c1_openai_DOC_NHUF_003_w0.json`, `w1.json`: 기금 대출 공고문 대상 청크 결과
  - `c1_openai_DOC_NHUF_005_w0.json`, `w1.json`: 청년 전세 대출 공고문 대상 청크 결과

### 2) [issue41_ab_run02/](issue41_ab_run02/) — Qwen3 Embedding 0.6B 예비 런
- **관련 이슈**: Issue #41 (Durable Embedding Model Evaluation)
- **대상 모델**: `Qwen/Qwen3-Embedding-0.6B` (경량 임베딩 모델)
- **산출물**:
  - `identity.json`: 모델 파라미터, 토크나이저 설정 및 실행 환경 메타데이터
  - `summary.json`: 검색 정확도(Top-1, Top-3, Top-5), MRR, 지연시간 요약 지표
  - `runs/195a83c17d7b408483ce164d9290275d.json`: 세부 평가 질의별 검색 랭킹 및 스코어 로그

### 3) [issue41_ab_run03/](issue41_ab_run03/) — Qwen3 Embedding 0.6B / 4B 비교 평가 (예비 A/B 런)
- **관련 이슈**: Issue #41
- **대상 모델**: `Qwen/Qwen3-Embedding-4B` (서비스 기본 모델은 0.6B 유지, 4B는 품질 우선 환경의 추가 검증 후보로 관리)
- **산출물**:
  - `identity.json`: 4B 모델 실행 환경 메타데이터
  - `summary.json`: 0.6B 대비 정량 평가 결과 (예비 결과: 4B가 Recall@5 88.5% → 94.2%, MRR@5 소폭 향상, 근거 1위는 34 → 33문항으로 1문항 감소)
  - `runs/ba8d0cd22aea434090e26ae5c046dde4.json`: 세부 벤치마크 평가 질의별 스코어 로그

---

## 3. 관련 기술 분석 보고서

- [ISSUE41_AB_RUN03_ANALYSIS.md](../ISSUE41_AB_RUN03_ANALYSIS.md): 0.6B vs 4B 모델 간의 심층 정량 비교 분석 및 PR 반영 가이드.
- [ISSUE41_EMBEDDING_AB_EVAL.md](../ISSUE41_EMBEDDING_AB_EVAL.md): 임베딩 모델 A/B 평가 설계 및 예비 실험 총괄 보고서.
- [ISSUE41_RUNPOD_GUIDE.md](../ISSUE41_RUNPOD_GUIDE.md): 대규모 벡터 추출을 위한 RunPod 클라우드 환경 설정 가이드.
