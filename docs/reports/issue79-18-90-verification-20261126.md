# #79·#18·#90 API·연령·공식 URL·출처 검증

> **2026-11-27 PR #140 리뷰 후 정정:** 이 문서의 저장 답변 재생 PASS는 당시 HTTP·정책 ID 확인 결과였다. 원본 citations까지 비교하면 7건 중 5건에서 출처가 달랐으므로 출처 보존 PASS 근거로 사용할 수 없다. 표시 번호→Context 번호 역매핑과 독립 연령 기대값을 보완한 결과는 [PR #140 리뷰 반영 보고서](pr140-review-20261127.md)와 새 증거 파일에 기록한다. 아래 2026-11-26 실측·원본 JSON은 역사적 증거로 보존했다.

2026-11-26 KST. **최종 PARTIAL. 증거용 PR 권장 YES. 서비스 코드 변경 없음. 데이터·DB 쓰기 0. 실제 외부 LLM 호출 7회.**

## A. Git·worktree

- 브랜치: `test/79-18-90-api-source-verification`
- worktree: `C:\Users\Playdata\Documents\ChatGPT\4th 단위 프로젝트\issue79-18-90-api-verify`
- `git fetch origin dev` 성공 후 기준: `befe2d026bb59511966f212dd40c968c0dcea5f2`.
- 권장 `C:\dev\...` 대신 쓰기 허용 workspace에 별도 worktree 생성. 동일 목적 브랜치·worktree가 없음을 전체 목록으로 확인했다.
- 기존 등록 worktree 17개를 `git status --short`로 조사했다. 루트·Django SSR·final-eval·#39 작업 2개·과거 RAG PR 준비 2개에 미커밋 파일이 있었다. 그대로 보존했다.
- #39(`feature/39-child-age-api-pipeline`)의 main.py·schemas.py·pipeline.py·계약·통합 테스트 수정은 읽기 전용 상태 조사만 했다. #103 `fix/103-sejong-apply-url-final`, #78 `feature/78-exclusion-reasons-api-integration`은 조사 시 clean이었다.
- 다른 브랜치 checkout/rebase/merge, 기존 수정 이동·삭제, commit/push/PR/issue 변경을 수행하지 않았다. worktree 생성에 필요한 공유 Git 메타데이터·origin/dev fetch만 갱신했다.

## B. 동결 DB 기준

| 항목 | 이번 실행 결과 |
|---|---|
| 릴리스 | `1.4.0-candidate` |
| 로컬 manifest SHA-256 | `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` |
| 실제 Neo4j Policy 수 | **249** (이번 SELECT 결과) |
| 실제 Qdrant B2 | `exp_b2_qwen3emb06b_v140_v1`, **1,249** |
| 실제 Qdrant B3 | `exp_b3_qwen3emb06b_v140_v1`, **1,338** |
| 접속 | Qdrant `127.0.0.1:56756`, Bolt `127.0.0.1:56787` |
| 검색 프로필 | `b2_06b_v140`, `qwen3-embedding:0.6b`, 1024차원, Cosine, `policy_section_body_v2` |
| 원문 해시 | 읽기 전용 release adapter로 **273문서** 확인 |
| 실제 청크 대조 | **11정책·58 B2 청크**, 동일 B2 재청킹 ID와 본문 모두 일치, 문서 URL 모두 일치 |

Runbook §3·§5.7.4·§5.12와 기존 복원 `04-verify-only.json`으로 전용 DPAPI 인증 경로·포트·프로젝트를 대조했다. 인증값을 출력·기록하거나 `.env`를 수정하지 않았다. 전용 환경변수는 실행 프로세스에만 두고 finally에서 지웠다.

Runbook 권장 번들은 `rag-final-v140-r3-57d90f23308a.zip`, source `57d90f23308a`, 기록된 ZIP SHA `cfa1001de5c193e199ed3fc93e086e9589a020b94bd2c59b85fd949a281e0d1a`이다. 이번에 ZIP 해시 전체·벡터 digest·전체 graph 속성/관계 수를 다시 검증하지 않았다. 이전 보고서 수치를 현재 실측으로 취급하지 않는다. DB 안의 manifest 메타데이터 자체는 별도 조회하지 않았으며 현재 릴리스 해시·실제 수량·선정 정책 graph·원문/B2 대조가 이번 실측 범위다.

