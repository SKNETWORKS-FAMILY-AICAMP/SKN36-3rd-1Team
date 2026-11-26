# #103 최종 R3D·LLM·브라우저 검증 및 PR 준비

> **최신 갱신:** PR #133은 `cdbc3533`에서 병합됐고 최신 dev는 `5b250ade`다. 별도 최신 통합 worktree에서 76개 회귀를 통과했다. 실제 R3D·LLM은 외부 PowerShell 실행 결과 대기다. 아래 기존 기록은 당시 기준을 보존하며, 최신 실행 절차와 A~O 결과는 문서 끝 「외부 PowerShell 최종 검증 준비」 절을 따른다.

2026-11-26 KST. 최종 판정: **BLOCKED**. 실제 브라우저/목록 HTTP 검증과 최신 #133 가상 통합은 완료했지만 실제 R3D·LLM은 정상 인증 환경 부재로 실행하지 않았다. 기존 Mock PASS를 실제 E2E PASS로 바꾸지 않았다.

기존 코드·테스트·보고서 8개 파일의 변경을 그대로 보존했다. 이번 단계에서는 기능 코드 수정 없이 이 보고서와 `issue103-final-readiness-20261126.json`만 추가했다. 이전 상세 보고서는 `issue103-api-verification-20261126.md`다.

## 1. R3D 인증·연결 결과

`docs/development/ISSUE105_R3_RUNBOOK.md` §3·§5.7.4·§5.10.14·§5.12를 확인했다. 현재 R3D verify 전용 변수 이름은 다음과 같다. 이전 R3/R3B나 build 인증값을 대신 사용하면 안 된다.

| 현재 PowerShell Process 변수 | 존재 여부 |
|---|---|
| ISSUE105_R3D_VERIFY_QDRANT_API_KEY | 없음 |
| ISSUE105_R3D_VERIFY_NEO4J_PASSWORD | 없음 |
| OPENAI_API_KEY | 없음 |
| QDRANT_API_KEY / NEO4J_PASSWORD / NEO4J_URI | 없음 |

값을 출력하지 않고 존재 여부만 검사했다. 사용자 지시에 따라 이 시점에서 인증된 DB·실제 LLM 검증만 중단했다. 이번 단계에서 DB 인증 재시도, 저장된 인증값 조회·DPAPI 복호화·Docker 환경변수 추출을 하지 않았다. 이전 단계의 Qdrant 401을 이번 단계의 새 연결 결과로 보고하지 않는다.

현재 지정된 대상은 Qdrant `http://127.0.0.1:56756`, Neo4j `bolt://127.0.0.1:56787`이다. DB·Docker·볼륨·복원 번들은 변경하지 않았다.

### 사용자가 직접 설정할 절차 (PowerShell 7)

기존 실행 DB에 이미 설정된 **R3D verify 전용 값**을 사용한다. Runbook의 신규 환경 생성용 `New-R3Secret`, 복원 `--apply`, 재적재 명령은 이번 검증에서 실행하지 않는다. 기존 인증값을 모르면 해당 환경 관리자에게 정상 전달 경로로 확인해야 한다. 채팅이나 보고서에 값을 붙여 넣지 않는다.

아래는 화면에 값을 드러내지 않고 사용자가 직접 입력하는 대안이다. Runbook의 '현재 프로세스에서만 설정하고 finally에서 제거' 원칙을 따른다. 조직에서 승인한 R3D 전용 `Enter-/Exit-R3VerifySecrets`가 이미 준비되어 있다면 사용자가 그 정상 절차를 직접 사용해도 된다. 여기서는 인증 파일 경로를 추측하거나 읽지 않았다.

```powershell
Set-Location '<workspace>\issue103-sejong-verify'

$issue103AuthNames = @(
  'ISSUE105_R3D_VERIFY_QDRANT_API_KEY',
  'ISSUE105_R3D_VERIFY_NEO4J_PASSWORD',
  'OPENAI_API_KEY', 'QDRANT_URL', 'QDRANT_API_KEY',
  'NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD', 'OLLAMA_BASE_URL'
)
# 기존 Process 설정은 보존하고 finally에서 복원한다. 값은 출력하지 않는다.
$issue103PreviousEnv = @{}
foreach ($name in $issue103AuthNames) {
  $issue103PreviousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
  $env:ISSUE105_R3D_VERIFY_QDRANT_API_KEY = Read-Host '기존 R3D verify Qdrant 키' -MaskInput
  $env:ISSUE105_R3D_VERIFY_NEO4J_PASSWORD = Read-Host '기존 R3D verify Neo4j 비밀번호' -MaskInput
  $env:OPENAI_API_KEY = Read-Host '실제 LLM 검증용 OpenAI 키' -MaskInput

  # 애플리케이션이 읽는 일반 접속 변수에 같은 프로세스에서 연결한다.
  $env:QDRANT_URL = 'http://127.0.0.1:56756'
  $env:QDRANT_API_KEY = $env:ISSUE105_R3D_VERIFY_QDRANT_API_KEY
  $env:NEO4J_URI = 'bolt://127.0.0.1:56787'
  $env:NEO4J_USER = 'neo4j'
  $env:NEO4J_PASSWORD = $env:ISSUE105_R3D_VERIFY_NEO4J_PASSWORD
  $env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'

  foreach ($name in $issue103AuthNames) {
    [pscustomobject]@{
      Name = $name
      Present = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, 'Process'))
    }
  }

  # Runbook에서 사용하는 준비된 .venv를 먼저 확인한다. 없으면 여기서 중단한다.
  if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Runbook 기준 .venv가 없습니다. 준비된 동일 버전 환경을 확인한 뒤 재개하세요.'
  }
  & .\.venv\Scripts\python.exe -c "import fastapi, httpx, neo4j, qdrant_client, openai, uvicorn"
  if ($LASTEXITCODE -ne 0) { throw '필요한 기존 의존성이 준비되지 않았습니다.' }

  # 실제 서버는 이 블록의 같은 셸에서 실행한다. 종료하면 finally가 설정을 복원한다.
  # 이 명령은 서버 시작일 뿐 R3D 검증 완료를 의미하지 않는다.
  & .\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8013
} finally {
  foreach ($name in $issue103AuthNames) {
    [Environment]::SetEnvironmentVariable($name, $issue103PreviousEnv[$name], 'Process')
  }
  $issue103PreviousEnv.Clear()
}
```

