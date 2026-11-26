# 청년월세 35세 502 재발 진단

2026-11-26 KST · R3D 상담 검증 · 강민지 → 나지인·박소희

기존 상담에서 `POL_009`의 가상 35세 요청이 한 차례 `502 LLM_OUTPUT_INVALID`를 반환했다. dev `befe2d026bb59511966f212dd40c968c0dcea5f2`에서 동일 사례를 재시험한 결과는 **502 0/3**이다. 현재 FastAPI 오류 처리 코드에는 공개 오류 코드까지만 기록돼 있어 기존 실패 지점은 미확정이다. [API 재시험 증빙](assets/issue122-befe2d0/ask-api.json)

## 기존 요청

재시험 증빙 `ask-api.json`의 `rent35-1`~`rent35-3`에 저장된 동일한 `POST /api/ask` 본문이다. 다음 검증에서 이 요청을 그대로 사용한다.

```json
{
  "question": "이 정책의 나이 기준을 충족하나요?",
  "profile": {"age": 35, "region": "서울", "income": null},
  "history": [],
  "selected_policy_id": "POL_009"
}
```

## 실패 단계와 기록 지점

아래 `stage`와 `reason`은 서버 로그용 제안이다. 요청마다 서버가 생성한 동일한 상관 ID로 FastAPI 오류 응답과 해당 분기의 로그를 연결한다. 로그에는 `request_id`, `stage`, 고정된 `reason`, 공개 오류 코드만 남긴다. 질문·프로필·프롬프트·모델 원문·예외 메시지와 인증 정보는 수집에서 제외한다.

| 단계 | 로그를 남길 코드 | 고정 사유 예시 |
| --- | --- | --- |
| 생성 `generation` | `backend/llm/generator.py`의 `_call_openai`에서 공급자 호출 실패, 타임아웃, 응답 잘림 또는 본문 누락 분기 | `provider_error`, `timeout`, `truncated_output`, `empty_content` |
| 파싱 `parsing` | `generate_answer`의 `json.loads` 예외 분기 | `invalid_json` |
| 스키마 `schema` | Generator의 `_validate_model_response`에서 객체·상태·답변·후속 질문 구조를 검사하는 분기와 Pipeline의 `GeneratorOutput.model_validate` 예외 분기 | `invalid_model_shape`, `invalid_generator_output` |
| 인용 검증 `citation` | Generator의 `_canonicalize_citation` 및 필수 인용 검사와 Pipeline의 `_citations` 실패 분기 | `unknown_source_id`, `missing_required_citation`, `citation_on_no_evidence` |
| 본문 검증 `content_validation` | Pipeline에서 답변·후속 질문에 내부 상태 문자열이 남아 오류를 내는 분기 | `internal_status_in_text` |

`LLM_OUTPUT_INVALID`에는 공급자가 반환한 불완전 출력, 파싱, 스키마, 인용, 본문 검증이 모두 포함될 수 있다. `LLM_UNAVAILABLE` 502 및 `LLM_TIMEOUT` 504는 생성 호출 실패와 타임아웃을 가리킨다. `ELIGIBILITY_OUTPUT_INVALID` 502는 조건 판정 결과 검증 경로를 확인한다. `detail.code`가 없는 502는 FastAPI 접근 로그와 프록시 로그를 먼저 대조한다. [오류 계약](../api/ask_contract.md#9-오류-처리-및-시간-제한)

## 다음 발생 시 남길 증빙

1. React DevTools의 Network에서 `POST /api/ask`를 확인한다. HTTP 상태, `detail.code`, 요청 시각(KST), 소요 시간, 제공되는 요청 ID, 화면 오류 문구를 기록한다. 인증 헤더나 전체 HAR는 공유 자료에서 제외한다.
2. API 담당과 같은 요청의 로그를 대조한다. 실행 SHA, R3D 릴리스 또는 manifest, 공급자·모델 식별자를 함께 적는다. 요청 ID가 아직 없다면 시각과 접근 로그로 대조하고, 동시에 처리된 요청을 구분하기 어려우면 실패 단계는 미확정으로 남긴다.
3. 서버 계측 반영 후 기존 Mock 기반 API 테스트로 공개 응답 계약을 확인하고, 생성·파싱·스키마·인용 분기별로 고정 사유와 요청 ID가 남는지 검증한다. 이후 예정된 R3D 회귀에서 위 요청을 사용한다. 재현 결과에는 502 발생 횟수와 총 요청 수를 함께 적는다.

## 지인·소희 공유 문안

> 지인님, 소희님. 청년월세 `POL_009` 35세 상담에서 이전에 `502 LLM_OUTPUT_INVALID`가 한 번 나왔고, dev 재시험은 0/3이었습니다. 다음 발생 건을 추적할 수 있게 지인님은 Pipeline·FastAPI, 소희님은 Generator 쪽의 실패 분기와 로그 위치를 확인 부탁드려요. 같은 요청 ID에 실패 단계와 고정 사유를 연결하고, 공개 오류 응답은 현행 계약을 유지하는 방향을 제안합니다. 민지 쪽에서는 위 요청을 그대로 사용해 재발 시 Network 상태·오류 코드·시각과 화면 문구를 남기겠습니다.
