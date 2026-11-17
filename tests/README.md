# tests

단위 테스트는 개별 로직을, 통합 테스트는 모듈 연결을, 평가 코드는 검색과 생성 품질을 확인합니다. 실제 DB 스모크 테스트는 복원된 DB와 접속 설정이 필요합니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`unit/`](unit/) | 개별 로직 테스트 |
| [`integration/test_rag_pipeline.py`](integration/test_rag_pipeline.py) | RAG 처리 흐름 테스트 |
| [`integration/test_final_v140_smoke.py`](integration/test_final_v140_smoke.py) | 최종 데이터가 적재된 DB 확인 |
| [`evaluation/llm_comparison/`](evaluation/llm_comparison/) | 답변 생성 모델 비교 코드와 결과 |

## 실행 방법

테스트는 데이터셋 두 가지를 전제로 나뉩니다. 한 환경에서 전체를 돌리면 다른 데이터셋 전제 테스트가 실패하므로 두 번 나눠 실행합니다. 아래 명령은 프로젝트 루트(PowerShell) 기준입니다.

1. **v1.4.0 (기본)**: `.env`의 `LIFEPOLICY_DATASET_DIR`가 비어 있거나 `data/releases/v1.4.0-candidate`일 때

   ```powershell
   $env:OPENAI_API_KEY=""; .venv\Scripts\python.exe -m pytest tests/unit tests/integration tests/test_candidate_release.py
   ```

2. **v1.3.2 legacy**: 인계 패키지(`data/handoff`)를 전제로 한 테스트 (청킹·색인·평가 실험 등)

   ```powershell
   $env:OPENAI_API_KEY=""; $env:LIFEPOLICY_DATASET_DIR="data/handoff"; $env:PYTHONPATH="tests/unit"
   .venv\Scripts\python.exe -m pytest tests/unit/test_vector_store.py tests/unit/test_full_index.py `
     tests/unit/test_hybrid_profiles.py tests/unit/test_retrieval_run.py tests/unit/test_chunking.py `
     tests/unit/test_chunking_b3.py tests/unit/test_handoff_data.py tests/unit/test_retrieval_eval.py `
     tests/unit/test_c1_section_titles.py tests/unit/test_release_v140_adapter.py
   ```

   `tests/unit` 전체를 이 환경으로 돌리면 v1.4.0 전용 테스트(`test_embedding_resume.py`, `test_runpod_transfer.py`, `test_embedding_ab_eval.py` 등)가 실패합니다.
   반대로 1번 환경에서는 위 legacy 파일들이 실패합니다(두 환경 결과를 합쳐서 판단).

- `OPENAI_API_KEY`를 비워 두면 mock이 빠진 테스트가 있어도 실제 OpenAI를 호출하지 않고 실패합니다.
- `tests/manual/`은 실제 OpenAI를 호출하므로 pytest로 함께 돌리지 않습니다 (비용 발생, 사전 승인).
- DB 스모크(`RUN_QDRANT_SMOKE`, `RUN_NEO4J_SMOKE`, `RUN_HYBRID_SMOKE`, `RUN_FINAL_V140_SMOKE`)는 복원된 DB가 있을 때 `1`로 켭니다.
- Windows에서 `.gitattributes`(`data/handoff/**`, `data/releases/** -text`)가 생기기 전에 받은 작업 트리는 데이터 파일이 CRLF로 남아 manifest 해시 테스트(`tests/test_candidate_release.py`)가 실패할 수 있습니다. `git ls-files --eol data/handoff`에서 `w/crlf`가 보이면 해당 파일을 다시 받아야 합니다.

참고: [PR #45 검증 기록](../docs/development/PR45_REVIEW_VALIDATION_20261110.md)

## 관련 문서

- [상세 가이드](../docs/development/ISSUE43_RESTORE_GUIDE.md)
