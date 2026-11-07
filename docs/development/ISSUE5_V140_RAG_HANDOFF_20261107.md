# Issue #5 RAG 로컬 복원 안내 — 2026-11-07

사용 환경은 승인 `data/releases/v1.4.0-candidate/`, 기본 프로필은 **`b2_06b_v140`(잠정)**이다. Qdrant `exp_b2_qwen3emb06b_v140_v1`(1,138 Points)과 Neo4j v1.4.0(1,074노드/3,703관계)을 사용한다. 임베딩은 Ollama `qwen3-embedding:0.6b`, 1,024차원이다. B3는 비교용이며 RAG에서 따로 설정하지 않는다.

공유 서버는 사용하지 않는다. 각 PC에 Docker Desktop·Python 3.12·Ollama가 필요하다. Git 외부 전달 패키지 `rag-local-v140-20261107.zip`을 새 폴더에 풀고 그 폴더에서 PowerShell을 실행한다. 비밀값은 패키지에 없다.

1. **코드 준비.** 동봉 소스는 커밋 `8509bd8`의 Git archive다. Git으로 준비할 때는 해당 커밋을 checkout한다. 동봉 ZIP으로도 아래와 같이 재현할 수 있다.

```powershell
Get-Content .\SHA256SUMS | ForEach-Object {
    $hash, $file = $_ -split '  ', 2
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower() -ne $hash) { throw "체크섬 불일치: $file" }
}
if (Test-Path .\LifePolicyAI) { throw '새 코드 폴더를 사용하세요' }
Expand-Archive .\source-8509bd8.zip .\LifePolicyAI
$repo = (Resolve-Path .\LifePolicyAI).Path
py -3.12 -m venv "$repo\.venv"
$python = "$repo\.venv\Scripts\python.exe"
& $python -m pip install -r "$repo\requirements.txt"
```

2. **새 Docker 환경 실행.** 인증값은 수신자가 정한다. 기존 기본 DB 포트와 다른 Qdrant 16333, Neo4j 17474/17687 및 새 전용 볼륨을 사용한다. 프로젝트/포트 충돌 시 중단하고 기존 환경을 삭제하지 않는다.

```powershell
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASSWORD = [Net.NetworkCredential]::new('', (Read-Host '새 Neo4j 비밀번호 (8자 이상)' -AsSecureString)).Password
$env:QDRANT_API_KEY = [Net.NetworkCredential]::new('', (Read-Host '새 Qdrant API 키' -AsSecureString)).Password
$project = 'lifepolicy-rag-v140-local'
if ((docker ps -aq --filter "label=com.docker.compose.project=$project") -or (docker volume ls -q --filter "label=com.docker.compose.project=$project")) { throw '새 프로젝트/볼륨이 필요합니다' }
docker compose -p $project -f .\compose.yaml up -d
docker compose -p $project -f .\compose.yaml ps
```

3–4. **B2 스냅샷 복원 → 승인 Neo4j JSONL 적재.** 두 서비스가 준비된 뒤 실행한다. 복원기는 먼저 대상 컬렉션 부재와 빈 그래프를 확인하고, Qdrant 복원·차원 검증 후 기존 Neo4j 로더로 적재·무결성을 검사한다. 전체 임베딩은 다시 생성하지 않는다.

```powershell
& $python .\restore_local.py --repo $repo
# 위 preflight가 통과한 경우에만 실행
& $python .\restore_local.py --repo $repo --confirm-new-local
```

성공 출력: `B2 1138 points / 1024 dimensions; Neo4j 1074 nodes / 3703 relationships verified`. 기존 컬렉션/그래프가 있으면 거부하며 자동 삭제·덮어쓰기·이어 적재를 하지 않는다. 실패 후 일부 데이터가 남았으면 중단하고 담당자에게 문의한다. 복원은 최초 1회이며, 재시작에는 `docker compose -p $project -f .\compose.yaml start`를 사용한다.

5. **Ollama 확인.** `ollama list`에서 `qwen3-embedding:0.6b`를 확인한다. 없으면 수신자 PC에서 `ollama pull qwen3-embedding:0.6b`로 설치한다. 기존 벡터를 복원해도 새 질문 벡터를 만들 모델은 필요하다.

