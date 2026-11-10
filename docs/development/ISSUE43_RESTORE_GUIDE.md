# Issue #43 — 팀원용 최종 DB 복원·검증

## 기준과 준비물

소스는 PR #35·#37 병합 a44bf08 및 #43 복원 도구다.
정책 246 / 문서 261 / 원본 청크 599 / Neo4j 1,004노드·2,431관계 /
REQUIRES_CONDITION 136 / Qdrant B2 1,138 Point·1,024차원·Cosine이 기준이다.
기본 프로필은 b2_06b_v140이다. 이미지 버전/digest는 동봉 Compose로 고정한다.

새 프로젝트·새 볼륨만 사용한다. 기존 DB를 삭제·초기화·덮어쓰지 않는다.
rag-local-v140-pr29-20261109.zip은 교정 전 자료이며 최신 최종본이 아니다.
최신 ZIP은 정합화된 0.6B 단일 snapshot, 소스 ZIP, Compose, 복원 도구, package.json, SHA256SUMS를 포함한다.
4B 부분 색인·모델·.env·실제 비밀값은 제외하며 대용량 파일은 Git/PR에 추가하지 않는다.
검증 환경은 Windows / Python 3.13.15다. 다른 실제 PC/OS의 설치는 미검증이다.
Docker Desktop, requirements.txt 의존성, Ollama qwen3-embedding:0.6b가 필요하다.
필요한 설치·다운로드는 수신자가 본인 환경에서 승인 후 수행한다.

## 체크섬과 소스 준비

전달받은 ZIP의 외부 SHA-256을 먼저 확인하고 새 폴더에 푼다. PowerShell에서 실행한다.

```powershell
Get-Content .\SHA256SUMS | ForEach-Object {
    if (-not $_.Trim()) { return }
    $expected, $name = $_ -split '  ', 2
    if ((Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLower() -ne $expected) {
        throw 'Checksum mismatch'
    }
}
if (Test-Path .\LifePolicyAI) { throw 'Use a new source directory' }
Expand-Archive .\source-a44bf08-issue43.zip .\LifePolicyAI
$repo = (Resolve-Path .\LifePolicyAI).Path
$bundle = (Get-Location).Path
```

가상환경이 없으면 본인 승인 후 준비한다. 검증된 기존 환경도 사용할 수 있다.

```powershell
py -3.13 -m venv "$repo\.venv"
$python = "$repo\.venv\Scripts\python.exe"
& $python -m pip install -r "$repo\requirements.txt"
```

## 새 DB와 비밀정보 설정

기존 .env를 복사하지 않는다. 인증값은 수신자가 입력해 현재 프로세스 환경에만 둔다.
echo / Get-ChildItem Env: / docker compose config 등으로 값 전체를 출력하지 않는다.
새 셸에서는 같은 인증값을 다시 입력한다.

```powershell
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASSWORD = [Net.NetworkCredential]::new('', (Read-Host 'New Neo4j password' -AsSecureString)).Password
$env:QDRANT_API_KEY = [Net.NetworkCredential]::new('', (Read-Host 'New Qdrant API key' -AsSecureString)).Password
$project = 'lifepolicy-final-team-local'
if ((docker ps -aq --filter "label=com.docker.compose.project=$project") -or
    (docker volume ls -q --filter "label=com.docker.compose.project=$project")) {
    throw 'Use a new project name'
}
docker compose -p $project -f .\compose.yaml up -d
docker compose -p $project -f .\compose.yaml ps
$env:LIFEPOLICY_DATASET_DIR="$repo\data\releases\v1.4.0-candidate"
$env:QDRANT_URL='http://127.0.0.1:56336'
$env:NEO4J_URI='bolt://127.0.0.1:57687'
$env:NEO4J_HTTP_URL='http://127.0.0.1:57474'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
```

포트 충돌 시 기동 전에 FINAL_QDRANT_PORT / FINAL_NEO4J_HTTP_PORT / FINAL_NEO4J_BOLT_PORT를
다른 값으로 지정하고 위 연결 주소도 맞춘다. localhost는 각자의 PC다.
Qdrant는 1.12.4, Neo4j는 5.26-community 계열을 digest로 고정했다.
메모리는 각각 512 MiB / 1,280 MiB 제한이다. 서버 readiness 후 다음을 실행한다.

## 복원·정합화·전체 무결성 검사

```powershell
& $python "$repo\scripts\restore_final_v140.py" --repo $repo --bundle $bundle
& $python "$repo\scripts\restore_final_v140.py" --repo $repo --bundle $bundle --apply --report .\restore-report.json
```

기본 실행은 체크섬·최신 manifest·본문 해시·입력 건수와 두 DB의 빈 상태만 검사한다.
--apply가 있어야 새 Qdrant snapshot 복원과 Neo4j JSONL 적재를 수행한다.
두 DB가 모두 비어 있지 않으면 첫 쓰기 전에 거부한다. 기존 데이터 삭제/재초기화 코드는 없다.
두 DB 사이에 원자적 트랜잭션은 없으므로 실패 시 새 부분 환경을 보존하고 담당자에게 문의한다.