다른 PowerShell 셸에 설정한 Process 변수는 현재 검증 도구 프로세스로 자동 전달되지 않는다. 설정한 동일 셸에서 테스트/서버를 실행해야 한다. 위 절차는 이번 작업에서 실행하지 않았다. 현재 worktree에는 Runbook 기준 `.venv`가 없고, 기존 `.review-venv`에는 `openai` SDK도 없다. 의존성을 설치하거나 모델을 다운로드하지 않았다.

## 2. 데이터 버전·manifest

| 항목 | 이번 확인 결과 |
|---|---|
| 최신 dev | a2ff03001c11738475bdf1ab1b9b4886f9ea52e6 |
| 릴리스 파일 버전 | 1.4.0-candidate |
| manifest SHA-256 | b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da |
| 직접 읽은 정책 파일 행 수 | 249 |
| 실행 DB와 manifest 일치 | BLOCKED |
| 실제 Qdrant B2 / B3 포인트 수 | BLOCKED / BLOCKED |
| 실제 Neo4j 노드 / 관계 수 | BLOCKED / BLOCKED |

Runbook의 B2 1,249·B3 1,338, Neo4j 1,147/2,702는 기대/과거 검증값이다. 이번 실행에서 직접 조회한 수치로 표시하지 않았다. B2 청크를 릴리스 원본 청크 ID와 문자열로 비교하지 않았다.

## 3. 실제 검색·API 테스트

DB를 쓰지 않는 목록 검증을 위해 `create_app(session_factory=lambda: None)`을 loopback 8013에 실행했다. 목록 API는 실제 제품 코드의 릴리스 파일·manifest 검증을 사용했다. 가상 정책 데이터나 Mock 응답 서버가 아니다. 검색 세션을 비활성화했으므로 실제 R3D 상담 API로 오해하면 안 된다.

| 대상 | 실제 HTTP 결과 | 한계 |
|---|---|---|
| K-패스 / K패스 / k패스 / k-패스 | 각각 200, POL_STG_MOLIT_001 1건 | 릴리스 목록 경로 |
| 모두의 카드 / 모두의카드 | 각각 200, 대상 정책 포함 2건 | 기존 POL_BOK_075 부분 일치도 유지 |
| 세종 위원회 목록 | 200, POL_YC_065, 신청 URL null, 접수 unknown | 실제 Neo4j 값은 BLOCKED |
| 청년수당 목록 | 200, POL_STG_SEOUL_001의 정상 official_apply_url 유지 | 실제 LLM의 URL 보존 여부는 BLOCKED |
| K-패스 실제 RAG 6표기 | BLOCKED | DB 인증 없음 |
| FAQ 3건 실제 검색·B2 청크 출처 | BLOCKED | DB 인증 없음 |

HTTP 응답과 관측 결과는 동명의 JSON에 저장했다.

## 4. 실제 LLM 테스트

**BLOCKED, 호출 0회, 비용 0.** 실제 원본 응답과 Pipeline 후처리 응답은 모두 미생성이며 JSON의 해당 항목은 null이다. 이전 Mock 출력을 이번 실제 응답으로 재사용하지 않았다.

남은 최소 실제 호출은 4회다:

1. 세종 청년위원회 신청 방법을 알려줘.
2. 세종 청년위원회 신청 링크 알려줘.
3. 세종 청년위원회 지금 모집 중이야?
4. 확인된 공식 신청 URL이 있는 다른 정책의 신청 방법.

같은 요청의 실제 검색 Context(policy/document/B2 chunk/source_id), Generator 원본, Pipeline 후처리, 최종 HTTP를 따로 저장해야 한다. 세종 홈페이지를 신청 페이지로 단정하지 않는지, unknown을 접수 중/마감으로 바꾸지 않는지, 다른 정책의 링크가 보존되는지, 정책 간 URL 혼합이 없는지를 확인해야 한다. 실제 LLM 일반 홈페이지 안내가 브라우저에 렌더링되는지도 아직 확인되지 않았다.