첫 source audit에서 B1 함수로 B2 ID를 비교해 불일치로 기록한 검증 도구 오류가 있었다. `chunk_documents(strategy='B2')`로 고쳐 읽기 전용 재조회했고 58/58 일치했다. 최종 JSON의 audit는 교정된 결과다. 추가 LLM 호출은 없었다.

## C. #90 인천 연령

**요청문과 실제 동결 데이터 상태가 다르다.** 현재 실제 DB는 `COND_AGE_19_39`, 두 연령 관계 `review_status=verified`, `evidence_document_id=DOC_STG_ICN_001_NOTICE_2026`이다. 이번 작업이 검수 상태를 바꾼 것이 아니다. PR #89 당시의 `needs_review`를 현재 상태로 가정하지 않았다. 정부24 원문의 18~39세와 공고문 19~39세 표현 차이는 여전히 실제 LLM 입력에 공존한다. 이 검증으로 공식 기준을 새로 확정하거나 #90을 종료하지 않는다.

| 입력 나이 | DB 조건·검수 | 개별 `_condition` | 공개 Checker 나이 / 종합 | API 조건 | 출처·실제 LLM | 결과 |
|---|---|---|---|---|---|---|
| 18 | 19~39 / verified | ineligible | needs_check / needs_check | needs_check 유지 | 정부24 18~39만 설명, 충돌 설명 누락 | 구조 PASS / 설명 FAIL |
| 19 | 동일 | eligible | needs_check / needs_check | needs_check 유지 | 18~39만 설명; null인 신청 URL을 정부24 상세로 표현 | 구조 PASS / 설명 FAIL |
| 39 | 동일 | eligible | needs_check / needs_check | needs_check 유지 | 18·19 충돌은 설명; 상세 출처를 신청 주소로 표현 | 연령 PASS / URL FAIL |
| 40 | 동일 | ineligible | needs_check / needs_check | needs_check 유지 | 충돌 설명; 상세 출처를 신청 링크로 표현 | 연령 설명 일부 확인 / URL FAIL |
| 미입력 | 동일 | needs_check | needs_check / needs_check | needs_check 유지 | 18~39만 제시, 나이 입력 요청 | UNKNOWN PASS / 충돌 설명 FAIL |

공개 Checker와 Pipeline은 UNKNOWN 나머지 조건 그룹 때문에 전체 판정을 보류한다. `_condition`은 개별 비교이며 실제 API 전체 판정과 구분해야 한다. 실제 LLM 5건 모두 전체 적격을 확정하지는 않았다. 40세 역시 공개 판정은 needs_check여서 답변의 유보를 API 불일치로 보지 않았다.

graph의 연령 근거는 공식 2026 공고문이고 실제 Hybrid Top-10에도 해당 문서가 들어 있다. 모든 검색 근거를 순회한 모의 Generator 경계 테스트에서는 Qdrant scroll 순서에 따라 첫 5개 Context가 조례/정부24에 치우칠 수 있다. 이것은 의미검색 순위 결과가 아니므로 검색 품질로 해석하지 않는다. 실제 Hybrid/LLM 근거와 scroll 기반 전수 청크 감사는 별도 JSON 구역이다.

## D. #79 교정 연령

교정 장부 `requires_condition_additions`의 실제 AGE 노드 **53정책**을 현재 릴리스 ID와 연결했다. 기존 fullscan은 관계·트리 연결·개별 하한-1/하한/상한/상한+1/미입력 기대값을 확인한다. 신규 통합 테스트는 같은 53개 실제 조건에서 파생한 유효 나이를 공개 Checker→Pipeline→Generator 입력→POST TestClient로 전달한다. -1/121은 Checker에서만 검증하고 API에 유효 나이로 보내지 않는다. 120은 무상한 관례여서 121을 정상 상한 초과 신청자로 취급하지 않는다.

실제 DB 대표 정책:

