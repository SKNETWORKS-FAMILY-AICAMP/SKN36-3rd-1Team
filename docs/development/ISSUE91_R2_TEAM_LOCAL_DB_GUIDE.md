# 팀원용 로컬 DB 배포 가이드 — r2 번들로 Neo4j·Qdrant 복원하고 SSOK 백엔드 연결하기

`rag-final-v140-r2-027f40b556c5.zip` 하나로 내 Windows PC에 최신 v1.4.0-candidate DB(Neo4j·Qdrant)를 만들고,
SSOK 백엔드를 그 DB에 연결하는 절차다. 명령은 모두 **Windows PowerShell**에서 실행한다.

> 이 절차는 작성자 PC(Windows 11, Python 3.13.15)에서 같은 번들로 복원·검증까지 통과했다.
> **다른 PC에서는 아직 실행해 보지 않았다.** 막히는 단계가 있으면 그 단계에서 멈추고 출력(비밀값 제외)을 공유해 달라.

## 0. 한눈에 보기

| 항목 | 값 |
|---|---|
| 받을 파일 | `rag-final-v140-r2-027f40b556c5.zip`, `rag-final-v140-r2-027f40b556c5.zip.sha256` |
| ZIP SHA-256 | `dd28f8cd14c242bda2e6d5697d5cbf23725e6b7dee7b72f4781830429c579197` |
| 소스 커밋 | `027f40b556c5408a2f0b9547acef4964254e37b8` |
| 만들어지는 Docker 프로젝트 | `issue91-r2-verify-20261119` (Qdrant + Neo4j, 새 볼륨 3개) |
| 내 PC 주소 | Qdrant `http://127.0.0.1:56556`, Neo4j `bolt://127.0.0.1:56587` (변경 불가, 아래 §2) |
| 복원 성공 기준 | Qdrant 1,139 Point · Neo4j 1,126 노드 · 2,668 관계 · vector_digest `8577c3c8…a3d3` 일치 · 교차 검증 PASS |
| 필요 메모리 | Qdrant 최대 1GiB + Neo4j 최대 1.25GiB |

필요한 것

- Docker Desktop (실행 중), PowerShell, Python 3.13(`py -3.13` 실행 가능), 인터넷(처음 한 번 이미지·패키지 받기)
- 검색을 쓰려면 Ollama와 `qwen3-embedding:0.6b`, 답변까지 보려면 OpenAI API 키 (복원 자체에는 필요 없음, §8)

## 1. 받을 파일과 공유 주의사항 (배포 담당자용)

- 보내는 파일: ZIP과 `.sha256` **두 개만**. ZIP에는 인증 정보가 없다(번들 6개 파일: snapshot, 소스 ZIP, `compose.yaml`,
  `restore_final_v140.py`, `package.json`, `SHA256SUMS`).
- 보내지 않는 것: `.cache`의 `*.clixml`(작성자 PC의 DPAPI 인증 파일, 다른 사용자는 복호화도 못 한다), `.report.json`(작성자 PC 경로 포함), `.env`.
- ZIP은 Git·PR에 넣지 않는다(24MB). 팀이 정한 파일 공유 채널을 쓰고, SHA-256 값은 가능하면 다른 경로(PR 본문·이 문서)로 함께 확인한다.
- `rag-final-v140-r2-fa37b8db47f8.zip`은 이전 후보이므로 보내지 않는다.

## 2. 먼저 알아둘 것 — 보호 래퍼와 고정값

복원은 소스 안의 `scripts/restore_r2_isolated.py`(보호 래퍼)로 한다. 래퍼는 기존 `restore_final_v140.py`의 복원 함수를 그대로 호출하되,
그 앞뒤에서 아래를 강제한다.

- 접속 주소는 `127.0.0.1:56556`(Qdrant)·`127.0.0.1:56587`(Neo4j Bolt)만 쓴다. 6333·7687 같은 기존·공유 DB 포트는 거부한다.
  내 `.env`나 PowerShell의 `QDRANT_URL`·`NEO4J_URI` 값은 **무시**하고 복원 동안만 위 주소로 덮어쓴다.
- 두 포트의 컨테이너가 정확히 `issue91-r2-verify-20261119` 프로젝트의 qdrant·neo4j이고, 고정 이미지 digest·127.0.0.1 바인딩·
  이 프로젝트 전용 볼륨만 쓰는지 Docker로 확인한다.