이 패키지는 최신 정합화 snapshot이므로 metadata_points_updated=0이 정상이다.
전체 1,138개 Point ID·벡터 차원/유한값·최신 예상 payload를 대조한다.
category/life_stages 차이만 있는 허용 입력은 실제 차이를 재계산해 해당 Point만 수정한다.
다른 본문·위치·content_hash·모델·입력 규칙 차이는 거부한다.
전체 벡터·보호 payload digest는 수정 전후 동일하고 패키지의 전체 벡터 digest와도 일치해야 한다.
임베딩을 재생성하거나 4B를 호출하지 않는다.

Neo4j는 최신 릴리스 JSONL과 기존 검증 로더로 적재하고 스키마 13개 문장을 적용한다.
전체 노드·관계 ID/속성과 실제 정책·문서·SUPPORTED_BY·해시 연결을 검사한다.
이미 복원된 환경은 읽기 전용으로 재검증한다.

```powershell
& $python "$repo\scripts\restore_final_v140.py" --repo $repo --bundle $bundle --verify-only --report .\verify-report.json
```

보고서는 덮어쓰지 않는다. 입력 데이터가 바뀌면 새 manifest/패키지/결과 경로를 사용한다.
본문·정책명·섹션·임베딩 입력이 바뀌면 기존 벡터를 무조건 재사용하지 않는다.

## Hybrid / checker 확인과 검증 범위

Ollama 0.6B 모델은 질문 임베딩에 필요하며 패키지에는 포함되지 않는다.

```powershell
Set-Location $repo
$env:RUN_FINAL_V140_SMOKE='1'
$env:RUN_NEO4J_SMOKE='1'
& $python -B -m unittest tests.integration.test_final_v140_smoke tests.integration.test_neo4j_smoke -v
```

POL_005, POL_GOV_006, POL_GOV_011, POL_009의 임베딩→Qdrant→Neo4j→checker 연결을 검사한다.
정책 필터를 사용한 연결 검사이며 검색 품질 A/B 평가가 아니다. k는 정책 수가 아닌 청크 수다.
graph.eligibility.evaluated=False는 검색 자체가 자격 판정을 하지 않는다는 뜻이다.
checker의 conditions[].status와 reception_status를 사용하고 UNKNOWN·기간/자격 미확정을 숨기지 않는다.
기존 test_hybrid_profiles_smoke.py는 동결 v1.3.2의 0.6B/4B-instruct 전용이다.
그 skip을 최신 Hybrid 통과로 기록하지 않는다.

같은 Windows PC의 새 소스 폴더·빈 Qdrant/Neo4j 볼륨에서 패키지 복원과 전체 검증이 통과했다.
추가 메타데이터 수정 0건, 임베딩 재생성 0회였고 실제 DB 스모크 18개와 안전 테스트 8개도 통과했다.
실제 다른 PC/OS의 설치와 OpenAI LLM·HTTP/UI E2E는 미검증이다.


## PR45 review package r1 ? latest distribution (2026-11-10)

Supersedes the earlier distribution for new restores; historical results below/above remain historical.

- ZIP: `rag-final-v140-pr45-review-worktree-20261110-r1.zip` (local artifact: `.cache/rag-final-v140-pr45-review-worktree-20261110-r1.zip`)
- ZIP SHA-256: `5b053525a13443b4afc0bc7650bf65c9c943429ebdf9ee96437ab248f260e5e8`
- Restore script SHA-256: `5433a24e5fb679e714f42dfe652c10e7a97d9c1643bbf8cb82acad2183f30de1`
- Source: uncommitted Issue #43 worktree based on `3b9da1bfef528e85d3a5f29907c2a1b478b38444`; this is not a new commit SHA.
- Extract `source-pr45-review-worktree.zip` from the bundle. Use this extracted source and its `scripts/restore_final_v140.py`, not the earlier source archive or another checkout.
- Independent ZIP extraction: six internal checksums matched; extracted restore safety tests 24/24 passed on Python 3.13.15. Both `--apply` and `--verify-only` passed against new empty DB volumes.
- Actual counts: Neo4j 1,004 nodes / 2,431 relationships / 136 REQUIRES_CONDITION; Qdrant 1,138 points / 1,024 dimensions / Cosine. Cross-DB PASS, 1,138/1,138; full graph properties verified; vector/protected payload unchanged; embedding calls zero.
- Vector digest: `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df`. Metadata updates zero because the snapshot is already aligned.
- Evidence: [PR45_PACKAGE_VALIDATION_20261110.json](PR45_PACKAGE_VALIDATION_20261110.json). This distribution-side evidence was recorded after packaging; it does not change the sealed ZIP or its checksums.
- Review items 1?5 are included in both script copies. The old ZIP and `.sha256` remain unchanged. The earlier statement that the review ZIP had not yet been rebuilt is historical and superseded by this entry.
- No secrets, `.env`, models, 4B vectors, local caches or test DB volumes are included. `.env.example` contains configuration names only. Dependencies must already be installed; another PC/OS and fresh dependency installation remain unverified.
