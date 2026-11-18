# docs/data/poc — 기술 PoC, 아키텍처 설계 및 검색 평가 보고서

> **위치**: `LifePolicy AI/docs/data/poc/`  
> **상위 가이드**: [docs/data 허브](../README.md) | [docs 전체 인덱스](../../README.md) | [시스템 아키텍처](../../architecture/README.md)

---

## 1. 개요

본 디렉터리는 대규모 정책 데이터 수집 파이프라인 최적화, 정량 스코어링 선별 알고리즘, Qdrant 벡터 청크 확장, GraphRAG 검색 시뮬레이션 등 **기술 PoC(Proof of Concept) 및 아키텍처 설계 검증 문서 6종**을 보관합니다.

---

## 2. 기술 PoC 및 설계 문서 목록 (총 6종)

| 문서명 | 역할 및 분야 | 핵심 내용 |
|---|---|---|
| [policy_selection_v2_design.md](policy_selection_v2_design.md) | 정책 선별 알고리즘 | 다차원 정량 스코어링 기반 정책 선별 알고리즘 v2 설계서 |
| [incremental_pipeline_test_report.md](incremental_pipeline_test_report.md) | 파이프라인 시험 | 선별 v2 알고리즘 및 증분 수집 파이프라인 동작 시험 보고서 |
| [qdrant_document_expansion_plan.md](qdrant_document_expansion_plan.md) | 벡터 DB 설계 | Qdrant 공식 문서 청크 확장 및 벡터 페이로드 스키마 설계서 |
| [document_expansion_poc_report.md](document_expansion_poc_report.md) | 문서 수집 PoC | 정부 공식 근거 문서(PDF/HWP/웹) 수집 PoC 및 품질 검증 보고서 |
| [graphrag_retrieval_evaluation.md](graphrag_retrieval_evaluation.md) | 검색 성능 평가 | Neo4j Graph + Qdrant Vector 하이브리드 GraphRAG 검색 시뮬레이션 평가 |
| [schema_extension_compatibility.md](schema_extension_compatibility.md) | 스키마 호환성 | 기존 스키마 우선 재사용 원칙과 공식 문서 확장의 호환성 분석 |

---

## 3. 상위 연계 기준 문서

- [Neo4j & Qdrant 통합 스키마 설계서 (db_schema.md)](../db_schema.md)
- [최종 통합 데이터 계약서 (data_contract.md)](../data_contract.md)
- [하이브리드 RAG 검색 평가 허브 (docs/development/retrieval_eval/)](../../development/retrieval_eval/README.md)
