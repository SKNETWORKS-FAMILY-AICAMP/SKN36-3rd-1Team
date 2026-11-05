# data/

담당: 데이터 (장반석). 이 폴더에는 아직 **실제 수집 데이터가 없다** (2026-11-04 기준).

## 예정 파일

| 경로 | 내용 | 상태 |
|---|---|---|
| `raw/youth_policy/` | 온통청년 API 원본 응답(XML) | 미수집 (API 승인 대기) |
| `raw/lh/` | LH 공고 원본 PDF | 미수집 |
| `raw/other/` | 기타 기관 원본 | 미수집 |
| `processed/policies.jsonl` | 정책 기본정보·조건 (Graph용) | 미생성 |
| `processed/documents.jsonl` | 원문 단위 텍스트·출처 (Vector용) | 미생성 |
| `processed/relationships.jsonl` | 정책-기관-대상-조건-문서 관계 | 미생성 |
| `metadata/source_registry.csv` | 출처 URL·기관·공고일·정정 여부·검증 상태 | 미생성 |
| `metadata/api_registry.md` | API 인증·파라미터·응답 형식·호출 제한 | 미생성 |
| `schemas/` | JSONL 필드 계약 (JSON Schema 등) | 미생성 |

필드 정의는 [docs/data/data_schema.md](../docs/data/data_schema.md), 정제 규칙은 [docs/data/preprocessing_rules.md](../docs/data/preprocessing_rules.md)를 따른다.

## 규칙

- `raw/` 원본은 덮어쓰지 않고, Git에서는 기본 제외된다 (`.gitignore`).
- `samples/`는 **형식 설명용 가상 예시**만 둔다. 실제 정책 데이터로 적재하지 않는다.
- 선행 수집 패키지(`LifePolicy_Official_Data_Collection_20261104.zip`)의 샘플은 `processed/*_sample.jsonl`로 반영되었다. 독립 검증 결과는 [docs/CROSSCHECK_REPORT.md](../docs/CROSSCHECK_REPORT.md), 검증 지시문은 [docs/data/ANTIGRAVITY_CROSSCHECK_PROMPT.md](../docs/data/ANTIGRAVITY_CROSSCHECK_PROMPT.md) 참고.
