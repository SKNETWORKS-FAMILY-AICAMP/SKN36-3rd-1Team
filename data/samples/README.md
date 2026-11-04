# data/samples/ — 형식 예시 전용

**이 폴더의 모든 파일은 가상 값이며 실제 정책 정보가 아니다.**

- 목적: JSONL 형식과 필드명을 팀원이 빠르게 확인하고, 단위 테스트 픽스처로 쓰기 위함.
- 모든 레코드에 `"verification_status": "sample_only"`를 넣고 ID는 `*_SAMPLE_*` 형식을 쓴다.
- URL은 `example.org`만 사용한다.
- Neo4j·Qdrant의 실데이터 컬렉션에 적재하지 않는다.

| 파일 | 형식 출처 |
|---|---|
| `policies.example.jsonl` | PRD §6.3 policies.jsonl 예시 |
| `documents.example.jsonl` | PRD §6.3 documents.jsonl 예시 |
| `relationships.example.jsonl` | PRD §6.3 relationships.jsonl 예시 |

필드 계약이 확정되면 (PRD D-03) 이 예시도 함께 갱신한다.
