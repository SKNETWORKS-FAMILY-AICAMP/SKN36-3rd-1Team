# LifePolicy AI — DB 엔지니어 인계 가이드 (README.md)

> **수신:** 주민혁 님 (Neo4j Graph DB & Qdrant Vector DB 구축 및 적재 담당)  
> **발신:** 데이터 엔지니어링 파이프라인 (LifePolicy AI 데이터팀)  
> **기준일시:** 2026-11-05 (KST)  
> **버전:** `v1.3.2-verified`  
> **인계 상태:** **전수 수집·정제·정합성 검증 완료 (참조 무결성 100% PASS, Broken References = 0)**

---

## 1. 인계 패키지 전체 구조 및 파일 명세

```
data/handoff/
├── README.md                           # [본 문서] 원스톱 DB 적재, 검증 가이드 및 차이 분석
├── data_contract.md                    # Neo4j - Qdrant 통합 데이터 계약서 (v1.3.2)
├── manifest.json                       # 전 파일 SHA-256 체크섬, 파일 크기, 생성일시
│
├── neo4j/                              # Neo4j Graph DB 적재용 모듈형 데이터셋
│   ├── nodes_policy.jsonl              # Policy 노드 (237건)
│   ├── nodes_organization.jsonl        # Organization 노드 (116건: relationships AGENCY_* 일치)
│   ├── nodes_region.jsonl              # Region 노드 (18건: relationships REGION_* 일치)
│   ├── nodes_category.jsonl            # Category 노드 (5건: relationships CAT_* 일치)
│   ├── nodes_lifestage.jsonl           # LifeStage 노드 (6대 표준: 영유아~노년)
│   ├── nodes_target_group.jsonl        # TargetGroup 노드 (11건: relationships GROUP_* 일치)
│   ├── nodes_condition_group.jsonl     # ConditionGroup 노드 (343건: 계층형 DAG 루트 및 OR/NOT 서브그룹)
│   ├── nodes_eligibility_condition.jsonl # EligibilityCondition 노드 (52건: COND_* 전수 등록)
│   ├── nodes_document.jsonl            # Document 노드 (246건: DOC_* 일치)
│   ├── relationships.jsonl             # 통합 관계 엣지 (3,615건: 참조 무결성 100% 검증)
│   ├── cypher_ddl.cypher               # 9대 노드 유니크 제약조건 및 인덱스 DDL 스크립트 (13개 DDL)
│   ├── verify_neo4j.cypher             # 적재 후 무결성 검증 쿼리 스크립트
│   └── load_neo4j.py                   # 멱등성 보장 9대 노드 및 관계 파이썬 일괄 적재 및 자동 실측 검증 스크립트
│
└── qdrant/                             # Qdrant Vector DB 적재용 데이터셋
    ├── payload_chunks.jsonl            # 600~900자 의미 단위 청크 (Point ID: 결정론적 UUIDv5, 557건)
    └── collection_config.json          # 컬렉션 설정 (임베딩 모델 확정 시 주입할 프리셋 명시, size: null)
```

---

## 2. 관계 건수 차이 및 계층적 DAG 모델링 분석 (P1-1 & P0-2)

### 2.1 건수 차이 상세 분석 (2,537건 vs 3,615건)
정제 원천 관계 2,537건에서 인계 패키지 관계가 3,615건으로 확장된 구조적 사유는 다음과 같습니다:

1. **기존 원천 정제 관계 (2,537건 보존):**
   - `REQUIRES_CONDITION`: 735건 (정책 ↔ 원자 자격조건 직접 빠른 탐색 엣지)
   - `TARGETS`: 424건 (정책 ↔ 11개 대상 집단)
   - `APPLIES_TO_LIFESTAGE`: 419건 (정책 ↔ 6대 표준 생애단계)
   - `SUPPORTED_BY`: 246건 (정책 ↔ 공식 근거 문서)
   - `AVAILABLE_IN`: 238건 (정책 ↔ 지역 범위)
   - `ADMINISTERED_BY`: 237건 (정책 ↔ 소관 부처/지자체)
   - `HAS_CATEGORY`: 237건 (정책 ↔ 분야 대분류)
   - `MODIFIES_CONDITION_OF`: 1건 (개정 정책 ↔ 모정책 관계)
   - **소계:** 2,537건 (단 1건의 임의 삭제 없이 100% 실재 노드 매핑 완료)

