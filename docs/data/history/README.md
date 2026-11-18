# docs/data/history — 데이터 계약·스키마 변경 제안 및 학습 이력

> **위치**: `LifePolicy AI/docs/data/history/`  
> **상위 가이드**: [docs/data 허브](../README.md) | [docs 전체 인덱스](../../README.md) | [CODING_STYLE §17](../../development/CODING_STYLE.md)

---

## 1. 개요

본 디렉터리는 프로젝트 진행 중 발생한 **데이터 계약 및 DB 스키마 개정 제안서, 에이전트 프롬프트 원문, 트러블슈팅 학습 기록 등 4종의 히스토리성 문서**를 보관합니다.  
현재 정식 채택된 표준 문서의 제정 배경과 논의 과정을 추적할 수 있습니다.

---

## 2. 히스토리 문서 목록 (총 4종)

| 문서명 | 성격 | 주요 내용 |
|---|---|---|
| [data_contract_change_proposal.md](data_contract_change_proposal.md) | 계약 개정 제안 | 데이터 계약 초기 설계안, 필드 확장 제안 및 최종 통합 계약서 승격 이력 |
| [db_schema_change_proposal.md](db_schema_change_proposal.md) | 스키마 개정 제안 | Neo4j 및 Qdrant 초기 설계 제안서 및 정식 `db_schema.md` 승격 안내 |
| [ANTIGRAVITY_CROSSCHECK_PROMPT.md](ANTIGRAVITY_CROSSCHECK_PROMPT.md) | 에이전트 프롬프트 | 데이터 수집 및 릴리스 검증 당시 사용된 독립 크로스체크 프롬프트 원문 |

---

## 3. 상위 정식 표준 문서

- [최종 통합 데이터 계약서 (data_contract.md)](../data_contract.md)
- [Neo4j & Qdrant 통합 스키마 설계서 (db_schema.md)](../db_schema.md)
- [데이터 수집 및 리뷰 표준 지침 (DATA_COLLECTION_AND_REVIEW_RULES.md)](../DATA_COLLECTION_AND_REVIEW_RULES.md)
