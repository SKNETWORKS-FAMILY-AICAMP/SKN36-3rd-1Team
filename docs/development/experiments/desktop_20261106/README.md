# Issue #5 C1(LLM 보조 청킹) 실험 자료 (2026-11-06, 데스크톱)

OpenAI `gpt-5.6-terra`(Responses API, Structured Outputs, reasoning effort low)로 섹션 제목·시작 문자열만 제안받은 결과다.
청크 본문은 항상 원문 슬라이스이며 LLM 문장은 본문에 들어가지 않는다. **이 폴더의 응답 파일만으로 API 없이 C1을 재현한다.**

- 대상: DOC_NHUF_003(창 2), DOC_NHUF_005(창 2), DOC_LH_001(창 7) = 실제 호출 11회, 모두 성공
- 비용(응답 usage 기준 추정): 누적 $0.137056 (입력 35,840 / 출력 5,448 토큰). 실제 청구액은 OpenAI 사용량 화면 기준
- 키·프롬프트 전체·원문 전체는 저장하지 않았다 (제목·시작 문자열·페이지, 응답 섹션 JSON, 토큰·비용·응답 id만)

| 파일 | 외부 호출 | 내용 |
|---|---|---|
| `c1_openai_<문서>_w<창>.json` (11개) | — | 창별 실제 응답: 제안(title·start_text·page), 호출 기록, 비용 |
| `c1_openai_batch_summary.json` | — | 저장 응답 재처리 요약 (문서별 C1/B3 비교) |
| `c1_openai_window_call.py` | **OpenAI 유료** | 창 1개 호출 (`--confirm-paid-call` 필요) |
| `c1_openai_batch.py` | **OpenAI 유료** | 남은 창 배치 호출 (`--confirm-paid-call` 필요). `--reprocess-only`는 API 없이 재처리 |
| `c1_revalidate_saved.py` | 없음 | 저장 응답을 현재 검증 규칙으로 재검증 |

## 재현 (API 호출 없음)

```powershell
.venv\Scripts\python.exe docs/development/experiments/desktop_20261106/c1_openai_batch.py --model gpt-5.6-terra --reprocess-only
.venv\Scripts\python.exe -m database.full_index --config C_B3C1 --no-db     # B3 243개 + C1 3개 = 1,141 청크
```

## 주의

- `c1_openai_DOC_NHUF_003_w0.json`의 `accepted_sections`·`chunks`·`b3_comparison`은 **첫 호출 당시 검증 규칙**의 결과다
  (그 뒤 반복 제목 처리·짧은 섹션 제목 보존·서식 머리말 규칙을 고쳤다). 현재 결과는 위 재현 명령으로 확인한다.
  `proposals`(LLM 응답)는 그대로 유효하다.
- 다시 호출하면 응답이 달라질 수 있다 (temperature 등 재현성 보장 없음). 재현은 저장된 응답으로만 한다.