- 두 DB가 **완전히 비어 있을 때만** 쓴다. preflight가 출력한 fingerprint를 넣어야 쓰기가 실행된다.

고정값 때문에 생기는 제약 (팀원 PC에서 그대로 쓸 수 있는지 분석한 결과)

| 상황 | 결과 | 대응 |
|---|---|---|
| 56556·56587이 비어 있음 | 그대로 사용 가능 | — |
| 두 포트 중 하나를 다른 프로그램이 사용 중 | 래퍼가 거부(포트 변경 옵션 없음) | 그 프로그램을 멈출 수 있으면 멈추고, 아니면 멈추고 문의 |
| 같은 이름 프로젝트가 이미 있음(예: 이전에 실패한 시도) | 래퍼가 거부 | §10 참고. 임의로 지우지 말고 먼저 확인 |
| Docker CLI가 PATH에 없음 | 소유 확인 불가로 거부 | Docker Desktop 설치·실행 확인 |

**보호되지 않는 경로 경고**: 번들의 `compose.yaml`(`issue43_restore.compose.yaml`)과 `restore_final_v140.py`를 래퍼 없이 직접 쓰는 옛 절차
([ISSUE43_RESTORE_GUIDE.md](ISSUE43_RESTORE_GUIDE.md))도 남아 있다. 이 경로는 `QDRANT_URL`·`NEO4J_URI` 환경 변수가 가리키는
**아무 loopback DB**에나 접속한다(포트 제한 없음). 셸에 개발용 공유 DB(6333/7687) 값이 남아 있으면 그 DB로 접속하게 되고,
빈 DB 검사만이 마지막 방어선이다. r2는 래퍼 경로만 쓴다.

## 3. ZIP 확인과 압축 풀기

경로는 예시다. 짧은 경로(예: `C:\ssok-r2`)를 권장한다(소스에 한글·긴 파일 이름이 있다).

```powershell
# 받은 두 파일이 있는 폴더에서 시작한다.
$zip  = (Resolve-Path .\rag-final-v140-r2-027f40b556c5.zip).Path
$work = 'C:\ssok-r2'                                     # 새 폴더여야 한다
if (Test-Path $work) { throw 'STOP: 작업 폴더가 이미 있음 (새 경로 사용)' }

# 1) ZIP SHA-256 확인 (.sha256 파일과 이 문서의 값 둘 다와 같아야 한다)
$expected = 'dd28f8cd14c242bda2e6d5697d5cbf23725e6b7dee7b72f4781830429c579197'
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected -or (Get-Content "$zip.sha256").Split(' ')[0] -ne $expected) { throw 'STOP: ZIP SHA-256 불일치' }

# 2) 압축 안 파일 목록 확인: 정확히 6개, 하위 경로·'..' 없음
Add-Type -AssemblyName System.IO.Compression.FileSystem
$reader = [IO.Compression.ZipFile]::OpenRead($zip)
try { $names = $reader.Entries | ForEach-Object FullName } finally { $reader.Dispose() }
$names
if ($names.Count -ne 6 -or ($names | Where-Object { $_ -match '[\\/]|\.\.' })) { throw 'STOP: ZIP 구성 이상' }

# 3) 번들 풀기 + 번들 안 SHA256SUMS 확인
Expand-Archive $zip "$work\bundle"
Get-Content "$work\bundle\SHA256SUMS" | ForEach-Object {
    if (-not $_.Trim()) { return }
    $sha, $name = $_ -split '  ', 2
    if ((Get-FileHash -LiteralPath "$work\bundle\$name" -Algorithm SHA256).Hash.ToLower() -ne $sha) { throw "STOP: $name 체크섬 불일치" }
}

# 4) 소스 풀기 (복원·백엔드는 이 소스로 실행한다)
Expand-Archive "$work\bundle\source-027f40b556c5.zip" "$work\src"
$src = "$work\src"; $bundle = "$work\bundle"
```

## 4. Python 환경 (처음 한 번)

패키지 설치는 본인이 승인한 뒤 실행한다. 소스 폴더 안에 가상환경을 만든다.

```powershell
py -3.13 -m venv "$src\.venv"
$python = "$src\.venv\Scripts\python.exe"
& $python -m pip install -r "$src\requirements.txt"
```

## 5. Docker 사전 점검 — 읽기 전용