6. **검색 환경변수.** 위 인증 환경변수를 유지하고 다음을 Python **import 전에** 적용한다. import한 프로세스는 재시작한다.

```powershell
$env:LIFEPOLICY_DATASET_DIR="$repo\data\releases\v1.4.0-candidate"
$env:NEO4J_URI='bolt://127.0.0.1:17687'
$env:QDRANT_URL='http://127.0.0.1:16333'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
Set-Location $repo
& $python
```

인증값은 문서·채팅·로그에 출력하지 않는다. 새 PowerShell 세션에서는 같은 인증값을 다시 입력한다. 위 명령은 환경변수만 사용하므로 `.env` 로딩 의존성 없이 실행된다. 다음 예제는 코드 폴더에서 `$python` 인터프리터로 실행한다.

7. **검색 호출.**

```python
from database import embedding as em
em.load_env_file()
from database.hybrid_search import HybridSearchSession

# 애플리케이션 수명 동안 재사용. 승인 데이터셋의 기본값은 b2_06b_v140.
with HybridSearchSession() as search:
    result = search.search_policy_context(
        '서울 동대문구 다자녀 가정의 입학축하금 조건을 알려주세요.',
        filters={'region': '서울특별시'},
        k=10,
    )
    for policy in result['policies']:
        print(policy['policy_id'], policy['policy_name'])
        # 원문은 policy['evidence']; 검토 사유는 policy['data_quality'].
```

8. **반환 확인.** `k`는 정책 수가 아닌 **청크 수**다. 정책 ID·문서 ID·출처와 다음 경고를 확인한다.

- `policies[].policy_id`, `evidence[].document_id/chunk_id/text/source_url/char_start/char_end`: 검색된 정책과 원문 출처. `graph`의 추출 텍스트와 원문 근거를 구별한다.
- `evidence[].region`: 원본 지역값. `region_match.match_basis`: exact/national/partial/unconfirmed. `result.region_search`에는 요청 지역과 실제 적용 필터가 있다.
- `policies[].data_quality.warnings/hold_auto_eligibility` 및 `result.warnings`: 원문 확인·판정 보류 사유를 RAG에도 전달한다. 부분 지역·UNKNOWN·미확인 조건을 적격 TRUE나 부적격 FALSE로 변환하지 않는다. `not_applicable` 정적 경고 목록 상태도 숨기지 않는다.
- `policies[].graph`: Neo4j에서 검색 **후** 덧붙인 정책·문서·지역·조건 관계. 후보 확장/재정렬이 아니다. `graph.eligibility.evaluated`는 False다. 검색만으로 지원 자격이나 신청 가능 여부가 확정되지 않는다.

**`127.0.0.1`은 각자 자신의 PC**다. 이 패키지는 각자 독립 실행하므로 상대 PC를 켜둘 필요가 없다. 공유 서버/외부 포트 개방은 하지 않았다. 같은 PC의 새 포트·새 볼륨 환경에서 복원과 Hybrid 검색을 검증했지만 실제 타 PC의 설치·포트·인증·모델 확인은 추가로 필요하다.

공식 문서 확인(2026-11-07): [Qdrant Snapshot](https://qdrant.tech/documentation/snapshots/)과 [Neo4j Docker](https://neo4j.com/docs/operations-manual/current/docker/operations/). Qdrant는 원본과 같은 **1.12.4**로 고정했고, Neo4j는 검증한 Community 5.26.32 이미지 digest를 고정했다. B3/모델 파일/비밀값은 패키지에 포함하지 않는다.

탐색적 평가: 기존 85문항+지역 8사례 × 4방식 = 372회, 오류 0. 네 방식의 미검수 참조 정책 탐지는 Top-5/10 모두 80/80, 지정 본문 근거 포함은 46/52·48/52였다. 사람 검수 gold가 없어 **공식 Hit/Recall/MRR은 미산출**, B2/B3 우열도 미확정이다. Vector/Hybrid 청크 순위는 동일했다. B2는 기존 기본값으로 잠정 유지하며, 필요한 근거가 Top-10에 없던 4문항과 중복 청크를 후속 검수한다. 상세 로그는 로컬 보존하고 PR에는 포함하지 않는다.