| 정책 | 범주·실제 조건 | 이번 실측 |
|---|---|---|
| POL_010 | 정부24 원문, AGE 15~69 | 14/15/69/70/미입력, TestClient 5건 |
| POL_YC_024 | 온통청년, AGE 7~120 | 6/7/120/미입력 4건; 실제 Hybrid·LLM 1건 |
| POL_BOK_004 | 복지로, AGE 15~40 | 14/15/40/41/미입력 5건; 40은 월 기준 불명으로 확인 필요; Hybrid·LLM 1건 |
| POL_STG_MOIS_001 | 신청자 AGE 없음, 자녀 216개월 미만 | 신청자 미입력/25/65세가 자녀 나이로 쓰이지 않음 |
| POL_BOK_001 | 자녀 156개월 미만 | 신청자 age와 혼용 없음 |
| POL_BOK_008 | 본인·보호자 겸용 대상자 9~24세 SPECIAL_TARGET | 신청자 65세로 대상자 나이 불충족을 확정하지 않음 |
| POL_BOK_043 | 예외 연령 검수 보류 장부 | AGE 자동 추가 없음, UNKNOWN/보류 유지 |
| POL_GOV_054 | 신청자 연령 미확인/삭제 | AGE 없음, 근거 없는 나이 단정 없음 |
| POL_YC_007 | 미확인 연령 표본 | AGE 없음, 확인 필요 유지 |

인천·신청 URL 교정 표본 POL_GOV_065까지 포함해 실제 DB **11정책·API 40건**. SPECIAL_TARGET 자녀/보호자 조건에는 신청자 나이만으로 판정하지 않는 별도 회귀를 추가했다. `child_age_months` API 필드는 구현하지 않았다. 최신 dev에 #112 Checker·Generator는 병합됐지만 #39 API 계약 통합은 별도 진행 중이다.

## E. #18 URL·문서·청크

구조화 `official_apply_url`은 graph의 실제 값 또는 null로 보존된다. `source_url`/citation URL은 정책 설명·공고 원문의 주소이며 전용 신청 주소를 뜻하지 않는다. POL_GOV_065의 구조화 신청 URL은 교정된 예약 경로를 유지했다. 확인되지 않은 URL을 새로 만들거나 데이터 값을 교정하지 않았다.

- **PASS:** 58청크가 같은 정책의 연결 문서이고 URL·동일 B2 본문이 일치한다. 273문서 해시 검증을 통과했다. 이 결과는 검증한 정책 집합에 한정한다.
- **PASS:** 복수 정책 회귀에서 정책 ID·문서·인용 번호 연결 유지. Generator 입력의 조건과 API 조건 일치. 구조화 신청 URL/null 보존.
- **FAIL:** 실제 인천 19/39/40세 답변은 `official_apply_url=null`인데 정부24 상세 안내 URL을 신청 URL/접수 주소로 설명했다.
- **FAIL:** POL_YC_024도 구조화 신청 URL은 null인데 경기도 홍보 안내 URL을 신청 URL로 표현했다.
- **BLOCKED(공개 계약):** Citation은 `title,url,document_id,page_number`만 제공한다. `chunk_id,retrieved_at,content_hash`는 공개 응답에 없어 API만으로 E2E-03-3 전체를 확인할 수 없다. 내부 Context에서 chunk_id와 실제 원문은 대조했다. timestamp를 임의로 생성하지 않았다.
- **SKIP:** 이번에 외부 공식 사이트에 접속해 신청 화면/URL 유효성·신청 가능 여부를 검증하지 않았다. 모든 URL 분류는 동결 메타데이터·원문에 근거한다.

PR #130은 merged, 실제 상담 11건의 **문서 단위** 인용 PASS만 기존 증거로 재사용한다. 청크 단위는 이번 58건이 새 증거다. K-패스 6표기·FAQ 전체를 이번에 다시 검색했다고 주장하지 않는다. #103 PR #136은 open/unmerged (`0744a1c4`), 일반 홈페이지/안내 URL의 신청 URL 오안내 방어를 진행 중이다. 이번 재현을 별도 신규 미병합 버그로 만들거나 같은 보정 함수를 재구현하지 않았다.

## F. Checker·Pipeline·FastAPI