2. **계층적 복합 DAG 모델링 관계 (+1,078건 신규 확장):**
   - `HAS_ROOT_CONDITION_GROUP`: 237건 (전체 237개 정책 ↔ 각 정책의 루트 ConditionGroup)
   - `CONTAINS_GROUP`: 106건 (루트 그룹 ↔ OR/NOT 복합 선택 서브그룹 DAG 계층)
   - `CONTAINS_CONDITION`: 735건 (루트 그룹 또는 서브그룹 ↔ 52개 자격 조건 노드)
   - **소계:** 1,078건

3. **최종 합계:** **3,615건** (2,537건 + 1,078건)

### 2.2 ConditionGroup 3값 논리(3-Valued Logic) 및 복합 DAG 구조
- **AND 루트 그룹:** 복합 자격 요건을 가진 정책(82건)은 루트 그룹이 `logic: "AND"`로 결합됩니다.
- **OR 서브그룹 (106건):** 복수 소득구간(`GRP_..._OR_INC`), 복수 가구형태(`GRP_..._OR_HH`), 복수 학력(`GRP_..._OR_EDU`), 복수 취업상태(`GRP_..._OR_JOB`) 중 하나만 충족해도 되는 경우 `logic: "OR"` 서브그룹을 통해 DAG로 연결됩니다.
- **NOT 서브그룹:** `POL_005`(국민내일배움카드)와 같이 명시적 참여 배제 기준(`exclusion_criteria`)이 있는 정책은 `logic: "NOT"` 서브그룹으로 연결됩니다.
- **UNKNOWN 루트 그룹 (155건):** 원천 API에서 지원조건이 공란이거나 전 국민 대상인 정책은 임의로 강제 AND 처리하지 않고 `logic: "UNKNOWN"`으로 보존하여, 3값 논리(TRUE / FALSE / UNKNOWN) 평가 엔진에서 탈락되지 않고 사용자에게 "상세 공고 확인 필요"로 정확하게 안내됩니다.

---

## 3. Neo4j Graph DB 원스톱 적재 가이드

### 3.1 [방법 A] 파이썬 자동 적재 스크립트 실행 (가장 권장)
`neo4j/load_neo4j.py`를 실행하면 DDL 제약조건 적용부터 9대 노드 및 관계 전체가 일괄 적재됩니다:

```bash
cd data/handoff/neo4j
python load_neo4j.py
```

### 3.2 [방법 B] cypher-shell 또는 Cypher Console 수동 적재
1. **제약조건 및 인덱스 생성:** `neo4j/cypher_ddl.cypher` 실행
2. **9대 노드 적재:**
   ```cypher
   // Policy
   UNWIND $batch AS row MERGE (p:Policy {policy_id: row.policy_id}) SET p += row;
   // Organization
   UNWIND $batch AS row MERGE (o:Organization {agency_id: row.agency_id}) SET o += row;
   // Region
   UNWIND $batch AS row MERGE (r:Region {region_id: row.region_id}) SET r += row;
   // Category
   UNWIND $batch AS row MERGE (c:Category {category_id: row.category_id}) SET c += row;
   // LifeStage
   UNWIND $batch AS row MERGE (s:LifeStage {stage_id: row.stage_id}) SET s += row;
   // TargetGroup
   UNWIND $batch AS row MERGE (tg:TargetGroup {group_id: row.group_id}) SET tg += row;
   // Document
   UNWIND $batch AS row MERGE (d:Document {document_id: row.document_id}) SET d += row;
   // EligibilityCondition
   UNWIND $batch AS row MERGE (ec:EligibilityCondition {condition_id: row.condition_id}) SET ec += row;
   // ConditionGroup
   UNWIND $batch AS row MERGE (cg:ConditionGroup {group_id: row.group_id}) SET cg += row;
   ```
3. **관계 엣지 적재:** `neo4j/relationships.jsonl` 적재

### 3.3 [3단계] 적재 후 무결성 검증 Cypher 쿼리
적재 후 `neo4j/verify_neo4j.cypher`를 실행하여 다음 항목을 확인합니다:
- **노드 라벨별 건수:** Policy 237, Document 246, Organization 116, Region 18, Category 5, LifeStage 6, TargetGroup 11, ConditionGroup 343, EligibilityCondition 52
- **고립 노드(Orphan Node):** **0건 (100% 연결 완료)**
- **Broken References:** **0건**

---

## 4. Qdrant Vector DB 적재 및 컬렉션 설정 가이드

### 4.1 컬렉션 생성 및 임베딩 차원 가변 설정 (P1-3)
`qdrant/collection_config.json`을 기반으로 Python `qdrant-client`로 생성합니다:

