# Issue #5 RAG 연결 초안 — 2026-11-07

사용 환경은 승인 `data/releases/v1.4.0-candidate/`, 기본 프로필은 **`b2_06b_v140`(잠정)**이다. Qdrant `exp_b2_qwen3emb06b_v140_v1`(1,138 Points)과 Neo4j v1.4.0(1,074노드/3,703관계)을 사용한다. 임베딩은 Ollama `qwen3-embedding:0.6b`, 1,024차원이다. B3는 비교용이며 RAG에서 따로 설정하지 않는다.

프로젝트 루트에서 다음 비밀 아닌 설정을 **Python import 전에** 적용한다. `.env` 실제 비밀값을 채팅·로그로 공유하지 않는다. 데이터셋은 import 시 결정되므로 이미 import한 프로세스는 설정 후 재시작한다.

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
$env:NEO4J_URI='bolt://127.0.0.1:17687'
$env:QDRANT_URL='http://127.0.0.1:6333'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
```

추가 필수 인증값은 `NEO4J_USER`, `NEO4J_PASSWORD`, `QDRANT_API_KEY`이며 개인 `.env`로 설정한다. 아래 `load_env_file()`은 이미 지정한 프로세스 환경변수를 덮어쓰지 않는다. 기존 `.env`의 Neo4j URI가 구 v1.3.2를 가리킬 수 있으므로 위 주소를 명시한다. 승인 릴리스와 본문 파일이 로컬에 필요하며, 모델은 현재 PC에 설치돼 있다. 다른 PC의 설치·접속은 별도 준비해야 한다.

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

`k`는 정책 수가 아닌 **청크 수**다. 같은 정책의 여러 청크가 포함될 수 있다. 정책 순서는 가장 높은 청크 순위를 따른다.

- `policies[].policy_id`, `evidence[].document_id/chunk_id/text/source_url/char_start/char_end`: 검색된 정책과 원문 출처. `graph`의 추출 텍스트와 원문 근거를 구별한다.
- `evidence[].region`: 원본 지역값. `region_match.match_basis`: exact/national/partial/unconfirmed. `result.region_search`에는 요청 지역과 실제 적용 필터가 있다.
- `policies[].data_quality.warnings/hold_auto_eligibility` 및 `result.warnings`: 원문 확인·판정 보류 사유를 RAG에도 전달한다. 부분 지역·UNKNOWN·미확인 조건을 적격 TRUE나 부적격 FALSE로 변환하지 않는다. `not_applicable` 정적 경고 목록 상태도 숨기지 않는다.
- `policies[].graph`: Neo4j에서 검색 **후** 덧붙인 정책·문서·지역·조건 관계. 후보 확장/재정렬이 아니다. `graph.eligibility.evaluated`는 False다. 검색만으로 지원 자격이나 신청 가능 여부가 확정되지 않는다.

현재는 이 PC의 로컬 서비스이며 **팀 공유 환경은 미준비**다. **다른 PC의 `127.0.0.1`은 그 PC 자신**이다. 팀이 접근 가능한 호스트 배포 또는 인증된 터널/VPN, 서비스 주소·인증·방화벽 설정과 타 PC 접속 검증이 필요하다. 이 단계에서는 포트를 개방하거나 원격 접속을 구성하지 않았다.

탐색적 평가: 기존 85문항+지역 8사례 × 4방식 = 372회, 오류 0. 네 방식의 미검수 참조 정책 탐지는 Top-5/10 모두 80/80, 지정 본문 근거 포함은 46/52·48/52였다. 사람 검수 gold가 없어 **공식 Hit/Recall/MRR은 미산출**, B2/B3 우열도 미확정이다. Vector/Hybrid 청크 순위는 동일했다. B2는 기존 기본값으로 잠정 유지하며, 필요한 근거가 Top-10에 없던 4문항과 중복 청크를 후속 검수한다. 상세 로그는 로컬 보존하고 PR에는 포함하지 않는다.