## 5. 실제 브라우저 검증

React Vite `http://127.0.0.1:5178/`와 위 FastAPI 목록 서버를 연결했다. ChatGPT in-app browser에서 UI를 실제 조작했다. 상담은 예시 모드이며 실제 상담 질문은 전송하지 않았다.

| 검증 항목 | 결과 |
|---|---|
| K-패스 6표기 입력 후 대상 카드 표시 | PASS (K 계열 1건, 모두의 카드 계열 2건) |
| 전체 정책 수·파일 버전 표시 | PASS (249, 1.4.0-candidate) |
| 세종시 위원회 청년참여 확대 카드 표시·펼치기 | PASS |
| 세종 접수 안내 | PASS — 일정 확인 필요, 신청 기간은 공식 원문 확인 |
| 세종 신청하기 링크·버튼 | PASS — 각각 0개 |
| 공식 원문 안내 링크 유지 | PASS — 온통청년 DOC_YC_065 정책 상세 주소 |
| 브라우저 console error / warn | PASS — 각각 빈 목록 |
| 실제 LLM 답변 내 세종시 홈페이지 안내 링크 | BLOCKED — 실제 LLM 미실행 |

온통청년 공식 원문 링크가 유지됨을 확인했다. 세종시 일반 홈페이지 URL 자체가 원래 목록 카드에 별도 표시된다고 주장하지 않는다. 이번 검증은 원문 링크와 신청 버튼을 구분한 것이다. 외부 원문 사이트의 HTTP 가용성은 별도로 확인하지 않았다.

임시 브라우저 탭과 이번에 시작한 8013/5178 프로세스는 검증 후 종료했다. 기존 서버·DB·컨테이너는 종료하거나 변경하지 않았다. 소스 변경 없이 같은 목록 화면을 재현하는 명령은 다음과 같다(각각 별도 PowerShell):

```powershell
# 목록 전용 FastAPI — 실제 R3D 상담 검증 서버가 아님
Set-Location '<workspace>\issue103-sejong-verify'
& '..\ssok-ui-analysis\.review-venv\Scripts\python.exe' -c "from backend.api.main import create_app; import uvicorn; uvicorn.run(create_app(session_factory=lambda: None),host='127.0.0.1',port=8013)"
```

```powershell
Set-Location '<workspace>\issue103-sejong-verify\frontend'
$env:VITE_POLICY_API_URL = 'http://127.0.0.1:8013/api/policies'
$env:VITE_ASK_API_URL = ''
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5178 --strictPort
# 브라우저에서 http://127.0.0.1:5178/ 을 열고 정책 검색을 조작한다.
```

## 6. PR #133 충돌·가상 통합

GitHub 연결 도구로 최신 상태를 다시 읽었다:

- dev: `a2ff03001c11738475bdf1ab1b9b4886f9ea52e6`
- #133: Open, HEAD `a59223cd27ef565da9c950dfc9f0854dc762b906`, base는 위 dev와 동일

두 SHA 모두 기존 가상 통합 기준과 같아 새 패치를 임의로 재적용하지 않았다. 기존 별도 `issue103-pr133-compat` worktree에서 #103 URL 방어 파일/테스트가 현재 브랜치와 바이트 단위로 같음을 확인한 뒤 #76 44개 + #103 11개 = **55개 PASS**를 재확인했다.

이전 인접 Pipeline import hunk의 텍스트 충돌과 해결안은 유지된다. 두 import와 #133의 생성 후 official_requirements 연결을 모두 보존한다. 추가 충돌 없음. 실제 Merge·Commit 없이 가상 패치 통합으로만 검증했다. #133 먼저 병합 후 #103 갱신·동일 회귀 재실행을 권장하지만 강제 순서는 아니다.

## 7. 기존 dev 대비 신규 회귀 실패

- 이번 단계 기능 코드 변경 0, 새로운 회귀 실패 0. #133 가상 통합 55 PASS.
- 앞선 dev 대조 결과는 그대로다: dev 125개 중 124 PASS + bs4 누락 환경 오류 1개; #103 136개 중 135 PASS + 동일 환경 오류 1개.
- React 기존 관련 49 PASS, 확대 실행 186 PASS + jsdom 누락 오류는 이전 결과다. 이번에는 실제 브라우저 확인을 추가했다. 전체 pytest 성공으로 바꿔 기록하지 않았다.
- diff 공백 검사 PASS, `data/`·`database/` 변경 없음, staged 파일 없음.

## 8. 변경 파일·최종 판정

이번 추가 파일:

1. `docs/reports/issue103-final-readiness-20261126.md`
2. `docs/reports/issue103-final-readiness-20261126.json`

기존 코드·테스트·이전 보고서 8개 파일은 그대로 보존했다. `.cache`의 수집 보조 파일은 Git 제외이며 기능 코드가 아니다. 브랜치 `fix/103-sejong-apply-url-guidance`는 기준 dev HEAD 위 미커밋·미스테이징 상태다.

**BLOCKED** — 실제 R3D·LLM·B2 출처 검증을 최종 제출 조건으로 요청받았으므로 아직 완료 판정할 수 없다. 정상 인증 변수를 제공한 같은 실행 환경과 Runbook 기준 의존성 준비가 필요하다. 브라우저/목록/Mock/가상 통합 결과만으로 READY를 선언하지 않는다.