실제 Hybrid 검색은 로컬 Ollama 임베딩→Qdrant B2→Neo4j→정책별 결합을 사용했다. 인천·온통청년·복지로 대표 3건 모두 선택한 정책 ID만 반환했다. Qdrant SDK/httpx 호환 TypeError 때문에 검증 하네스에 표준 HTTP 읽기 어댑터를 사용했다. 서비스 검색 코드·DB는 변경하지 않았다.

FastAPI는 **TestClient만 사용**했다. 실제 HTTP 서버를 새로 띄우지 않았다. 실제 LLM 7건은 실제 Hybrid 결과→Checker→Pipeline→Generator 경로에서 실행했고, 저장된 최종 답변을 별도로 TestClient에 재생해 HTTP 200·정책 ID 계약을 검증했다. 신규 실시간 LLM이 FastAPI 서버를 통과했다고 표시하지 않는다.

`GET /api/policies`는 HTTP 200, total 249, dataset_version·manifest 일치. POST 경계 40건 200. 기존 ask_api 회귀에서 422 입력 오류, 503 검색 장애, 502 LLM/출력 오류 등 정상/오류 계약을 구분했다. API policy_checks에는 최상위 status가 없으므로 종합 결과는 마지막 eligibility 조건으로 읽었다. #122의 `policy_checks[].status` 표현과 실제 계약 차이를 인계한다.

## G. 실제 R3D·LLM

[실측 JSON](issue79-18-90-e2e-20261126.json)의 `policies`는 DB/모의 Generator 40건, `hybrid`는 실제 의미검색 3건, `llm`은 실제 외부 호출 7건, `catalog`는 GET 응답이다. `semantic_findings`에 실패를 명시했다. JSON `status=PARTIAL`은 실행 성공과 의미 검증 PASS를 구분한다.

사용자가 공개 정책 질문·공식 근거·가상 나이 최대 7회 OpenAI 전송을 승인한 뒤 실행했다. 추가 재생/청킹 교정에는 외부 호출이 없다. 성공 답변 7/7, 정책 ID/문서 인용 계약은 유지됐다. 그러나 인천 충돌 설명 누락 3/5, null URL의 신청 주소 오설명 4/7로 전체 의미 검증은 FAIL이다. 서로 다른 실패 범주는 같은 답변에 중복될 수 있다. 금액·수급을 보증하는 답변은 없었다. 호출 7회는 Generator 실행 횟수이며 공급자 내부 과금 요청 로그를 별도로 조회하지 않았다.

## H. 테스트·회귀

- 수정 전 서비스 코드 기준 기존 관련 suite: **404 PASS, 기존 FAIL 1, 303 subtests PASS**.
- 기존 FAIL: `test_rag_pipeline.py::PipelineTests::test_regenerated_doc_gov_048_chunks_reach_api_response_intact`; Git 제외 `data/raw/gov24/items/449000000108.json` 없음. 단독 재현 1 FAIL. 데이터를 복사/수정해 숨기지 않았다.
- 최종 suite: **430 PASS, 기존 FAIL 1, 603 subtests PASS**, 13.38초. [실행 로그](issue79-18-90-tests-20261126.txt)에 기록. 신규 테스트 5개(인천 보류, 53정책 API 경계, 복수 정책, 수혜자 구분, 실제 응답 재생) 모두 PASS. 기존 suite와 source/catalog 회귀를 함께 실행했다.
- #40 실데이터/fullscan/별칭, #76 official_requirements/API, #112 월령 grounding/문구, #39 Checker, FastAPI·RAG·최신 Generator·미입력 알림·목록·Context 회귀를 실행했다. #103 이름의 별도 dev 테스트 파일은 없었으며 미병합 #136 테스트를 가져오지 않았다.
- Windows 샌드박스 TestClient 실행이 멈춘 초기 시도 3개는 중단하고 승인된 실행 환경에서 재실행했다. 중단을 PASS로 세지 않는다. Qdrant SDK TypeError는 검증 하네스에서만 우회했다.
- Python 문법 검사·`git diff --check` 수행. 서비스 코드가 같으므로 이번 변경에 따른 새 서비스 회귀 실패 **0건**. 전체 저장소/수동 LLM 테스트 일괄 실행은 하지 않았다.

## I. 오류와 수정

