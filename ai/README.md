# ai

답변 생성에 사용할 프롬프트를 관리합니다. 임베딩과 답변 생성 실행 코드는 각각 `database/`와 `backend/llm/`에 있습니다. `embeddings/`, `workflows/`, `evaluation/`은 예약 경로입니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`prompts/system_prompt.py`](prompts/system_prompt.py) | 근거 사용·인용·추가 질문을 위한 생성 지침 |
| [`../backend/llm/generator.py`](../backend/llm/generator.py) | 프롬프트를 사용하는 답변 생성기 |

## 관련 문서

- [상세 가이드](../docs/api/ask_contract.md)
