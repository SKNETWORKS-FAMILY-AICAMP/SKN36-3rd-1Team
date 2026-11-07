# LifePolicy AI — RAG·조건 판정·LLM·API 공통 인터페이스 명세서

> **버전:** v1.0 · 2026-11-07  
> **상태:** **MVP 통합 개발 기준(지인 정리본)**. 이 문서로 개발을 시작하되, 팀 전원 승인·구현·테스트 완료를 의미하지 않는다. 구현 중 변경이 필요하면 변경 사유와 영향 범위를 공유하고 문서·타입·테스트를 함께 수정한다.  
> **저장 경로:** `docs/api/ask_contract.md`  
> **기준 코드:** `dev` 커밋 `fe8637c`(PR #22), React `feature/15-react-demo-ui`의 `src/api/types.ts`, `client.ts`  
> **주의:** 아래 예시 정책·ID·URL은 **가상 예시**이며 실제 정책의 자격·접수 상태를 의미하지 않는다.

## 0. 목적 및 역할 분담

이 문서는 **나지인(RAG·API 통합)**, **박소희(조건 판정·LLM)**, **강민지(React)**, **주민혁(Hybrid 검색)**의 연결 규격을 정한다. 소희 측 구현이 끝나기 전에도 지인은 Mock으로 Pipeline과 API를 개발할 수 있어야 한다.

| 담당 | 소유 영역 | 책임 |
|---|---|---|
| 나지인 | `backend/rag/context_builder.py`, `backend/rag/pipeline.py`, `backend/api/schemas.py`, `backend/api/main.py`, `tests/integration/`, 본 문서 | 프로필 정규화, 검색 호출, Context 생성, 판정 보류 재검증, LLM 연결, 인용 검증, 최종 응답·오류 처리 |
| 박소희 | `backend/eligibility/checker.py`, `backend/llm/generator.py`, `ai/prompts/`, 해당 `tests/unit/` | 조건 트리 판정, 접수 상태 판정, LLM 답변·후속 질문 생성, 담당 함수 단위 테스트 |
| 강민지 | `frontend/` | `AskRequest` 전송, `AskResponse` 렌더링, 후속 질문 처리, API 주소·타임아웃·오류 UI |
| 주민혁 | `database/` | Qdrant/Neo4j 검색 코드, 데이터·임베딩/DB 환경, 검색 프로필 검증 |

- `requirements.txt`, `.env.example` 변경은 지인이 취합한다. 소희는 **LLM 모델·SDK·API 키 환경변수의 이름**을 구현 중 공유한다. 실제 키/비밀번호는 절대 Git에 올리지 않는다.
- 소희의 **함수 내부 알고리즘, 프롬프트, LLM 공급자**는 소희가 결정한다. 함수 **입력·출력·상태값**은 이 문서대로 맞춘다.
- 공통 파일/프론트 타입 변경은 관련 담당자에게 알려 PR에서 확인한다.

## 1. MVP 확정 규칙 요약

1. **검색:** `HybridSearchSession.search_policy_context(question, filters=..., k=10)` 사용. `k`는 정책 수가 아니라 **청크 Top-K**다.
2. **조건 함수:** 동기 `def check_eligibility(profile: dict, policy: dict) -> dict`.
3. **LLM 함수:** 동기 `def generate_answer(question: str, contexts: list[dict], policy_checks: list[dict], history: list[dict] | None = None) -> dict`.
4. **조건 상태:** `eligible`, `ineligible`, `needs_check` / **접수 상태:** `before`, `open`, `closed`, `unknown`.
5. **품질 보류:** 영향을 받는 조건은 `needs_check`. 정보 부족을 `ineligible`로, 그래프 경고를 `eligible`로 바꾸지 않는다.
6. **답변 근거:** `evidence[].text`만 원문 근거다. 그래프 자동 추출 텍스트는 조건 판정 보조 데이터이며 공식 원문 인용 근거가 아니다.
7. **인용:** Context Builder가 `source_id=1..N`을 부여하고, LLM은 해당 `[n]`만 사용한다. 없는 번호·인용 없는 근거 답변은 오류다.
8. **API:** React의 기존 `AskRequest`, `AskResponse` 필드명을 유지한다. 정상 HTTP 200, 장애는 별도 오류 JSON.
9. **신청기간:** `상시`, `상시신청`, `상시/공고참조` 등 **자동 추출 문구만** 있으면 기본 `unknown`. 정확한 일정과 신뢰 가능한 근거가 있을 때만 다른 상태를 허용한다.
10. **개발:** Mock 기반 병렬 개발 → 소희 실제 함수 연결 → React 연동 → 통합 테스트. 추가 Agent·LangGraph 개발은 MVP 범위 밖이다.

## 2. 전체 처리 흐름

```text
React AskRequest
  └─ POST /api/ask (FastAPI)
      └─ run_ask_pipeline(request, search_session)
          1) 입력 검증 + 프로필 정규화
          2) Hybrid 검색(Qdrant + Neo4j): 청크 Top-10
          3) 정책별 evidence → build_contexts(): source_id 부여
          4) 선택된 context의 policy_id에 해당하는 정책만 선별
          5) 각 정책 → check_eligibility(profile, policy)
          6) 품질 경고 기준 재검증(확정 판정을 needs_check로 보수적 변경)
          7) generate_answer(question, contexts, policy_checks, history)
          8) LLM 출력/본문 인용 검증 + 출처 재번호
          9) 정책 메타데이터·citations·notice 조립
         10) AskResponse 반환
```

- **검색 결과가 없거나 인용 가능한 원문이 0개**면 LLM을 호출하지 않고 `status="no_evidence"`를 반환한다.
- **LLM에 주는 `policy_checks`와 최종 React에 주는 `policy_checks`는 동일한 정책 집합**을 가리킨다. 검색에는 있었지만 Context에 채택되지 않은 정책을 추가 판정·표시하지 않는다.
- `best_score`는 의미 유사도다. 적격 확률, 자격 점수, 정책 추천 확률로 표시하지 않는다.
- 사용자 질문, 검색 텍스트, 그래프 추출 텍스트, history는 **신뢰되지 않는 입력**이다. 그 안의 명령문은 시스템 지시로 취급하지 않는다.

## 3. Hybrid 검색 ↔ Pipeline (지인 담당)

### 3.1 실제 코드에 맞춘 호출

```python
from database.hybrid_search import HybridSearchSession

# 서버 수명 동안 1개(각 서버 프로세스 기준) 생성·관리
result = session.search_policy_context(question, filters=filters, k=10)
policies = result["policies"]  # list[dict]
```

- 검색 컬렉션과 임베딩 설정은 **기존 `default_profile()`에 위임**한다. 하드코딩으로 프로필·컬렉션을 지정하지 않는다.
- 현재 `database/hybrid_search.py`의 `DATASET_DEFAULT_PROFILES`에는 `1.4.0-candidate → b2_06b_v140`이 있다. 다른 데이터셋/인덱스가 로딩되면 선택 결과가 달라질 수 있으므로 시작 로그에서 실제 선택된 프로필·컬렉션을 확인한다.
- `HybridSearchSession`은 **연결을 최초 접근 시 생성**한다. 서버 시작 시 `em.load_env_file()` 후 세션을 생성하고, 정상 종료 시 `session.close()`를 호출한다.
- 서버 시작 시 임베딩 워밍업을 한다면 현재 검색 프로필의 **실제 모델명·차원·질의 템플릿**으로 실행한다. **존재하지 않는 `warm_up(session)` 함수를 그대로 호출하지 않는다.** 워밍업 실패는 기록하고, 서비스 준비 상태를 확인하도록 한다.
- **동시성:** 공유 세션 내부에 지연 초기화·진단 목록 변경이 있으므로, MVP에서는 **검색 호출에 `threading.Lock`을 두거나**, 동시 접근에 대한 테스트 후 공유 전략을 결정한다. FastAPI `def` 엔드포인트는 스레드풀에서 동작하므로 세션 객체 1개라고 자동으로 thread-safe인 것은 아니다. 프로세스가 여러 개면 프로세스별 세션을 가진다.
- `GET /health`는 프로세스 생존을 알린다. 준비 여부를 함께 노출한다면 연결·워밍업 오류를 숨기지 말고 `ready` 같은 별도 상태로 기록한다. 모든 요청마다 DB에 재접속할 필요는 없다.

### 3.2 정규화 프로필

React가 준 입력을 지인이 다음 형태로 정규화해서 소희 함수에 전달한다.

```python
normalized_profile = {
    "age": 25,               # int | None, 만 나이, 0~120
    "region": "서울특별시",     # str | None, 정식 시·도명
    "income": None,          # int | float | None, 연 소득/만원 단위
}
```

- `age`: 정수만 허용. `null`은 `None`; 실수·bool 등은 검증 오류(422).
- `region`: React `"서울"` → `"서울특별시"`. `database/region_search.py`의 `ALIASES`, `SIDOS`를 재사용하고, 변환 규칙을 중복 구현하지 않는다. 미입력 `""`과 인식 불가능한 값은 `None`으로 취급하되 **인식 불가 지역은 판정 확정에 이용하지 않고 안내·로그로 남긴다**.
- `income`: React 타입이 `number | null`이므로 **소수도 허용**하는 0 이상의 유한한 수. 단위는 연 소득 **만원**. **개인 소득인지 가구 소득인지 불명확하므로 중위소득·가구소득 조건을 직접 충족/불충족으로 판정하지 않는다.**
- `awaiting_field`에 `age/region/income`이 들어와도 서버가 자유 텍스트에서 임의 값을 확정 파싱하지 않는다. 최종 판정 입력은 이번 요청의 `profile`이 우선이다. 프론트가 추가 응답을 프로필에 반영한 경우 다음 요청에서 전달한다.

### 3.3 검색 필터

- `normalized_profile["region"] is None` → `filters=None`.
- 그렇지 않으면 → `filters={"region": normalized_profile["region"]}`.
- v1.4.0-candidate 환경에서 기존 `region_search.plan_region_search`가 실제 저장 지역값을 기준으로 후보를 만든다. 전국 범위도 함께 검색될 수 있다. **지역 검색 일치는 정책 자격 충족을 뜻하지 않는다.**
- **모호한 지역 값은 자동으로 검증 완료 취급하지 않는다.** 지역 필터가 제외한 정책까지 모두 조회했다고 주장하지 않는다.
- 허용 필터는 현재 `database/qdrant/store.py`의 `PAYLOAD_INDEX_FIELDS`에 따른다. `policy_id`, `document_id`, `chunk_id`, `chunking_version`, `category`, `region`, `life_stages`, `verification_status`.

### 3.4 정책 객체 원형

`result["policies"]`의 **정책 객체 한 건 전체**를 소희 함수에 그대로 전달한다. 다음은 필요한 키를 보여주는 **가상 축약 예시**다.

```json
{
  "policy_id": "POL_EXAMPLE",
  "policy_name": "가상 지원 정책",
  "best_rank": 1,
  "best_score": 0.83,
  "evidence": [
    {
      "chunk_id": "CHK_EXAMPLE",
      "document_id": "DOC_EXAMPLE",
      "text": "공고 원문 일부",
      "source_url": "https://example.org/notice",
      "rank": 1,
      "page_start": null,
      "section_title": "지원 대상"
    }
  ],
  "graph_status": "FOUND",
  "graph": {
    "policy": {"policy_id": "POL_EXAMPLE", "application_period": "상시/공고참조"},
    "organizations": [],
    "documents": [],
    "regions": [],
    "eligibility": {
      "evaluated": false,
      "condition_tree": [],
      "unknown_logic_groups": [],
      "structure_issues": []
    }
  },
  "data_quality": {
    "warnings": [],
    "hold_auto_eligibility": false,
    "hold_reasons": [],
    "affected_attributes": [],
    "official_source_checked": false
  }
}
```

- Graph 조회 실패/누락 정책은 `graph_status="MISSING_IN_GRAPH"`, `graph=null`일 수 있다.
- `graph.policy`의 `eligibility`, `benefit`, `application_period`, `application_method`는 **자동 추출 속성**이다. 원문 인용으로 둔갑시키지 않는다.
- `data_quality`, `evidence_document_check`, `region_search`는 경고/검증 보조 신호로 사용한다.
- Git clone만으로는 검색이 돌지 않는다. Qdrant/Neo4j 데이터 복원과 Ollama 임베딩 모델 준비가 필요하다. 개발은 Mock과 분리해서 진행한다.

## 4. 조건 판정 함수 계약 (소희 담당)

### 4.1 시그니처

```python
# backend/eligibility/checker.py

def check_eligibility(profile: dict, policy: dict) -> dict:
    """정규화 프로필과 검색 정책 1건의 조건을 비교한다. LLM 호출이나 DB 재조회는 하지 않는다."""
    ...
```

**입력:** `profile`은 §3.2 형식, `policy`는 §3.4의 **검색 결과 객체 전체**. 두 입력을 제자리에서 변경하지 않는다.

**반환 (필수 필드)**

```json
{
  "policy_id": "POL_EXAMPLE",
  "conditions": [
    {
      "field": "age",
      "label": "나이",
      "status": "eligible",
      "reason": "신뢰 가능한 구조화 기준이 만 19~35세이며 입력한 나이는 만 25세입니다."
    },
    {
      "field": "region",
      "label": "거주 지역",
      "status": "needs_check",
      "reason": "지역 세부 범위는 공식 공고에서 확인해야 합니다."
    }
  ],
  "reception_status": "unknown"
}
```

| 반환 키 | 자료형·허용값 | 규칙 |
|---|---|---|
| `policy_id` | `str` | 입력 정책의 ID와 동일해야 함 |
| `conditions` | `list[dict]` | **비어 있으면 안 됨**. 최소 `field`, `label`, `status`, `reason` |
| `conditions[].field` | `str` | 기본 `age`, `region`, `income`, `eligibility`; 그 외는 실제 조건명(예: `housing`, `employment`) 가능 |
| `conditions[].status` | enum | `eligible` / `ineligible` / `needs_check` |
| `conditions[].reason` | `str` | 비어 있지 않은 구체적 판정/보류 사유. 자격 보증 문구 금지 |
| `reception_status` | enum | `before` / `open` / `closed` / `unknown` |

조건 트리에 아무 정보도 없거나 그래프를 찾을 수 없으면 다음 단일 항목을 반환한다.

```json
{"field": "eligibility", "label": "자격 요건", "status": "needs_check", "reason": "확인 가능한 자격 조건 구조가 없어 공식 공고 확인이 필요합니다."}
```

### 4.2 판단 원칙

1. `eligible`: **그 개별 조건**을 신뢰 가능한 구조화 데이터로 확인했을 때만. **정책 전체 신청 가능**을 의미하지 않는다.
2. `ineligible`: 신뢰할 수 있는 조건과 **명백하게 불일치**할 때만. 사용자 정보 부족은 `needs_check`다.
3. `needs_check`: 프로필 누락, 검증되지 않은 데이터, `UNKNOWN`, 의미가 불명확한 복합 조건, MVP 미지원 조건.
4. 조건 그룹은 `graph.eligibility.condition_tree`의 **AND/OR/NOT/UNKNOWN 구조 그대로** 해석한다. 트리를 단순히 납작하게 펼쳐 모든 조건을 AND로 만들거나, `NOT`을 일반 조건처럼 취급하면 안 된다.
5. `unknown_logic_groups`, `structure_issues`, `graph_status="MISSING_IN_GRAPH"`의 불확실성을 결격 사유로 처리하지 않는다.
6. `region_search`의 지역 일치로 적격을 확정하지 않는다.
7. 연령 조건이 있을 경우 `graph.policy.min_age` 등을 가정하지 말고 **조건 트리의 `type="AGE"` 조건**을 파싱한다. `value`는 숫자가 아니라 `"19~35"` 같은 문자열일 수 있다. 파싱 실패 시 `needs_check`.
8. **소득은 현재 개인/가구 구분이 없는 값**이므로 자동 부적격 판정에 사용하지 않는다. 소득 조건은 근거 수준과 비교 단위를 검증할 때까지 `needs_check`.

### 4.3 데이터 품질 경고(소희 1차 검사, 지인 2차 검사)

`policy["data_quality"]`에는 `hold_auto_eligibility`, `affected_attributes`, `warnings`, `hold_reasons`가 있다.

- `hold_auto_eligibility=True`는 **자동 판정 보류**, `ineligible`이 아니다.
- `affected_attributes=["region"]`이면 지역 관련 확정 상태(`eligible` 또는 `ineligible`)를 **`needs_check`**로 변경하고, 영향 없는 연령 상태는 유지한다.
- `affected_attributes`가 `"all"`을 포함하거나, 보류가 참인데 영향 범위를 신뢰할 수 없으면 **전체 조건 `needs_check`**.
- `graph.eligibility.unknown_logic_groups`나 `structure_issues`의 범위를 판별할 수 없다면 전체 보류.
- **파이프라인 방어:** 소희 결과에 확정 상태가 남으면 지인이 위 규칙으로 다시 검사하여 `needs_check`로 내리고, 사유에 데이터 불확실성을 덧붙인다. **보류 → 적격/부적격** 방향 자동 상향은 금지.
- 검증 자체가 불가능할 정도로 반환 JSON이 깨졌다면 임의로 전부 적격 처리하지 말고 출력 검증 오류로 취급한다.

### 4.4 신청기간 `reception_status` (MVP 기본 정책 확정)

| 근거 | 반환 |
|---|---|
| 확인 가능한 신뢰 근거와 명시된 접수일이 있고 아직 시작 전 | `before` |
| 확인 가능한 신뢰 근거와 명시된 접수일이 있고 현재 포함 | `open` |
| 확인 가능한 신뢰 근거와 명시된 접수일이 있고 이미 종료 | `closed` |
| `상시`, `상시신청`, `상시/공고참조`, `접수기관별 상이` 등의 **자동 추출 텍스트만 존재**하거나, 날짜/현재 운영 상태/근거가 불확실 | `unknown` |

- `graph.policy.application_period`는 추출 텍스트이므로 **문구만으로 `open` 확정 금지**.
- 날짜 비교가 필요하다면 한국 정책 서비스의 기준 날짜를 **Asia/Seoul**로 통일한다. 서버 시스템 시계가 곧 정책 원문 확인일은 아니다.
- 분류 불가능하면 `unknown`; 접수 상태와 개인 자격 충족은 서로 독립이다.

### 4.5 데이터 스냅샷 참고(계약 조건이 아님)

v0.2 작성자가 저장소의 v1.4.0 데이터를 직접 집계했다고 전달한 값: **정책 246건, 청크 599개, 신청 URL 보유 정책 22건**. 접수 문구 예로 `상시/공고참조` 126건, `상시신청` 38건 등이 보고되었다. **이 수치는 데이터셋 버전이 바뀌면 달라지며, 본 문서 작성 과정에서 다시 전수 집계한 것은 아니다.** 함수에서 이 수치를 상수·판정 기준으로 쓰지 않는다.

## 5. LLM 답변 함수 계약 (소희 담당)

### 5.1 시그니처

```python
# backend/llm/generator.py

def generate_answer(
    question: str,
    contexts: list[dict],
    policy_checks: list[dict],
    history: list[dict] | None = None,
) -> dict:
    """제공한 원문 근거·조건 판정을 사용해 답변과 후속 질문을 생성한다."""
    ...
```

**입력**

- `question`: 검증·공백 정리된 사용자 질문.
- `contexts`: §6의 Context Builder 결과(출처 번호 포함) **그대로**.
- `policy_checks`: §4의 **재검증을 거친** 정책별 결과 목록. `contexts`에 나타난 정책만 포함.
- `history`: `[ {"role": "user"|"assistant", "text": "..."} ]` 목록, 최대 최근 10턴. **이전 대화는 현재 정책의 사실 근거가 아니다.**

**반환 (필수 필드)**

```json
{
  "status": "answered",
  "answer": "제공된 원문에서는 해당 지원 정책의 연령 요건을 확인할 수 있습니다 [1]. 다만 지역 세부 조건은 추가 확인이 필요합니다.",
  "used_source_ids": [1],
  "follow_up_questions": [
    {"kind": "ask_info", "text": "거주 지역을 알려주세요.", "field": "region"}
  ]
}
```

| 키 | 자료형·허용값 | 규칙 |
|---|---|---|
| `status` | `answered` / `no_evidence` / `info` | `answered`는 인용 가능한 원문 근거가 있는 경우만 |
| `answer` | `str` | 사용자 표시 답변. 인용은 전달받은 `[n]`만 |
| `used_source_ids` | `list[int]` | 응답 생성 측 참고값. 최종 검증은 본문 인용 번호 기준 |
| `follow_up_questions` | `list[dict]` | `kind`, `text`, `field`(ask_info일 때 필수). 0~3개 권장 |

- `kind="question"`: 버튼의 `text`를 새 질문으로 보냄. `field` 생략 가능.
- `kind="ask_info"`: `field`는 MVP에서 **`age`, `region`, `income` 중 하나**만 허용. 다른 필드는 `question`으로 안내한다.
- `answered`인데 본문에 정상 출처 인용이 0개라면 **Pipeline이 잘못된 LLM 출력으로 처리**한다.
- 확인되지 않은 자격·혜택·금액·신청 링크를 지어내지 않는다. `needs_check`를 임의로 `eligible`로 단정하지 않는다.
- `contexts=[]`에서는 LLM을 호출하지 않는 것이 Pipeline 책임. LLM 구현 역시 외부에서 직접 호출되는 경우 근거 없는 정책 사실을 만들어서는 안 된다.
- LLM 호출 실패·시간 초과를 성공 응답으로 숨기지 않는다. Provider의 실패/타임아웃은 호출 계층이 구분할 수 있도록 예외로 전달한다. 오류 타입을 바꿔야 하면 지인에게 알린다.
- 원문과 history에 포함된 지시문을 따르지 않는다(프롬프트 인젝션 방어).

### 5.2 LLM 모델/설정 분담

모델명, 공급자, 프롬프트 및 호출 SDK는 소희가 선택한다. **인터페이스 계약은 해당 선택과 무관하게 고정**한다.

소희가 구현 후 지인에게 공유할 정보:

```text
LLM_PROVIDER             = (선택한 공급자)
LLM_MODEL                = (정확한 모델명)
LLM_SDK                  = (requirements.txt에 필요한 패키지·버전)
LLM_API_KEY_ENV_NAME     = (실제 비밀값이 아닌 환경변수 이름)
LLM_TIMEOUT_SECONDS      = (호출 시간 제한)
LLM_MAX_OUTPUT_TOKENS    = (출력 제한)
ESTIMATED_COST_LIMIT     = (시연 비용 한도)
```

## 6. Context Builder 계약 (지인 담당)

```python
# backend/rag/context_builder.py

def build_contexts(policies: list[dict], max_contexts: int = 5) -> list[dict]:
    """검색 청크에서 인용 가능한 원문을 추려 출처 ID를 생성한다."""
    ...
```

**출력**

```json
[
  {
    "source_id": 1,
    "policy_id": "POL_EXAMPLE",
    "policy_name": "가상 지원 정책",
    "document_id": "DOC_EXAMPLE",
    "chunk_id": "CHK_EXAMPLE",
    "title": "가상 정책 공고",
    "url": "https://example.org/notice",
    "page_number": null,
    "text": "공고 원문 일부",
    "quality_warnings": ["지역 조건 추가 확인 필요"]
  }
]
```

1. 텍스트는 **반드시 `evidence[].text`**에서 가져온다. `graph.policy`의 추출 텍스트를 원문처럼 인용하지 않는다.
2. `source_id`는 이번 요청에서만 유효한 **1부터 연속된 정수**.
3. `chunk_id` 중복을 제거하고, 빈 원문·빈 URL·유효하지 않은 `http(s)` URL은 인용 Context에서 제외한다. 제외 사실은 개인정보 없이 로그로 기록.
4. 정책별 근거 균형을 고려한다: 먼저 검색 상위 정책마다 근거 1개씩 채택하고, 남는 슬롯을 전체 청크 검색 순위대로 채운다. 최종 `source_id`는 선별된 Context 순서대로 붙인다. `max_contexts=5`는 정책 수가 아니라 **최대 출처 5개**다.
5. `title`: 같은 `document_id`의 Graph 문서 제목을 찾으면 사용, 없으면 `policy_name`으로 대체한다.
6. `page_number`: `page_start`가 **양의 정수**일 때만 사용, 나머지는 `null`. v0.2 보고에 따르면 현재 스냅샷의 청크에는 페이지 정보가 없으나 **영구적으로 null이라고 가정하지 않는다**.
7. `quality_warnings`는 데이터 품질 주의 메시지이지 인용 원문이 아니다. 답변에서 불확실성을 알릴 때만 참고한다.
8. 텍스트는 공백 정리 이상의 의미 변경을 하지 않는다. 원문이 길어 절단한다면 절단 사실이 드러나게 처리한다.
9. **선택한 Context에 포함된 `policy_id`만** 조건 판정·API 카드에 사용한다. `contexts=[]`이면 `no_evidence`로 바로 반환한다.

## 7. 인용 검증 및 조립 (지인 담당)

React는 답변 본문 `[1]`을 **`citations[0]`**에 대응시킨다.

1. 본문에서 정규식 `\[(\d+)\]`으로 인용 번호를 찾는다. **본문에서 실제 사용된 `[n]`이 최종 기준**이다.
2. `contexts`에 없는 번호가 하나라도 있으면 `502 LLM_OUTPUT_INVALID`로 실패 처리한다. 존재하지 않는 출처를 추측해 붙이거나 번호를 조용히 제거하지 않는다.
3. `used_source_ids`가 본문 인용과 다르면 **불일치 로그만 남기고 본문 인용을 기준**으로 한다.
4. 실제 본문 인용으로 사용된 원래 번호를 **오름차순** 정렬하여 `1..M`으로 다시 번호를 매긴다. 치환은 정규식 콜백으로 **한 번에** 수행하고 연속 `str.replace`는 사용하지 않는다.
5. 번호 `i`에 대한 `citations[i-1]`을 작성한다. 출처 필드는 `title`, `url`, `document_id`, `page_number`로 구성한다.
6. `status="answered"`이지만 인용이 0개면 `502 LLM_OUTPUT_INVALID`.
7. 동일 인용 반복은 citations 항목을 중복 생성하지 않는다.

**예시:** LLM 본문이 `[1]`, `[3]`만 사용하면 `{1:1, 3:2}`로 치환하여 최종 본문은 `[1]`, `[2]`, `citations`는 정확히 2개가 된다.

## 8. React ↔ FastAPI 계약

### 8.1 엔드포인트/실행

| 항목 | 개발 기준 |
|---|---|
| 질문 API | `POST /api/ask` |
| 상태 확인 | `GET /health` |
| Swagger | `GET /docs` |
| 백엔드 실행 | `python -m uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000` |
| 프론트 설정 | `frontend/.env.local`의 `VITE_ASK_API_URL=http://127.0.0.1:8000/api/ask` (**전체 URL**) |
| CORS | 최소 `http://127.0.0.1:5173` 허용; 추가 개발 주소는 명시적으로 추가 |
| 프론트 요청 타임아웃 | **60,000ms를 통합 목표값으로 설정**. 현재 `client.ts`는 30,000ms이므로 **민지의 실제 코드 수정 필요** |

`127.0.0.1`과 `localhost`는 브라우저에서 다른 origin일 수 있다. 시연/로컬 개발 주소를 맞춘다. **프론트 타임아웃을 60초로 바꿨다고 서버 요청 전체에 60초 중단 제한이 자동 적용되는 것은 아니므로**, 모델/DB별 타임아웃·응답 시간 측정이 필요하다.

### 8.2 요청 JSON (React 현재 타입 유지)

```json
{
  "question": "서울에 사는 25세 청년이 확인할 지원 정책이 있나요?",
  "profile": {"age": 25, "region": "서울", "income": null},
  "history": [],
  "awaiting_field": null
}
```

| 필드 | 검증 규칙 |
|---|---|
| `question` | 공백 정리 후 1~2000자; 위반 422 |
| `profile.age` | 정수 `0..120` 또는 `null` |
| `profile.region` | 문자열, `""`은 미입력. 미인식은 `None` 정규화 및 보류 안내 |
| `profile.income` | 0 이상 유한한 `number` 또는 `null`, 단위 만원 |
| `history` | `role=user/assistant`, `text` 문자열, 최근 10턴까지만 사용 |
| `awaiting_field` | `string|null`; MVP 확정 판정에 반영되는 필드는 `profile`의 `age/region/income`뿐 |

`age` 정수와 `income` 유한수 검증에서는 bool, NaN, Infinity, 문자열 자동 변환 등에 주의한다. 프론트가 후속 답변을 사용자 프로필에 반영하는지는 프론트 처리(`parseProfile` 등)와 통합 테스트에서 확인한다.

### 8.3 응답 JSON (React 현재 타입 유지)

```json
{
  "status": "answered",
  "answer": "정책 공고에 기재된 연령 범위는 만 19~35세입니다 [1]. 지역 세부 요건은 추가 확인이 필요합니다.",
  "citations": [
    {"title": "가상 지원 공고", "url": "https://example.org/notice", "document_id": "DOC_EXAMPLE", "page_number": null}
  ],
  "policy_checks": [
    {
      "policy_id": "POL_EXAMPLE",
      "policy_name": "가상 지원 정책",
      "agency": "",
      "reception_status": "unknown",
      "conditions": [
        {"field": "age", "label": "나이", "reason": "검증된 조건을 충족합니다.", "status": "eligible"},
        {"field": "region", "label": "거주 지역", "reason": "세부 지역 검증이 필요합니다.", "status": "needs_check"}
      ],
      "source_url": "https://example.org/notice",
      "official_apply_url": null
    }
  ],
  "follow_up_questions": [
    {"kind": "ask_info", "text": "거주 지역을 알려주세요.", "field": "region"}
  ],
  "checked_at": null,
  "notice": {"kind": "warn", "title": "추가 확인 필요", "text": "자동 판정을 보류한 조건이 있습니다. 공식 공고를 확인해 주세요."},
  "is_sample": false
}
```

**상태값·출처 규칙**

- `status`: `answered`, `no_evidence`, `info` 중 하나. `no_evidence`인 경우 `citations=[]`, `policy_checks=[]`.
- `citations`: 원문 Context에서 만든 실제 출처만 포함. `answer`의 `[n]`과 **배열 인덱스+1** 일치.
- `policy_checks`: **Context에 채택된 정책만** 표시. 소희 `conditions` 결과를 보수적 재검증한 후 메타데이터를 덧붙인다.
- `policy_name`: 검색 결과의 `policy_name`.
- `agency`: `graph.policy.agency` 또는 `graph.policy.organization` → `graph.organizations[]`의 실제 기관명 속성 → 없으면 `""`. **없는 기관명 생성 금지**.
- `source_url`: 해당 정책의 인용 가능한 `evidence[].source_url` 우선 → 적합한 Graph 원문 URL → 없으면 `""`. 링크가 비어 있으면 프론트는 링크 버튼을 숨겨야 한다.
- `official_apply_url`: 실제 `graph.policy.official_apply_url`에 **유효한 신청 URL**이 있을 때만. 공고 URL을 임의로 복사하지 않는다. 없으면 `null`.
- `checked_at`: **공식 원문 재확인 시간**을 검증 가능할 때만 ISO 8601 문자열. 데이터 수집시각/오늘 날짜를 임의로 넣지 않는다. MVP 기본은 `null`.
- `notice`: 하나라도 보류된 조건이 있으면 `warn`, 그렇지 않으면 `null`(또는 의미 있는 `info`).
- `is_sample`: 실제 검색·실제 LLM 응답은 `false`; **프론트 Mock 등 가상 응답만 `true`**.
- `kind="ask_info"`의 `field`는 `age/region/income`만 허용. 화면과 연결되지 않은 필드를 새로 만들지 않는다.

## 9. 오류 처리 및 시간 제한

| 상황 | HTTP | 반환/정책 |
|---|---:|---|
| 원문 기반 정상 답변 | 200 | `AskResponse.status="answered"` |
| 검색 성공, 채택 가능한 원문 없음 | 200 | `no_evidence`, `citations=[]`, `policy_checks=[]`, LLM 미호출 |
| 답변 대신 정보 안내 | 200 | `info`, 필요 시 `follow_up_questions` |
| 요청 형식·값 오류 | 422 | Pydantic/입력 검증 오류 |
| 검색 DB·Ollama 연결/설정 장애 | 503 | `SEARCH_BACKEND_UNAVAILABLE` |
| 검색 요청 시간 초과 | 504 | `SEARCH_TIMEOUT` |
| LLM 서비스 연결·호출 실패 | 502 | `LLM_UNAVAILABLE` |
| LLM 호출 시간 초과 | 504 | `LLM_TIMEOUT` |
| LLM 출력 JSON·인용 번호·스키마 오류 | 502 | `LLM_OUTPUT_INVALID` |
| 조건 함수 반환 구조 오류 | 502 | `ELIGIBILITY_OUTPUT_INVALID` |
| 예기치 않은 서버 코드 오류 | 500 | 내부 오류, 상세 노출 금지 |

오류 JSON은 정상 `AskResponse`가 아니다.

```json
{
  "detail": {
    "code": "SEARCH_BACKEND_UNAVAILABLE",
    "message": "정책 검색 서비스를 잠시 이용할 수 없습니다. 다시 시도해 주세요."
  }
}
```

- `HybridSearchInputError`는 입력 원인이면 422, `HybridSearchBackendError.kind=="timeout"`은 504, 기타 해당 백엔드 장애는 503으로 분류한다. 검색 프로필/컬렉션 설정 문제는 클라이언트 잘못이 아닐 수 있으므로 **내부 설정 오류를 422로 단정하지 않는다**.
- LLM/provider의 타임아웃·연결 오류를 구분해 전달한다. 일반적인 프로그래밍 버그를 502로 일괄 은폐하지 않는다.
- 사용자에게 공개하는 오류에는 상세 스택, DB URI, API 키, 비밀번호를 넣지 않는다. 로그에도 민감한 프로필·키를 쓰지 않는다.
- 현재 React `client.ts`는 HTTP 오류 본문을 읽지 않고 상태 코드로 안내한다. `detail.message`를 보여주고 싶다면 **민지 코드 수정**이 필요하다.
- API 60초 목표에서도 초기 Ollama 로딩+검색+LLM 시간을 실제로 측정한다. 워밍업은 **첫 요청 지연을 줄일 뿐 성공·타임아웃 보장은 아니다.** 무제한 재시도는 금지하고, 실패 시 명확한 오류를 반환한다.

## 10. 개발 시작 전/후 설정 체크

**지인**

- [ ] `dev` 최신 상태 확인 후 `feature/rag-api-integration` 브랜치 생성
- [ ] 본 문서를 `docs/api/ask_contract.md`로 추가하고 PR 리뷰
- [ ] `build_contexts()` 및 Mock 기반 `run_ask_pipeline()` 구현
- [ ] 검색 Session 수명·동시성 보호·선택적 워밍업 구현
- [ ] 품질 보류 재검증, 인용 검증/재번호, 출력 Pydantic 검증
- [ ] `POST /api/ask`, `GET /health`, CORS, Swagger 구현
- [ ] 소희가 선택한 LLM 패키지/환경변수 이름을 `requirements.txt`·`.env.example`에 반영
- [ ] Qdrant/Neo4j/Ollama 복원 및 검색 프로필 확인
- [ ] 소희 PR 병합 뒤 실제 함수 연결·React 통합 테스트

**소희**

- [ ] §4 `check_eligibility()` 구현(동기 함수, 필수 JSON 필드·enum 준수)
- [ ] `AGE` 문자열 값, `AND/OR/NOT/UNKNOWN`, `MISSING_IN_GRAPH` 처리 단위 테스트
- [ ] `hold_auto_eligibility`, `affected_attributes` 및 접수기간 `unknown` 테스트
- [ ] §5 `generate_answer()` 구현(동기 함수, 출처 번호·후속 질문 규격 준수)
- [ ] 모델명·SDK·API 키 **변수명**·타임아웃·최대 토큰·비용 한도 공유
- [ ] `feature/eligibility-llm`에서 PR. 개발 중 시그니처 변경이 필요하면 지인에게 먼저 공유

**민지**

- [ ] 기존 `AskRequest`/`AskResponse` 필드 유지 확인
- [ ] `frontend/.env.local`의 전체 API URL 설정
- [ ] 요청 제한 **60,000ms로 변경** 또는 통합 시연 전 실제 측정값 기반 조정
- [ ] `no_evidence/info/answered`, `needs_check`, `unknown`, 빈 URL/`null` 및 `notice` 표시
- [ ] `ask_info.field`를 `age/region/income` 프로필에 반영하는 흐름 확인
- [ ] API 4xx/5xx 화면 표시와 실제 `is_sample=false` 확인

**민혁**

- [ ] `dev`와 DB 인계 데이터셋/컬렉션/임베딩 모델 일치 여부 확인
- [ ] 검색 데이터 복원 절차와 지역 필터 동작 확인

## 11. 최소 통합 테스트 (T01~T18)

| 번호 | 시나리오 | 기대 결과 |
|---|---|---|
| T01 | 원문 청크 2개 + 정상 소희 함수 | 200 `answered`, 실재하는 번호만 인용 |
| T02 | `policies=[]` | 200 `no_evidence`, LLM 미호출 |
| T03 | 정책은 있지만 인용 가능한 원문 없음 | 200 `no_evidence`, 정책 카드 없음 |
| T04 | `graph_status="MISSING_IN_GRAPH"` | 자격 상태 `needs_check` |
| T05 | `hold_auto_eligibility=True`, `affected_attributes=["region"]` | 지역만 `needs_check`, 영향 없는 나이 유지 |
| T06 | 품질 보류 범위 `all`/불명확 | 전체 조건 `needs_check` |
| T07 | `UNKNOWN` 조건 그룹/구조 문제 | 영향 범위 모르면 전체 보류 |
| T08 | 소희 함수가 보류 대상에 `ineligible`/`eligible` 반환 | Pipeline이 `needs_check`로 조정 |
| T09 | LLM이 없는 번호 `[9]` 인용 | 502 `LLM_OUTPUT_INVALID` |
| T10 | LLM이 `[1]`, `[3]`만 인용 | 최종 `[1]`, `[2]`; citations 2개 |
| T11 | `used_source_ids`와 본문 인용 불일치 | 본문 기준 응답 + 로그 |
| T12 | `answered`인데 인용 없음 | 502 `LLM_OUTPUT_INVALID` |
| T13 | 지역 `""` vs `"서울"` | 필터 없음 vs `"서울특별시"` 검색 필터 |
| T14 | DB 연결/인증 실패 | 503; 근거 없음으로 위장하지 않음 |
| T15 | LLM 타임아웃 | 504; 가짜 답변 없음 |
| T16 | 신청기간이 `상시/공고참조` 또는 `상시신청` 추출 텍스트뿐 | `unknown` |
| T17 | `awaiting_field="child_age"` | 프로필에 없는 필드로 자동 자격 판정하지 않음 |
| T18 | 선택된 Context 정책 2건, 검색 정책 5건 | 조건 검사·정책 카드는 Context의 2건에 한정 |

테스트는 **Mock/픽스처 단위 → 소희 실제 함수 → 실제 검색 데이터 → React 브라우저** 순서로 한다. 테스트가 통과하기 전에는 구현 완료라고 표시하지 않는다.

## 12. 변경 관리 및 미확정 운영값

- **인터페이스는 본 v1.0을 초기 구현 기준으로 사용한다.** 소희·민지는 승인 응답을 기다리느라 개발을 멈출 필요 없이 본 규격으로 작업한다.
- 소희 구현 중 **입력/출력 필드, 상태값, 예외 처리**를 바꿔야 하면 변경 내용을 공유해 `ask_contract.md`와 Mock·Pydantic·React 타입·테스트를 같은 변경에 맞춘다.
- **미정이지만 인터페이스를 막지 않는 값:** LLM 공급자·모델, 실제 API 키 환경변수 이름, 소희 내부 프롬프트, 시연 비용 한도, 성능에 따른 검색/LLM 타임아웃 세부값. 이는 구현 담당자가 선택하여 공유한다.
- **이미 정한 MVP 기본 정책:** `상시신청` 단독은 `unknown`, `income`은 개인/가구 구분 없으면 자동 확정 금지, `ask_info.field`는 3가지, 정책 카드는 Context에 채택된 정책만, 본문 인용 기준 재번호, 60초 프론트 요청 제한은 실제 코드에 반영해야 할 목표.
- **팀 확인이 필요한 코드 변경:** 프론트 타임아웃, 추가 입력 UX, 공통 `requirements.txt`/`.env.example`, PR 병합. 현재 팀원이 이미 승인했다고 가정하지 않는다.

### 참조 코드

- [Hybrid 검색 구현 (`dev`)](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/blob/dev/database/hybrid_search.py)
- [지역 검색/약칭 (`dev`)](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/blob/dev/database/region_search.py)
- [React 요청·응답 타입 (기능 브랜치)](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/blob/feature/15-react-demo-ui/frontend/src/api/types.ts)
- [React API 클라이언트 (기능 브랜치)](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/blob/feature/15-react-demo-ui/frontend/src/api/client.ts)

> **MVP 성공 기준:** 근거 없는 적격/부적격 판정 없음 · 실제 원문 출처만 표시 · React와 API 스키마 일치 · 검색/LLM 오류 분리 · 시연 전 통합 테스트 완료.
