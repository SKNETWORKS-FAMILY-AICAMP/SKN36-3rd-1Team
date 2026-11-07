# Issue #21 최종 데이터 — RAG/검색 담당 인계

- 데이터: **`data/candidates/issue21-v1.4.0-rag/`** (커밋 `7a2515b`, manifest `source_commit` `742a2b0`, 21개 파일 SHA-256 고정, LF 고정)
- 통합 브랜치: `feature/5-hybrid-rag-db` (Issue #21을 일반 merge, Issue #5 로더가 이 데이터를 직접 읽음)
- 상태: `REVIEW_ONLY_NOT_FOR_DB` — 반석님 검토 전 운영 DB 적재 금지. 검토용 격리 Neo4j(7688)와 신규 Qdrant 컬렉션 2개에만 적재됨 (§7)
- 데이터 선택: 환경 변수 `LIFEPOLICY_DATASET_DIR=data/candidates/issue21-v1.4.0-rag` (비우면 기존 v1.3.2 `data/handoff`)

## 1. 반드시 지킬 판정 규칙

| 값 | 의미 | 서비스에서 |
|---|---|---|
| `FALSE` | **확인된 조건**(나이·학력·취업 등) 중 하나를 확실히 충족하지 않음 | "대상이 아닐 수 있음" + 불충족 조건 표시 |
| `UNKNOWN` | 판단할 근거가 부족함 (조건 없음, 의미 미확인, 원문 미구조화) | **적격이 아님.** 추천 후보에는 남기고 "공고 원문 확인 필요" |
| `TRUE` | 모든 조건 충족 | **현재 246개 정책 중 0개.** 나오지 않는다 |

- UNKNOWN을 TRUE(신청 가능)로 해석하거나 "자격 제한 없음"으로 안내하지 않는다.
- 판정 결과로 검색 결과를 걸러내지 않는다. FALSE도 정렬 하위로만 내리고 사유를 보여 준다.
- 모든 정책의 `eligibility_review_status`는 `needs_review`다. 자동 적격 판정은 하지 않는다.

## 2. 환경과 데이터 받기

```bash
git checkout feature/5-hybrid-rag-db                  # Issue #21 통합 브랜치 (원격 반영 후)
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt   # Python 3.12 이상
# 받은 데이터 검증 (manifest SHA-256 바이트 일치, 참조·DAG·불변 필드, 계약 검증기)
.venv/Scripts/python.exe scripts/rebuild_eligibility_candidate.py --verify-only data/candidates/issue21-v1.4.0-rag
# (선택) 같은 데이터 재생성: 추적 파일만으로 결정론적. 데이터 파일 18개는 바이트 동일, 출처 기록 3개만 다름
.venv/Scripts/python.exe scripts/rebuild_eligibility_candidate.py --output-dir data/candidates/<새_폴더>
```

깨끗한 저장소 사본(추적 파일만, 원본 `data/raw` 없음)에서 재생성해 데이터 파일 18개 바이트 일치를 확인했다. 달라지는 파일은 `manifest.json`(source_commit), `comparison.json`(생성 코드 커밋 일치 여부), `validation.json`(PC별 보호 파일 목록)뿐이다.

## 3. 파일

| 경로 | 내용 | 건수 |
|---|---|---:|
| `neo4j/nodes_*.jsonl`, `neo4j/relationships.jsonl` | Graph (Issue #5 `load_graph` 입력) | 노드 1,185 / 관계 4,427 |
| `qdrant/payload_chunks.jsonl` | 청크 payload (ID·본문·point_id는 v1.4.0과 동일) | 599 |
| `qdrant/policy_metadata.jsonl` | 정책 단위 검색 메타데이터 (청크 조인용) | 246 |
| `documents.jsonl` | 문서 본문 (`content_hash` 검증, Issue #5 재청킹 입력) | 261 |
| `uncertainty_audit.jsonl` | 정책별 보류 사유, 확인/보류 조건 | 246 |
| `policy_changes.jsonl` | v1.4.0 대비 정책별 변경 전후 | 246 |
| `processed/`, `age_conflict_candidates.jsonl`, `comparison.json`, `validation.json`, `manifest.json` | 근거·검증·SHA-256 | — |

## 4. 필드 (v1.4.0 대비 변경분)

| 위치 | 필드 | 의미 |
|---|---|---|
| Policy, 청크, policy_metadata | `region` | 시도 또는 전국 (기존과 동일, Qdrant `region` 필터 그대로) |
| Policy, 청크, policy_metadata | `region_detail` (선택) | 원문에 명시된 시군구 (22개 정책). 없으면 속성 자체가 없음 |
| Policy, 청크, policy_metadata | `eligibility_review_status` | 246개 모두 `needs_review` |
| 청크·policy_metadata | `eligibility_attributes.income_level` | 정부24 JA 소득 구간 전체 |
| REQUIRES_CONDITION | `raw_value`, `condition_type` | 온통청년 '기타' 소득 원문 JSON 14개. 숫자 비교 금지 |
| EligibilityCondition | `type`/`operator`/`value` | `AGE`/`BETWEEN`/`"19~34"`, 라벨형은 `IN`·`EQUAL`·`IN_RANGE`/slug, 미확인은 `OTHER`/`UNKNOWN` |

정식 계약: `docs/data/data_contract.md` §8. 청크 `eligibility_attributes`·`verification_status`는 문서별 값(법령 조문 등)이라 정책 5개에서 청크마다 다르다(v1.4.0부터 동일). 정책 단위 값은 `policy_metadata.jsonl`을 쓴다.

## 5. 조건 그룹

| 그룹 | 정책 수 | 평가 |
|---|---:|---|
| 루트 `AND` | 30 | 하위 하나라도 FALSE면 FALSE, 아니면 UNKNOWN |
| 루트 `UNKNOWN` (조건 없음) | 126 | 항상 UNKNOWN |
| 루트 `UNKNOWN` (모든 조건 검토 대기) | 90 | 항상 UNKNOWN |
| `OR_{EDU,SP,JOB}` (온통청년) | — | 3값 OR |
| `OR_{INC,HH,JOB}` (정부24 JA) | — | UNKNOWN (결합 의미 미공개) |
| `UNKNOWN_REVIEW` / `UNKNOWN_UNSTRUCTURED` / `UNKNOWN_EXCL` | — | 항상 UNKNOWN (검토 대기 조건 / 미구조화 원문·연령·지역 / 제외 문장) |

## 6. 검색·판정 예시 (`scripts/eligibility_3vl.py` 실행 결과)

```bash
.venv/Scripts/python.exe scripts/eligibility_3vl.py --candidate data/candidates/issue21-v1.4.0-rag --age 29 --region 충청남도 --employment 미취업자
```

| 프로필 | 지역 TRUE / UNKNOWN / FALSE | 조건 FALSE | 최종 FALSE / UNKNOWN | TRUE |
|---|---|---:|---|---:|
| 29세, 충청남도, 미취업자 | 130 / 2 / 114 | 5 | 114 / 132 | 0 |
| 50세, 부산광역시, 재직자 | 132 / 3 / 111 | 18 | 114 / 132 | 0 |
| 25세, 서울특별시, 미취업자, 대학 졸업 | 133 / 2 / 111 | 9 | 111 / 135 | 0 |
| 22세만 입력 | 246 UNKNOWN | 0 | 0 / 246 | 0 |

- 지역: 충청남도 검색에 `POL_GOV_047`·`048`(`region_detail`="충청남도 천안시")이 나오지만 지역 판정은 UNKNOWN(천안시 거주 확인 필요).
- 조건 FALSE 예(50세 재직자): `POL_GOV_024` `COND_AGE_19_39`, `POL_GOV_046` `COND_AGE_15_39`, `POL_STG_SEOUL_001`·`POL_YC_003` `COND_JOB_미취업자`.

```cypher
// 시도 검색 + 시군구 한정 표시
MATCH (p:Policy)-[:AVAILABLE_IN]->(r:Region)
WHERE r.name IN [$province, '전국']
RETURN p.policy_id, p.policy_name, p.region_detail, p.eligibility_review_status;

// 정책 조건 트리
MATCH (p:Policy {policy_id: $policy_id})-[:HAS_ROOT_CONDITION_GROUP]->(root)
OPTIONAL MATCH (root)-[:CONTAINS_GROUP*0..3]->(g)-[:CONTAINS_CONDITION]->(c)
RETURN root.logic, g.group_id, g.logic, c.condition_id, c.type, c.operator, c.value;
```

## 7. Issue #5 연동 (적용 완료)

### 7.1 코드

| 파일 | 변경 |
|---|---|
| `database/handoff_data.py` | `LIFEPOLICY_DATASET_DIR`로 패키지 선택. 후보는 `documents.jsonl`(본문), manifest `summary` 기대 건수, `dataset_version()`. `load_policy_metadata`는 `qdrant/policy_metadata.jsonl` 우선 |
| `database/full_index.py` | 후보 전용 구성 `A_B2_I21`(`exp_b2_qwen3emb06b_i21_v1`, 1,138), `B_B3_I21`(`exp_b3_qwen3emb06b_i21_v1`, 1,220). 구성별 `dataset` 확인으로 v1.3.2 구성과 섞이지 않음. `available_configs()`, `manifest_equivalent()` |
| `database/qdrant/store.py` | 정책 메타데이터에 있으면 `region_detail`·`eligibility_review_status`를 payload에 추가 (v1.3.2 payload 불변) |
| `database/hybrid_search.py` | `region_detail`이 있으면 `REGION_LIMITED_TO_MUNICIPALITY`(판정 보류) 경고, 시군구 유실 의심 경고는 내지 않음 |
| `database/data_quality/manifest_equivalence.json` | 반석님 `d500ff2`·`92dfad2`로 v1.3.2 manifest만 바뀐 사실(데이터 파일 18개 동일)을 기록. 고정 평가셋·정적 플래그 파일은 수정하지 않음 |

### 7.2 Neo4j (격리 인스턴스, 적재·검증 완료)

| 항목 | 값 |
|---|---|
| 컨테이너 / 볼륨 | `lifepolicy-neo4j-i21` (`neo4j:5.26-community`) / `lifepolicy-neo4j-i21-data`, `lifepolicy-neo4j-i21-logs` |
| 접속 | `bolt://127.0.0.1:7688`, 브라우저 `http://127.0.0.1:7475` (로컬 전용, 인증 비활성) |
| 기존 인스턴스 | `lifepolicy-neo4j` (7687, 볼륨 `skn36-3rd-1team_neo4j_data`) — 접속·변경하지 않음 |
| 적재 결과 | 노드 1,185 / 관계 4,427, 고립 노드 0, `verify_loaded_graph` ok, `raw_value` 관계 14 |

Issue #5 검색 코드로 연결할 때 (`.env`가 아닌 실행 셸에서만 지정):

```powershell
$env:LIFEPOLICY_DATASET_DIR="data/candidates/issue21-v1.4.0-rag"
$env:NEO4J_URI="bolt://127.0.0.1:7688"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="unused"   # 인증 비활성 인스턴스라 값은 쓰이지 않음
```

### 7.3 Qdrant (색인·검증 완료)

| 컬렉션 | 청킹 | 포인트 | 문서 / 정책 | 모델 |
|---|---|---:|---|---|
| `exp_b2_qwen3emb06b_i21_v1` (기본 프로필 `b2_06b_i21`) | B2 | 1,138 | 261 / 246 | `qwen3-embedding:0.6b`, 1024, Cosine, 질의 지시문 없음 |
| `exp_b3_qwen3emb06b_i21_v1` (프로필 `b3_06b_i21`) | B3 | 1,220 | 261 / 246 | 같음 |

- 적재: `full_index --execute --confirm-write` (로컬 Ollama, 유료 API 없음). 임베딩 18.2초 / 17.2초. 기록: `docs/development/experiments/full_index_20261106/execute_{A_B2,B_B3}_I21.json`
- 검증: ID 집합·청크 본문 위치·`content_hash` 불일치 0, 누락 문서·정책 0, `region_detail` 82포인트(22개 정책), 모든 포인트 `eligibility_review_status=needs_review`. null은 선택 필드(`page_start/end`, `section_group`)뿐. `raw_value`는 Neo4j 관계에만 있다
- Qdrant↔Neo4j 교차 검증(`verify_cross_db`) 1,138포인트 7개 항목 모두 PASS
- 기존 컬렉션 5개(107/1,081/1,117/1,141/1,081) 포인트 수 불변

### 7.4 hybrid_search 사용

```python
import os
os.environ["LIFEPOLICY_DATASET_DIR"] = "data/candidates/issue21-v1.4.0-rag"   # import 전에 지정
from database import hybrid_search as hs
with hs.HybridSearchSession() as session:          # 기본 프로필 b2_06b_i21 (데이터셋에 따라 자동)
    r = session.search_policy_context("충청남도 거주 29세 미취업자 지원 정책",
                                      filters={"region": ["충청남도", "전국"]}, k=5)
for p in r["policies"]:
    print(p["policy_id"], p["data_quality"]["hold_auto_eligibility"], [w["code"] for w in p["data_quality"]["warnings"]])
```

- 결과의 `policies[]`: 근거 청크(`evidence`, `source_url` 포함), Neo4j 문맥(`graph`: 지역·조건 트리·문서), `data_quality`(판정 보류 여부·경고).
- `region_detail`이 있는 정책은 `REGION_LIMITED_TO_MUNICIPALITY` 경고(판정 보류). v1.3.2용 정적 플래그는 이 데이터셋에서 `not_applicable`.
- 실제 결과(격리 Neo4j + B2 컬렉션): 대표 질문 5개 모두 근거 문서-정책 연결 PASS, 지역 필터 적용, 반환 정책 전부 `evaluated=False`·`hold_auto_eligibility=True`. 부산 질의의 부산 중구 정책 3개, 천안 질의의 `POL_GOV_048`에 시군구 한정 경고. 참조 평가기 기준 지역이 다른 정책(세종 `POL_GOV_058`)은 FALSE, 나머지는 UNKNOWN(TRUE 0).

### 7.5 검색 품질 (retrieval_eval_v0 고정 55문항, 채점 52)

| 컬렉션 | R@1 | R@3 | R@5 | MRR@5 |
|---|---:|---:|---:|---:|
| v1.3.2 B2 (`exp_b2_qwen3emb06b_full_v1`) | 0.6923 | 0.8654 | 0.8846 | 0.7731 |
| **Issue #21 B2** | 0.6731 | 0.8462 | 0.8846 | 0.7554 |
| v1.3.2 B3 | 0.6346 | 0.8462 | 0.8846 | 0.7436 |
| **Issue #21 B3** | 0.6154 | 0.8462 | 0.8846 | 0.7276 |

- 같은 질문 벡터·채점 함수로 4개 컬렉션 비교. 평가 파일 해시 불변, 정답 문서 본문 `content_hash` 두 데이터셋 동일 확인. v1.3.2 값은 기존 기록과 같다.
- 순위 변화는 RQ017·RQ022 두 문항뿐이며, v1.4.0 신규 문서 `DOC_STG_001_FAQ`(LH Q&A)가 정답보다 위에 온 경우다. 평가셋은 v1.3.2 문서로 만들어 신규 문서 15건을 정답으로 세지 않는다.
- 재실행: `.venv\Scripts\python.exe docs/development/experiments/retrieval_eval_i21_20261107/compare_v132_vs_i21.py` (결과 JSON 같은 폴더)

### 7.6 다른 PC에서 처음 구성

전제: Docker, Python 3.12 이상, Ollama + `qwen3-embedding:0.6b`(기준선 모델, 최종 모델 아님), `.env`의 `QDRANT_URL`·`QDRANT_API_KEY`·`OLLAMA_BASE_URL`(값은 팀 공유 경로로 받음, 문서에 쓰지 않음). 유료 API는 쓰지 않는다.

2026-11-07 기준, requirements.txt에 qdrant-client==1.12.2와 neo4j==5.28.8을 추가했으며, .env.example에 OLLAMA_BASE_URL 예시를 추가했다. 별도의 수동 패키지 설치 없이 requirements.txt로 의존성을 설치한다. 아래처럼 검증된 버전을 직접 설치하고 `.env`에 `OLLAMA_BASE_URL`을 추가한다.

```powershell
git checkout feature/5-hybrid-rag-db; .venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts/rebuild_eligibility_candidate.py --verify-only data/candidates/issue21-v1.4.0-rag
ollama pull qwen3-embedding:0.6b
docker compose up -d qdrant                 # 저장소 docker-compose.yml (Qdrant v1.12.4, .env의 QDRANT_API_KEY)
# 격리 Neo4j: 새 볼륨, 로컬 전용 포트, 인증 비활성 (비밀번호 생성·저장 없음)
docker run -d --name lifepolicy-neo4j-i21 -p 127.0.0.1:7475:7474 -p 127.0.0.1:7688:7687 `
  -v lifepolicy-neo4j-i21-data:/data -v lifepolicy-neo4j-i21-logs:/logs -e NEO4J_AUTH=none neo4j:5.26-community
$env:LIFEPOLICY_DATASET_DIR="data/candidates/issue21-v1.4.0-rag"
.venv\Scripts\python.exe -c "from neo4j import GraphDatabase; from database.neo4j import load_graph as lg; g=lg.read_graph_input(); assert lg.validate_graph_input(g).ok; print(lg.load_graph(g, lg.Neo4jDriverRunner(GraphDatabase.driver('bolt://127.0.0.1:7688', auth=None)))['ok'])"
.venv\Scripts\python.exe -m database.full_index --config A_B2_I21 --config B_B3_I21 --no-db                    # 계획
.venv\Scripts\python.exe -m database.full_index --config A_B2_I21 --config B_B3_I21 --execute --confirm-write  # 임베딩·적재
```

`load_graph`는 비어 있지 않은 DB에서 멈춘다. `full_index`는 같은 이름 컬렉션이 이미 있고 내용이 다르면 쓰지 않는다.

### 7.7 이 PC에서 재시작·확인

```powershell
docker start lifepolicy-neo4j-i21          # 격리 Neo4j (볼륨 유지). 기존 lifepolicy-neo4j와 별개
$env:LIFEPOLICY_DATASET_DIR="data/candidates/issue21-v1.4.0-rag"
$env:RUN_NEO4J_SMOKE="1"; $env:NEO4J_URI="bolt://127.0.0.1:7688"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="unused"
.venv\Scripts\python.exe -m unittest tests/integration/test_neo4j_smoke.py      # 10/10
.venv\Scripts\python.exe -m database.full_index --config A_B2_I21 --config B_B3_I21 --no-db   # 색인 계획 재확인
```

## 8. 아직 확정할 수 없는 원천 코드와 영향

| 항목 | 조사 결과 | 영향 |
|---|---|---|
| 정부24 JA 전체 Y·카테고리 결합 | 공공데이터포털 명세에 코드명만 있음 | 정부24 소득·가구·취업 조건 판정 불가 (검색 영향 없음) |
| 온통청년 `sprtTrgtAgeLmtYn` | 명세는 이름뿐, 원천 3,861건에서 Y·N 모두 범위/0~0 혼재 | 독립 원문에 같은 범위가 있는 경우만 연령 판정 |
| 기타 소득 14건 | 기준·단위가 제각각 | 소득 판정 불가, 원문 표시 |
| POL_GOV_011 연령 13~18 vs 12~24 | 같은 공식 원천 안의 충돌, 법무부 안내에 연령 없음 | 연령 판정 불가 |
| 서울 청년수당 official_id | 정책 ID가 원천에 없음, 내용이 같은 원천(213994)으로만 조건 복원 | 데이터 담당 정정 대기 (검색 영향 없음) |
