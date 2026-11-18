# docs/development/retrieval_eval — 검색 성능 벤치마크 평가 데이터셋

> **위치**: `LifePolicy AI/docs/development/retrieval_eval/`  
> **역할**: 하이브리드 RAG 검색 엔진 및 임베딩 모델의 품질 회귀(Regression)를 방지하기 위한 벤치마크 평가셋 관리  
> **상위 가이드**: [docs/development 인덱스](../README.md) | [docs 전체 인덱스](../../README.md)

---

## 1. 벤치마크 평가셋 개요

본 디렉터리는 다양한 생애주기별(사회초년생, 신혼부부, 취업준비생, 저소득 가구 등) 사용자 자연어 질의에 대해, 검색 파이프라인이 정확한 정책과 근거 본문을 추천하는지 검증하기 위한 평가 데이터셋을 포함합니다.

> **상태 주의 (후보 상태)**: 본 평가셋의 각 질문 항목은 사용자 검수 및 RAG 담당자 간 정답 형식 최종 합의 전의 **후보 상태(`status: "candidate"`)**로 관리되고 있습니다. 임의로 확정된 골든셋으로 간주하지 않으며, 사후 변조를 방지하기 위해 freeze 파일의 해시 계약을 엄격히 준수합니다.

---

## 2. 파일 구성 및 세부 명세

| 파일명 | 역할 | 설명 |
|---|---|---|
| [questions_candidates_v0.jsonl](questions_candidates_v0.jsonl) | **평가 질의 및 정답 세트 (후보)** | 총 55개 질의(채점 대상 52개)를 포함하는 JSONL 데이터. 사용자 검수·합의 전 후보 상태(`status: "candidate"`)로 보존 |
| [questions_candidates_v0.source.json](questions_candidates_v0.source.json) | **평가셋 메타데이터** | 벤치마크 평가 질의 생성 출처, 설계 의도 및 검증자 정보 기록 |
| [retrieval_eval_v0.freeze.json](retrieval_eval_v0.freeze.json) | **무결성 고정 기준선 (Baseline Freeze)** | 질문·정답 파일의 SHA-256 해시를 고정하여, 평가 대상 모델/청킹 변경 시 질문이나 정답이 사후 변조되거나 결과에 맞춰 조작되는 것을 원천 차단하는 기준선 |

### `questions_candidates_v0.jsonl` 실제 필드 구조

각 행은 단순 정책 ID 목록이 아니라, 문서 본문 내의 실제 근거 구절(Character-level Offset)을 포함하는 정밀 구조로 구성되어 있습니다:

- `question_id`: 질의 고유 식별자 (예: `RQ001`)
- `question`: 사용자 자연어 질문 텍스트
- `question_type`: 질문 유형 (`policy_named`, `numeric_table`, `age_income_asset`, `eligibility`, `application` 등)
- `answerable`: 답변 가능 여부 불리언 (`true` / `false`)
- `policy_ids`: 관련 정답 정책 ID 배열 (예: `["POL_003"]`)
- `gold.required`: 필수 근거 청크 객체 배열
  - `evidence_id`: 근거 고유 식별자 (예: `RQ001-E1`)
  - `document_id`: 근거가 수록된 문서 ID (예: `DOC_NHUF_003`)
  - `evidence_text`: 정답을 뒷받침하는 핵심 원문 인용구
  - `char_start` / `char_end`: 문서 내 원문 텍스트 시작·종료 오프셋
  - `match_mode`: 일치 모드 (`exact`, `whitespace` 등)
  - `status`: 근거 검증 상태 (`verified_exact`, `verified_whitespace_normalized`)
- `status`: 평가셋 상태 (`candidate` — 검수·합의 전 후보 상태)
- `verification`: 검증 상태 (`verified`)

### `retrieval_eval_v0.freeze.json`의 역할과 규칙

- **역할**: 평가셋의 무결성 동결(Freeze). `questions_candidates_v0.jsonl` 및 `source.json`의 SHA-256 해시와 채점 설정(K=5, Recall@5, MRR, Full Containment)을 기록합니다.
- **규칙**: **이 freeze 파일에 명시된 해시와 일치하지 않는 질문·정답으로는 어떠한 벤치마크 평가도 수행할 수 없습니다.** 정답을 수정해야 할 경우 기존 파일을 덮어쓰지 않고 `v1`을 신규 생성하며 변경 사유를 명시해야 합니다.

---

## 3. 벤치마크 재현 및 평가 실행 방법

Qdrant·임베딩 서버가 준비된 뒤 프로젝트 루트에서 **모듈 형태로** 실행합니다. 파일 경로로 직접 실행하면(`python database/…py`) `database` 모듈을 찾지 못해 멈춥니다. 목적에 따라 실행기가 다르고 점수의 의미도 다릅니다.

| 목적 | 명령 | 점수의 의미 |
|---|---|---|
| v1.3.2 동결 평가 | `python -m database.retrieval_run --record` | freeze의 해시·manifest가 현재 데이터와 같을 때만 실행됩니다. 동결 기준의 Recall@K·MRR@5 |
| v1.4.0 진단 (기본 `legacy` 모드) | `python -m database.retrieval_v140_eval --output <결과 폴더>` | 승인 v1.4.0의 B2/B3를 vector·hybrid로 비교하는 진단. 정답이 검수 전 후보라 공식 지표(`formal_recall`, `formal_mrr`)는 비어 있습니다 |
| 0.6B·4B 예비 비교 | `python -m database.retrieval_v140_eval --mode model-ab --environment <환경 JSON> --output <결과 폴더>` | B2 청크에서 두 임베딩 모델의 예비 Recall@5·MRR@5. 사람 검수 전 결과입니다 |

v1.3.2 동결 평가에는 레거시 인계본을 지정합니다. PowerShell에서는 이 평가용 터미널에서 아래 두 줄을 실행합니다.

```powershell
$env:LIFEPOLICY_DATASET_DIR = 'data/handoff'
python -m database.retrieval_run --record
```

v1.4.0 평가를 실행할 때는 새 터미널을 열거나 `Remove-Item Env:LIFEPOLICY_DATASET_DIR`로 위 설정을 해제합니다.

- `retrieval_v140_eval`의 `--output`은 필수입니다. 결과는 문서 폴더가 아닌 곳(예: `data/staging/evaluation/retrieval_v140_<날짜>`)에 둡니다.

### 주요 평가 지표
- **Recall@5 (Full Containment)**: 필수 근거 구간 전체(`gold.required`)가 Top-5 검색 청크 내에 완전히 포함된 질문 비율
- **MRR (Mean Reciprocal Rank)**: `first_hit_rank`의 역수 평균(5위 안에 없으면 0)
- **first_hit_rank**: 필수 근거를 **모두** 찾은 순위. 근거가 여러 개면 가장 늦게 찾은 근거의 순위입니다(`database/retrieval_eval.py` `score_question`)
- **outcomes**: 질문별 결과 분류(`hit`, `split_only`, `partial`, `doc_only`, `miss`, `not_scored`)

> freeze 파일의 채점 지표는 위 네 가지(`recall_at_5`, `mrr`, `first_hit_rank`, `outcomes`, 근거 일치 방식 `full_containment`)뿐이다. freeze의 `data_version`은 `v1.3.2-verified`이므로 v1.4.0 데이터로 평가할 때는 데이터 버전이 다르다는 점을 결과에 함께 적는다.
