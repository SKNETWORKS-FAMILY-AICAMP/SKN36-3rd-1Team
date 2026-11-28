# data/schemas

이 폴더에는 별도 스키마 파일을 두지 않는다. 데이터 계약과 스키마의 정본은 아래 문서다.

| 문서 | 내용 |
| --- | --- |
| [docs/data/data_contract.md](../../docs/data/data_contract.md) | Neo4j·Qdrant 공통 식별자, 필드 타입, 3값 논리 (v1.4.0, 정본) |
| [docs/data/data_schema.md](../../docs/data/data_schema.md) | 수집 단계 JSONL 필드 명세 |
| [docs/api/ask_contract.md](../../docs/api/ask_contract.md) | 질문 API 요청·응답 계약 |
| [data/releases/v1.4.0-candidate/manifest.json](../releases/v1.4.0-candidate/manifest.json) | 서비스 데이터의 파일 목록·건수·SHA-256 |

`data/handoff/data_contract.md`는 v1.3.2 인계 패키지 안의 당시 사본이다. manifest 해시로 고정돼 있어 수정하지 않는다.
