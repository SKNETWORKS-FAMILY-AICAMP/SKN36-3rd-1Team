# Issue #91 — r2 격리 Neo4j 적재와 Qdrant 교차 검증 절차 (Windows PowerShell)

이 문서는 실행 절차서다. 작성 시점(2026-11-19)에는 아무 명령도 실행하지 않았다.
단계마다 결과를 확인하고 기대와 다르면 **그 자리에서 멈춘다**. 쓰기 단계는 `[쓰기]`로 표시했다.

| 항목 | 값 |
|---|---|
| Compose 파일 | `docs/development/issue91_r2_neo4j.compose.yaml` (#91 Qdrant와 별도 프로젝트) |
| 프로젝트 이름 | `issue91-r2-neo4j-20261119` (볼륨 `…_neo4j_data`, `…_neo4j_logs`) |
| 이미지 | `neo4j:5.26-community@sha256:42ef3ebf…` = Neo4j Community 5.26.32 (r1 복원과 같은 digest) |
| Bolt 주소 | `bolt://127.0.0.1:56487` (스크립트는 이 주소만 허용, HTTP 7474는 열지 않음) |
| 비밀번호 | `ISSUE91_NEO4J_PASSWORD` (공유 DB의 `NEO4J_PASSWORD`와 분리). 현재 사용자 DPAPI 파일로 보관 |
| 스크립트 | `scripts/load_neo4j_r2_isolated.py` (기존 `load_graph`·`verify_loaded_graph`·`verify_cross_db` 재사용) |
| 기대 건수 | 노드 1,126 · 관계 2,668 · REQUIRES_CONDITION 211 (정책 246·문서 262·릴리스 청크 600·B2 1,139) |
| 교차 검증 Qdrant | `http://127.0.0.1:56446` (#91 Qdrant, 1,139 Point, 읽기만) |
| 보고서 위치 | `.cache/issue91-r2-20261119/` (Git 제외) |

금지: `docker compose down -v`, `docker volume rm`, 기존 프로젝트(`skn36-3rd-1team`, `lifepolicy-*`, `issue91-r2-qdrant-*`)에
대한 명령, `.env`의 `NEO4J_URI`로 접속하는 다른 스크립트(`restore_final_v140.py`, `hybrid_search` 등)로 이 DB에 쓰는 것.

## 1. Docker 사전 점검 — 읽기 전용

```powershell
Set-Location C:\dev\project\SKN36-3rd-1Team
# 작업 브랜치와 #91 테스트 (DB 접속 없음).
git branch --show-current
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\unit\test_load_neo4j_r2_isolated.py tests\unit\test_update_b2_from_snapshot.py tests\unit\test_seed_b2_from_r1_vectors.py
# Docker 엔진 응답, 이 프로젝트 이름의 컨테이너·볼륨·네트워크가 아직 없어야 한다 (세 줄 모두 빈 결과).
docker version --format '{{.Server.Version}}'
docker ps -a --filter name=issue91-r2-neo4j --format '{{.Names}}'
docker volume ls --filter name=issue91-r2-neo4j --format '{{.Name}}'
docker network ls --filter name=issue91-r2-neo4j --format '{{.Name}}'
# 56487 포트가 비어 있어야 한다 (출력 없음).
Get-NetTCPConnection -LocalPort 56487 -ErrorAction SilentlyContinue
# 이미지가 로컬에 있어야 한다 (없으면 멈추고 보고: 자동 다운로드하지 않는다).
docker image inspect neo4j@sha256:42ef3ebfd671ebd01fdebb6dcbe80cf7b45eef2aa3d346ef4132f7ef31e67d2f --format '{{.Id}}'
# 기존 컨테이너 상태 기록 (이후 비교용).
docker inspect --format '{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}' $(docker ps -aq) |
    Out-File -Encoding utf8 .cache\issue91-r2-20261119\20-before-neo4j-state.txt
```

## 2. [쓰기] 새 Neo4j 컨테이너 생성

```powershell
# 이 창에서만 쓰는 무작위 비밀번호(64자리 16진수)를 만들고, 현재 사용자 DPAPI 파일로 보관한다 (평문 출력 없음).
$pwFile = '.cache\issue91-r2-20261119\neo4j_password.clixml'
if (Test-Path $pwFile) { throw 'STOP: 비밀번호 파일이 이미 있음 (이전 시도 확인)' }
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:ISSUE91_NEO4J_PASSWORD = -join ($bytes | ForEach-Object { $_.ToString('x2') })
Remove-Variable bytes
ConvertTo-SecureString $env:ISSUE91_NEO4J_PASSWORD -AsPlainText -Force | Export-Clixml $pwFile
# 구문만 검사 (--quiet 를 빼면 비밀번호가 화면에 나온다).
docker compose -f docs\development\issue91_r2_neo4j.compose.yaml -p issue91-r2-neo4j-20261119 config --quiet
# 새 프로젝트만 시작한다. Qdrant 프로젝트(issue91-r2-qdrant-20261119)는 이 명령의 대상이 아니다.
docker compose -f docs\development\issue91_r2_neo4j.compose.yaml -p issue91-r2-neo4j-20261119 up -d
docker compose -f docs\development\issue91_r2_neo4j.compose.yaml -p issue91-r2-neo4j-20261119 ps
# 기동 완료 대기: 로그에 "Started." 가 나올 때까지 최대 90초 (읽기 전용).
for ($i = 0; $i -lt 45; $i++) {
    if ((docker logs issue91-r2-neo4j-20261119-neo4j-1 2>&1 | Select-String -SimpleMatch 'Started.')) { 'started'; break }
    Start-Sleep -Seconds 2
}
```

다른 창에서 이어 할 때는 비밀번호를 DPAPI 파일에서 복원한다(평문 출력 없음, BSTR 해제).

```powershell
$sec = Import-Clixml .cache\issue91-r2-20261119\neo4j_password.clixml
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { $env:ISSUE91_NEO4J_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
Remove-Variable sec, bstr
```

## 3·4. 인증·버전·빈 DB 검증과 적재 전 dry-run — 읽기 전용

```powershell
# 틀린 무작위 비밀번호가 거부되는지(auth_enforced), Neo4j 5.26.32 community, 빈 DB(state=empty),
# 최신 릴리스 건수(1,126 / 2,668 / 211)를 확인하고 fingerprint를 출력한다. 쓰기 0회.
.\.venv\Scripts\python.exe scripts\load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487 `
    --report .cache\issue91-r2-20261119\21-neo4j-dry-empty.json
```

기대: `status=PASS`, `auth_enforced=true`, `neo4j.version=5.26.32`, `state=empty`, `totals={nodes:0, relationships:0}`,
`expected_totals={nodes:1126, relationships:2668}`, `expected_requires_condition=211`, `fingerprint`.

## 5. [쓰기] 실제 적재

```powershell
# 직전 dry-run의 fingerprint를 넣는다. 쓰기 직전 같은 드라이버로 다시 계산해 다르면 쓰지 않는다.
# 빈 DB에만 적재하고, 적재 후 verify_loaded_graph(건수·스키마·ID·관계 양끝·속성값)를 함께 실행한다.
.\.venv\Scripts\python.exe scripts\load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487 `
    --load --confirm-write --expect-fingerprint <21번 보고서의 fingerprint> `
    --report .cache\issue91-r2-20261119\22-neo4j-load.json
```

기대: `result.status=LOADED`, `result.totals={nodes:1126, relationships:2668}`, `verification_ok=true`, 이어서 `state=complete`.

중간 실패(`FAILED`, `GraphLoadError`) 시: 아무것도 지우지 않는다. dry-run을 다시 실행해 `state`를 본다.
`partial`이면 그 fingerprint로 승인 후 `--resume --confirm-write --expect-fingerprint <새 값>`(입력의 부분집합인지
다시 확인하고 이어서 완성). `foreign_data`·`schema_conflict`면 멈추고 보고한다(새 프로젝트 이름으로 다시 시작).

## 6. Neo4j 단독 검증 — 읽기 전용

```powershell
.\.venv\Scripts\python.exe scripts\load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487 --verify `
    --report .cache\issue91-r2-20261119\23-neo4j-verify.json
```

기대: `ok=true`, `totals={nodes:1126, relationships:2668}`, `count_mismatches={}`, `checks`의 다섯 항목 모두 `true`.

## 7. Qdrant 교차 검증 — 두 DB 모두 읽기 전용

```powershell
# #91 Qdrant 키를 DPAPI 파일에서 복원한다 (평문 출력 없음).
$sec = Import-Clixml .cache\issue91-r2-20261119\qdrant_api_key.clixml
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { $env:QDRANT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
Remove-Variable sec, bstr
# Qdrant 격리 서버·1,139 Point·전체 벡터 digest(8577c3c8…) 확인 → Neo4j verify_loaded_graph 통과 확인
# → verify_cross_db(정책·문서·SUPPORTED_BY·content_hash) + verify_full_index(원문 슬라이스) → 검증 전후 Qdrant 불변 확인.
.\.venv\Scripts\python.exe scripts\load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487 `
    --cross-db --qdrant-url http://127.0.0.1:56446 --report .cache\issue91-r2-20261119\24-cross-db.json
# 끝나면 이 창의 키를 지운다.
Remove-Item Env:QDRANT_API_KEY, Env:ISSUE91_NEO4J_PASSWORD
```

기대: `status=PASS`, `qdrant_points=1139`, `qdrant_unchanged=true`, `cross_db.status=PASS`,
`cross_db.summary`의 7개 검사 모두 PASS 1,139, `full_index_verification.passed=true`.

## 멈춤 기준과 안전한 중단

| 증상 | 판단 |
|---|---|
| 1단계에 `issue91-r2-neo4j` 자원이 이미 있음, 56487 사용 중, 이미지 없음 | 멈춤. 지우거나 내려받지 말고 보고 |
| `auth_enforced`가 true가 아님, 버전이 5.26.32 community가 아님 | 멈춤 |
| 4단계 `state`가 `empty`가 아님 | 멈춤 (5단계 진행 금지) |
| 5단계 `VERIFY_FAILED`·`FAILED` | 멈춤. 데이터 보존, dry-run으로 상태 확인 후 보고 |
| 7단계 Qdrant digest·Point 수 불일치, `cross_db` MISMATCH | 멈춤. Qdrant는 쓰지 않았으므로 그대로 두고 보고 |

```powershell
# 중단 시 컨테이너만 정지한다 (데이터 보존). down -v, docker volume rm 은 쓰지 않는다.
docker compose -f docs\development\issue91_r2_neo4j.compose.yaml -p issue91-r2-neo4j-20261119 stop
```

## 리소스

Neo4j 컨테이너는 메모리 1,280MiB(힙 128~256MiB, 페이지 캐시 128MiB)·CPU 2로 제한한다. r1 복원 구성과 같은 값이며
노드 1,126·관계 2,668 규모에 충분하다. 현재 실행 중인 기존 컨테이너(약 3.8GiB)와 #91 Qdrant(1GiB 제한)를 합쳐도
Docker 엔진 메모리(15.47GiB) 안에 들어간다.