```powershell
docker version --format '{{.Server.Version}}'                                            # 엔진 응답
docker ps -a --filter "label=com.docker.compose.project=issue91-r2-verify-20261119" -q   # 빈 결과여야 함
docker volume ls -q --filter name=issue91-r2-verify-20261119                              # 빈 결과여야 함
foreach ($p in 56556, 56587) { Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue }   # 출력 없어야 함
```

기존 개발용 DB(`docker-compose.yml`, 6333/7687)나 다른 프로젝트 컨테이너는 **멈추거나 지울 필요가 없다**. 이 절차는 새 프로젝트·새 볼륨만 만든다.

## 6. 인증 정보 만들기와 DB 실행

내 PC 전용 Qdrant API 키와 Neo4j 비밀번호를 난수로 만들고, 내 Windows 계정만 열 수 있는 DPAPI 파일로 보관한다(화면에 출력하지 않는다).
**작업 폴더 밖이나 `$work\secrets`처럼 공유하지 않는 곳**에 둔다. 개발용 공유 DB 비밀번호를 재사용하지 않는다.

```powershell
New-Item -ItemType Directory "$work\secrets" | Out-Null
function New-Secret { $b = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) }
$env:ISSUE91_VERIFY_QDRANT_API_KEY = New-Secret
$env:ISSUE91_VERIFY_NEO4J_PASSWORD = New-Secret
ConvertTo-SecureString $env:ISSUE91_VERIFY_QDRANT_API_KEY -AsPlainText -Force | Export-Clixml "$work\secrets\qdrant_api_key.clixml"
ConvertTo-SecureString $env:ISSUE91_VERIFY_NEO4J_PASSWORD -AsPlainText -Force | Export-Clixml "$work\secrets\neo4j_password.clixml"

$compose = "$src\docs\development\issue91_r2_verify.compose.yaml"
docker compose -f $compose -p issue91-r2-verify-20261119 config --quiet     # --quiet 를 빼면 비밀값이 화면에 나온다
docker compose -f $compose -p issue91-r2-verify-20261119 up -d              # 처음이면 고정 digest 이미지를 내려받는다
# Neo4j 기동 대기 (로그에 'Started.' 가 보일 때까지 최대 2분)
for ($i = 0; $i -lt 60; $i++) { if ((docker logs issue91-r2-verify-20261119-neo4j-1 2>&1 | Out-String) -match 'Started\.') { 'started'; break }; Start-Sleep 2 }
```

새 PowerShell 창에서 이어 할 때는 DPAPI 파일에서 다시 불러온다(§11의 `Restore-R2Secrets`).

## 7. 복원 — preflight → 복원 → 재검증

`QDRANT_API_KEY`·`NEO4J_PASSWORD`(백엔드용 변수)를 이 창에 아직 설정하지 않는다. 래퍼는 검증용 값과 같은 값이 그 변수에 있으면
"재사용"으로 보고 거부한다(§9에서 백엔드용 값을 설정한다).

```powershell
Set-Location $src
# (1) 읽기 전용 preflight: 실패하면 여기서 멈춘다. 출력의 fingerprint 값을 복사한다.
& $python scripts\restore_r2_isolated.py --repo $src --bundle $bundle --report "$work\01-preflight.json"
# (2) 실제 복원 (쓰기): <fingerprint>에 (1)의 값을 넣는다. 한 번만 실행한다.
& $python scripts\restore_r2_isolated.py --repo $src --bundle $bundle --apply --confirm-write --expect-fingerprint <fingerprint> --report "$work\02-apply.json"
"exit=$LASTEXITCODE"
# (3) 읽기 전용 재검증
& $python scripts\restore_r2_isolated.py --repo $src --bundle $bundle --verify-only --report "$work\03-verify.json"
```

### 성공 기준 (`02-apply.json`, `03-verify.json`)

- 종료 코드 0, `"status": "PASS"`, `post_verify.ok = true`
- Qdrant: 컬렉션 `exp_b2_qwen3emb06b_v140_v1`, **1,139 Point**, `vector_digest` = `8577c3c8698fcb891fb35544eac01ec02066fb5c2ef0313d15b6a26e576da3d3`
- Neo4j: `graph_totals` = **1,126 노드 · 2,668 관계**(REQUIRES_CONDITION 211 포함, 레이블별 건수는 `verify_loaded_graph`가 입력과 대조)
- 교차 검증: `cross_db.status = PASS`, 7개 항목 각각 PASS 1,139, `failures = []`
- `verify_full_index.passed = true`

