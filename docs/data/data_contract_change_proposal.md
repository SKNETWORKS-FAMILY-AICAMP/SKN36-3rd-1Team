# SSOK — 데이터 계약 변경 제안서 (Data Contract Change Proposal)

- **제안 문서 번호**: DCP-20261106-01
- **현재 계약 버전**: `v1.3.2-verified` (2026-11-05 기준)
- **제안 계약 버전**: `v1.4.0-draft`
- **작성일자**: 2026-11-06
- **제안 주체**: SSOK 데이터 수집·정제 파트
- **수신 주체**: Neo4j Graph DB 엔지니어 (주민혁 님), Qdrant Vector DB 검색 파트

---

## 1. 제안 배경 및 핵심 원칙

### 1.1 배경
신규 발굴 정책 30건(청년 소득세 감면, 고속도로 다자녀 통행료 할인 등)과 공식 근거 문서 8대 유형(법령, 지침, FAQ, 고시, 조례 등)을 시스템에 확장 수용하기 위해, 기존 `v1.3.2-verified` 데이터 계약을 최소한으로 확장 개정하고자 합니다.

### 1.2 핵심 원칙 (Non-Destructive Incremental Extension)
1. **기존 데이터 불변 보장 (Zero Regression)**:
   - 기 구축된 237개 정책, 246개 문서, 557개 청크, 1,034개 노드, 3,615개 관계는 **단 1건도 삭제하거나 변경하지 않습니다**.
2. **선택적 속성 확장(Additive Only)**:
   - 신규 필드는 모두 **선택적(Nullable)**으로 정의하여, 기존 레코드에 대한 Not-Null 위반이나 마이그레이션 오류를 원천 차단합니다.
3. **스키마 우선 재사용**:
   - 신규 레이블이나 관계를 무분별하게 신설하지 않고, 기존 `Document` 노드와 `SUPPORTED_BY` 관계를 최우선 재사용합니다.

---

## 2. Before vs After 데이터 계약 필드 비교표 (Schema Diff)

### 2.1 Neo4j `(:Document)` 노드 스키마 변경 대비

| 필드명 | v1.3.2-verified (현재) | v1.4.0-draft (제안) | 변경 구분 | 설명 및 호환성 영향 |
| :--- | :---: | :---: | :---: | :--- |
| `document_id` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 기본 키 (`^DOC_[A-Z0-9_]+$`) |
| `policy_id` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 주 연결 정책 FK |
| `title` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 문서 제목 |
| `source_type` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 원본 포맷 (`pdf`, `html`, `api`) |
| `source_url` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 원천 출처 공식 URL |
| `local_raw_path` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | 로컬 디스크 원본 파일 경로 |
| `content_hash` | 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | SHA-256 무결성 해시 |
| `verification_status`| 필수 (NOT NULL) | 필수 (NOT NULL) | 유지 | `verified` / `unverified` / `needs_review` |
| `published_at` | 선택 (Nullable) | 선택 (Nullable) | 유지 | 발행일시 |
| `effective_at` | 선택 (Nullable) | 선택 (Nullable) | 유지 | 효력발생일시 |
| **`doc_type`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | `notice` \| `guideline` \| `faq` \| `law` \| `ordinance` \| `administrative_rule` (기존 문서는 묵시적 `notice` 처리) |
| **`law_article`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 법령/조례 조·항·호 표기 (예: `제30조 제1항`). 공고문은 `null` |
| **`effective_date`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 법령/공고 시행일자 (`YYYY-MM-DD`). |
| **`version`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 판본/개정 정보 (예: `2026년 개정`, `3차 공고`). |

### 2.2 Neo4j `[:SUPPORTED_BY]` 관계 스키마 변경 대비

| 필드명 / 속성 | v1.3.2-verified (현재) | v1.4.0-draft (제안) | 변경 구분 | 설명 및 호환성 영향 |
| :--- | :---: | :---: | :---: | :--- |
| 관계 방향 | `(Policy)-[:SUPPORTED_BY]->(Document)` | 동일 | 유지 | 기본 관계 방향 동일 유지 |
| **`evidence_type`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | `announcement` (공고문) \| `statutory_basis` (법적근거) \| `administrative_guideline` (시행지침) \| `faq_reference` (FAQ) |
| **`law_article`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 해당 정책과 연결되는 구체적 법 조항 번호 |

### 2.3 Qdrant `payload_chunks` 벡터 페이로드 스키마 변경 대비

