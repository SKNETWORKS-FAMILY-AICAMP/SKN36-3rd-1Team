# database

Qdrant 원문 검색과 Neo4j 정책 관계 조회를 결합합니다. 기본 검색 프로필은 `b2_06b_v140`입니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`hybrid_search.py`](hybrid_search.py) | 벡터 검색 결과와 정책 관계 결합 |
| [`embedding.py`](embedding.py) | Ollama 임베딩 호출 |
| [`full_index.py`](full_index.py) | 문서 청킹과 벡터 색인 구축 |
| [`neo4j/load_graph.py`](neo4j/load_graph.py) | 정책 그래프 적재 |
| [`qdrant/store.py`](qdrant/store.py) | 컬렉션 및 접속 설정 |

## 관련 문서

- [상세 가이드](../docs/development/ISSUE43_RESTORE_GUIDE.md)
