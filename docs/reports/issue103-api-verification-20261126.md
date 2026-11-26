# Issue #103 — 신청 URL 역할 검증 및 수정 (2026-11-26 KST)

판정: **BLOCKED — 최종 R3D·실제 LLM 검증 미완료**. 코드 수정과 아래 Mock/파일 기반 회귀 검증은 완료했다. 실제 DB 검색·B2 청크·답변 의미 검증을 완료한 것으로 해석하면 안 된다.

## A. 시작 환경

- 최초 디렉터리: `<workspace>` (Git 저장소 아님).
- 작업 worktree: `issue103-sejong-verify`, 브랜치 `fix/103-sejong-apply-url-guidance`.
- 기준 `origin/dev`: `a2ff03001c11738475bdf1ab1b9b4886f9ea52e6`. Git fetch는 DNS 제약으로 실패했지만 GitHub 연결 도구의 `/branches/dev` 조회로 동일한 최신 SHA를 확인했다.
- GitHub 확인: #103 Open, PR #125·130·131·132 Merged, #133 Open. #133 HEAD `a59223cd27ef565da9c950dfc9f0854dc762b906`, base `a2ff030`.
- 기존 dev worktree `ssok-ui-analysis` (`d414dfe`)의 `.review-venv/`, UI 검토 문서와 `issue78-api-verify`의 schemas/pipeline 미커밋 변경 및 검증 파일을 보존했다. 기존 `issue78-api-baseline`도 보존했다. 로컬에 #39/#76/#103 브랜치·worktree는 없었으며 외부 작업은 변경하지 않았다.
- 독립 검증 worktree `issue103-dev-baseline`(깨끗한 dev), `issue103-pr133-compat`(dev + #133 패치 + #103 가상 통합)을 추가했다. 커밋·브랜치 재설정·실제 Merge를 하지 않았다.
- 설치 없이 기존 `.review-venv`와 frontend `node_modules`를 재사용했다. 새 worktree의 node_modules junction만 만들었다. 기존 환경은 변경하지 않았다.

### 동결 데이터와 실행 DB

| 항목 | 직접 확인한 값 | 한계 |
|---|---|---|
| 릴리스 | `1.4.0-candidate` | manifest 상태는 `CANDIDATE / PENDING_DB_REVIEW` |
| manifest SHA-256 (raw/LF 동일) | `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` | 파일 버전이며 DB 적재 버전 증명 아님 |
| 정책 / 노드 / 관계 / 원본 청크 | 249 / 1,147 / 2,702 / 672 | 실제 파일을 읽어 집계. B2/B3 포인트 수 아님 |
| 실행 컨테이너 | `issue105-r3d-verify-qdrant-1`, `issue105-r3d-verify-neo4j-1` | 읽기 전용 `docker ps` 확인 |
| 실제 포트 매핑 | `127.0.0.1:56756 → 6333`, `127.0.0.1:56787 → 7687` | 앱 연결 성공으로 해석하지 않음 |
| Qdrant GET `/collections` | HTTP 401 | 정상 환경변수 인증값 미설정. 추출·복호화 안 함 |
| 실제 Neo4j 노드·관계 / B2·B3 수 | BLOCKED | 인증된 DB 조회 미실행 |
| 현재 복원 번들 버전 | BLOCKED | PR #130의 `rag-final-v140-r3-57d90f23308a`는 과거 기록이며 이번 실행에서 검증하지 못함 |
| 실제 LLM | 0회, 비용 0 | 키·SDK 없음. 호출 전 확인했고 개인정보·비밀값 출력 안 함 |

`data/`·`database/` 변경 없음. DB 쓰기·재적재·재임베딩·manifest 재생성·컨테이너/볼륨 변경 없음.

## B. 기능별 결과

| 기능 | 기존 상태 | 이번 결과 | 수정 필요 여부 |
|---|---|---|---|
| React K-패스 검색 | #125 구현·검증 완료 | 기존 여섯 표기 및 정상 동작 PASS | 없음 |
| FastAPI 목록 검색 | 공백·대소문자 정규화만 구현 | dev에서 K패스/k패스 누락 재현 → 여섯 표기 HTTP 200 PASS | 붙임표 비교 정규화 수정 |
| RAG K-패스 검색 | #130의 과거 실제 6건 PASS | 현재 실제 검색 BLOCKED | 재구현 안 함 |
| FAQ 문서·출처 | #130 문서 단위 검증 | 릴리스 동일 체계 정책/문서/청크·citation 연결 PASS; 실제 B2 BLOCKED | 공개 필드 추가 안 함 |
| 세종 신청 URL | null 유지하나 본문 오안내 | 과거 문구 Mock 재현 → 정책별 보정 PASS | 프롬프트·Pipeline 방어 |
| 세종 모집 상태 | unknown, 원문 일정 미확인 | 실제 Checker 경유 unknown 유지; 상시 단정 보정 PASS | 기존 OPEN_CLAIM 최소 보완 |
| 공식 홈페이지·일반 원문 링크 | 표시 가능 | 정상 안내·출처 유지 PASS | 숨기지 않음 |
| React 신청 버튼 | null 주소 숨김 구현 | 기존 SSR 테스트 PASS | 화면 코드 변경 없음 |
| contacts·판정·citation 번호 | 기존 구현 | 관련 기존 테스트 PASS | 계약 변경 없음 |
| exclusion_reasons | 현재 dev는 자리 예약/null | 스키마·기존 동작 유지 | #78 연동 작업을 포함하지 않음 |
| official_requirements | 현재 dev는 자리 예약/null | #133 가상 통합 44개 PASS | #76 구현은 본 브랜치에 포함 안 함 |
| child_age_months | 현재 dev 정식 입력 필드 없음 | 정식 연동 검증 SKIP | #39를 이번에 구현하지 않음 |

## C. 세종 URL 오안내 분석

### 실제 근거와 원인

PR #130 JSON의 실제 과거 응답: “신청 URL은 세종시 홈페이지로 제시되어 있습니다[1].” 같은 응답의 `official_apply_url`은 null, `reception_status`는 unknown이다.

동결 릴리스 정책 `POL_YC_065`에는 null 신청 주소와 “위원회별 청년위원 모집 일정은 공식 공고 확인 필요”가 있다. 반면 `DOC_YC_065_C01` 원문에는 `[신청 URL] https://www.sejong.go.kr`가 남아 있다. source URL은 온통청년 정책 상세 원문이다. 실제 실행 DB의 동일 값 여부는 인증 부재로 확인하지 못했다.

경로를 추적한 결과:

1. Context Builder는 원문을 그대로 보존하며 내부 `policy_id/document_id/chunk_id/url/source_id`를 유지한다.
2. Pipeline은 같은 정책의 구조화 `official_apply_url=null`을 이미 Generator에 전달한다. 누락 문제가 아니다.
3. Generator의 `_validated_contexts`는 모델 입력을 원문 텍스트·정책 ID/이름·source_id 중심으로 정리한다. 기존 프롬프트에 원문의 '신청 URL' 라벨과 null 필드가 충돌할 때의 우선 규칙이 없었다.
4. 기존 Pipeline 보정은 접수·자격 단정만 검사했다. URL 역할 오해는 통과했고 “상시 신청 가능합니다”도 기존 OPEN_CLAIM에서 누락됐다.
5. FastAPI는 null을 유지했다. React는 확인된 주소·open 일정이 모두 있어야 신청 버튼을 표시한다. 버튼에서 원문 URL을 신청 주소로 승격한 오류는 확인되지 않았다.

수정은 데이터 자체가 아니라 이 충돌을 해석하는 프롬프트와 출력 검증에 적용했다. `ai/prompts/system_prompt.py`는 소희 담당 영역과 겹치는 최소 변경이며 검토가 필요하다. Generator 함수 시그니처·Checker·공개 API 스키마·citations 번호 체계는 변경하지 않았다.

### 질문별 재현과 수정 후 결과

아래는 **실제 릴리스 + Mock 검색 + 실제 Checker/Generator 코드 + Mock SDK** 결과다. 새로운 실제 LLM 답변이 아니다. 단계별 입력·원문·정책 체크·Mock LLM 출력·최종 HTTP JSON은 동명의 JSON 보고서에 저장했다.

| 질문 | Mock 원본 답변 | 수정 후 | HTTP |
|---|---|---|---|
| 세종 청년위원회 신청 방법을 알려줘. | 홈페이지에서 신청할 수 있어요 | 전용 주소 없음, 방법·일정은 공식 공고 확인 | 200 |
| 세종 청년위원회 지금 신청할 수 있어? | 현재 신청할 수 있어요 | 현재 접수 여부는 공식 공고 확인 필요 | 200 |
| 세종 청년위원회 신청 링크 알려줘. | 신청 링크: 세종시 홈페이지 | 전용 주소 없음, 공식 공고 확인 | 200 |
| 세종 청년위원회 모집 기간은 언제야? | 상시 신청 가능합니다 | 현재 접수 여부는 공식 공고 확인 필요 | 200 |
| 세종 청년위원회 관련 공식 홈페이지 알려줘. | 공식 홈페이지는 세종시 홈페이지 | 해당 정상 안내 그대로 유지 | 200 |

모두 정책 ID `POL_YC_065`, 문서 `DOC_YC_065`, 내부 릴리스 청크 `DOC_YC_065_C01`, 신청 URL null, 접수 unknown을 유지했다. 공개 citation에 chunk_id를 추가하지 않았다. API 계약상 citation은 문서/URL/페이지를 제공하며 내부 청크 추적과 다르다.

### 안전 범위

`align_application_links`는 구조화 신청 주소와 정책 ID를 기준으로 판단한다. 문장 패턴은 웹 신청 주장 검출을 위한 보조 수단이다. 정상 원문 링크·일반 홈페이지·방문 신청 안내를 유지하며, 정책 A의 null 주소를 정책 B의 주소로 채우지 않는다. 정책별 분리 가능한 절에서 B의 정상 신청 문장과 출처 번호를 보존한다. 명확한 A 이름에 B 출처를 붙인 경우 B 번호를 빌리지 않는다. 한 절에서 여러 정책을 섞어 대상을 안전하게 확정할 수 없으면 기존 `LLM_OUTPUT_INVALID`/502로 거절한다.

자유 생성 문장의 모든 한국어 표현을 의미적으로 증명하는 검증기는 아니다. 실제 LLM 표현 다양성·혼합 FAQ 내용·과거 FAQ의 현재 사실 오해 여부는 실제 R3D/LLM 후속에서 확인해야 한다.

## D. K-패스 검색 결과

대상 이름은 동결 데이터의 `모두의 카드(K-패스) (대중교통비 환급지원)`를 유지했다. 새 별칭·임베딩 변경 없음.

| 검색 표기 | 검색 경로 | policy_id | 결과 |
|---|---|---|---|
| K-패스 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / 200 PASS / BLOCKED |
| K패스 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / dev 누락 수정 후 200 PASS / BLOCKED |
| k패스 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / dev 누락 수정 후 200 PASS / BLOCKED |
| k-패스 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / 200 PASS / BLOCKED |
| 모두의 카드 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / 200 PASS / BLOCKED |
| 모두의카드 | React / 목록 HTTP / RAG | POL_STG_MOLIT_001 | PASS / 200 PASS / BLOCKED |

K 계열 목록 결과는 해당 정책 1건이다. '모두의 카드' 계열은 기존처럼 POL_BOK_075도 함께 찾는다. 이것은 기존 부분 문자열 검색 동작이며 이번 변경으로 생긴 혼입이 아니다. 목록 데이터의 연결 출처와 RAG가 실제 채택한 문서는 구분해야 한다. 후자는 이번 실행에서 BLOCKED이며 #130의 과거 6건 PASS만 재사용했다.

## E. FAQ 출처 검증

| FAQ | policy_id | document_id | 실제 B2 chunk_id | 결과 |
|---|---|---|---|---|
| 모두의 카드 한시 상향 혜택 기간 | POL_STG_MOLIT_001 | DOC_STG_MOLIT_001_FAQ_2026 | BLOCKED | 릴리스 동일 체계 추적 PASS; 실제 검색/답변 최신성 BLOCKED |
| 2011년 이전 중소기업 취업자 감면 | POL_STG_NTS_001 | DOC_STG_NTS_001_FAQ_2026 | BLOCKED | 릴리스 동일 체계 추적 PASS; 실제 답변 자격 단정 검증 BLOCKED |
| 서울시 청년수당 사업 공고 확인 | POL_STG_SEOUL_001 | DOC_STG_SEOUL_001_FAQ_2026 | BLOCKED | 릴리스 동일 체계 추적 PASS; 실제 검색/근거 혼합 검증 BLOCKED |

테스트는 같은 릴리스의 실제 청크 목록과 Pipeline에 채택된 청크를 비교한다. 실제 B2 ID를 원본 릴리스 ID와 문자열로 비교하지 않았다. 모든 릴리스 chunk_id 및 원문 URL은 JSON에 기록했다. 실제 B2 값은 가져오지 못했으므로 null이다. Mock 검색 결과를 지정해 문서·정책·citation 전달을 확인한 것이며 질의별 검색 정확도 시험으로 과장하지 않는다.

## F. 테스트 결과

| 테스트 | 상태 | 건수 | 근거 |
|---|---|---|---|
| #103 신규 회귀 | PASS | unittest 11개 (다수 subTest 포함) | 과거 오안내 재현, 질문 5종, 문구 변형, 정상 링크 보존, 다중 정책, ID 추적, 목록 6표기, FAQ 3문서 |
| 기존 Pipeline/API/Generator/Context/공통 계약 | PASS + BLOCKED | 125개 중 124 PASS, 1 환경 오류 | 깨끗한 dev와 수정본에서 동일한 `bs4` 누락 오류 |
| 위 기존 테스트 + #103 | PASS + BLOCKED | 136개 중 135 PASS, 1 환경 오류 | 신규 실패 0; 전체 성공이라고 기록하지 않음 |
| #133 가상 통합 | PASS | 55개 (#76 44 + #103 11) | 별도 worktree에서 패치 결합 후 재실행 |
| React 검색·TurnView·신청 정보 SSR | PASS | 49개 | 관련 테스트 3파일, null 신청 버튼·출처 보존 포함 |
| React 전체 실행 | PASS + BLOCKED | 186 PASS + 미수행 DOM 파일 1개 | `jsdom` 미설치로 unhandled error 1. 전체 PASS 아님 |
| TypeScript | PASS | 1회 | `tsc --noEmit` exit 0 |
| 실제 R3D·B2/B3 정합성 | BLOCKED | 미실행 | Qdrant 401, 정상 인증 환경 없음 |
| 실제 LLM | BLOCKED | 0회 | 키·openai SDK 없음 |
| 전체 pytest suite | BLOCKED | 미실행 | pytest 없음. 설치하지 않음 |
| 실제 브라우저 조작 | SKIP | 0회 | 이번에는 기존 SSR 테스트로 버튼 분기만 확인 |
| child_age_months 정식 연동 | SKIP | 0회 | #39 범위, dev 필드 없음 |
| diff 공백·데이터 변경 검사 | PASS | 1회 | `git diff --check`, `git diff --exit-code HEAD -- data database` |

환경 오류 테스트: `test_rag_pipeline.PipelineTests.test_regenerated_doc_gov_048_chunks_reach_api_response_intact`. dev와 수정본 모두 `collectors.preprocessing.normalize`의 `bs4` import에서 실패했다. 해당 입력을 실행했다고 보고하지 않는다. 전체 React 실행에서도 개별 성공 186개와 `jsdom` 오류를 분리했다.

## G. 변경 파일

| 파일 | 수정 내용 | 수정 이유 |
|---|---|---|
| backend/rag/application_links.py | 정책별 신청 링크 주장 검증, 정상 안내 유지, 모호한 다중 정책 거절 | null 주소를 일반 홈페이지로 승격하지 않음 |
| backend/rag/pipeline.py | URL 검증 연결, 상시 신청 단정 보완, 안내 로그·notice | 기존 접수/자격 보정이 놓친 출력 방어 |
| ai/prompts/system_prompt.py | 같은 정책의 확인된 신청 주소와 출처 구분 | 원문 라벨과 null 메타데이터 충돌 처리 |
| backend/api/policy_catalog.py | React와 같은 공백·붙임표·대소문자 정규화 | dev의 K패스/k패스 목록 누락 수정 |
| tests/integration/test_issue103_guidance.py | 11개 회귀 테스트 | 오안내·오탐·정상 링크·출처·목록 검증 |
| tests/manual/verify_issue103_offline.py | Mock/릴리스 단계별 증거 저장 | 실제 R3D와 구분되는 재현 도구 |
| docs/reports/issue103-api-verification-20261126.json | 실제 실행한 로컬 검증 증거 | 원문·내부 청크·Mock 원본·최종 응답 추적 |
| docs/reports/issue103-api-verification-20261126.md | 본 보고서 | A~J 결과와 미검증 사항 기록 |

## H. 다른 담당자에게 전달할 사항

- 반석: 원문 청크에 세종 홈페이지가 신청 URL로 남아 있고 구조화 null과 충돌한다. 동결 결정에 따라 데이터는 수정하지 않았다. 과거 FAQ의 현재 적용 여부는 원문 날짜와 구분해 검토해야 한다.
- 민혁: 실행 포트는 확인했으나 정상 인증 환경이 없어 B2/B3 수량·실제 청크·Neo4j 정합성·번들 버전은 BLOCKED다. 인증값 추출 없이 정상 설정된 환경에서 읽기 전용 검증이 필요하다.
- 소희: 시스템 프롬프트에 신청 주소 우선 규칙을 최소 추가했다. Checker·Generator 시그니처는 그대로다. 실제 LLM에서 프롬프트 효과와 후처리 오탐/누락을 확인해야 한다.
- 민지: React 코드는 변경하지 않았다. null 주소 비노출과 출처 유지 SSR은 통과했다. 실제 브라우저와 jsdom DOM 테스트는 미완료다.
- 지인: 실제 R3D 환경에서 K-패스 6표기·FAQ 3건·세종 5종을 재검증해야 한다. 공개 citation에 chunk_id 추가는 필요하지 않았고 내부 검색 결과로 추적할 수 있다.

팀원에게 메시지를 전송하지 않았으며 위 내용은 전달용 기록이다.

## I. PR 준비 판정

**BLOCKED (최종 완료 승인 기준).** 로컬 Mock/파일 기반 수정은 검토 가능하지만 실제 R3D·실제 LLM·B2 출처 검증은 남았다. 데이터 변경은 필요하지 않았고 신규 회귀 실패는 확인되지 않았다. 위 BLOCKED 항목을 PASS로 바꾸거나 #103을 Close하지 않았다.

PR #133은 본 브랜치에 포함하지 않았다. 별도 `issue103-pr133-compat`에 GitHub에서 읽은 #133 패치를 적용하고 #103 패치를 가상 결합했다. 겹치는 파일은 `backend/rag/pipeline.py`이며 인접 import hunk에서 텍스트 충돌이 있었다. 두 import를 유지하는 최소 수동 결합으로 해결했고 #133의 생성 후 official_requirements 연결은 보존했다. 55개 테스트 통과. 실제 Merge나 커밋은 없었다. 권장 순서는 #133 리뷰·병합 후 #103을 새 dev 기준으로 갱신하고 같은 테스트 재실행이다. 강제 병합 순서는 아니다.

## J. Git 상태와 재실행

- 현재 브랜치: `fix/103-sejong-apply-url-guidance`.
- HEAD와 dev 기준 SHA는 동일, 새 커밋 없음. 변경 8개 파일은 모두 미커밋·미스테이징 상태.
- dev 직접 수정 없음. PR #133·#39·#76·기존 #78 작업 변경 없음.
- Commit / Push / PR 생성 / Merge / Issue Close 실행 안 함.

로컬 재현(PowerShell, 준비된 의존성 환경 사용):

```powershell
Set-Location '<workspace>\issue103-sejong-verify'
$issue103Python = '..\ssok-ui-analysis\.review-venv\Scripts\python.exe'
& $issue103Python -m unittest discover -s tests/integration -p test_issue103_guidance.py -v
& $issue103Python tests/manual/verify_issue103_offline.py
Push-Location frontend
node node_modules/vitest/vitest.mjs run src/lib/policySearch.test.ts src/components/TurnView.test.tsx src/components/PolicyApplicationInfo.test.tsx
node node_modules/typescript/bin/tsc --noEmit
Pop-Location
```

아래는 **사용자가 최종 승인한 뒤 직접 실행할 명령 예시**다. 이번 작업에서 실행하지 않았다. 최종 R3D 검증이 끝나기 전에는 검증 완료·Issue Close 문구를 PR에 넣지 않는다.

```powershell
Set-Location '<workspace>\issue103-sejong-verify'
git status --short --branch
git diff --check
$issue103Files = @(
  'backend/rag/application_links.py',
  'backend/rag/pipeline.py',
  'backend/api/policy_catalog.py',
  'ai/prompts/system_prompt.py',
  'tests/integration/test_issue103_guidance.py',
  'tests/manual/verify_issue103_offline.py',
  'docs/reports/issue103-api-verification-20261126.json',
  'docs/reports/issue103-api-verification-20261126.md'
)
git add -- $issue103Files
git diff --cached --stat
git commit -m 'fix(#103): distinguish application URLs from source homepages'
git push -u origin fix/103-sejong-apply-url-guidance
gh pr create --draft --base dev --head fix/103-sejong-apply-url-guidance --title 'fix(#103): 세종 신청 URL 오안내 방어 및 목록 검색 보완' --body '구조화 신청 주소가 null인 정책에서 기관 홈페이지를 신청 페이지로 안내하는 출력을 정책별로 검증합니다. K패스 목록 API 검색 누락도 보완합니다. Mock 회귀와 #133 가상 통합은 통과했으며 실제 R3D·LLM 검증은 BLOCKED입니다. 상세 근거: docs/reports/issue103-api-verification-20261126.md. 관련 이슈 #103.'
```


## 실제 R3D·LLM 검증 재개 확인 — 2026-11-26T12:15:29.432017+09:00

**최종 판정: BLOCKED.** 기존 결과와 파일을 보존하고 최신 요청에 따라 다시 확인했다. 새 기능·코드·데이터 변경 없이 이 보고서와 대응 JSON의 `resume_checks`에 이번 결과를 추가했다.

### R3D 인증·실제 연결

Runbook §3·§5.7.4·§5.10.14·§5.12를 재확인했다. 현재 PowerShell Process에서 `ISSUE105_R3D_VERIFY_QDRANT_API_KEY`, `ISSUE105_R3D_VERIFY_NEO4J_PASSWORD`, `OPENAI_API_KEY`는 **모두 미설정**이었다. 출력·기록은 변수 이름과 존재 여부만 포함한다. 인증값 추출·복호화·`.env` 조회/수정·Docker 환경값 조회는 하지 않았다.

따라서 실제 인증 연결 시도 0회. Qdrant/Neo4j 연결, 실행 DB 정책·문서·청크·포인트 수량, B2/B3 포인트 수, Neo4j 노드·관계 수, DB↔manifest 및 현재 복원 번들 일치, FAQ 3건 실제 B2 근거는 모두 **BLOCKED**다. 이번에 실제 R3D에서 확인한 정책 ID는 없다. 이전 HTTP 401을 이번에 새로 재현한 결과로 기록하지 않았다.

### 실행 환경·LLM

기존 `../ssok-ui-analysis/.review-venv/Scripts/python.exe`는 FastAPI/httpx/Neo4j/Qdrant/dotenv를 사용할 수 있었다. `openai` SDK와 pytest는 없고 이 worktree의 `.venv`도 없다. 가상환경 생성·의존성 설치/변경은 하지 않았다.

실제 OpenAI 호출 **0회**, 비용 **0**, 실제 LLM 원본과 Pipeline 후처리 응답은 모두 미생성(null). 세종 URL·홈페이지 참고 안내·모집 미확인·K-패스 상담·다른 정책 URL 보존·정책 간 혼합을 실제 LLM에서 확인하는 항목은 **BLOCKED**다. 기존 Mock 또는 브라우저 결과로 대체하지 않았다.

### 기준선·manifest

- GitHub 최신 dev: `a2ff03001c11738475bdf1ab1b9b4886f9ea52e6`.
- #133 Open, HEAD `a59223cd27ef565da9c950dfc9f0854dc762b906`; 기존 가상 통합 기준과 동일.
- 로컬 릴리스 `1.4.0-candidate`, 정책 파일 249행.
- manifest SHA-256: `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da`.
- 이 값은 파일 기준선이다. 현재 DB 적재 버전 또는 복원 번들과 일치한다고 단정하지 않는다.

### #133 가상 통합 회귀

기존 `issue103-pr133-compat`에서 #103 방어 파일과 회귀 테스트가 현재 브랜치와 바이트 단위로 같음을 확인했다. 최신 SHA가 같아 패치 재적용이나 Merge는 하지 않았다. 기존 worktree 파일 변경 없이 관련 테스트 **55 PASS, FAIL 0, 신규 회귀 0**을 재확인했다. 실행 전후 기존 소스 파일 SHA-256 비교의 변경 목록은 `[]`다. Python bytecode 생성도 비활성화했다.

릴리스/Mock 테스트에서 확인한 정책 ID: `POL_YC_065`, `POL_STG_MOLIT_001`, `POL_STG_NTS_001`, `POL_STG_SEOUL_001`. 실제 DB에서 확인한 ID와 구분한다. B2 ID와 릴리스 ID를 단순 비교하지 않았다.

재현 명령(기존 통합 worktree에서 실행, 실제 DB·LLM 호출 없음):

```powershell
Set-Location '<workspace>\issue103-pr133-compat'
$env:PYTHONDONTWRITEBYTECODE = '1'
& '..\ssok-ui-analysis\.review-venv\Scripts\python.exe' -B -c "import sys,unittest; sys.path[:0]=['tests/integration','tests/unit']; names=['test_issue103_guidance','test_official_requirements','test_official_requirements_api']; r=unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)); sys.exit(not r.wasSuccessful())"
```

### 재개 조건과 원인 확인

세 인증변수를 정상적으로 설정한 동일 프로세스에서 재개해야 한다. 다른 셸의 Process 환경변수는 현재 프로세스로 자동 전달되지 않으므로, 별도 셸에서만 설정했다면 계속 미설정으로 보일 수 있다(가능한 원인이며 사용자 설정 이력을 확인한 것은 아님). 기존 R3D verify 인증값을 사용하고 새 인증값 생성·DB 복원·인증 우회는 하지 않는다. 값은 채팅에 보내지 않는다.

직접 설정 절차는 `issue103-final-readiness-20261126.md` §1에 있다. 준비된 기존 가상환경에서 openai SDK와 검색 임베딩 실행 환경도 확인해야 한다. 그 뒤 실제 수량/manifest/B2 출처와 실제 LLM 원본→후처리를 검증해야 최종 PASS 판정이 가능하다.

기존 브라우저 PASS는 보존했으며 이번에 재실행하지 않았다. 데이터·DB·다른 worktree의 기존 파일·`.env`를 변경하지 않았고 Commit·Push·PR·Merge·Issue Close를 실행하지 않았다.


## 외부 실행 증거 수신 — 2026-11-26 14:21 KST

사용자가 인증값을 설정한 별도 PowerShell에서 실행한 실제 JSON 두 개를 읽어 검토했다. 기존 결과를 보존하며 이번 관측을 추가한다. 외부 결과를 Codex 프로세스 환경변수 누락으로 실패 처리하지 않는다.

| 항목 | 결과 | 실제 실행 여부 |
|---|---|---|
| 외부 사전 검사 | PASS | 인증변수 세 개 존재, 누락 패키지 없음, Git 오류 없음 |
| Qdrant 첫 조회 | BLOCKED | get_collections 단계에서 UnexpectedResponse 발생 |
| Neo4j 인증 | BLOCKED | 앞 단계 중단으로 미실행 |
| DB 수량·manifest·B2 출처 | BLOCKED | 미실행 |
| 실제 OpenAI | BLOCKED | 호출 0회, 원본·후처리 응답 미생성 |
| 진단 보완 후 로컬 통합 회귀 | PASS | 79개 + subtests 153개, 실제 DB·LLM 미호출 |

외부 실행 기준은 dev `5b250ade`이며 제품 소스 8개 파일의 SHA-256이 현재 최신 통합 worktree와 모두 일치했다. 외부 사전 검사 PASS는 '변수가 있고 모듈을 import할 수 있음'을 뜻한다. 인증값이 서버에서 수락됐음을 의미하지는 않는다.

원본 결과: `../issue103-latest-dev-verify-20261126/.cache/issue103-preflight-20261126-142102-640.json`, `.../.cache/issue103-r3d-llm-20261126-142102-640.json`. 파일 해시와 실행 시각을 대응 JSON의 `external_execution_reviews`에 기록했다. 원본 파일을 수정하지 않았다.

### 원인 분석과 진단 보완

기존 결과는 `checks=[]`, `UnexpectedResponse`만 남겼다. 코드 실행 순서상 첫 Qdrant 조회에서 중단됐지만 HTTP 상태 번호가 없어 **401이라고 확정할 수 없다**. 서버 인증 거절·권한/주소 문제·중간 HTTP 응답 등은 가능한 원인일 뿐 아직 판정되지 않았다.

검증기를 최소 보완했다:

- 예외의 숫자 HTTP 상태(100~599)와 실행 단계를 기록한다. 응답 본문·헤더·예외 메시지는 출력하거나 저장하지 않는다.
- `--connections-only`: DB 인증만 확인하고 기준선 스캔·Ollama·LLM을 실행하지 않는다. `--llm`과 함께 쓰면 실행 전에 거절한다.
- Qdrant의 고정 loopback 주소는 `trust_env=False`로 직접 연결한다. 외부 HTTP 프록시로 전달되는 가능성을 제거한 것이며 프록시가 원인이라고 확인한 것은 아니다. DB 인증과 서버 설정은 그대로다.
- HTTP 상태 마스킹 및 접속 전용 모드의 비용/실행 경계 시험을 추가했다. 최신 #103/#133 회귀는 **79 PASS**, 신규 제품 실패 0이다.

### 다음 실행 (인증값이 있는 같은 PowerShell)

```powershell
$issue103Root = '<workspace>\issue103-latest-dev-verify-20261126'
$issue103Python = '<repo>\.venv\Scripts\python.exe'
Set-Location $issue103Root
$issue103Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$issue103Connection = ".cache\issue103-connection-$issue103Stamp.json"
& $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --connections-only --out $issue103Connection
$issue103ConnectionExit = $LASTEXITCODE
Write-Output "접속 진단 결과: $(Join-Path $issue103Root $issue103Connection)"
if ($issue103ConnectionExit -eq 0) {
    $issue103Result = ".cache\issue103-r3d-llm-$issue103Stamp.json"
    & $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --llm --max-llm-calls 11 --out $issue103Result
    Write-Output "전체 검증 결과: $(Join-Path $issue103Root $issue103Result)"
}
```

실패하면 생성된 접속 진단 JSON만 전달하면 된다. `http_status=401`이면 서버가 해당 인증을 거절한 것이므로 기존 R3D verify 전용 인증값의 정상 전달 경로를 확인해야 한다. 403/404/5xx이면 숫자와 단계로 별도 분석한다. 키를 채팅에 보내거나 파일에 기록하지 않는다. 인증 우회·DB/컨테이너 설정 변경·복원·재적재는 하지 않는다.

현재 최종 판정 **BLOCKED**, PR 생성 가능 여부 **NO**. 이는 외부 사전 검사 실패가 아니라 실제 DB 조회 단계가 완료되지 않았기 때문이다. 기존 목록·브라우저 PASS 및 통합 회귀 결과는 유지한다. 데이터·DB·.env·기존 외부 증거 수정과 Commit·Push·PR·Merge·Issue Close는 실행하지 않았다.


## 외부 접속 진단 확정 — 2026-11-26 14:29 KST

증거 `issue103-connection-20261126-142901-769.json`을 확인했다. 외부 사전 검사는 PASS이며 세 인증변수와 필요한 모듈이 존재한다. 고정 loopback 직접 연결(`trust_env=False`)에서 `execution_stage=qdrant_authentication`, **HTTP 401**, `kind=auth`가 기록됐다.

따라서 현재 장애는 'Codex 환경변수 없음'이 아니라 **외부 프로세스가 제공한 Qdrant 인증을 서버가 수락하지 않은 것**이다. 실제 값이 잘못됐는지, 다른 환경의 키인지, 컨테이너의 현재 인증과 달라졌는지는 값을 추출/대조하지 않아 확정하지 않았다. 일반 프록시 환경변수를 통한 우회 경로는 사용하지 않았다.

| 검증 | 판정 | 근거 |
|---|---|---|
| 외부 환경·의존성 | PASS | missing 항목 없음 |
| 실제 Qdrant 인증 | BLOCKED | HTTP 401 |
| Neo4j 인증·DB 수량·manifest·FAQ B2 | BLOCKED | 앞 단계 중단으로 미실행 |
| 실제 LLM | BLOCKED | 0회 |
| 기존 #103 제품 회귀 | PASS 유지 | 직전 최신 통합 79 PASS, 이번 제품 코드 변경 없음 |

다음 조치는 사용자가 정상 전달 경로에서 **현재 실행 중인 R3D verify 전용 기존 Qdrant 키**를 확인하고 같은 PowerShell에 다시 설정하는 것이다. 공유/build/R3/R3B 키 또는 예시 문자열을 대신 쓰지 않는다. 값을 채팅/로그로 전달하지 않는다. 인증 파일 추출·복호화, Docker 환경 조회, 인증값 재설정, DB 복원/수정은 하지 않았다.

PowerShell 7에서 기존 값을 다시 직접 입력하려면:

```powershell
$env:ISSUE105_R3D_VERIFY_QDRANT_API_KEY = Read-Host '현재 R3D verify Qdrant의 기존 키' -MaskInput
$issue103Python = '<repo>\.venv\Scripts\python.exe'
Set-Location '<workspace>\issue103-latest-dev-verify-20261126'
$issue103Diagnostic = ".cache\issue103-connection-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
& $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --connections-only --out $issue103Diagnostic
Write-Output "결과 파일: $issue103Diagnostic"
```

기존 키를 모르면 환경 관리자에게 정상 확인이 필요하다. 같은 거절된 값으로 반복 실행하거나 새 키를 임의 생성해도 기존 서버 인증은 해결되지 않는다. 접속 진단 PASS 후 전체 R3D·LLM 검증을 재개한다. 현재 최종 판정 BLOCKED, PR 생성 가능 여부 NO. 원본 외부 JSON은 보존했다.


## 외부 접속 재진단 — 2026-11-26 14:32 KST

외부 사전 검사는 PASS이나 Qdrant 인증은 다시 HTTP 401이다. 사용자 입력을 보면 키 대신 인증 파일명을 입력한 것으로 보인다. Read-Host는 파일을 불러오지 않고 입력 문자열을 그대로 전달하므로, Runbook에 따라 이미 구성된 R3D 전용 정상 인증 로딩 절차를 사용자 PowerShell에서 사용해야 한다. 인증 파일을 읽거나 복호화하지 않았다. 인증값 자체나 입력 문자열을 보고서에 복사하지 않았다. 실제 LLM 호출 0회, Neo4j 및 이후 검증은 미실행 BLOCKED이며 기존 PASS는 보존한다.


## 외부 R3D 인증 성공 — 2026-11-26 14:49 KST

외부 접속 진단 JSON `issue103-connection-20261126-144925-493.json`을 확인했다. `qdrant_authentication=PASS`, `neo4j_authentication=PASS`, 실행 오류 없음, `automated_status=PASS`다. 실제 Qdrant·Neo4j 인증 성공을 최신 관측으로 인정하며 이전 HTTP 401 이력과 구분한다. Codex 프로세스의 인증 상태로 이 결과를 무효 처리하지 않는다.

이번 실행은 접속 전용이므로 DB 수량·manifest·FAQ B2·LLM 검증은 아직 미실행이다. 실제 LLM 호출 0회다. 같은 외부 JSON에서 OPENAI_API_KEY 존재 여부는 false였으므로 실제 LLM 전에는 사용자 PowerShell의 정상 OpenAI 키 설정이 필요하다. 인증값은 출력/기록/추출하지 않았다. 기존 통합 회귀 79 PASS는 유지하며 새 코드 변경은 없다. 최종 PR 판정은 전체 DB 기준선 및 실제 답변 검토 전까지 BLOCKED다.


## 최종 코드 검토 및 외부 실제 증거 반영 — 2026-11-26 (최신 판정)

이 절이 앞선 인증 BLOCKED 기록 이후의 최신 결과다. 과거 기록·외부 원본 JSON은 보존했다.

### 환경·Git 및 판정
작업 경로: `<workspace>\issue103-pr-ready-20261126`. 브랜치 `fix/103-sejong-apply-url-final`, 기준 최신 dev `042d0fd84979d385323342dea4bcea2dd2b5fc35`.
기존 dirty worktree는 그대로 두고 최신 dev에서 새 작업 공간을 만들었다. #133은 현재 dev에 병합되었고 #78 관련 #135도 이미 포함되어 있다. 별도 #78 브랜치를 가져오지 않았다. Issue #103은 OPEN이며 PR 본문은 `Related to #103`을 사용한다.
**PR 준비: READY. 일반 PR: YES, Draft PR: YES. 신규 회귀 실패 0건.** PR 준비와 전체 실시간 E2E 완료는 별개다.

### 실제 R3D·LLM 결과
외부 실행 증거: `<workspace>\issue103-latest-dev-verify-20261126\.cache\issue103-r3d-llm-20261126-150052-281.json`. SHA-256 `088c551457fcc1ff848af63eb4926b235f46fb5af1966540932c68f758b18c58`.
사용자 PowerShell의 실제 인증 성공을 인정한다. 별도 프로세스의 Process 환경변수는 기존 Codex 프로세스로 자동 역전파되지 않으므로, Codex 인증변수 미상속을 외부 검증 실패로 취급하지 않았다. 인증값·인증 파일은 추출/복호화하지 않았다.

| 검증 | 판정 | 실제 확인 내용 |
|---|---|---|
| Qdrant·Neo4j 인증 | PASS | 외부 실제 연결 성공 |
| 릴리스·전체 DB 기준선 | PASS | 정책249·문서273, B2 1249·B3 1338, 노드1147·관계2702; payload·graph 전체 일치 |
| manifest | PASS | `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` |
| 현재 권장 번들 기준 | PASS | Runbook source 57d90f23308a의 R3 pins·벡터·graph와 내용 일치 |
| 번들 파일 자체 provenance | BLOCKED | --bundle-dir 미제공, ZIP/추출 파일 자체 대조는 미실행 |
| K-패스 실제 RAG 6표기 | PASS | 모든 검색에 POL_STG_MOLIT_001 포함; TopK 보조 후보와 목록 API 결과를 구분 |
| FAQ 실제 B2·인용 | PASS | 아래 문서·청크·내용·URL 대응 확인 |
| 외부 실제 LLM 원본 실행 | FAIL | 11회 호출/응답, HTTP200 10건·502 1건; 원본 FAIL 결과 보존 |
| 502 수정 후 실제 기록 재생 | PASS | 실제 검색결과+LLM 출력 고정, 현재 Checker·Pipeline·FastAPI로 11/11 HTTP200; 새 DB/LLM 호출 0회 |
| 수정 후 신규 실시간 E2E | BLOCKED | 재호출하지 않음; 재생을 실시간 호출로 보고하지 않음 |

B2 vector digest `7a39e5c9e6c6a7181c0827abcee0bf026160de3dd8dc7facd68b5df7861a1ab4`, B3 `d3ed4140122a9a8f815ee7eb01b0aad7c57119eb7a96c139179ed30427c5237b`.

FAQ B2 청크는 원본 릴리스 chunk_id와 직접 등치하지 않고 동일 B2 청킹 payload 기준으로 비교했다.
- K-패스: DOC_STG_MOLIT_001_FAQ_2026, C010/C002/C017/C026/C012, korea-pass.kr/notice/faqList.do. 한시 혜택 기간 2026-04-01~09-30 근거 유지.
- 취업자 감면: DOC_STG_NTS_001_FAQ_2026, C001/C010/C002/C012/C021, call.nts.go.kr/call/qna/selectQnaInfo.do?mi=1318.
- 서울 청년수당: DOC_STG_SEOUL_001_FAQ_2026 C001 및 같은 정책의 안내 문서 청크. youth.seoul.go.kr 공고 출처 유지. 같은 정책의 복수 문서 인용을 타 정책 혼합으로 오분류하지 않았다.
공개 citations의 title/url/document/page 계약은 유지하며 B2 내부 ID를 신규 공개 필드로 추가하지 않았다. 원본 검색결과·Checker·Generator 입력·SDK 원본·검증된 Generator 출력·최종 API 응답은 외부 JSON에 구분되어 있다.

### 발견·수정한 오류와 의미 검토
1. 실제 혼합 답변은 서울 정책의 신청 안내 다음 문장에 정책명을 생략했다. 기존 URL guard가 그 문장을 세종에도 적용해 LLM_OUTPUT_INVALID 502를 만들었다. 명시된 정책을 같은 문단에서 유지하고 빈 문단(LF/CRLF/공백 포함)에서 초기화했다. 다중 정책이 모호한 절은 여전히 안전하게 거절한다.
2. 일정 미확인 정책의 '언제든지 신청하실 수 있습니다', '연중 모집합니다'가 기존 단정 탐지를 빠져나갔다. POL_003/004/006/007로 재현 후 최소 정규식 보완 및 회귀를 추가했다.
3. 세종 POL_YC_065의 official_apply_url=null/reception_status=unknown 유지. 실제 응답의 sejong.go.kr 일반 참고 링크는 유지하며 전용 신청 페이지·현재 모집 중이라고 단정하지 않는다. 서울 POL_STG_SEOUL_001 정상 신청 URL·모집 마감 상태 유지. 4개 대출 정책은 원문의 상대적 기한과 현재 모집 확인 필요를 구분한다.
4. K패스/k패스 목록 누락은 하이픈 불일치가 원인이었다. 원문/별칭 데이터 수정 없이 React와 같은 검색 키를 적용했다. 6표기 모두 HTTP200 및 목표 정책 포함. 모두의 카드 검색은 기존 다른 관련 정책을 함께 반환할 수 있다.
5. 검증 CLI 단위 테스트의 전역 logging/warnings 설정은 teardown에서 복구해 다른 테스트에 영향을 주지 않게 했다.

### 회귀 및 기존 실패 분리
- 최종 관련 회귀: **111 PASS / 203 subtests PASS**.
- 전체 backend 최종: 2457 cases, 실패/오류 38, skip 129.
- 깨끗한 동일 dev: 2429 cases, 실패/오류 38, skip 129.
- 실패 case ID 집합 비교: 신규 0건. 상세 목록은 대응 JSON final_review.backend_final/backend_baseline에 보존. 기존 실패는 #103 신규 결함으로 분류하지 않았다.
- React 186 PASS, jsdom 없는 실행 환경의 DOM suite BLOCKED. TypeScript 재검사 PASS. bs4는 실제 venv에 설치되어 있어 이전 누락 환경과 구분했다.
- 브라우저: 이전 데스크톱·모바일 390x844 검증 PASS(K-패스6표기, 세종 신청 버튼 비노출, 원문 링크, 정상 정책 링크, 콘솔 오류 없음). 이번 최종 diff에는 frontend 변경이 없다. 이번에 새 브라우저 실행을 했다고 보고하지 않는다.
- #133 기존 가상 통합 55 PASS 기록 유지. 최신 dev의 #133/#135 포함 상태에서 관련 테스트도 통과. schemas.py·official_requirements.py·contract 변경 없음. Pipeline 인접 변경은 후속 #78 작업에서 두 처리 단계를 함께 보존해야 한다.

### 재현 명령과 남은 작업
Python: `<repo>\.venv\Scripts\python.exe` (3.12.13). 설치/새 venv/의존성 파일 수정 없음. 실제 openai 3.59.0과 저장소 지정 3.46.0 차이는 기록하며 임의 변경하지 않았다.
```powershell
$issue103Python = '<repo>\.venv\Scripts\python.exe'
Set-Location '<workspace>\issue103-pr-ready-20261126'
& $issue103Python -B -m pytest tests/integration/test_issue103_actual_response_replay.py tests/integration/test_issue103_guidance.py tests/integration/test_official_requirements_api.py tests/integration/test_issue78_exclusion_reasons_api.py tests/unit/test_official_requirements.py tests/unit/test_issue103_readonly_verifier.py -q --disable-warnings -p no:cacheprovider
& $issue103Python -B -m pytest tests -q --disable-warnings -p no:cacheprovider --tb=no
Push-Location frontend
& .\node_modules\.bin\vitest.cmd run
& .\node_modules\.bin\tsc.cmd --noEmit
Pop-Location
# 선택적 후속 실시간 검증: 인증이 준비된 사용자 PowerShell에서만 실행
$out = ".cache\issue103-postfix-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
& $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --llm --max-llm-calls 11 --out $out
```
새 실제 호출은 실행되지 않았으므로 후속 결과와 별도로 보존해야 한다. 번들 파일 자체 대조는 이미 존재하는 추출 번들 경로를 --bundle-dir에 제공할 때만 가능하며 복원/재적재하지 않는다. 최종 통합 실시간 E2E와 DOM 환경 보완은 #122 공유 항목이다.

### 변경 파일·PR 준비
제품 코드: ai/prompts/system_prompt.py, backend/api/policy_catalog.py, backend/rag/application_links.py, backend/rag/pipeline.py.
회귀·증거: tests/integration/test_issue103_guidance.py, tests/integration/test_issue103_actual_response_replay.py, tests/fixtures/issue103-r3d-mixed-url-response.json, tests/unit/test_issue103_readonly_verifier.py, tests/manual/verify_issue103_r3d_readonly.py.
문서: 기존 이름의 API/final-readiness MD·JSON 4개. (리뷰 반영으로 PR 작성 보조 파일 2개는 PR에서 제외) 기존 구현·과거 worktree는 보존했고 불필요한 옛 offline helper는 최종 PR에 복사하지 않았다.
PR 제목: **fix: 세종 신청 URL 오안내와 K-패스 목록 검색 누락 수정 (#103)**.
PR 본문·Git 명령 보조 파일(`issue103-pr-description.md`, `issue103-pr-commands.ps1`)은 리뷰에 따라 저장소에서 제외했다. Commit·Push·PR 생성·Merge·Issue Close·데이터/DB/.env 변경은 실행하지 않았다.

기존 실패 원인 보충: raw gov24/items/449000000108.json 누락, candidate 저장 본문/hash/manifest 대조 실패, bundle 임시 Git 및 과거 candidate git show의 exit 128이 깨끗한 동일 dev에서도 재현됐다. 해당 입력·데이터는 변경하지 않았다.

## PR #136 리뷰 피드백 반영 — 2026-11-26

리뷰 반영 내용(재현·수정·회귀 결과·남은 검증)은 `docs/reports/issue103-final-readiness-20261126.md`의 같은 제목 절에 기록했다.