Commit·Push·PR 생성·Merge·Issue Close는 하지 않았다. 승인 후 Git 명령은 기존 상세 보고서 J절의 8개 파일 목록에 이번 보고서 2개를 함께 추가하면 된다. 실제 검증이 끝나기 전에는 #103 Close나 실제 E2E 완료 문구를 넣지 않는다.


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

## 외부 PowerShell 최종 검증 준비 — 최신 dev 5b250ade

### A. 작업 환경 및 Git 상태 — PASS

주 작업 브랜치 `fix/103-sejong-apply-url-guidance`와 HEAD `a2ff030` 및 기존 미커밋 변경을 보존했다. `git fetch origin dev`로 원격 참조만 갱신했다. dev는 `5b250ade107c4d2c8ad12b71c645db13df4a56ad`로 이동했고 #133은 최종 HEAD `cdbc3533231b87d675e60cb089196971665da489`에서 병합됐다. 이전 Open/55 PASS 기록은 과거 결과다.

주 브랜치에 Merge하지 않고 새 detached worktree `../issue103-latest-dev-verify-20261126`에서 최신 dev + #103 변경을 가상 결합했다. 인접 Pipeline import hunk의 충돌만 두 기능을 모두 유지해 해결했다. 기존 다른 worktree를 초기화·삭제·수정하지 않았다. 최종 PR 제출 전 주 브랜치의 최신 dev 동기화와 import 충돌 정리는 승인 후 별도 Git 작업이 필요하다.

### B. 환경변수 전달·Python 환경 — PASS(조사), 실제 인증은 BLOCKED

Codex 명령은 Windows PowerShell 7.6.9의 별도 프로세스에서 실행된다. 현재 프로세스에는 R3D 전용 두 변수와 OPENAI_API_KEY가 없지만 사용자 PowerShell에는 존재한다는 사용자 관측을 보존했다. 별도 Process 환경변수는 자동 공유되지 않으므로 미상속 가능성이 높다. 부모 프로세스 CIM 조회는 권한상 불가했고 사용자 PowerShell PID를 대조하지 못했으므로 정확한 부모 관계를 확인했다고 주장하지 않는다.

사용자가 지정한 실제 기존 Python을 확인했다:

`<repo>\.venv\Scripts\python.exe` — Python **3.12.13**.

설치 상태: fastapi 0.162.1, httpx 0.28.1, qdrant-client 1.12.2, neo4j 5.28.8, openai **3.59.0**, pytest 9.2.0, beautifulsoup4 4.15.0. 이전 `.review-venv`와 달리 openai·bs4·pytest가 준비되어 있다. `requirements.txt`의 openai 고정값은 3.46.0이므로 버전 차이는 기록했다. 패키지 설치·다운그레이드·의존성 파일 변경은 하지 않았다. 관련 Generator/Mock 회귀는 이 사용자 환경에서 통과했으며 실제 SDK 네트워크 호환성은 외부 실행에서 확인한다.

### C. 실제 인증·DB 연결 — BLOCKED, 외부 실행 경로 준비 완료

`tests/manual/verify_issue103_r3d_readonly.py`를 추가했다. Runbook §3·§5.7.4·§5.10.14·§5.12에 따라 현재 실행 프로세스의 `ISSUE105_R3D_VERIFY_QDRANT_API_KEY`, `ISSUE105_R3D_VERIFY_NEO4J_PASSWORD`만 사용해 56756/56787에 접속한다. 일반 애플리케이션 변수는 이 자식 Python 프로세스에서만 연결한다. `.env` 로딩은 비활성화하고 Credential Manager·DPAPI·Docker 인증 환경은 읽지 않는다.

Qdrant 메서드는 읽기 allowlist로 제한한다. Neo4j는 기존 ReadOnlyTimedRunner를 사용한다. 복원·재적재·재임베딩·컬렉션/alias/스키마 변경 진입점은 호출하지 않는다. 검색을 위한 Ollama 질의 임베딩만 사용하며 기존 모델 다운로드를 하지 않는다.

모든 결과는 새로운 `--out` 파일에만 저장하며 기존 파일을 덮어쓰지 않는다. raw/URL-encoded 인증값은 문자열·키·중첩값에서 마스킹한다. 라이브러리 로그·경고는 억제하고 예외는 종류/백엔드/분류만 기록한다. Codex에서 실행한 것은 `--preflight`뿐이며 실제 연결·LLM 호출은 0회다. 외부에서 이미 수행한 결과가 있다면 그 JSON을 별도 증거로 평가하며 Codex 환경 누락으로 실패 처리하지 않는다.

### D. DB 수량·manifest — 파일 검증 PASS, 실제 DB BLOCKED