| 페이로드 필드 | v1.3.2-verified (현재) | v1.4.0-draft (제안) | 변경 구분 | 인덱스 설정 |
| :--- | :---: | :---: | :---: | :---: |
| `chunk_id`, `policy_id`, `policy_name` | 필수 | 필수 | 유지 | `policy_id` (Keyword Index) |
| `document_id`, `document_title`, `text` | 필수 | 필수 | 유지 | 비인덱스 (Fulltext 검색용) |
| `category`, `region`, `life_stages` | 필수 | 필수 | 유지 | 각 Keyword Index 유지 |
| `target_groups`, `verification_status` | 필수 | 필수 | 유지 | 각 Keyword Index 유지 |
| `income_level`, `benefit_type` | 선택 | 선택 | 유지 | - |
| `eligibility_attributes` | 선택 | 선택 | 유지 | Payload JSON 유지 |
| **`doc_type`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | **Keyword Index 신규 제안** |
| **`law_article`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 비인덱스 메타데이터 |
| **`effective_date`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 비인덱스 메타데이터 |
| **`version`** | **(없음)** | **선택 (Nullable)** | **신규 추가** | 비인덱스 메타데이터 |

---

## 3. DB 엔지니어 협의 및 합의 필요 항목 (3선)

다음 3가지 항목에 대해 다운스트림 DB 담당자의 사전 기술 검토 및 최종 합의를 요청합니다.

```
┌────────────────────────────────────────────────────────────────────────┐
│                     DB 담당자 합의 요청 안건 (3선)                     │
├────────────────────────────────────────────────────────────────────────┤
│ [안건 1] Neo4j Document 노드 내 4개 Nullable 메타 속성 수용 여부       │
│   - 제안: doc_type, law_article, effective_date, version 추가          │
│   - DB 영향: DDL 변경 불필요, 기존 Cypher 로더 스크립트 수정 불필요   │
│                                                                        │
│ [안건 2] 법령-정책 연결 모델링 최종 채택안 결정                        │
│   - Option A (데이터팀 권고): [:SUPPORTED_BY {evidence_type}] 재사용   │
│   - Option B (대안): 신규 관계 유형 [:BASED_ON_LAW] 신설               │
│   - 결정 요청: Graph 탐색 쿼리 작성 편의성에 따른 DB 엔지니어 선택      │
│                                                                        │
│ [안건 3] Qdrant doc_type 페이로드 인덱스 1건 추가 생성 일정             │
│   - 제안: payload index (field: "doc_type", type: "keyword") 추가      │
│   - DB 영향: 무중단 생성 가능 (컬렉션 재시작/재생성 불필요)            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 비파괴 롤백 및 증분 배포 가이드 (Deployment & Rollback)

### 4.1 증분 배포 절차 (Zero-Downtime Incremental Upsert)
1. **Neo4j 증분 적재**:
   - 기존 데이터를 TRUNCATE하지 않고, `MERGE (p:Policy {policy_id: line.policy_id})` 및 `MERGE (d:Document {document_id: line.document_id})` Cypher 쿼리를 통해 신규 건만 증분 적재합니다.
2. **Qdrant 증분 업서트**:
   - `client.upsert(collection_name="lifepolicy_chunks", points=[...])` 실행.
   - Point ID가 UUIDv5로 결정론적 매핑되어 있으므로, 기존 557건 벡터는 건드리지 않고 신규 청크만 덮어쓰기/추가됩니다.
3. **Qdrant 인덱스 생성**:
   - 적재 완료 후 `client.create_payload_index("lifepolicy_chunks", "doc_type", "keyword")` 비동기 실행.

### 4.2 즉시 롤백 절차 (Safe Rollback Protocol)
만약 신규 적재 데이터에서 정합성 이슈나 성능 저하가 발생할 경우, 다음의 격리 롤백 스크립트로 신규 데이터만 정밀 삭제합니다.

```cypher
// 1. Neo4j 신규 추가 법령/문서 및 정책 롤백 (기존 v1.3.2 데이터 100% 보존)
MATCH (d:Document)
WHERE d.doc_type IN ['law', 'ordinance', 'administrative_rule', 'faq']
   OR d.document_id STARTS WITH 'DOC_LAW_'
DETACH DELETE d;

MATCH (p:Policy)
WHERE p.policy_id IN $new_policy_ids // 신규 30건 정책 ID 목록
DETACH DELETE p;
```

```python
# 2. Qdrant 신규 청크 격리 롤백
from qdrant_client import QdrantClient
from qdrant_client.http import models

client = QdrantClient(host="localhost", port=6333)
client.delete(
    collection_name="lifepolicy_chunks",
    points_selector=models.FilterSelector(
        filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="doc_type",
                    match=models.MatchAny(any=["law", "ordinance", "administrative_rule", "faq"])
                )
            ]
        )
    )
)
```

---

## 5. 승인 서명란

| 역할 | 성명 / 소속 | 검토 결과 | 서명 일시 |
| :--- | :--- | :---: | :---: |
| **데이터 아키텍트 (발의)** | AI_TEAM 데이터 파트 | **승인 (Approved)** | 2026-11-06 14:20 KST |
| **Neo4j DB 엔지니어** | 주민혁 님 (DB 파트) | *검토 대기 (Pending)* | |
| **Qdrant 검색 엔지니어** | 검색/서빙 파트 | *검토 대기 (Pending)* | |
| **프로젝트 총괄 (PM)** | 총괄 PM | *검토 대기 (Pending)* | |

