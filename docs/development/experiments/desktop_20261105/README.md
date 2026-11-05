# Issue #5 데스크톱 실험 자료 (2026-11-05)

데스크톱 환경 복구 후 실행한 Qdrant 재생성, Neo4j 적재, Hybrid 검색 실험의 결과와 스크립트를 보존한다.
노트북 원본 자료는 상위 폴더(`docs/development/experiments/`)에 있다.

- 환경: i5-14600K, RAM 31.7GB, RTX 5070 12GB (Ollama 100% GPU), Python 3.13.15
- 서버: Qdrant 1.12.4 (`exp_b2_qwen3emb06b_v1`, 107 point), Neo4j 5.26.32 community (노드 1,034 / 관계 3,615)
- 질문은 원문 근거로 만든 스모크 질문이다. **검색 성능 평가 결과가 아니다.**

| 파일 | DB 쓰기 | 내용 |
|---|---|---|
| `exp1_report_desktop.json` | — | `exp1_load_search.py` 실행 결과: 34 point 적재, 재적재 중복 0, 질문 4개 × 지시문 2종 검색, GPU 임베딩 시간 |
| `exp2_report_desktop.json` | — | `exp2_lh001.py` 실행 결과: DOC_LH_001 73 point 추가(총 107), 기존 34개 불변, PDF 페이지 검증, 질문 6개 검색 |
| `neo4j_load_desktop.json` | — | Neo4j 1차 적재·기본 보호(재적재 거부)·`allow_existing=True` 재적재 결과, 검증·지문(sha256) |
| `neo4j_property_check.json` | — | 노드·관계 속성값 원본 대조(읽기 전용) 결과 |
| `hybrid_experiment.json` | — | `search_policy_context` 질문 5개 결과, P6 필터(없음/document_id/policy_id) × 지시문 2종 비교 |
| `neo4j_load_desktop.py` | **있음** | Neo4j 적재 스크립트. 파일 첫 부분의 사용 조건을 확인하고 승인 후에만 실행 |
| `hybrid_experiment.py` | 없음 | Hybrid 검색 실험 스크립트 (읽기 전용, Ollama 호출 있음) |

## 결과 해석 시 주의

- Neo4j 검증(ID·관계 양끝·속성 일치)은 **DB가 인계 파일과 같다**는 뜻이다. 그래프의 자격 조건이 **의미상 정확하다**는 뜻이 아니다.
  정부24 정책 조건 생성 규칙의 오류 의심은 인수인계서 §12에 정리했다.
- 노트북 결과와의 차이(벡터·점수)는 CPU/GPU 연산 차이로 생길 수 있다. 순위 비교는 인수인계서와 작업 보고 기준.
- JSON 안의 정책 원문 조각은 공공 정책 정보(수집 데이터)이며 개인정보가 아니다.