manifest SHA-256 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da`, 릴리스 `1.4.0-candidate`. 로컬 manifest 파일 36개 모두 일치, 문서 273개를 읽고 같은 B2/B3 청킹 함수를 사용해 1,249/1,338개를 재구성했다. 이 수를 실제 DB 조회값이라고 기록하지 않는다.

외부 검증기는 실제 Neo4j 전체 ID·관계·속성·수량, Qdrant B2/B3 전체 포인트 ID·벡터 digest·원문 슬라이스·전체 payload, 두 DB 연결을 기존 검증 함수로 대조한다. 기대값은 R3_PINS와 현재 릴리스에서 읽는다.

기존 압축 해제 번들이 있다면 `--bundle-dir`로 추가한다. 이때 SHA256SUMS·번들 source·현재 코드의 복원 계약·payload digest도 읽기만 해서 비교한다. 지정하지 않으면 실행 DB 내용의 R3 기준선 비교와 번들 파일 출처 검증을 분리하며 후자는 BLOCKED로 남긴다. 실제 사용한 번들 파일을 찾지 못한 상태에서 그 버전이 확인됐다고 기록하지 않는다.

### E. FAQ B2 출처 — BLOCKED, 검증기 준비 PASS

FAQ 대상은 `DOC_STG_MOLIT_001_FAQ_2026`, `DOC_STG_NTS_001_FAQ_2026`, `DOC_STG_SEOUL_001_FAQ_2026`이다. 실제 검색 결과 point_id로 Qdrant를 다시 retrieve하여 같은 B2의 policy_id/document_id/chunk_id/본문/source URL을 채택 Context와 대조한다. 릴리스 원본 chunk_id와 문자열 비교하지 않는다. 공개 citations에 chunk_id를 추가하지 않고 문서/URL/번호 계약을 검사한다. 답변 내용의 사실성·과거 FAQ의 현재 적용 여부는 실제 결과를 받아 사람의 의미 검토가 필요하다.

### F. 세종 실제 LLM — BLOCKED

신청 방법+참고 홈페이지, 신청 링크, 현재 모집 여부의 3질문을 실제 LLM으로 실행하도록 준비했다. 실제 검색 결과, Checker를 반영한 Generator 입력, SDK 원본 문자열, Generator 검증 후 출력, Pipeline 후처리 및 최종 FastAPI 응답을 구분한다. 세종 URL null/접수 unknown은 구조적으로 확인하고 문장의 의미는 실제 결과를 보고 판정한다.

### G. K-패스 실제 RAG — BLOCKED

6표기 검색은 policy_id 필터 없이 실제 검색하고 POL_STG_MOLIT_001 포함 여부와 전체 결과를 기록한다. K-패스 FAQ 상담이 실제 LLM 상담도 함께 담당한다. 실제 검색 전에는 PASS로 표시하지 않는다.

### H. 미확인 일정·정상 URL·다중 정책 — BLOCKED

POL_003/004/006/007을 각각 상담해 Checker unknown 여부와 최종 답변을 남긴다. 정상 신청 URL이 있는 실제 정책(우선 POL_STG_SEOUL_001)을 목록에서 선택하고 세종 정책과 함께 묻는 질문을 1회 실행한다. 두 정책이 실제 검색·카드에 포함되는지, 정상 URL과 세종 null이 유지되는지 확인하며 답변 문장의 URL 혼합 여부는 따로 검토한다.

### I. 최신 #133 통합 회귀 — PASS

최신 dev `5b250ade`에 이미 최종 #133이 포함되어 있다. 새 worktree에서 #103과 가상 통합 후 **76 PASS, 153 subtests PASS**. 최종 #133 55개 + #103 11개 + 외부 검증기 10개다. 이전 가상 통합의 55 PASS는 삭제하지 않았다. 공식 자격기준·Checker·citations 연결을 보존했다.

```powershell
Set-Location '<workspace>\issue103-latest-dev-verify-20261126'
$env:PYTHONDONTWRITEBYTECODE = '1'
& '<repo>\.venv\Scripts\python.exe' -B -m pytest tests/integration/test_issue103_guidance.py tests/integration/test_official_requirements_api.py tests/unit/test_official_requirements.py tests/unit/test_issue103_readonly_verifier.py -q --disable-warnings -p no:cacheprovider
```

### J. FastAPI·React 회귀 — PASS(핵심), 환경 예외 BLOCKED

사용자 Python으로 기존 관련 193개를 실행해 **192 PASS, 510 subtests PASS, 환경 실패 1개**. bs4 누락은 해소됐으나 Git 제외 `data/raw/gov24/items/449000000108.json`이 없었다. 깨끗한 dev a2ff030에서도 같은 실패를 재현해 신규 #103 회귀와 구분했다.

React는 다시 **186 PASS**, jsdom 부재 오류 1개로 전체 성공은 아니다. TypeScript PASS. 기존 의존성을 설치/변경하지 않았다.

최신 통합 코드를 실제 React + 릴리스 목록 API로 실행해 390×844 모바일 viewport에서 6표기 검색, 세종 카드·버튼 비노출·공식 원문 링크·가로 넘침 없음(clientWidth=scrollWidth=375)·콘솔 오류 없음 확인. 기존 데스크톱 결과도 보존했다. 실제 R3D 상담 화면 검증으로 오해하지 않는다. 임시 viewport·탭·이번 실행 서버는 종료/원복했다.

### K. 변경 파일·검증기 시험 — PASS

이번 제품 기능 코드 추가 변경은 없으며 새 파일은 다음 2개다:

- `tests/manual/verify_issue103_r3d_readonly.py`: 외부 PowerShell 실제 검증기.
- `tests/unit/test_issue103_readonly_verifier.py`: 읽기 제한·마스킹·B2 대조·11케이스 기록·호출 상한·사전검사 성공/실패·덮어쓰기 방지 10개 테스트.

최종 보고서 Markdown/JSON은 과거 이력을 보존하며 갱신했다. 검증기 개발 중 확인한 준비 검사 종료 코드와 Git 메타데이터 예외 처리는 수정·재검증했다. 제품 회귀 실패로 분류하지 않는다. 데이터·DB·manifest·`.env` 변경 없음.

### L. 남은 BLOCKED와 사용자가 직접 실행할 명령

**인증값이 이미 준비된 사용자 PowerShell에서 아래를 실행한다. 값을 채팅에 보내지 않는다.** 최신 dev/#133과 #103의 결합을 확인하기 위해 원래 작업 브랜치가 아닌 새 검증 worktree를 실행 대상으로 쓴다. 준비된 기존 Python을 그대로 사용한다.

```powershell
$issue103Root = '<workspace>\issue103-latest-dev-verify-20261126'
$issue103Python = '<repo>\.venv\Scripts\python.exe'
Set-Location $issue103Root
$env:PYTHONDONTWRITEBYTECODE = '1'
$issue103Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$issue103Preflight = Join-Path $issue103Root ".cache\issue103-preflight-$issue103Stamp.json"
$issue103Result = Join-Path $issue103Root ".cache\issue103-r3d-llm-$issue103Stamp.json"

