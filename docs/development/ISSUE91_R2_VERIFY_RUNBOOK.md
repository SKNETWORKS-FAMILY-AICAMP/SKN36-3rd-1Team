# Issue #91 — r2 번들 독립 복원 검증 절차 (Windows PowerShell)

이 문서는 실행 절차서다. 작성 시점(2026-11-19)에는 아무 명령도 실행하지 않았다.
단계마다 결과를 확인하고 기대와 다르면 **그 자리에서 멈춘다**. 쓰기 단계는 `[쓰기]`로 표시했다.

| 항목 | 값 |
|---|---|
| 번들 | `.cache/issue91-r2-20261119/bundle-r2/rag-final-v140-r2-<sha12>.zip` (최종 커밋 기준으로 다시 만든 것) |
| Compose | `docs/development/issue91_r2_verify.compose.yaml`, 프로젝트 `issue91-r2-verify-20261119` |
| 주소 | Qdrant `http://127.0.0.1:56556`, Neo4j Bolt `bolt://127.0.0.1:56587` (HTTP 미노출) |
| 인증 | `ISSUE91_VERIFY_QDRANT_API_KEY`, `ISSUE91_VERIFY_NEO4J_PASSWORD` (새 난수, DPAPI 보관). #91 원본·공유 값 재사용 금지 |
| 래퍼 | 압축을 푼 소스의 `scripts/restore_r2_isolated.py` → 기존 `restore_final_v140.restore()` 호출 |
| 기대값 | Qdrant 1.12.4·1,139 Point·vector_digest `8577c3c8…`, Neo4j 5.26.32·1,126/2,668/211, cross-db 1,139 PASS |
| 작업 폴더 | `.cache/issue91-r2-20261119/verify-<sha12>/` (Git 제외) |

금지: `down -v`, `docker volume rm`, `prune`, 원본(`issue91-r2-qdrant-*`, `issue91-r2-neo4j-*`)·공유 프로젝트에 대한 명령,
`restore_final_v140.py`를 래퍼 없이 직접 실행(환경 변수의 다른 DB로 접속할 수 있다).

## 0. 원본 #91 DB 상태 기록 — 읽기 전용

```powershell
Set-Location C:\dev\project\SKN36-3rd-1Team
# 원본 #91 Qdrant·Neo4j fingerprint (각각 DPAPI에서 QDRANT_API_KEY / ISSUE91_NEO4J_PASSWORD 복원 후).
.\.venv\Scripts\python.exe scripts\update_b2_from_snapshot.py --qdrant-url http://127.0.0.1:56446 --report .cache\issue91-r2-20261119\40-orig-qdrant-before.json
.\.venv\Scripts\python.exe scripts\load_neo4j_r2_isolated.py --neo4j-url bolt://127.0.0.1:56487 --report .cache\issue91-r2-20261119\40-orig-neo4j-before.json
# 원본 인증값은 이 창에서 지운다 (검증용 값과 섞지 않는다).
Remove-Item Env:QDRANT_API_KEY, Env:ISSUE91_NEO4J_PASSWORD -ErrorAction SilentlyContinue
```

## 1. 번들 확인과 압축 풀기 — 로컬 쓰기(새 폴더만)

```powershell
$sha12 = '<최종 커밋 앞 12자리>'
$zip = ".cache\issue91-r2-20261119\bundle-r2\rag-final-v140-r2-$sha12.zip"
$work = ".cache\issue91-r2-20261119\verify-$sha12"
if (Test-Path $work) { throw 'STOP: 작업 폴더가 이미 있음' }
# ZIP 외부 SHA-256이 .sha256 파일과 같아야 한다.
$expected = (Get-Content "$zip.sha256").Split(' ')[0]
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'STOP: ZIP checksum' }
Expand-Archive $zip "$work\bundle"
Expand-Archive "$work\bundle\source-$sha12.zip" "$work\src"
$src = (Resolve-Path "$work\src").Path; $bundle = (Resolve-Path "$work\bundle").Path
```

## 2. Docker 사전 점검 — 읽기 전용

```powershell
docker ps -a --filter name=issue91-r2-verify --format '{{.Names}}'          # 빈 결과
docker volume ls --filter name=issue91-r2-verify --format '{{.Name}}'       # 빈 결과
foreach ($p in 56556, 56587) { Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue }   # 출력 없음
```