프로덕션 Pipeline·API 수정 **없음**. 신규 검증 하네스와 증거만 추가했다. 실제 답변 결함은 재현했으나 URL 문장 보정은 #136이 동일 영역에서 진행 중이며, 연령 충돌 설명은 변경 금지된 Generator/프롬프트와 관련된다. 담당 범위 밖 코드와 병행 PR을 덮어쓰지 않았다. 저장된 7개 응답을 네트워크 없이 재현하는 회귀로 후속 수정의 비교 기준을 남겼다. 이 테스트의 PASS는 문장 의미의 올바름을 뜻하지 않는다.

## J. 남은 데이터·검수 항목: 3개

1. 인천 18/19세 원문 차이와 이미 verified인 관계의 행정적 근거를 데이터 담당이 확인. verified를 이번 작업에서 되돌리거나 기준을 확정하지 않음.
2. POL_YC_024 실제 AGE 7~120과 원문 연령 표기/LLM 미확인 설명의 관계 검토. 교정 장부의 기록과 현재 원문을 함께 검토해야 함.
3. null 신청 URL 정책의 안내·공고·일반 홈페이지 분류와 전용 신청 경로 부재 확인. 인천·POL_YC_024를 구체 표본으로 전달.

## K. 병행 작업 보호·향후 통합

#39/#103/#78 worktree 수정 없음. 잠재 충돌 파일은 `backend/rag/pipeline.py`, `backend/api/main.py`, `backend/api/schemas.py`와 기존 API/RAG 통합 테스트이나 이번에는 수정하지 않았다. 신규 테스트 파일만 겹치지 않는 이름으로 추가했다. #39 API 반영 후 신청자/자녀 입력 분리, #136 반영 후 저장된 URL 실패 답변 재생 및 실제 LLM 재검증, #78/#76 병합 코드의 보류·사유·official_requirements 회귀를 최종 #122에서 함께 확인해야 한다.

## L. #122 인계

