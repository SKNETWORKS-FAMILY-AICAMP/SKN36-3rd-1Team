# #40 후속 R3D·LLM 판정 일치 검증 — 2026-11-27

## A. 최종 결론

**CONDITIONAL. #40 후속 실DB 증빙은 확보했으나 전체 후속 검증 및 #122 완료 기준은 미충족이다.**

- 실제 R3D → Ollama → Qdrant → Neo4j → Checker → Pipeline → Generator → FastAPI 경로 실행.
- 최종 Mock Generator 묶음 11건 HTTP 200, 실제 LLM 3건 HTTP 200. 서로 중복되는 테스트 수는 합산하지 않는다.
- Checker·Generator 입력(Pipeline)·API 개별 상태 배열 불일치 **0건**(최종 Mock 11건, 실제 LLM 3건 각각).
- 실제 LLM 최종 사용자 안내 결함 **2건**: POL_GOV_042/45세의 종합 ineligible을 미확정으로 안내(1건), GOV059 신청 방법 나열을 접수 상태로 오인해 제거(1건). 종합 자격 판정 의미만의 불일치는 기존 1건이며, GOV059 자격 상태는 일치한다.
- 실제 LLM 3건의 문서 ID 소속 불일치 0건. 다중 정책 문장별 인용 혼용·청크 원문 전수 검증까지 통과했다는 뜻은 아니다.
- POL_005/80세: needs_check, exclusion_reasons=null 유지.
- POL_GOV_050: 실제 그래프 LTE 24 유지. 직접 Checker는 24개월 포함·25개월 미포함 사유를 보존하나 품질 보류로 둘 다 needs_check. 현재 dev API는 월령 필드를 버리므로 경계 API E2E는 미완료(#139).
- 전체 회귀 3027 PASS, 31 FAIL, 7 ERROR, 129 SKIP. 서비스 기능 수정 없이 확인한 dev 기준 결과다.
- PR #148은 Open이며 미병합이다(2026-11-27 GitHub 조회). 증빙 커밋 `34986f279bd1ce4babe7cfc8a23bb5afade5f43d`는 원격 PR head 및 로컬 HEAD와 일치한다. 이 보고서 정정 작업에서는 commit/push/댓글 등록/merge·DB 변경을 수행하지 않았다.

## 검증 목적·환경·기준 커밋

작업 환경: PR #148 head 브랜치에 연결된 전용 Worktree. 아래 프로젝트 파일 경로는 저장소 루트 기준이다.

작업 브랜치: `test/122-r3d-llm-e2e-consistency-latest`

기준 dev SHA: `8c4085823fc1f06a7d1b805dd6ea1d66200079ca` (PR #144 병합).

최초 일반 fetch는 DNS 제한으로 실패했다. 네트워크 허용 fetch 성공 후 origin/dev가 042d0fd에서 8c40858로 갱신됐다. 최초 생성한 `test/122-r3d-llm-e2e-consistency`(042d0fd)는 보존하고, 동일 Worktree에서 새 `-latest` 브랜치를 생성했다. 기존 브랜치 이동·merge·rebase는 하지 않았다. 쓰기 허용 workspace 아래 전용 Worktree를 사용했다.

- Python: 기존 프로젝트 `.venv\Scripts\python.exe`, Python 3.12.13. 패키지 설치·모델 다운로드·컨테이너 기동 없음.
- 데이터: `data/releases/v1.4.0-candidate`, manifest SHA256 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da`.
- R3D verify: Qdrant `127.0.0.1:56756`, Neo4j `127.0.0.1:56787`, Ollama `127.0.0.1:11434`, `qwen3-embedding:0.6b`, B2 `exp_b2_qwen3emb06b_v140_v1`.
- 노드 1147, 관계 2702, B2 points 1249. Mock/LLM 보고서 각각 실행 전후 건수 동일. 건수 비교는 전체 payload 불변 증명이 아니다. 기존 final smoke의 교차 DB 검증도 별도 실행했다.
- 정책 수 249는 릴리스/기존 보고서 기준이며 별도 새 정책 전수 집계·번들 ZIP 대조는 수행하지 않았다.
- 인증은 기존 로컬 DPAPI 파일을 프로세스 환경 변수에만 읽었다. 값은 출력·기록하지 않았다. Neo4j는 기존 읽기 전용 runner 사용.
- 최종 생성 파일 검사: 실제 DPAPI 인증값 포함 0건, OpenAI 등 .env 비밀값 포함 0건. 단순 문자열 스캔의 두 일치는 인증 파일명으로 확인한 오탐이었다. Python AST 구문 검사 및 git diff --check 통과.
- 실제 LLM: 기존 OpenAI 설정 `gpt-5.4-mini`, api.openai.com, 3회, 출력 상한 1600 tokens/회, timeout 45초, SDK retry 0. 사용자 명시 추가 승인 후 실행. 초기 자동 승인 거절은 호출 전 발생했다. token 사용량·청구액은 수집하지 않았다.

## 선행 증빙과 미병합 범위

다음은 GitHub에서 읽은 **과거 결과**이며 이번 dev 새 실행 결과에 합산하지 않았다.

| 출처 | 커밋/환경 | 범위·한계 |
|---|---|---|
| [#40 후속 댓글](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/40#issuecomment-7068055078) | c9188c6, r2 JSONL, Fake Generator | 19/19 PASS, 실DB·실LLM 없음. 해당 임시 19건 실행기는 현재 dev에서 추적되지 않아 동일 19건 재실행으로 표기하지 않음 |
| [PR #94](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/94) | head df6503d, merge c9188c6 | 소득 UNKNOWN 필수 AND 경로 예외와 구조 안전성 |
| [#102](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/102), [PR #131](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/131) | f2f4343, v1.4.0 flags | 정적 보호 141개 정책, 실DB 지정 정책 검색·Checker. Generator/API는 당시 제외 |
| [PR #119](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/119) | 보고서 8229620, dev 6b4fdc9 적용 | 일정 회귀 636건+subtests132, 과거 실DB/LLM 상담. 이번 환경 실행으로 재표기하지 않음 |
| [PR #130](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/130) | 2026-11-25 R3D 보고서 | 과거 11건 HTTP 200, 문서 단위 인용. 청크 ID API 미노출, 세종 신청 안내 미완료 |
| [#122](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/122) | open, 최신 dev 기준 | 전체 RAG·API·Django·React·장애·모바일까지 포함. 이번 PR만으로 닫을 수 없음 |

최초 조회 시 다음 PR은 모두 open/unmerged였다. 새 Worktree에 적용하거나 다른 세션을 수정하지 않았다.

- [#139](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/139): bafdf6589b7cfebca6541ab9aef333858c039d1a, child_age_months API 스키마·경계.
- [#136](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/136): dac94d8683b96a4f13ccf33d47f26041dea14ecf, 세종 URL·신청 일정 안내·검색.
- [#140](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/140): d948819a8d61b69b29c0b7a382a62664ae339371, 연령·인용·URL 실DB 증거.

## B. 테스트 결과

| 테스트 구분 | PASS | FAIL | 미실행 | 비고 |
|---|---:|---:|---:|---|
| 전체 unit+integration | 3027 | 31 | 129 SKIP | ERROR 7 별도. subtests 17671 PASS, 중첩 집계 금지 |
| #40/Checker/Pipeline/API 대상 묶음 | 211 | 1 | 0 | subtests166 PASS. 전체 회귀의 부분집합 |
| 실제 R3D final smoke | 13 | 0 | 0 | subtests13 PASS, loopback HTTP API-key warning1 |
| 실제 R3D + Mock Generator 상태 전달 | 11 | 0 | 0 | HTTP·상태 배열·문서 소속 검사. 기능 요구 전부 PASS 의미 아님 |
| 실제 LLM 전송·HTTP | 3 | 0 | 0 | 외부 실제 호출 3회 |
| 실제 LLM 종합 판정 의미 | 2 | 1 | 0 | GOV042/45세 ineligible → 미확정 안내. GOV059 자격 상태는 일치 |
| 실제 LLM 최종 사용자 안내 종합 | 1 | 2 | 0 | 저장 3건 재검토: GOV042 의미 불일치 + GOV059 신청 방법 손실. 위 행과 중복 합산 금지 |
| 기존 r2 19건 동일 실행 | — | — | 19 | 과거 증빙 인용만; 현재 대상 회귀로 대체 |
| Django·React·브라우저·전체 서비스 장애 | — | — | 미실행 | 이번 범위에서 완료 주장하지 않음 |

실행 명령(Worktree 루트, `$py`는 기존 프로젝트 Python):

```powershell
$py = '.venv\Scripts\python.exe' # 실제 실행은 기존 프로젝트 가상환경(Python 3.12.13)을 사용
& $py -m pytest tests/unit tests/integration -q --tb=short --junitxml=docs/reports/issue122-regression-20261127.xml
& tests/manual/run_issue122_readonly.ps1 -Smoke
& tests/manual/run_issue122_readonly.ps1 -Out docs/reports/새로운-검증파일.json
# 외부 호출은 사용자 승인 3건으로 제한; 기존 결과 경로는 덮어쓰지 않는다.
& tests/manual/run_issue122_readonly.ps1 -RealLlm -Out docs/reports/새로운-LLM파일.json
```

대상 묶음은 `test_unknown_and_scoped_hold`, `test_unknown_and_scope_safety`, `test_issue40_alias_consistency`, `test_issue40_r2_fullscan`, `test_issue40_r2_realdata`, `test_checker_quality_regression`, `test_eligibility`, `test_ask_api`, `test_rag_pipeline`, `test_issue78_exclusion_reasons_api`를 실행했다. 정확한 노드·결과는 targeted XML에 있다.

### 실패 분류

- 신규 서비스 회귀: 확인된 것 0. `git diff --exit-code HEAD -- backend database tests/unit tests/integration`가 빈 결과로 성공했다. 실행 코드는 기준 dev 그대로이며 새 manual recorder는 pytest 수집 대상이 아니다.
- dev 그대로 재현된 기능/자료 실패 2: `test_shared_constants::TestFrontendCheckerWording` UNKNOWN 문구 prefix 불일치, `test_rag_pipeline::test_regenerated_doc_gov_048_chunks_reach_api_response_intact`의 gitignore 원본 `449000000108.json` 누락. 후자는 대상 묶음에서도 동일 재현.
- 환경·Git 실행/객체 문제: 나머지 29 FAIL + 7 ERROR. bundle r3/v140·r3b 테스트의 임시 git init/show/archive exit128 및 candidate historical SHA `0a091268...` git show exit128. 원인별 trace는 전체 XML에 보존했다. 모두 기능 실패로 단정하지 않는다. 테스트의 임시 저장소 Git 명령이 실패했으며 작업 저장소에 커밋하지 않았다.
- 최초 sandbox 테스트 실행은 진행 출력 없이 중단했다. 집계는 완료된 허용 환경 실행 XML 기준이다. 전체 테스트 재실행은 하지 않았다.

## C. 판정 일치 결과

여기서 종합은 마지막 `field=eligibility` 행이다. 같은 필드의 UNKNOWN 하위 그룹 결과와 구별했다. 전체 행 배열은 JSON에서 대조한다.

| 정책 | 입력 조건 | Checker | Pipeline | API | LLM | 일치 여부 |
|---|---|---|---|---|---|---|
| POL_GOV_042 | 45세 | ineligible | ineligible | ineligible | 전체 자격 미확정 | 구조 일치 / 의미 FAIL |
| POL_GOV_042 | 10세 | needs_check | needs_check | needs_check | Mock | 상태 일치 |
| POL_GOV_039 | 30세 | needs_check | needs_check | needs_check | Mock | 상태 일치 |
| POL_005 | 80세 | needs_check | needs_check | needs_check | 추가 확인 | 일치, 제외사유 null |
| POL_GOV_050 | 신청자30세/자녀24개월 | needs_check | needs_check | needs_check | Mock | 상태만 일치, API 월령 소실 |
| POL_GOV_050 | 신청자30세/자녀25개월 | needs_check | needs_check | needs_check | Mock | 상태만 일치, API 월령 소실 |
| POL_GOV_050 | 월령 미입력 | needs_check | needs_check | needs_check | Mock | 보류 유지 |
| POL_GOV_011 | 30세 | ineligible | ineligible | ineligible | Mock | 접수 unknown 유지 |
| POL_GOV_059 | 30세/세종 | needs_check | needs_check | needs_check | 신청 방법 나열 제거 | 상태 일치 / 안내 FAIL, URL 요구 미검증 |
| POL_GOV_007 | 17세 | ineligible | ineligible | ineligible | Mock | 소득 UNKNOWN와 독립적 실패 유지 |
| POL_GOV_016 | 18세 | needs_check | needs_check | needs_check | Mock | AND/OR 보류 유지 |

### 단계별 항목과 한계

- Policy ID: 선택 정책 필터 적용, Generator contexts·API citations 문서 소속 검사. 최종 11+3 요청에서 소속 불일치 0.
- Condition ID: 현재 Checker/API `ConditionResult`에는 ID가 없다. 자동 생성·행 순서 기반 ID 추정 금지. 기록한 실제 graph `eligibility_structure`의 ID를 참고할 수 있지만 최종 조건 행과의 ID 단위 계약 검증은 **미검증**.
- condition_status: 필드·상태·순서 배열 대조. 이유는 Checker 및 Generator checks, API response에 원문 그대로 보존했다. 독립 Pipeline 결과 대신 실제 Generator가 받은 checks를 경계 관측값으로 사용했다.
- overall_status: 전용 필드 없음. 마지막 eligibility 행을 표에 표시했으며 raw 모든 행은 별도로 보존.
- exclusion_reasons: 최종 Mock11/실LLM3 모두 null, Pipeline→API 동일. ineligible이어도 NOT 확정 trace가 없으면 null인 현재 계약이다. null을 지원 가능으로 해석하지 않았다. 실제 확정 NOT 제외 사유가 있는 양성 사례는 이 묶음에서 **미검증**; #78 테스트는 별도 통과.
- 품질 보류: JSON quality에 경고 코드·affects·원문 근거 및 graph 그룹 구조 기록. GOV039 소득/고용/자녀, GOV050 자녀/가구·지역 보류 확인.
- 자녀 월령: 직접 Checker의 실제 DB 결과 24개월은 “포함”, 25개월은 “포함되지 않습니다”, 미입력은 “개월 수 확인”이다. children 상태는 품질 보류로 셋 다 needs_check. API Generator profile에는 child_age_months 자체가 없다. 정제 데이터나 품질 플래그를 제거한 합성 결과를 실제 DB 판정으로 표기하지 않았다.
- 최종 사용자 안내 및 원시 Generator 출력은 실제 LLM JSON에 둘 다 기록. GOV059의 원시 신청 방법 나열이 최종 Pipeline에서 마감 안내로 대체되어 제거됐다. 종전 정상 보정 해석을 철회하고 안내 FAIL로 정정한다(아래 리뷰 재확인 절).

### 의미 불일치 1건

GOV042/45세: 나이 조건 ineligible 및 필수 AND 종합 ineligible, 소득 needs_check가 함께 전달됐다. Generator는 나이 미충족을 설명하면서 “지역과 소득은 추가 확인이 필요해 전체 신청 자격은 확정할 수 없습니다”라고 했다. 최종 API answer도 이를 유지했다. 확정 불충족을 미확정으로 낮춰 안내하는 #40 제한적 예외의 의미 전달 문제다. Checker/Pipeline 구조화 계산 오류로 분류하지 않는다. 1회 관측이며 반복 안정성은 미검증.

### 인용·신청 안내 결과와 남은 시나리오

- 실LLM 인용 문서는 각 선택 정책의 채택 contexts 문서에 속했다. 다른 정책을 함께 반환하는 요청·문장별 entailment는 수행하지 않았다. 따라서 정책 간 혼용 전체 안전성은 **미검증**.
- GOV059는 이름을 확인해 세종 행정인턴으로 선택했으나 실제 `official_apply_url=https://www.sejong.go.kr`, reception=closed였다. 따라서 **세종 null URL·unknown 접수 시나리오로 집계하지 않는다**. 일반 홈페이지를 전용 신청 URL로 간주할 수 있는 데이터/기능 문제는 #136 연계 확인 대상.
- 실LLM GOV042·POL005는 apply URL null이며 허위 URL을 새로 만들지는 않았다. 그러나 전용 URL 미확인이라는 명시 안내는 충분히 확인하지 못했다. 해당 요구 전체 PASS 아님.
- 접수 unknown은 Mock GOV011·GOV016에서 보존. unknown 상태 실제 LLM의 상시 신청 추측 방지는 미검증.
- 명확한 종합 eligible 양성 사례, 확정 NOT 제외사유 전달, 전체 품질 보류 조건을 분리한 전용 사례, 실제 다중 정책 인용 혼용은 미검증. AND/OR/NOT의 일반 3값 의미는 기존 단위 회귀 및 final smoke 증거 범위로 제한한다.

## D. 변경 파일 목록

- `tests/manual/verify_issue122_consistency.py`: 실DB 단계 recorder. Checker 원본, 품질·구조, Generator checks/profile/context IDs/출력, API 응답 기록. 실제 LLM은 명시 3개 고정 사례만. 새 출력 exclusive-create.
- `tests/manual/run_issue122_readonly.ps1`: 기존 DPAPI 읽기 방식 재사용, 대상 loopback R3D 고정, 호출 제한과 환경 복원. 기존 기본 호출은 유지하며 인증 파일·Python·환경 파일 경로를 선택 매개변수로 지정할 수 있다. 기본 결과 파일명은 실행 날짜·시간을 포함하며 명시 -Out이 우선한다. PowerShell 5.1 한글 호환을 위해 UTF-8 BOM을 적용했다.
- 본 보고서: 결과와 미검증 범위 및 PR #148 리뷰 재확인. 미등록 댓글 초안과 개인 Git 명령은 최종 보고서에서 제거했다.
- `issue122-r3d-mock-20261127.json`: 초기10건. 초기 `sejong_unknown_url` 라벨이 실제 GOV011을 가리켰으므로 세종 증거로 사용 금지.
- `issue122-r3d-mock-20261127-v2.json`: 정책 ID 수정 11건.
- `issue122-r3d-mock-20261127-v3.json`: 최종11건 + 실제 Checker 월령 직접 비교. 최종 Mock 판단 기준. 초기 파일도 덮어쓰지 않고 보존.
- `issue122-r3d-llm-20261127-v2.json`: 실제 외부 호출3건. v2 명명은 출력 충돌 회피용이며 이전 실LLM 실행이 있었다는 뜻이 아님.
- `issue122-regression-20261127.xml`, `issue122-targeted-20261127.xml`: pytest 원본 증거. `.log`는 Git ignore된 로컬 출력이며 PR add 대상에서 제외.
- `tests/manual/review_issue148_saved_evidence.py`: 외부 호출 없이 당시 답변 3건을 재생하고 원본·Pipeline 식별자를 기록하는 리뷰 확인기.
- `issue148-saved-evidence-review-20261127.json`: GOV059 오탐과 POL_005 문장부호 확인 증거. `issue148-review-checks-20261127.json`: 경로·XML 불변성·보고서 수치·PowerShell 5.1 확인 결과.

## E. 후속 이슈와 #122 기준

- #122: GOV059 신청 방법 나열의 OPEN_CLAIM 오탐 수정(서비스 후속 작업), GOV042 종합 불충족의 LLM 의미 보존, 명확한 eligible·NOT 제외사유 실제 사례, 다중 정책 인용·조건 ID 추적, 접수 unknown 실LLM, 전체 서비스 장애·Django·React·모바일 검증. 현재 이슈를 닫지 않는다.
- #139: child_age_months 스키마 병합 후 이 Worktree의 월령 API 24/25/null 재실행. 중복 구현 없음.
- #136: null 전용 URL과 공식 참고 URL 구분, unknown 접수, 방문 불가 문구 보존. 이번 세종 사례는 closed·홈페이지 URL로 null/unknown 요구를 충족하지 않음.
- #140: POL005/80세 needs_check 보존을 이번 dev에서 추가 확인. 청크·문장별 인용 및 인천 원문 연령 충돌 결과와 함께 검토. 기존 PR 기능 수정 없음.
- 데이터 담당: GOV050 children·가구 보류 근거, GOV059 홈페이지 official_apply_url 의미, 릴리스/번들 최신 버전 확인. R3D 재적재·데이터 변경하지 않음.
- 전체 회귀: Git exit128 환경 및 문구 prefix 기존 실패 후속 확인. 누락 원본을 다른 세션에서 복사하지 않음.

## PR #148 리뷰 재확인 — 2026-11-27

### 기준 및 원본 보존

- 리뷰: [팀장님 댓글](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/148#issuecomment-7213137224), 게시 2026-11-27 17:05:40 KST.
- PR head `34986f279bd1ce4babe7cfc8a23bb5afade5f43d`, 당시 실행 기준 dev `8c4085823fc1f06a7d1b805dd6ea1d66200079ca`. 두 커밋의 `backend/rag/pipeline.py`는 동일하며 로컬 서비스 파일도 같다. 최신 dev 재실행 결과로 표현하지 않는다.
- 원본: `docs/reports/issue122-r3d-llm-20261127-v2.json`. 원본 답변·context ID·checks·데이터 manifest를 변경하지 않았다. 재검토 증거: `docs/reports/issue148-saved-evidence-review-20261127.json`(원본 SHA256·Pipeline blob SHA·실제 재생 시각 포함).
- 재검토는 저장 응답 재생과 정규식 문자열 검사다. 신규 R3D·LLM 호출 0회. 원본 XML의 실행 timestamp(2026-11-27)와 모든 test/outcome 수치는 유지했다.

### GOV059 — 정상 보정 해석 철회, 안내 FAIL

저장 원시 Generator 답변:

> 신청 방법은 2026년 하계 청년 행정인턴 최종선발자 21명에 한해 접수하는 화면이며, 온라인(정부24 혜택알리미)·이메일·방문 접수 중 하나로 제출하도록 안내되어 있습니다. [1]

같은 요청에 저장된 최종 API 답변:

> 신청 방법은 2026년 하계 청년 행정인턴 최종선발자 21명에 한해 접수하는 화면이며, 접수가 마감됐어요. [1]

`OPEN_CLAIM`의 `접수\s*(?:중|하고\s*있)`가 “방문 접수 중 하나”의 **접수 중**에 매칭된다. 여기서 “중 하나”는 온라인·이메일·방문이라는 선택지 나열이며 현재 접수 진행을 단정하는 표현이 아니다. 이미 뒤 문장에서 기간 종료·마감을 명시하고 있었으므로 신청 방법 제거는 정상적인 상태 보정으로 볼 수 없다.

`_align_answer_with_checks(raw, contexts, generator_checks)`를 당시와 동일한 코드로 호출한 결과, 저장된 최종 API 답변 전체와 정확히 일치했고 `changed=True`였다. 따라서 단순 정규식 가능성뿐 아니라 **이 PR의 실제 저장 응답에서 같은 오탐 경로가 발생했음**을 확인했다. 최소 문자열 “온라인·이메일·방문 접수 중 하나로 제출” 검사도 따로 기록했다.

팀장님이 최신 dev에서 관찰한 “이메일 접수, 접수가 마감됐어요.”와 이 PR의 최종 문자열은 서로 다르다. 하지만 이 PR 저장 원문에도 동일 OPEN_CLAIM 오탐이 있고, 당시 코드 재생이 실제 저장 결과와 일치하며 온라인·이메일·방문 안내가 제거되므로 **동일 원인 계열로 분류**한다. 최신 dev의 정확한 출력까지 재현했다고 주장하지 않는다.

분류 변경: GOV059 **자격 상태 일치 유지**, **신청 방법 보존 FAIL**. 실제 LLM HTTP 3/3 및 구조화 판정 불일치0은 유지한다. 사용자 안내 종합은 기존 2 PASS/1 FAIL에서 **1 PASS/2 FAIL**로 정정한다. 기존 pytest·Smoke 수치는 수정하지 않는다. 서비스 코드 수정은 팀장님 후속 작업으로 남긴다.

### POL_005 — 원본 미관측과 팀장님 재검증을 구분

이 PR의 `age80` 저장 원시 답변 및 최종 API 답변에는 `.,`가 각각 0회다. 당시 Pipeline 재생도 저장 최종 답변과 정확히 일치한다. 따라서 이 PR 원본에 문장부호 오류가 있었다고 소급하여 실패 수치를 바꾸지 않는다.

팀장님은 위 리뷰에서 최신 dev 실제 재검증 시 POL_005 `.,` 1회 발생을 보고했다. [PR #152](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/152)에도 별도 환경의 보정 문구 겹침·후속 수정 기록이 있다. 이는 외부 검증 보고이며 이 PR의 저장 응답 재현 결과와 구분한다. 해당 후속 코드의 최신 수정 효과는 이번 작업에서 검증하지 않았다. 서비스 코드 변경 없이 후속 추적한다.

### 초기 Mock 자료 보존 판단

초기 파일(버전 접미사 없음)은 10건이며 세종 시나리오 라벨이 GOV011을 가리킨다. v2는 정책 ID를 고친 11건, v3는 동일 11건에 직접 Checker 월령 경계 기록이 추가된다. v1/v2는 전체 바이트가 같은 복사본이 아니며 검증 경과를 추적할 수 있어 모두 보존했다. 최종 Mock 판단은 v3를 사용한다. 실제 LLM v2는 Mock v2와 별개로 외부 호출 3건 원본이므로 삭제 대상이 아니다.

### 경로 정리와 선택 스크립트 개선

프로젝트 파일은 저장소 상대 경로로 표시했다. XML은 개인 Worktree 접두 경로만 제거하고 외부 Python 설치 경로는 `[python-env]/Lib/...`로 표시했다. XML 구조·testcase 속성의 결과·실행 시각·failure/skip/error 종류·원인 문구는 유지했다. 전체 XML을 재생성하거나 기존 수치를 바꾸지 않았다.

실행기 선택 매개변수: `-QdrantSecretPath`, `-Neo4jSecretPath`, `-PythonExecutable`, `-EnvFilePath`. 외부 인증 파일의 실제 경로·값은 보고서에 기록하지 않는다. 기존 `-RealLlm`, `-Smoke`, `-Out` 호출 및 이전 기본 경로 fallback을 유지했다. 기본 결과명에 날짜·시간(밀리초)을 넣고 기존 결과 경로는 인증 파일 접근 전에 거부한다. Python recorder의 exclusive-create도 유지한다. 기본값 생성·명시 Out 우선·기존 파일 거부·PowerShell 5.1 구문/한글 해독은 외부 호출 없는 검사로 확인하며, 새 실제 인증 연결은 미실행이다.

### 이번 리뷰 수정의 검사 결과

보고서 Markdown fence·표 및 수치, 개인 경로 제거, XML 파싱·경로만의 정확한 치환, 원본 JSON 4개 불변, 서비스 코드 불변, 저장 응답 3/3 재생, GOV059 오탐 확인, POL_005 원본 문장부호 검사, `git diff --check`를 통과했다. Windows PowerShell `5.1.26100.9818`에서 parser·BOM/한글·기존/신규 매개변수·기본 날짜시간 파일명·명시 Out 우선·기존 파일 보존을 통과했다. 초기 Python 하위 프로세스의 PowerShell 검사는 상속된 모듈 환경에서 `Get-FileHash`를 찾지 못했으나, Windows PowerShell 5.1 직접 실행으로 같은 검사를 완료했다. 기존 전체 회귀·실DB Smoke를 재실행한 결과가 아니다.
