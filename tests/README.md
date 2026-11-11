# tests

단위 테스트는 개별 로직을, 통합 테스트는 모듈 연결을, 평가 코드는 검색과 생성 품질을 확인합니다. 실제 DB 스모크 테스트는 복원된 DB와 접속 설정이 필요합니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`unit/`](unit/) | 개별 로직 테스트 |
| [`integration/test_rag_pipeline.py`](integration/test_rag_pipeline.py) | RAG 처리 흐름 테스트 |
| [`integration/test_final_v140_smoke.py`](integration/test_final_v140_smoke.py) | 최종 데이터가 적재된 DB 확인 |
| [`evaluation/llm_comparison/`](evaluation/llm_comparison/) | 답변 생성 모델 비교 코드와 결과 |

## 관련 문서

- [상세 가이드](../docs/development/ISSUE43_RESTORE_GUIDE.md)
