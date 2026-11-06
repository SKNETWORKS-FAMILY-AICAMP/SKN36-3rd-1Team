# Issue #21 자격 조건 재구성 후보 (REVIEW_ONLY_NOT_FOR_DB)

v1.4.0-candidate의 정책·문서·청크 본문은 그대로 두고 자격 조건·지역 상세 메타데이터만 재구성했다.

- neo4j/: Graph 노드·관계 (Issue #5 load_graph 입력)
- qdrant/payload_chunks.jsonl: 청크 payload (v1.4.0과 ID·본문 동일), qdrant/policy_metadata.jsonl: 정책 단위 메타데이터
- documents.jsonl: 문서 261건 본문 (content_hash 검증, Issue #5 재청킹 입력)
- processed/, policy_changes.jsonl, uncertainty_audit.jsonl, comparison.json: 변경 근거와 보류 사유

UNKNOWN은 적격(TRUE)이 아니다. 사용법·판정 규칙: docs/development/ISSUE21_RAG_HANDOFF_20261107.md
검증: python scripts/rebuild_eligibility_candidate.py --verify-only <이 폴더>
