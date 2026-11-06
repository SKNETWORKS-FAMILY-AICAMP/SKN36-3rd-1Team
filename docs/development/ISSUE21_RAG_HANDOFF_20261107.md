# Issue #21 최종 데이터 — RAG/검색 담당 인계

- 대상 브랜치: `feature/21-eligibility-data-fix`
- 배포 데이터 경로: **`data/candidates/issue21-v1.4.0-rag/`** — 코드 커밋 후 생성해 Git에 함께 커밋한다(약 9MB, 최대 파일 2.8MB, LFS 불필요, `.gitattributes`로 LF 고정).
- 상태: `REVIEW_ONLY_NOT_FOR_DB` (반석님 검토 전 운영 DB 적재 금지)
- 호환 확인: Issue #5 `feature/5-hybrid-rag-db` `04a9edc` (읽기 전용 검사)

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
git checkout feature/21-eligibility-data-fix          # 커밋 반영 후
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

## 7. Issue #5 연동 — 최소 수정 (Issue #5 브랜치에서 별도 작업)

검사 결과(`04a9edc` 코드를 임시 사본에서 경로만 바꿔 실행, DB 없음):
- `verify_candidate_issue5_compat.py`: 입력 검증 오류 0, dry-run 불일치 0, payload 599 링크 정상, 조건 트리 246/246.
- 색인 계획: manifest 일치, 문서 261개 해시 검증, B2 1,138·B3 1,220 청크, 누락 문서·정책 0, `ready: true`.

| 파일 / 함수 | 필요한 변경 | 이유 |
|---|---|---|
| `database/handoff_data.py` 경로 상수 (`HANDOFF_DIR`, `NEO4J_HANDOFF_DIR`, `QDRANT_HANDOFF_DIR`, `PAYLOAD_CHUNKS_PATH`, `RELATIONSHIPS_PATH`, `MANIFEST_PATH`, `DOCUMENTS_PATH`) | 환경 변수 등으로 후보 폴더를 받도록. `DOCUMENTS_PATH` = `<후보>/documents.jsonl` | 현재 `data/handoff`(v1.3.2)와 `data/processed/documents.jsonl`(246건) 고정 |
| `database/handoff_data.py` `load_policy_metadata` | `<후보>/qdrant/policy_metadata.jsonl`이 있으면 그 값 사용 | 청크 기준 수집 시 정책 5개 불일치로 `ValueError` (v1.4.0 청크 구조) |
| `database/handoff_data.py` `EXPECTED_*_COUNT` | 후보 manifest `summary` 값 사용 (246/261/599/1,185/4,427) | v1.3.2 고정값 |
| `database/full_index.py` `FULL_INDEX_CONFIGS` | 새 구성·새 컬렉션 추가 예: `exp_b2_qwen3emb06b_i21_v1`(1,138), `exp_b3_qwen3emb06b_i21_v1`(1,220) | 기존 컬렉션 보호, `expected_chunks` 고정값 |
| `database/neo4j/load_graph.py` | 변경 불필요. `read_graph_input("<후보>/neo4j")` | 검증 오류 0 |

적재 순서 (기존 v1.3.2 DB·컬렉션은 건드리지 않음):

```python
# Neo4j: 빈 격리 DB(별도 DBMS 또는 새 database)에만. load_graph는 기본적으로 빈 DB가 아니면 중단한다.
graph = lg.read_graph_input("data/candidates/issue21-v1.4.0-rag/neo4j")
assert lg.validate_graph_input(graph).ok
lg.load_graph(graph, lg.Neo4jDriverRunner(driver, database="<격리 DB>"))
```

```bash
# Qdrant: 위 수정 후 새 구성 이름으로 dry-run → --execute --confirm-write
python -m database.full_index --config A_B2_I21 --no-db
```

## 8. 아직 확정할 수 없는 원천 코드와 영향

| 항목 | 조사 결과 | 영향 |
|---|---|---|
| 정부24 JA 전체 Y·카테고리 결합 | 공공데이터포털 명세에 코드명만 있음 | 정부24 소득·가구·취업 조건 판정 불가 (검색 영향 없음) |
| 온통청년 `sprtTrgtAgeLmtYn` | 명세는 이름뿐, 원천 3,861건에서 Y·N 모두 범위/0~0 혼재 | 독립 원문에 같은 범위가 있는 경우만 연령 판정 |
| 기타 소득 14건 | 기준·단위가 제각각 | 소득 판정 불가, 원문 표시 |
| POL_GOV_011 연령 13~18 vs 12~24 | 같은 공식 원천 안의 충돌, 법무부 안내에 연령 없음 | 연령 판정 불가 |
| 서울 청년수당 official_id | 정책 ID가 원천에 없음, 내용이 같은 원천(213994)으로만 조건 복원 | 데이터 담당 정정 대기 (검색 영향 없음) |