복원 단계에서는 임베딩을 하지 않는다(`embedding_calls = 0`, 벡터는 snapshot에 들어 있다).

## 8. Ollama가 필요한 경우와 필요 없는 경우

| 작업 | Ollama | 이유 |
|---|---|---|
| §7 복원·검증 | **불필요** | 벡터 1,139개가 snapshot에 들어 있고, 복원 도구는 임베딩을 호출하지 않는다 |
| 백엔드 검색(질문하기) | **필요** | 질문 문장을 `qwen3-embedding:0.6b`(1024차원)로 임베딩해 Qdrant에서 찾는다. 다른 모델·차원이면 검색이 거부되거나 결과가 맞지 않는다 |
| 문서 추가·재색인 | 필요 | 새 청크를 임베딩한다. 복원된 DB에 직접 재색인하지 않는다(이번 배포 범위 밖, 담당자와 협의) |

검색용 모델 확인(설치·다운로드는 본인 승인 후):

```powershell
ollama list        # qwen3-embedding:0.6b 이 있어야 한다. ID가 ac6da0dfba84 로 시작하면 작성자 PC와 같은 모델이다
```

## 9. 백엔드 연결

백엔드는 **압축을 푼 소스(`$src`)**에서 실행하는 것을 권장한다. 데이터 파일(manifest `d48ef11b…`)이 복원한 DB와 같은 커밋이기 때문이다.
내 저장소 체크아웃에서 실행하려면 같은 데이터인지 먼저 확인한다(아래 마지막 명령).

백엔드가 실제로 읽는 DB 관련 변수(코드 확인: `database/qdrant/store.py`, `database/hybrid_search.py`, `database/embedding.py`, `backend/api/main.py`):
`QDRANT_URL`, `QDRANT_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `OLLAMA_BASE_URL`, `LIFEPOLICY_DATASET_DIR`.
답변 생성에는 `OPENAI_API_KEY`(+선택 `LLM_MODEL` 등)가 필요하다. 백엔드는 소스 루트의 `.env`를 읽되, **이미 설정된 환경 변수를 덮어쓰지 않는다.**

`.env` 예시 (비밀값 없음, `$src\.env`로 저장. 실제 값은 넣되 공유·커밋하지 않는다):

```dotenv
# r2 로컬 DB (이 가이드로 만든 컨테이너)
QDRANT_URL=http://127.0.0.1:56556
# §6에서 만든 Qdrant 키. 아래처럼 세션 변수로 넣으면 비워 둬도 된다
QDRANT_API_KEY=
NEO4J_URI=bolt://127.0.0.1:56587
NEO4J_USER=neo4j
# §6에서 만든 Neo4j 비밀번호. 세션 변수 권장
NEO4J_PASSWORD=
# 검색용 임베딩
OLLAMA_BASE_URL=http://127.0.0.1:11434
# 비우면 승인 v1.4.0(data/releases/v1.4.0-candidate) 사용
LIFEPOLICY_DATASET_DIR=
# 답변 생성
OPENAI_API_KEY=
```

비밀번호·키를 `.env` 평문으로 두지 않으려면 DPAPI 파일에서 세션 변수로 넣고 실행한다(세션 변수가 `.env`보다 우선한다).

```powershell
Set-Location $src
$env:QDRANT_URL = 'http://127.0.0.1:56556'; $env:NEO4J_URI = 'bolt://127.0.0.1:56587'; $env:NEO4J_USER = 'neo4j'
$env:QDRANT_API_KEY = Read-R2Secret "$work\secrets\qdrant_api_key.clixml"      # §11의 함수
$env:NEO4J_PASSWORD = Read-R2Secret "$work\secrets\neo4j_password.clixml"
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
& $python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

확인: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)는 `status: ok`. `ready`는 정책이 검색되는 첫 질문에 성공한 뒤 `true`가 된다.
v1.4.0 데이터셋의 기본 검색 프로필은 `b2_06b_v140`(컬렉션 `exp_b2_qwen3emb06b_v140_v1`)이다. 로그 설정에 따라
`검색 세션 설정: profile=… collection=…` 로그가 보이면 이 값인지 확인한다.

백엔드용 변수(`QDRANT_API_KEY`·`NEO4J_PASSWORD`)를 설정한 창에서는 복원 래퍼를 실행하지 않는다. 래퍼는 검증용 값과 같은 값이
그 변수에 있으면 인증 재사용으로 보고 거부한다. 재검증(`--verify-only`)은 새 창에서 §11로 시작해 실행한다.
프론트엔드 연결은 저장소 README의 "전체 로컬 실행" 절을 따른다.