## 3. [쓰기] 검증용 인증값 생성과 새 컨테이너 시작

```powershell
function New-Secret { $b = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) }
foreach ($f in 'verify_qdrant_api_key.clixml', 'verify_neo4j_password.clixml') { if (Test-Path ".cache\issue91-r2-20261119\$f") { throw "STOP: $f exists" } }
$env:ISSUE91_VERIFY_QDRANT_API_KEY = New-Secret
$env:ISSUE91_VERIFY_NEO4J_PASSWORD = New-Secret
ConvertTo-SecureString $env:ISSUE91_VERIFY_QDRANT_API_KEY -AsPlainText -Force | Export-Clixml .cache\issue91-r2-20261119\verify_qdrant_api_key.clixml
ConvertTo-SecureString $env:ISSUE91_VERIFY_NEO4J_PASSWORD -AsPlainText -Force | Export-Clixml .cache\issue91-r2-20261119\verify_neo4j_password.clixml
docker compose -f docs\development\issue91_r2_verify.compose.yaml -p issue91-r2-verify-20261119 config --quiet   # --quiet 필수
docker compose -f docs\development\issue91_r2_verify.compose.yaml -p issue91-r2-verify-20261119 up -d
# Neo4j 기동 대기 (로그 'Started.' 최대 120초)
for ($i = 0; $i -lt 60; $i++) { if ((docker logs issue91-r2-verify-20261119-neo4j-1 2>&1 | Out-String) -match 'Started\.') { 'started'; break }; Start-Sleep 2 }
```

## 4. preflight — 읽기 전용

```powershell
# Docker 소유(프로젝트·서비스·이미지·127.0.0.1 바인딩·전용 볼륨), 키 없는 Qdrant 401, 틀린 Neo4j 비밀번호 거부,
# 버전, 빈 서버, 번들 checksum·소스 일치, restore preflight(PREFLIGHT_PASS_NO_WRITES)를 확인하고 fingerprint를 출력한다.
.\.venv\Scripts\python.exe "$src\scripts\restore_r2_isolated.py" --repo $src --bundle $bundle `
    --report .cache\issue91-r2-20261119\41-verify-preflight.json
```

## 5. [쓰기] 복원

```powershell
.\.venv\Scripts\python.exe "$src\scripts\restore_r2_isolated.py" --repo $src --bundle $bundle `
    --apply --confirm-write --expect-fingerprint <41번 보고서의 fingerprint> `
    --report .cache\issue91-r2-20261119\42-verify-apply.json
```

기대: `status=PASS`, `restore_apply.status=PASS`(metadata_points_updated 0, vector_digest `8577c3c8…`),
`post_verify.ok=true`(restore verify-only PASS, cross-db 1,139, graph 1,126/2,668, verify_full_index PASS).
실패하면 다시 실행하지 않는다. 새 DB의 부분 자산은 보존하고 보고한다(다음 시도는 새 프로젝트 이름·새 볼륨).

## 6. 재검증 — 읽기 전용

```powershell
.\.venv\Scripts\python.exe "$src\scripts\restore_r2_isolated.py" --repo $src --bundle $bundle --verify-only `
    --report .cache\issue91-r2-20261119\43-verify-only.json
Remove-Item Env:ISSUE91_VERIFY_QDRANT_API_KEY, Env:ISSUE91_VERIFY_NEO4J_PASSWORD
```

## 7. 원본 #91 DB 불변 확인 — 읽기 전용

0단계와 같은 명령을 `41-orig-*-after.json`으로 실행해 두 fingerprint가 0단계와 같은지 확인한다.

## 멈춤 기준

| 증상 | 판단 |
|---|---|
| ZIP checksum 불일치, 작업 폴더·컨테이너·볼륨이 이미 있음, 포트 사용 중 | 멈춤 |
| preflight `FAILED` (Docker 소유·인증·버전·빈 서버·번들 불일치) | 멈춤. 원인 보고 |
| apply `FAILED` | 멈춤. 재실행 금지, 새 DB 보존 |
| 원본 fingerprint 변화 | 멈춤. 즉시 보고 |

```powershell
# 중단 시 검증 컨테이너만 정지 (데이터 보존). down -v 금지.
docker compose -f docs\development\issue91_r2_verify.compose.yaml -p issue91-r2-verify-20261119 stop
```