# DB・LLM 호출 없이 변수 존재·의존성·Git 메타데이터만 검사한다.
& $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --preflight --llm --out $issue103Preflight
if ($LASTEXITCODE -ne 0) {
  Write-Output "사전 검사 결과: $issue103Preflight"
  throw '사전 검사 BLOCKED. 인증값 자체는 출력하지 말고 JSON의 missing_env_names/missing_packages/git_error를 확인하세요.'
}

# 실제 읽기 전용 DB + Ollama 질의 검색 + OpenAI 최대 11회. 자동 재시도 없음.
& $issue103Python -B tests/manual/verify_issue103_r3d_readonly.py --llm --max-llm-calls 11 --out $issue103Result
$issue103ExitCode = $LASTEXITCODE
Write-Output "결과 JSON: $issue103Result"
Write-Output "자동 검사 종료 코드: $issue103ExitCode"
```

선택: 실제 기존 압축 해제 번들 폴더를 알고 있다면 마지막 명령에 `--bundle-dir '<실제 기존 번들 폴더>'`를 추가한다. 번들 경로가 없으면 임의로 복원/다운로드/재생성하지 않는다. 위 실행은 새 결과 파일을 만들고 기존 결과를 덮어쓰지 않는다. 최대 출력 토큰은 프로젝트 Generator 설정을 사용하며 호출 수는 11회로 제한한다. 정확한 사용량/비용을 임의 계산하지 않는다.

JSON의 `automated_status`와 `semantic_review`를 구분한다. 자동 검사가 PASS여도 실제 답변의 의미 검토 전 `semantic_review`/최종 `verdict`는 BLOCKED다. 이는 실제 테스트 실패라는 뜻이 아니라 검토 대기다. 생성된 JSON과 필요 시 콘솔 요약을 전달하면 LLM 원본·후처리·출처를 분석해 최종 보고서에 반영한다. 별도 프로세스 인증 누락을 이유로 성공한 외부 결과를 무효 처리하지 않는다.

### M. PR 준비 가능 여부 — BLOCKED

핵심 검증 영역 5개 중 목록/API·브라우저·최신 통합 회귀 3개는 확인했고, 실제 R3D와 실제 LLM 2개는 결과 대기다. 전체 PASS를 선언할 수 없다. 실제 데이터/LLM 결과와 필요 시 번들 근거를 확인한 뒤 최종 판정한다. 현재 Codex의 실제 OpenAI 호출은 0회다.

### N. 담당자 공유·PR 초안

- 민혁: R3 기준선·실제 B2 출처·번들 파일 확인 결과 대기. DB 변경 안 함.
- 소희·지인: 외부 결과의 원본→후처리 비교, 세종/미확인 일정/정상 URL/정책 간 혼합 의미 검토 필요.
- 민지: 최신 통합 모바일 목록 확인 PASS. jsdom 환경 예외는 남음.
- 반석: 동결 데이터 수정 없음. 기존 신청 URL 라벨과 구조화 null의 차이는 안전 처리로 유지.

PR 제목 초안: `fix(#103): 세종 신청 URL 오안내 방어 및 K-패스 목록 검색 보완`

본문 초안: 구조화 신청 주소가 null인데 원문의 기관 홈페이지를 신청 페이지로 설명하던 출력을 정책별로 검증한다. 일반 출처와 다른 정책의 정상 신청 링크는 유지하며 K패스 목록 API 누락을 보완한다. 최신 #133 병합 dev와의 가상 통합 76개, React 186개 및 모바일 핵심 흐름을 확인했다. 실제 R3D·LLM 최종 근거는 외부 PowerShell 결과 검토 대기다. raw JSON/jsdom 환경 예외를 구분했다. 데이터·DB·공개 API 구조 변경 없음. 관련 #103.

### O. 승인 후 Git 명령어 (이번에는 실행하지 않음)

외부 결과 검토와 최종 승인이 끝난 후에만 아래를 실행한다. 주 작업 브랜치는 아직 최신 dev보다 뒤에 있으므로 제출 전에 동기화·import 충돌 정리·재검증이 필요하다. `rebase --continue`는 충돌을 실제로 해결한 뒤에만 사용한다. 강제 push는 필요하지 않다.

```powershell
Set-Location '<workspace>\issue103-sejong-verify'
git diff --check
git add -- backend/rag/application_links.py backend/rag/pipeline.py backend/api/policy_catalog.py ai/prompts/system_prompt.py tests/integration/test_issue103_guidance.py tests/unit/test_issue103_readonly_verifier.py tests/manual/verify_issue103_offline.py tests/manual/verify_issue103_r3d_readonly.py docs/reports/issue103-api-verification-20261126.md docs/reports/issue103-api-verification-20261126.json docs/reports/issue103-final-readiness-20261126.md docs/reports/issue103-final-readiness-20261126.json
git diff --cached --stat
git commit -m 'fix(#103): distinguish application links and verify R3D responses'
git fetch origin dev
git rebase origin/dev
# 충돌이 발생하면 두 기능을 유지해 해결하고 git add <해결 파일>, git rebase --continue 후 관련 회귀를 다시 실행한다.
git push -u origin fix/103-sejong-apply-url-guidance
gh pr create --draft --base dev --head fix/103-sejong-apply-url-guidance --title 'fix(#103): 세종 신청 URL 오안내 방어 및 K-패스 목록 검색 보완' --body '구조화 신청 주소와 공식 출처를 구분하고 K패스 목록 검색을 보완합니다. 최신 통합 및 실제 검증 결과는 docs/reports/issue103-final-readiness-20261126.md를 참고하세요. 관련 #103.'
```


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

## PR #136 리뷰 피드백 반영 — 2026-11-26 (최신 판정)

리뷰: PR #136 코멘트(2026-11-26 08:41 UTC). 작업 브랜치 `fix/103-sejong-apply-url-final`(PR HEAD `52be7f9`), 최신 dev `befe2d0`(리뷰 당시 기준 `f42e726`). 이번 반영은 새 worktree에서 진행했고 기존 worktree·브랜치·데이터·DB·`.env`는 변경하지 않았다.

### A. 리뷰 피드백
1. 정책명 없는 일반 마무리 문구("자세한 내용은 각 신청 페이지에서 확인하세요.") 때문에 서울·세종 혼합 정상 답변 전체가 HTTP 502.
2. 전용 신청 주소가 없는 정책에서 "주민센터 방문 또는 온라인으로 신청할 수 있습니다 [6]"을 보정하면서 원문 근거가 있는 방문 신청 안내까지 삭제.
3. PR 작성 보조 파일(`issue103-pr-commands.ps1`, `issue103-pr-description.md`) 포함. 전자에는 개인 PC 로컬 경로 포함.

### B. 수정 전 재현 결과 (PR HEAD `52be7f9`, 코드 변경 전)
- 기록된 실제 R3D·LLM 서울·세종 응답(`tests/fixtures/issue103-r3d-mixed-url-response.json`) 끝에 빈 줄 + 마무리 문구 → **HTTP 502 `LLM_OUTPUT_INVALID`** 재현.
  - 원인: `align_application_links()`의 `LINK_CLAIM`이 "신청 페이지"를 신청 주소 주장으로 보고, 정책명·인용이 없고 빈 문단 뒤라 `paragraph_policy`도 없어 대상이 전체 정책(서울+세종)이 된다. 세종 `official_apply_url=null`이 섞여 `len(targets) != 1` → 무조건 `AskError`.
  - 같은 문단(공백 구분) 변형은 200이었지만, 앞 문장 끝 인용 `[1][3]`이 마무리 절 앞에 붙어 세종 문장으로 오인되어 마무리 문구가 세종 전용 보정 문구로 바뀌고 인용 위치도 이동했다.
- 릴리스 원문 `POL_GOV_020`(출산장려금 지원, `official_apply_url=null`, 원문 "거주지 관할 읍면동 주민센터에 방문 신청")에 "주민센터 방문 또는 온라인으로 신청할 수 있습니다 [1]." → 200이지만 답변이 "현재 확인된 전용 신청 주소는 없습니다…"로 바뀌며 **주민센터 방문 안내 삭제** 재현.
  - 원인: `LINK_CLAIM`의 `온라인.{0,100}신청`이 절 전체를 신청 주소 주장으로 판정하고, 단일 정책 보정이 절 전체를 `NO_APPLICATION_LINK`로 치환한다. 방문 근거 확인 단계가 없었다.
- Pipeline 후처리와 Generator 프롬프트(2-2절)의 역할: 프롬프트는 생성 단계 지침, `align_application_links()`는 생성 후 결정적 검증으로 중복이 아닌 이중 방어다. 이번 결함은 모두 후처리에 있어 프롬프트는 변경하지 않았다.

### C. 수정 내용
변경 파일: `backend/rag/application_links.py`(제품 코드 유일 변경), `tests/integration/test_issue103_review_feedback.py`(신규), 보고서 MD·JSON 4개(경로 익명화·본 절), PR 보조 파일 2개 제외.
- 일반 안내 구분: 여러 정책이 대상인데 **정책명·인용·URL이 모두 없는 절**만 그 절을 중립 문구 "자세한 신청 방법과 일정은 각 정책의 공식 안내를 확인해 주세요."로 바꾼다. 대상 정책 모두에 근거 문맥이 있을 때만 이 문구를 쓰고, 근거 없는 정책이 섞이면 문구 없이 그 절만 뺀다.
- 기존 방어 유지: 여러 정책 이름이 섞이거나, 여러 정책 인용이 섞이거나, URL(`https?://`, `www.`)을 직접 제시하는 모호한 신청 안내는 기존처럼 `LLM_OUTPUT_INVALID`(502). 정책명이 있는 잘못된 URL은 기존처럼 해당 정책 문장만 보정하고 다른 정책 링크·인용을 빌리지 않는다.
- '각/모든/두 + 정책·신청·공식…' 복수 지칭은 같은 문단의 마지막 정책 하나로 좁히지 않는다.
- 절 맨 앞의 인용(`… 주세요. [1][3] 다음 문장`)은 앞 문장 출처로 보고 위치를 그대로 유지한다.
- 방문·온라인 혼합: 절에 오프라인 접수처+방문(주민센터·행정복지센터·동사무소·구청·시청·군청)이 있고, **같은 정책의 근거 문맥**에 그 접수처·방문·신청/접수가 함께 있을 때만 "{접수처} 방문 신청 방법은 확인되지만, 전용 온라인 신청 주소는 확인되지 않았습니다."로 보정하고 원래 인용 번호를 유지한다. 근거가 없거나 다른 정책 문맥에만 있으면 방문 안내를 만들지 않고 기존 `NO_APPLICATION_LINK`를 쓴다. '홈페이지 방문'은 방문 신청으로 보지 않는다.
- 보존: 방문 신청만 안내하는 문장, 일반 홈페이지·공고 확인 안내, 유효한 `official_apply_url`이 있는 정책의 온라인 안내, policy_id·citations·응답 스키마.
- PR 보조 파일: 두 파일은 `52be7f9`에 커밋되어 PR 변경에 포함되어 있었고 서비스·테스트에서 참조하지 않는다. 저장소 밖 백업(`<workspace>\issue103-pr-helper-backup-20261126\`) 후 작업 트리에서 삭제했다(커밋 시 삭제 반영). 보고서 4개의 개인 경로는 `<workspace>`·`<repo>`로 익명화했고 데이터 manifest 해시는 보고서 파일과 무관하다.

### D. 수정 후 테스트 (로컬, `<repo>\.venv` Python 3.12.13)
| 범위 | 결과 |
|---|---|
| 신규 리뷰 회귀 `test_issue103_review_feedback.py` | 15 passed |
| 기존 #103 회귀(guidance·replay·verifier) | 28 passed (신규 포함 43 passed, 70 subtests) |
| PR 브랜치 전체 unit+integration | 2300 passed, 129 skipped, **1 failed(기존)** |
| 최신 dev `befe2d0` + PR + 이번 변경 임시 병합 전체 | 3104 passed, 129 skipped, **1 failed(기존)** |
| 임시 병합: #112 자녀 월령·#133 official_requirements·Checker·Generator·ask API·catalog·Pipeline | 333 passed (doc_gov_048 1건 제외) |
| `py_compile` | PASS |
| ruff/flake8 | 미설치로 **미실행** |

기존 실패 1건: `test_rag_pipeline.py::test_regenerated_doc_gov_048_chunks_reach_api_response_intact` — `data/raw/gov24/items/449000000108.json`이 `origin/dev`에도 추적되지 않아 동일하게 발생. **신규 실패 0건.**
병합 정합성: `git merge-tree` 기준 최신 dev와 충돌 없음. #78 후속 `fix/78-exclusion-reasons-api`(`b2bb499`)와도 `backend/rag/pipeline.py`는 자동 병합(그 브랜치는 주석 2줄만 추가), 단 그 브랜치와 dev 사이 `docs/api/ask_contract.md` 충돌은 이 PR과 무관. #78 병합 후 `pytest tests/integration/test_issue103_review_feedback.py tests/integration/test_issue103_guidance.py tests/integration/test_rag_pipeline.py`로 재확인 필요.

### E. 남은 검증
- 실제 R3D 재검증: **미실행** (이번 반영은 기록된 R3D 응답·동결 릴리스 재생만 사용).
- 실제 LLM 재검증: **미실행** (마무리 문구·혼합 안내는 가상 생성 결과로 검증).
- React 브라우저 검증: **미실행** (응답 스키마 변경 없음).
- 방문 접수처 동의어(예: 답변 "행정복지센터" vs 원문 "주민센터")는 보수적으로 방문 안내를 남기지 않는다. 방문만 안내하는 문장의 원문 근거 검증은 이번 범위 밖(기존 동작 유지).