실제 [#122](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/122) 본문과 `issue122-e2e-verification-matrix.md`를 대조했다. ID는 본문이 아닌 저장소 검증표에 존재한다.

| ID | 실제 정의 | 이번 증거·판정 |
|---|---|---|
| E2E-03-2 | 카드 판정과 Checker 및 답변 문장 일치 | TestClient 보류·Generator 조건 일치 PASS; 실제 LLM 연령 충돌 설명 FAIL; React 화면은 SKIP |
| E2E-03-3 | source_url·retrieved_at·content_hash 원문 일치 | 273문서 해시·58 B2 본문/URL PASS; 공개 citation timestamp/hash/chunk 부재로 전체 BLOCKED |
| E2E-05-6 | 인천 근거·연령, #90 전후 상태 구분 | 실DB는 verified/2026공고 연결, UNKNOWN 보류 PASS; 충돌 설명 FAIL. 과거 needs_review 기대를 현재 DB에 덮어씌우지 않음 |
| E2E-06-2 | POST 선택 정책·일정·서류·문의처 계약/null | 실제 DB 40건과 실응답 재생 7건 및 기존 계약 회귀 PASS; 실제 HTTP 서버 경로는 미실행 |

## M. PR 초안·사용자 Git 명령

증거 공유용 PR **YES(권장)**. 서비스 코드 fix PR은 없음. 이슈 전체 완료/자동 종료 근거가 아니므로 Closes를 사용하지 않는다.

제목: `test(#79,#18,#90): R3D 연령·출처·URL 검증과 실제 답변 실패 증거 추가`

본문 초안:

> 동결 R3D와 최신 dev에서 53개 교정 정책의 API 경계, 실제 DB 11정책 40건, Hybrid 검색 3건, 승인된 실제 LLM 7건을 검증했습니다. 원문 273개 해시와 동일 B2 청크 58개 본문·URL을 대조했습니다.
>
> 구조화 판정/출처 계약은 유지되지만 실제 LLM에서 인천 18/19세 충돌 설명 누락과 null 신청 URL의 안내 주소 오설명을 재현했습니다. #136 URL 방어와 Generator/데이터 검토로 인계하며 서비스·데이터·DB는 변경하지 않았습니다. 실제 HTTP 서버 대신 TestClient를 사용했습니다.
>
> 검증: 신규 테스트 5개 PASS, 기존 누락 원본 실패 1건은 변경 전후 동일, 신규 회귀 실패 0. 상세 수치는 보고서와 실행 로그 참고.
>
> Related to #79, #18, #90, #122. #39/#103/#78 병행 작업 보존.

사용자만 아래를 실행한다. 이 작업에서는 add/commit/push를 실행하지 않았다.

```powershell
Set-Location -LiteralPath 'C:\Users\Playdata\Documents\ChatGPT\4th 단위 프로젝트\issue79-18-90-api-verify'
git status --short
git branch --show-current
git diff --check
git add -- tests/integration/test_issue79_18_90_verification.py docs/reports/issue79-18-90-verification-20261126.md docs/reports/issue79-18-90-e2e-20261126.json docs/reports/issue79-18-90-tests-20261126.txt
git diff --cached --stat
git diff --cached --check
git commit -m 'test: verify issues 79 18 90 against frozen R3D'
git push -u origin test/79-18-90-api-source-verification
```

GitHub 저장소의 Pull requests → New pull request에서 base `dev`, compare `test/79-18-90-api-source-verification`을 선택하고 위 제목·본문과 4파일의 차이를 확인해 생성한다. 자동 종료 키워드는 넣지 않는다.

## 로컬 재현 명령

기존 `.venv`를 사용했으며 패키지를 설치하지 않았다. 다음은 실제 실행한 범위의 재현 명령이다. `--llm`은 외부 호출이므로 별도 승인과 키 설정이 필요한 선택 실행이며 아래 읽기 전용 명령에는 포함하지 않았다. output 파일은 매번 새 이름이어야 한다.

```powershell
Set-Location -LiteralPath 'C:\Users\Playdata\Documents\ChatGPT\4th 단위 프로젝트\issue79-18-90-api-verify'
$verifyPython = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe'
$verifyTests = @(
  'tests/unit/test_age_corrections_v140.py',
  'tests/unit/test_issue79_age_corrections_fullscan.py',
  'tests/unit/test_issue40_r2_realdata.py',
  'tests/unit/test_issue40_r2_fullscan.py',
  'tests/unit/test_issue40_alias_consistency.py',
  'tests/unit/test_eligibility.py',
  'tests/unit/test_issue39_child_age.py',
  'tests/unit/test_pr112_month_age_grounding.py',
  'tests/unit/test_pr112_month_bound_wording.py',
  'tests/unit/test_official_requirements.py',
  'tests/unit/test_context_builder.py',
  'tests/integration/test_policy_catalog_api.py',
  'tests/integration/test_official_requirements_api.py',
  'tests/integration/test_rag_pipeline.py',
  'tests/integration/test_ask_api.py',
  'tests/integration/test_latest_generator_api.py',
  'tests/integration/test_missing_age_notice_api.py',
  'tests/integration/test_issue79_18_90_verification.py'
)
& $verifyPython -m pytest @verifyTests -q -p no:cacheprovider
& $verifyPython -m py_compile tests/integration/test_issue79_18_90_verification.py

# Runbook과 같은 현재 사용자 DPAPI 절차. 값은 출력하지 않는다.
$verifySecretDir = 'C:\dev\project\SKN36-3rd-1Team\restore-bundle\secrets-01'
try {
  foreach ($pair in @(@('qdrant.clixml','ISSUE105_R3D_VERIFY_QDRANT_API_KEY'), @('neo4j.clixml','ISSUE105_R3D_VERIFY_NEO4J_PASSWORD'))) {
    $secure = Import-Clixml -LiteralPath (Join-Path $verifySecretDir $pair[0])
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
      [Environment]::SetEnvironmentVariable($pair[1], [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process')
    } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
  }
  & $verifyPython tests/integration/test_issue79_18_90_verification.py --live --hybrid --source-root 'C:\dev\project\SKN36-3rd-1Team\restore-bundle\repo-01' --output .cache/issue79-18-90-recheck-new.json
} finally {
  Remove-Item Env:ISSUE105_R3D_VERIFY_QDRANT_API_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:ISSUE105_R3D_VERIFY_NEO4J_PASSWORD -ErrorAction SilentlyContinue
}
```
