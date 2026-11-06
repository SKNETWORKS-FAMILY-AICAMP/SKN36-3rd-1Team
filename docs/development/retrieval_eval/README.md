# Retrieval 평가 자료 (검색 단계, 임베딩·DB 담당)

> 상태: **`retrieval_eval_v0`으로 고정 (2026-11-06)** — 해시는 `retrieval_eval_v0.freeze.json`.
> 질문 행의 `status`는 작성 당시 값(`candidate`) 그대로다. 고정 여부는 freeze 기록이 기준이다.
> 사용자 검수·RAG 담당(나지인·박소희)과 형식 합의 결과 바꿔야 하면 v0을 고치지 않고 v1을 새로 만든다.
> 기준 데이터: `v1.3.2-verified` (`data/processed/documents.jsonl`, 문서 해시 검증 후 위치 계산)

## 역할 구분

| 구분 | 담당 | 무엇을 보나 | 위치 |
|---|---|---|---|
| Retrieval 평가 (이 폴더) | 주민혁 (임베딩·DB) | Qdrant Top-K 청크에 **정답 근거 원문**이 들어 있는가 | `docs/development/retrieval_eval/`, `database/retrieval_eval.py` |
| RAG 답변 평가 | 나지인·박소희 | 답변·출처·추가 질문의 품질 | `tests/evaluation/`, `ai/evaluation/` (이 작업에서 수정하지 않음) |

같은 질문을 두 평가에서 함께 쓰려면 질문 ID·정답 근거 형식을 합의해야 한다 (아래 "합의 필요").

## 파일

| 파일 | 내용 |
|---|---|
| `questions_candidates_v0.source.json` | 사람이 작성한 원본: 질문, 유형, 근거 문서와 근거 문구(원문 그대로) |
| `questions_candidates_v0.jsonl` | 원본을 원문과 대조해 만든 결과: 근거 위치(`char_start`, `char_end`), 중복 위치, 같은 정책의 다른 문서, 검증 상태 |
| `retrieval_eval_v0.freeze.json` | v0 고정 기록: 두 파일의 sha256(줄바꿈 LF 정규화), manifest sha256, 질문 수, 채점 규칙 |

다시 만들기 (원문 해시 검증 포함, DB·API 접속 없음):

```powershell
.venv\Scripts\python.exe -c "from database import retrieval_eval as r; r.build_from_source('docs/development/retrieval_eval/questions_candidates_v0.source.json', 'docs/development/retrieval_eval/questions_candidates_v0.jsonl')"
```

`tests/unit/test_retrieval_eval.py`가 두 파일이 서로 맞는지, 모든 근거가 원문 슬라이스와 같은지 검사한다.

## 질문 한 줄 형식 (jsonl)

```text
question_id, question, question_type, answerable, policy_ids, status("candidate"), verification("verified"/"needs_review"),
gold.required[]: evidence_id, document_id, policy_id, char_start, char_end, evidence_text(원문 슬라이스),
                 evidence_text_source(작성 문구), match_mode(exact/whitespace), occurrences_in_document,
                 alternatives[{document_id, char_start, char_end}], same_policy_alternative_documents,
                 other_documents_with_same_text(다른 정책의 우연 일치 — 정답 아님), status
```

- 근거가 여러 구간에 필요한 질문은 `required`가 여러 개다 (예: RQ019 총자산 + 자동차).
- 같은 근거 문장이 같은 정책의 다른 문서에도 있으면 `alternatives`에 함께 넣는다 (예: 주택도시기금 "대출안내"와 "이용절차" 화면의 상단 요약).
- 다른 정책 문서에 우연히 같은 문장이 있으면 정답으로 보지 않는다 (예: RQ049 신청기간 문구가 DOC_YC_023에도 있음).

## 채점 기준 (`database/retrieval_eval.py`)

- 근거 적중 = 검색된 청크 하나가 근거 구간 **전체**를 포함 (1자만 겹치면 적중 아님)
- `hit`: 모든 required 근거 적중 → 첫 정답 순위 = 마지막 required 근거가 처음 적중한 순위, MRR = 1/순위
- `split_only`: 근거가 여러 청크에 나뉘어야만 덮임 / `partial`: 일부 근거만 / `doc_only`: 정답 문서만 / `miss`
- `general_no_single_answer`(answerable=false)는 채점하지 않고 검색된 정책 수만 기록
- 도달 가능성: 구성의 전체 청크 중 근거를 통째로 담은 청크가 없으면 그 구성은 원리상 적중 불가 → 함께 보고
  (2026-11-06 확인: A_B2·B_B3·C_B3C1 모두 55문항 전부 도달 가능)
- 비교 조건: 같은 질문·같은 임베딩 모델·같은 Top-K(5)·질의 지시문 없음, 청킹 구성만 바꾼다.

## 질문 작성·고정 규칙

1. 원문에서 확인한 문구로만 근거를 쓴다. 원문에 없는 정책 정보는 질문으로 만들지 않는다.
2. 수집 단계 오류 표기(예: "대상연령: 미상 (원문 0~0세 표기 오류…)")는 근거로 쓰지 않는다.
3. 검색 결과를 본 뒤 질문·정답을 바꾸지 않는다. 바꿔야 하면 새 버전(v1)으로 만들고 이유를 남긴다.
4. 확정 시 `status`를 `frozen`으로 바꾸고 고정일·검수자를 기록한다 (아직 하지 않음).

## 합의 필요 (RAG 담당과)

- 질문 ID 체계(RQ…)와 RAG 평가 질문 ID의 연결 방식
- 정답 근거를 글자 위치로 공유할지, `chunk_id`로 공유할지 (청킹 구성마다 chunk_id가 달라 글자 위치를 권장)
- 일반 질문(정답 없음)을 RAG 평가에서 "근거 없음/추가 질문" 시나리오로 함께 쓸지