내 저장소 체크아웃에서 백엔드를 돌릴 때 데이터가 같은지 확인(같아야 `d48ef11b599466b394bef15e275be26b24be9dfde1e74771c86b8e3d384acf50`):

```powershell
& <내 저장소>\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from database import full_index as fi; print(fi.manifest_sha256())"
```

## 10. 주의사항

- **기존 볼륨 보호**: `docker compose down -v`, `docker volume rm`, `docker system prune`을 쓰지 않는다. 멈출 때는 `stop`만 쓴다.
- **포트 충돌**: 56556·56587은 래퍼에 고정돼 있다. 다른 포트로 바꿔 띄우면 래퍼가 거부한다(§2).
- **이미 데이터가 있는 DB**: 래퍼는 비어 있지 않은 DB에 쓰지 않는다. 기존 데이터를 지워서 맞추지 않는다.
- **실패 후 재실행 금지**: `--apply`가 실패하면 다시 실행하지 않는다. 두 DB 사이에 원자적 처리가 없어 일부만 들어가 있을 수 있고,
  두 번째 `--apply`는 "비어 있지 않음"으로 거부된다. `02-apply.json`(비밀값 없음)을 공유하고 문의한다.
  같은 프로젝트 이름은 래퍼에 고정이라, 다시 시도하려면 실패한 `issue91-r2-verify-20261119` 컨테이너와 그 볼륨 3개를 정리해야 한다.
  이 볼륨들은 이 절차가 만든 것이지만, 정리는 원인을 확인한 뒤 본인이 결정한다.
- **비밀값**: 키·비밀번호를 채팅·문서·Git·스크린샷에 남기지 않는다. `docker compose config`는 `--quiet`로만 쓴다.
- **PC 재부팅 후**: 컨테이너는 자동으로 다시 켜지지 않는다(`restart: "no"`). §11로 인증 변수를 불러온 뒤
  `docker compose -f $compose -p issue91-r2-verify-20261119 start` 로 켠다(`up`이 아니라 `start`).

## 11. 새 PowerShell 창에서 이어 하기

```powershell
$work = 'C:\ssok-r2'; $src = "$work\src"; $bundle = "$work\bundle"; $python = "$src\.venv\Scripts\python.exe"
$compose = "$src\docs\development\issue91_r2_verify.compose.yaml"
function Read-R2Secret($file) {
    $sec = Import-Clixml $file
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
function Restore-R2Secrets {
    $env:ISSUE91_VERIFY_QDRANT_API_KEY = Read-R2Secret "$work\secrets\qdrant_api_key.clixml"
    $env:ISSUE91_VERIFY_NEO4J_PASSWORD = Read-R2Secret "$work\secrets\neo4j_password.clixml"
}
Restore-R2Secrets      # docker compose start/config, 래퍼 실행 전에 필요
```

## 12. 다른 PC에서 아직 확인하지 못한 것

작성자 PC(같은 Windows PC의 별도 Docker 프로젝트)에서 확인된 것: ZIP 무결성, 새 프로젝트 생성, preflight·복원·재검증
(위 성공 기준 전부), 기존 DB 불변, 그리고 복원 DB에 대한 Hybrid 검색·checker 스모크 19개 PASS(실패 0·skip 0,
교육부 2026 신규 문서 검색 포함, 실행 전후 DB 불변). 스모크 실행 방법과 결과는
[검증 기록 §10](ISSUE91_R2_RESTORE_VALIDATION_20261119.md#10-독립-환경-복원과-결과)에 있다.

아래는 **아직 확인하지 않았다.**

- 다른 PC·OS에서의 전체 절차, 이미지 최초 다운로드(작성자 PC는 같은 digest 이미지가 이미 있었다)
- 소스 폴더 안 새 가상환경에 `requirements.txt` 설치(작성자는 기존 프로젝트 가상환경으로 래퍼·스모크를 실행했다)
- 다른 Windows 사용자 계정·경로(한글·공백 경로, 긴 경로)에서의 압축 해제와 DPAPI 동작
- §9의 HTTP 백엔드 → LLM(OpenAI) → 프론트엔드 전체 E2E(스모크는 검색·checker까지만 확인)
- Windows 10, 다른 Docker Desktop 버전, 메모리가 적은 PC
