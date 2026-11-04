# 데이터 스키마 (초안)

> 담당: 데이터 (장반석) · 상태: **초안 — 필드 확정 전 (PRD D-03)**
> 근거: [PRD §6](../product/PRD.md), [CODING_STYLE §8](../development/CODING_STYLE.md)
> 확정 시 `data/schemas/`에 기계 검증용 스키마를 두고 이 문서에 결정일·PR 링크를 기록한다.

## 1. 공통 식별자

| 필드 | 의미 | 비고 |
|---|---|---|
| `policy_id` | 프로젝트 내부 정책 ID | 기관 공식 ID 아님. 공식 ID는 `external_id`로 별도 보존 (제안) |
| `document_id` | 원문 파일/웹 문서 단위 ID | |
| `chunk_id` | 원문 내 단락 ID | `document_id` + 순번/해시. 청킹 책임은 DB 담당 (PRD D-04) |
| `source_url` | 사람이 열 수 있는 공식 링크 | |
| `content_hash` | 원문 변경 검증용 해시 | 알고리즘 미확정 (SHA256 제안) |
| `retrieved_at` | 수집 시각, ISO 8601 + 시간대 | |

## 2. 파일별 필드

### processed/policies.jsonl

PRD §6.3 예시 필드: `policy_id`, `policy_name`, `category`, `agency`, `regions`, `requirements`, `eligibility_review_status`,
`application_start`, `application_end`, `official_apply_url`, `source_document_ids`, `retrieved_at`

### processed/documents.jsonl

PRD §6.3 예시 필드: `document_id`, `policy_id`, `title`, `source_type`, `source_url`, `published_at`, `retrieved_at`,
`content_hash`, `text`, `review_status`

### processed/relationships.jsonl

PRD §6.3 예시 필드: `source_id`, `relation`, `target_id`, `evidence_document_id`, `evidence_location`, `review_status`

## 3. 확정이 필요한 항목

- [ ] 본문 필드명: PRD는 `text`, CODING_STYLE 예시는 `content`
- [ ] 수집 시각 필드명: PRD는 `retrieved_at`, CODING_STYLE 예시는 `collected_at`
- [ ] 검수 상태 필드명·값: `review_status` / `eligibility_review_status` / `verification_status` 통일 여부
- [ ] 관계 파일명: `relationships.jsonl` (PRD) vs `graph_edges.jsonl` (선행 수집 패키지)
- [ ] `requirements`의 AND/OR 조건 그룹 표현 방식
- [ ] `unknown` / `not_applicable` / `null` 사용 기준
