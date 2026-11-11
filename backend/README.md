# backend

FastAPI에서 질문과 사용자 조건을 받아 정책 근거 검색, 자격·접수 상태 확인, 답변 생성을 연결합니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`api/main.py`](api/main.py) | 질문 API와 상태 확인 진입점 |
| [`api/schemas.py`](api/schemas.py) | 요청·응답 스키마 |
| [`rag/pipeline.py`](rag/pipeline.py) | 검색·판정·근거 선택·답변 생성 흐름 |
| [`eligibility/checker.py`](eligibility/checker.py) | 자격 조건과 접수 상태 판정 |
| [`llm/generator.py`](llm/generator.py) | 공식 근거와 판정에 기반한 답변 생성 |

## 관련 문서

- [상세 가이드](../docs/api/ask_contract.md)
