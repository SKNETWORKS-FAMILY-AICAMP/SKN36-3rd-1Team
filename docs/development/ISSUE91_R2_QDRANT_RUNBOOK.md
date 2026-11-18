# Issue #91 — r2 격리 Qdrant 준비와 pr29 snapshot 복원 절차 (Windows PowerShell)

이 문서는 실행 절차서다. 작성 시점(2026-11-19)에는 아무 명령도 실행하지 않았다.
단계마다 결과를 확인하고, 기대와 다르면 **그 자리에서 멈춘다**. 쓰기 단계는 `[쓰기]`로 표시했다.

| 항목 | 값 |
|---|---|
| Compose 파일 | `docs/development/issue91_r2_qdrant.compose.yaml` |
| 프로젝트 이름 | `issue91-r2-qdrant-20261119` (볼륨 `issue91-r2-qdrant-20261119_qdrant_storage`) |
| 이미지 | `qdrant/qdrant:v1.12.4@sha256:241edb9d…` (pr29 snapshot을 만든 1.12.4) |
| 주소 | `http://127.0.0.1:56446` (스크립트는 이 포트만 허용) |
| snapshot | `venv/issue31_20261109/rag-local-v140-pr29-20261109/b2-v1.12.4.snapshot`, SHA-256 `4f7a67ea…c3c` |
| 보고서 위치 | `.cache/issue91-r2-20261119/` (Git 제외) |

금지: `docker compose down -v`, `docker volume rm`, 기존 프로젝트(`lifepolicy-*`, `issue43-*`, `issue41-*`)에 대한 명령,
`python -m database.full_index ... --execute` 직접 실행(격리 확인이 없다. 4개 임베딩은 이후 단계의 `--index`로만 한다).

## 0. 준비 — 읽기 전용

```powershell
# 프로젝트 루트로 이동한다.
Set-Location C:\dev\project\SKN36-3rd-1Team
# 작업 브랜치인지 확인한다. feature/91-final-db-restore-r2 가 나와야 한다.
git branch --show-current
# DB 없이 도는 #91 테스트. 실패가 있으면 멈춘다.
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\unit\test_update_b2_from_snapshot.py tests\unit\test_seed_b2_from_r1_vectors.py
# pr29 ZIP과 snapshot 파일의 SHA-256. 각각 a9c55d29…3694 / 4f7a67ea…c3c 이어야 한다.
(Get-FileHash .\venv\issue31_20261109\rag-local-v140-pr29-20261109.zip -Algorithm SHA256).Hash.ToLower()
(Get-FileHash .\venv\issue31_20261109\rag-local-v140-pr29-20261109\b2-v1.12.4.snapshot -Algorithm SHA256).Hash.ToLower()
# 보고서 폴더 (Git 제외 위치). 이미 있으면 그대로 둔다.
New-Item -ItemType Directory -Force .cache\issue91-r2-20261119 | Out-Null
```

## 1. 기존 Docker 상태 확인 — 읽기 전용

Docker Desktop은 사용자가 직접 켠다. 아래 명령은 목록만 본다.

```powershell
# Docker 엔진이 응답하는지 (버전 번호만 출력).
docker version --format '{{.Server.Version}}'
# 모든 컨테이너 목록. issue91-r2-qdrant 로 시작하는 컨테이너가 없어야 한다.
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
# 이 프로젝트 이름의 볼륨·네트워크·Compose 프로젝트가 아직 없어야 한다 (세 줄 모두 빈 결과).
docker volume ls --filter name=issue91-r2-qdrant --format '{{.Name}}'
docker network ls --filter name=issue91-r2-qdrant --format '{{.Name}}'
docker compose ls --all --filter name=issue91-r2-qdrant
```

## 2. 56446 포트 충돌 확인 — 읽기 전용

```powershell
# 56446을 쓰는 프로세스가 없어야 한다 (아무것도 출력되지 않으면 정상).
Get-NetTCPConnection -LocalPort 56446 -ErrorAction SilentlyContinue
```

## 3. API 키 준비와 Compose 구문 검사 — 컨테이너를 만들지 않음

```powershell
# 이 PowerShell 창에서만 쓰는 무작위 API 키(64자리 16진수)를 만든다. 화면·파일에 출력하지 않는다.
# 창을 닫으면 키가 사라지므로 7~8단계가 끝날 때까지 같은 창을 쓴다.
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:QDRANT_API_KEY = -join ($bytes | ForEach-Object { $_.ToString('x2') })
Remove-Variable bytes
# Compose 파일 구문만 검사한다. --quiet 를 빼면 키가 화면에 풀려 나오므로 반드시 붙인다.
docker compose -f docs\development\issue91_r2_qdrant.compose.yaml config --quiet
```

## 4. [쓰기] 새 Compose 환경 시작 — 새 컨테이너·볼륨·네트워크 생성

```powershell
# 새 프로젝트로만 시작한다. 처음이면 이미지를 digest로 내려받는다.
docker compose -f docs\development\issue91_r2_qdrant.compose.yaml -p issue91-r2-qdrant-20261119 up -d
# 컨테이너가 running 이고 포트가 127.0.0.1:56446->6333 인지 본다.
docker compose -p issue91-r2-qdrant-20261119 ps
# 실제로 받은 이미지 digest. sha256:241edb9d… 가 들어 있어야 한다.
docker image inspect qdrant/qdrant@sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636 --format '{{json .RepoDigests}}'
# 새 볼륨이 이 프로젝트 이름으로 만들어졌는지 (issue91-r2-qdrant-20261119_qdrant_storage).
docker volume ls --filter name=issue91-r2-qdrant --format '{{.Name}}'
```

