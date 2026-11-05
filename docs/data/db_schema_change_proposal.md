# LifePolicy AI — DB 스키마 설계 이력 및 정식 설계서 안내

> **알림:** 본 문서는 초기 DB 스키마 변경 제안서였으며, 데이터팀의 통합 확정 권한에 따라 정식 확정된 최종 DB 스키마 설계 문서는 아래 링크로 이관되었습니다.

👉 **공식 기준 문서:** [최종 통합 DB 스키마 설계 명세서 (db_schema.md)](db_schema.md)  
👉 **공식 데이터 계약서:** [최종 통합 데이터 계약서 (data_contract.md)](data_contract.md)

---

## 핵심 확정 요약
1. **Neo4j 생애단계 표준 레이블:** `LifeStage`로 단일화 완료 (`LifeCycle` 병기 배제)
2. **복합 자격 조건:** 중첩 `ConditionGroup` DAG(비순환 방향 그래프) 모델링 채택 (AND/OR/NOT 계층 결합 및 3값 논리 TRUE/FALSE/UNKNOWN 지원)
3. **Qdrant Vector DB:** `lifepolicy_chunks` 컬렉션 페이로드 메타데이터 규격 및 필터 인덱스 명세 확정 (임베딩 차원 파라미터화)
4. **인계 패키지:** `data/handoff/` 디렉터리에 노드별 전용 파일, 관계 파일, Cypher DDL, Qdrant 설정 및 README 완비