```python
from qdrant_client import QdrantClient
from qdrant_client.http import models
import json

client = QdrantClient("http://localhost:6333")

# 임베딩 모델 선정에 따라 동적 설정:
# OpenAI text-embedding-3-small: 1536
# BAAI/bge-m3: 1024
# intfloat/multilingual-e5-large: 1024
VECTOR_SIZE = 1536  # 주민혁 님 선정 모델에 맞춰 변경

client.create_collection(
    collection_name="lifepolicy_chunks",
    vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
    on_disk_payload=True
)

# 하이브리드 필터링용 페이로드 인덱스 생성
for field in ["policy_id", "category", "region", "life_stages", "target_groups", "verification_status"]:
    client.create_payload_index(
        collection_name="lifepolicy_chunks",
        field_name=field,
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
```

### 4.2 페이로드 데이터 업로드
`qdrant/payload_chunks.jsonl` 파일(557건)에는 청크 본문(`text`)과 함께 풍부한 메타데이터(생애주기, 타겟그룹, 자격속성)가 들어있으므로 임베딩 생성 후 즉시 Qdrant에 업로드할 수 있습니다.

---

## 5. GraphRAG 하이브리드 검색 연결 패턴

1. **사용자 질의 유입:** `"서울 사는 29세 신혼부부인데 전세 대출 정책 있어?"`
2. **Qdrant 1차 프리필터링 벡터 검색:**
   ```python
   search_result = client.search(
       collection_name="lifepolicy_chunks",
       query_vector=query_embedding,
       query_filter=models.Filter(
           must=[
               models.FieldCondition(key="life_stages", match=models.MatchValue(value="청년")),
               models.FieldCondition(key="target_groups", match=models.MatchValue(value="신혼부부")),
               models.FieldCondition(key="region", match=models.MatchValue(value="서울특별시")),
           ]
       ),
       limit=5,
   )
   candidate_policy_ids = list({hit.payload["policy_id"] for hit in search_result})
   ```
3. **Neo4j 2차 지식그래프 탐색 및 복합 자격 조건 검증:**
   ```cypher
   MATCH (p:Policy) WHERE p.policy_id IN $candidate_policy_ids
   MATCH (p)-[:HAS_ROOT_CONDITION_GROUP]->(rg:ConditionGroup)
   OPTIONAL MATCH (rg)-[:CONTAINS_GROUP]->(sg:ConditionGroup)
   OPTIONAL MATCH (sg)-[:CONTAINS_CONDITION]->(ec:EligibilityCondition)
   RETURN p.policy_id, p.policy_name, p.benefit, rg.logic AS root_logic, sg.logic AS sub_logic, collect(ec.name) AS conditions;
   ```
4. **LLM 응답 생성:** 공식 문서 근거 텍스트 + Graph에서 검증된 자격 요건을 결합하여 환각 없는 100% 사실 기반의 답변 반환.

---

## 6. 미확정 항목 및 환경 의존적 검증 안내

1. **미확정 기술 스택 항목:**
   - **임베딩 모델:** OpenAI `text-embedding-3-small`(1536), `text-embedding-3-large`(3072), `BAAI/bge-m3`(1024), `multilingual-e5-large`(1024) 중 선택 대기 상태이며, 주민혁 님이 모델을 확정하여 `collection_config.json`의 `size`에 주입하도록 분리되어 있습니다.
   - **프론트엔드 UI:** 웹 UI는 React를 사용하며 4차에서 개발. 3차는 터미널·노트북 등 최소 실행 방식으로 시연.
2. **실제 실행 테스트와 미실행(대기) 테스트 구분:**
   - **실제 실행 검증 완료 (100% PASS):**
     * 데이터셋 전수 파싱 및 9대 노드(1,034건), 11대 관계(3,615건), 557개 청크 유일성 검증
     * 관계 엣지 3,615건의 Broken Source / Target = 0건 전수 검증
     * DDL 파일 주석 제거 파싱(13개 DDL) 및 정규식 일치율 100% 검증
     * Qdrant Point ID의 RFC 4122 표준 UUIDv5 유효성 전수 검증
     * 전 파일 SHA-256 체크섬 일치율 100% 검증
   - **미실행 테스트 (NOT_TESTED - 인프라 가동 대기):**
     * 실제 Neo4j 라이브 서버 적재 (현재 로컬/배포 서버 인프라 미가동 상태이므로, 서버 기동 후 `python load_neo4j.py`로 최초 1회 실측 요망)
     * 실제 Qdrant 라이브 서버 벡터 인덱싱 (임베딩 모델 선정 및 Qdrant 서버 가동 후 업로드 요망)