## 5. Qdrant 버전·인증 확인 — 읽기 전용

```powershell
$base = 'http://127.0.0.1:56446'
# (a) 키 없이 요청하면 401이어야 인증이 켜진 것이다. 401이 아니면 멈춘다.
try { Invoke-RestMethod "$base/collections" | Out-Null; 'STOP: 키 없이 응답함 (인증 꺼짐)' }
catch { "HTTP $($_.Exception.Response.StatusCode.value__)" }
# (b) 키를 넣어 버전 확인. 1.12.4 이어야 한다. (키는 헤더로만 보내고 출력하지 않는다)
$h = @{ 'api-key' = $env:QDRANT_API_KEY }
(Invoke-RestMethod "$base/" -Headers $h).version
# (c) 컬렉션 수. 새 볼륨이므로 0 이어야 한다.
(Invoke-RestMethod "$base/collections" -Headers $h).result.collections.Count
```

## 6. dry-run — 읽기 전용 (빈 서버 확인)

```powershell
# 스크립트는 --qdrant-url 과 현재 창의 QDRANT_API_KEY 만 쓴다 (.env·QDRANT_URL 무시).
.\.venv\Scripts\python.exe scripts\update_b2_from_snapshot.py --qdrant-url http://127.0.0.1:56446 `
    --report .cache\issue91-r2-20261119\01-dry-empty.json
```

기대: `"status": "PASS"`, `"state": "empty_server"`, `"db_writes": 0`.

## 7. [쓰기] pr29 snapshot 업로드 — DB 쓰기

컬렉션이 하나라도 있으면 스크립트가 거부한다(빈 서버에만 복원). 업로드 전 파일 SHA-256을 확인하고,
같은 값을 `checksum`으로 보내 Qdrant도 검증하게 한다. 요청: `POST /collections/exp_b2_qwen3emb06b_v140_v1/snapshots/upload?wait=true&priority=snapshot&checksum=…`.

```powershell
.\.venv\Scripts\python.exe scripts\update_b2_from_snapshot.py --qdrant-url http://127.0.0.1:56446 `
    --restore-snapshot --confirm-write --report .cache\issue91-r2-20261119\02-restore.json
```

기대: `result.status = RESTORED`, `points = 1138`, 이어서 출력되는 상태가
`state = payload_pending`, `collections = ["exp_b2_qwen3emb06b_v140_v1"]`, `full_vector_digest_matches_r1 = true`,
`delete_present` 3개, `fingerprint` 값.

## 8. dry-run 재확인 — 읽기 전용

```powershell
.\.venv\Scripts\python.exe scripts\update_b2_from_snapshot.py --qdrant-url http://127.0.0.1:56446 `
    --report .cache\issue91-r2-20261119\03-dry-restored.json
```

기대: 7단계와 같은 `state`·`fingerprint`. 여기까지가 이번 범위다. `--apply`(payload 최신화·3개 삭제)와
`--index`(4개 임베딩)는 별도 승인 후 이 fingerprint로 진행한다.

## 멈춤 기준과 안전한 중단

| 증상 | 판단 |
|---|---|
| 1단계에 `issue91-r2-qdrant` 볼륨·컨테이너가 이미 있음 | 멈춤. 이전 시도 자료이므로 지우지 말고 보고 |
| 2단계에서 56446 사용 중 | 멈춤. 사용 중인 프로세스를 확인해 보고 |
| 5(a)가 401이 아님, 5(b)가 1.12.4가 아님, 5(c)가 0이 아님 | 멈춤 |
| 6단계가 `empty_server`가 아님 | 멈춤 (7단계 진행 금지) |
| 7단계 `FAILED` (HTTP 오류, Point 수 이상) | 멈춤. 볼륨 보존 후 아래 재시도 |
| 7·8단계 `full_vector_digest_matches_r1 = false` 또는 `vector_mismatch` | 멈춤. pr29 벡터가 r1과 다름 → 전체 재임베딩 여부를 팀이 결정 |

중단할 때는 컨테이너만 멈춘다. 볼륨·데이터는 남는다.

```powershell
# 컨테이너 정지 (데이터 보존). down -v, docker volume rm 은 쓰지 않는다.
docker compose -p issue91-r2-qdrant-20261119 stop
```

재시도는 실패한 볼륨을 고치지 않고 **새 프로젝트 이름**으로 한다(`-p`가 파일의 `name:`보다 우선하므로
볼륨·네트워크 이름도 새로 생긴다). 이전 컨테이너가 정지된 상태여야 56446을 다시 쓸 수 있다.

```powershell
docker compose -f docs\development\issue91_r2_qdrant.compose.yaml -p issue91-r2-qdrant-20261119-retry1 up -d
```

## 리소스

Qdrant 컨테이너는 메모리 1GiB·CPU 2개로 제한한다(B2 1,138 Point, snapshot 약 15MB).
`.wslconfig`가 없어 Docker Desktop(WSL2)은 기본값(호스트 RAM 16GB의 절반)을 쓴다. 이후 Ollama 0.6B 임베딩은
호스트에서 1~2GB 정도를 더 쓰므로 여유가 있다. 기존 컨테이너를 함께 켜 둘 필요는 없다.
