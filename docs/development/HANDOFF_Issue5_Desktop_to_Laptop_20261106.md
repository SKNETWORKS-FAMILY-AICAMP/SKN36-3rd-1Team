# LifePolicy AI — Issue #5 데스크톱 → 학원 노트북 작업 인수인계서

> 작성일: 2026-11-06 (데스크톱 Claude Code 세션 → 내일 학원 노트북 새 세션)
> 담당: 주민혁 (청킹·임베딩·Qdrant·Neo4j·Hybrid 검색) / 브랜치 `feature/5-hybrid-rag-db`
> **노트북 Claude Code는 이 문서를 가장 먼저 읽는다.** 그다음 실제 코드·Git 상태를 직접 확인한다. 문서와 코드가 다르면 **코드가 기준**이다.
> 이 문서에는 비밀값(.env 값·API 키·비밀번호)이 없다. 비밀값은 출력·커밋하지 않는다.
> 이전 인수인계서: [HANDOFF_Issue5_hybrid_rag_db_20261105.md](HANDOFF_Issue5_hybrid_rag_db_20261105.md) (노트북 → 데스크톱, §12 데이터 품질). 중복 내용은 여기서 요약만 한다.

---

## 0. 한눈에 보는 현재 상태 (2026-11-06 데스크톱 실측)

| 항목 | 상태 |
|---|---|
| 브랜치 / HEAD | `feature/5-hybrid-rag-db` / 이 문서 커밋 직전 HEAD `a91631e` (이 문서는 그 다음 커밋으로 추가됨 — `git log`로 확인) |
| 원격 동기화 | 로컬 HEAD = `origin/feature/5-hybrid-rag-db` (push 완료), 미커밋 변경 없음 |
| origin/dev | `a8e85a0` (PR #8). 이 브랜치보다 9커밋 앞서고, 이 브랜치는 dev보다 3커밋(이 문서 포함 시 4커밋) 앞섬. 기준점 `9aa8e03`. **dev 변경은 아직 이 브랜치에 병합하지 않았다** |
| 단위 테스트 | **257 PASS** (2026-11-06 재실행) |
| 통합 테스트 | Neo4j **10 PASS**, Qdrant **9 PASS**, Hybrid **9 PASS** (데스크톱 실서버, 읽기 전용, 2026-11-06 재실행) |
| Qdrant (데스크톱) | `exp_b2_qwen3emb06b_v1` **107 point** (문서 5개) |
| Neo4j (데스크톱) | 노드 **1,034** / 관계 **3,615** |
| 검색 범위 | **정책 5개(문서 5개)만 색인됨.** 전체 정책 237개·문서 246개는 아직 색인하지 않음 |
| PR | 만들지 않음. PR 대상은 `dev`, **merge는 팀장님** |

> ⚠️ 위 DB 상태는 **데스크톱** 기준이다. 노트북 DB는 다르다(§8). 노트북 Qdrant에는 2026-11-05 노트북에서 만든 107 point가 남아 있을 수 있고, **노트북 Neo4j는 비어 있었다**(적재는 데스크톱에서만 했다).
>
> ⚠️ 107 point 실험 결과는 **"5개 정책 스모크 검증"**이다. 전체 정책 추천 시스템의 검증 완료가 아니다.

### 0-1. 커밋 이력 (이 브랜치에서 한 일)

| 커밋 | 장소 | 내용 |
|---|---|---|
| `b1436ed` | 노트북 (11-05) | feat: 정책 문서 청킹 및 Qdrant·Neo4j 데이터베이스 기반 구축 (청킹·임베딩·Qdrant·Neo4j 로더 초안, 노트북 실험 자료, 이전 인수인계서) |
| `73101a4` | 데스크톱 | feat: Neo4j 무결성 검증 및 하이브리드 정책 검색 연동 (로더 검증 함수, 실제 적재, `hybrid_search.py`, 데스크톱 실험 자료, 인수인계서 §12) |
| `a91631e` | 데스크톱 | fix: 하이브리드 검색 연결 관리 및 오류 처리 안정화 (세션·타임아웃·오류 분류·데이터 품질 경고) |
| (이 문서) | 데스크톱 (11-06) | docs: 노트북 작업 연속성을 위한 Issue 5 인수인계서 작성 |

---

## 1. 프로젝트 개요와 담당 범위

### 1-1. 서비스가 해결하려는 문제

- 정부 정책은 기관·사이트마다 흩어져 있고, 자격 조건(연령·지역·소득·가구 등)이 공고문 속 긴 문장으로 쓰여 있어서 "나에게 해당하는지" 알기 어렵다.
- **LifePolicy AI**: 생애주기 기반 정부 정책 추천 + 공식 원문 근거 기반 질의응답 (SKN36 3차 LLM/RAG 프로젝트).
- **입력:** 사용자 조건(연령·지역·기본 상태 등)과 자연어 질문.
- **반환 목표:** 관련 정책, 공식 원문에 근거한 답변, 출처(원문 위치·URL), 조건 확인 결과, 부족한 정보에 대한 추가 질문. 근거가 없으면 지어내지 않는다.
- **필수 구조:** Hybrid RAG = Qdrant(원문 청크 의미 검색) + Neo4j(정책 관계·자격 조건 구조). 공통 키는 `policy_id`·`document_id`·`chunk_id`.

### 1-2. 3차 / 4차 범위 (origin/dev PRD v1.2 기준)

- **3차:** 질문 → 관련 원문 검색 → 사용자 조건 확인 → LLM 답변 → 출처 표시. **터미널·노트북으로 시연**한다.
- **4차:** 웹 UI(**React 확정**)는 4차로 이관됐다. 3차에서는 UI를 만들지 않는다.
- PRD D-05: Graph 자격 판정은 **연령·지역·기본 상태만 규칙화**하고, 복잡한 조건은 "추가 확인"으로 안내한다.
- PRD D-07: 결과 전달 계약은 `answer`, `citations`, `status`, `follow_up_question`, `error`와 조건 확인 결과 형식이다(초안: 강민지).
- 주의: 이 feature 브랜치의 `docs/product/PRD.md`·`CODING_STYLE.md`·`AGENTS.md`는 **dev 최신본이 아니다.** 최신본은 `git show origin/dev:docs/product/PRD.md`처럼 읽기만 한다.

### 1-3. 팀 역할과 경계 (origin/dev PRD 기준)

| 담당 | 사람 | 영역 | 이 세션에서의 원칙 |
|---|---|---|---|
| 데이터·API·README | 장반석 | `collectors/`, `scripts/`, `data/` (인계 패키지 `data/handoff/`) | **수정 금지.** 문제는 보고하고 담당자에게 공유 |
| **임베딩·DB (나)** | **주민혁** | `database/`, 관련 `tests/`, 산출물 02(아키텍처), A/B 비교 설정·인덱스 재현 지원 | 승인 범위 안에서만 수정 |
| 검색·평가 / LLM·RAG | 나지인·박소희 | `backend/rag/`, `ai/`, 평가 질문, 산출물 04·05 | **RAG 답변 생성을 내가 구현하지 않는다** |
| 사용자 조건 확인·발표 | 강민지 | `backend/eligibility/` (연령·지역 비교, 신청기간 구분), 산출물 03 | **최종 자격 판정을 내가 구현하지 않는다** |

- 내 검색 함수는 **원문 근거 + 그래프 문맥**만 돌려준다. `eligibility.evaluated`는 항상 `False`다. 자격 충족 여부 판정은 강민지 담당, 답변 생성은 나지인·박소희 담당이다.
- **공용 파일**(`README.md`, `.env.example`, `requirements.txt`, `AGENTS.md`, `docs/api/`, `data/schemas/`, `docs/architecture/`)은 수정 전에 팀에 알리고 사유를 남긴다(AGENTS §2).
- 미확정 항목(임베딩·LLM 모델, 청킹 최종안, A/B 대상 등 PRD §13)은 임의로 확정하지 않는다.

---

## 2. 구현 파일 지도

| 파일 | 역할 | 대표 함수·클래스 |
|---|---|---|
| `database/handoff_data.py` | 인계 데이터 로드·검증 | `load_documents(verify_hash=True)`, `load_payload_chunks`, `load_neo4j_nodes/relationships`, `NODE_SPECS`, `RELATIONSHIP_SPECS`, `LABEL_ID_FIELDS`, `verify_manifest_checksums`, `REVIEWED_NOISE_CHUNKS`(27건, 미합의), `get_verified_page_number`, `load_policy_metadata` |
| `database/chunking.py` | B1/B2 구조 청킹·구조 인식 | `chunk_documents(docs, strategy)`, `merge_short_chunks`, `make_chunk_id`, `make_point_id`, `verify_coverage`, `analyze_structure` |
| `database/embedding.py` | Ollama 임베딩 | `build_embedding_text(chunk, meta, rule)`, `ollama_embed_batch`, `make_ollama_embedder`, `resolve_ollama_base_url`, `build_query_text(question, query_template)` |
| `database/qdrant/store.py` | Qdrant 저장·검색 (실험 컬렉션 전용, 삭제 함수 없음) | `assert_experiment_collection`, `build_points`, `get_client`, `ensure_collection`, `upsert_points`, `count_points`, `search_chunks`, `PAYLOAD_INDEX_FIELDS` |
| `database/vector_index.py` | 최소 적재·검색 흐름 | `prepare_chunks`, `run_minimal_index`, `run_smoke_search` |
| `database/neo4j/load_graph.py` | 안전한 Neo4j 로더·검증 | `validate_graph_input`, `build_schema_statements`, `build_load_plan`, `load_graph`, `Neo4jDriverRunner`, `preflight_database`, `verify_schema`, `verify_node_ids`, `verify_relationship_endpoints`, `verify_node_properties`, `verify_relationship_properties`, `verify_loaded_graph`, `classify_notifications` |
| `database/hybrid_search.py` | Hybrid 검색 (읽기 전용) | `search_policy_context`, `HybridSearchSession`, `search_policy_evidence`, `get_policy_graph_context`, `combine_policy_evidence`, `assess_policy_data_quality`, `verify_cross_db`, `open_neo4j_runner`, `HybridSearchInputError`, `HybridSearchBackendError` |

실험 자료:
- `docs/development/experiments/`: 노트북 원본(11-05). `exp1_load_search.py`·`exp2_lh001.py`는 **Qdrant 쓰기 있음**.
- `docs/development/experiments/desktop_20261105/`: 데스크톱 결과. `neo4j_load_desktop.py`는 **Neo4j 쓰기 있음**, 파일 첫 부분의 경고를 읽고 승인 후에만 실행한다.

---

## 3. 오늘까지 구현한 기능 (설계 이유·대안·한계)

### A. 청킹

- **전략 비교** (전체 246문서 기준 실측)
  - **A**: 인계 패키지의 750자 / 80자 겹침 고정 창, **557개** (`data/handoff` 원본 청크)
  - **B1**: 원문 필드·제목 경계로 자름, **1,300개**
  - **B2**: B1에서 100자(`MIN_CHUNK_CHARS`) 미만 청크를 **허용된 의미 그룹끼리만** 병합, **1,081개**
    - 허용 짝: 기본정보+지원내용, 기본정보+자격, 신청+문의, 신청+유의, 문의+유의. 자격 청크에는 다른 주제를 섞지 않는다.
  - **C**(LLM 섹션 제목 추출): 미구현, 설계만 있음
- **B2를 실험에 쓴 이유:** B1은 짧은 조각이 많아 문맥이 끊긴다. A는 제목·자격 문단 경계를 무시하고 자른다. B2는 원문 경계를 지키면서 짧은 조각만 줄인다. **실험용 선택일 뿐 최종 청킹 규칙으로 확정된 것은 아니다**(PRD §13).
- **원문 보존**
  - 모든 청크는 `text == 원문[char_start:char_end]`이다.
  - 제외 구간(페이지 표지·출처 URL·수정일시)은 따로 기록한다. "누락 0자"를 `verify_coverage`로 검사한다.
  - `chunk_id = CHK_{버전}_{document_id}_C001` 형식이다(예: `CHK_B2_DOC_LH_001_C005`). dev 데이터 계약의 확장 패턴(`CHK_…`)과도 맞는다.
  - Qdrant `point_id = uuid5(chunk_id)`라서 다시 적재해도 중복이 생기지 않는다.
- **PDF 페이지:** 추출 단계의 `--- [Page N] ---` 표지에서만 가져오고 추측하지 않는다. DOC_LH_001의 73개 청크 모두 표지와 일치했다(1~25쪽).
- **B1/B2 출력 해시는 테스트 상수로 고정되어 있다.** 청킹 코드를 바꾸면 테스트가 실패하게 만들어 두었다.
- **현재 검색 실패 사례** (DOC_LH_001 PDF 질문, 데스크톱 재현)
  - **P3** "예비신혼부부는 언제까지 혼인신고를 해야 하나요?"
    - 근거 청크가 Top-5 밖이다. 지시문 유무와 관계없이 같았다.
    - 가설: 741자 청크 안 ①~⑤ 목록 중 한 줄이라 의미가 희석됐다.
  - **P6** "신청 관련 상담 전화번호는 무엇인가요?"
    - 정책 범위가 없는 일반 질문이라, 필터가 없으면 다른 정책 청크가 상위를 차지한다. 근거 청크는 10위였다.
    - `document_id` 또는 `policy_id` 필터를 쓰면 근거 청크가 **3위**로 오른다(`hybrid_experiment.json`).
    - 시사점: 정책 범위를 좁히는 필터나 질문 재작성이 필요하다. 아직 구현하지 않았다.
  - 한계: LH 부록(14~25쪽) 청크가 마지막 제목을 물려받는 B 구조 인식의 한계가 있다.

### B. 임베딩

- 모델: **`qwen3-embedding:0.6b`** (Ollama, **1024차원**, Cosine, 정규화 벡터). **기준선(baseline)일 뿐 최종 모델이 아니다.**
- 입력 규칙 **`policy_section_body_v2`**
  - 저장 text와 임베딩 입력을 분리했다. 입력은 정책명·섹션명·본문을 조합한다.
  - v1은 B 청크의 83%에서 섹션명이 두 번 들어갔다. v2는 본문 줄 시작에 이미 있는 제목만 생략한다.
  - 규칙 이름은 payload `embedding_input_rule`에 남겨 다른 규칙의 벡터와 섞이지 않게 했다.
- **질의 지시문 실험**
  - 비교: 지시문 없음(A_none) vs 모델카드 지시문 `Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}`(B_instruct)
  - 결과: B는 점수만 전반적으로 높고, 근거 순위는 같거나 나빴다. **표본이 작아 결론 없음.**
  - `build_query_text`에는 기본 지시문이 없다. 호출하는 쪽이 `None` 또는 템플릿을 직접 넘긴다.
  - Hybrid 검색 기본값은 `query_template=None`(지시문 없음)이다.
- **성능 (실측, Ollama 서버 측정)**

  | 환경 | 34개(11,953토큰) | 73개(31,834토큰) | 질의 임베딩 1건 |
  |---|---|---|---|
  | 노트북 i5-1135G7, Iris Xe (Ollama **100% CPU**) | 69.98초 | 245.5초 (batch 4) | 0.155~0.41초 |
  | 데스크톱 RTX 5070 (Ollama **100% GPU**) | 0.643초 | 1.419초 | 약 0.013~0.07초 |

  - 노트북에서는 데스크톱보다 **100배 이상 느리다.** 노트북에서는 전체 재임베딩을 하지 않는다.
  - 노트북에서 큰 배치를 쓰면 60초 타임아웃에 걸릴 수 있다. 11-05에는 batch 4, timeout 180으로 실행했다.

### C. Qdrant

- 컬렉션 **`exp_b2_qwen3emb06b_v1`** (1024, Cosine, B2, v2 입력). `assert_experiment_collection`은 `^exp_[a-z0-9_]{1,60}$` 이름만 허용하고, 서비스용 이름 `lifepolicy_chunks`는 거부한다.
- **107 point**, 문서별 청크 수:

  | 문서 | 청크 수 |
  |---|---|
  | DOC_NHUF_003 | 14 |
  | DOC_BOK_001 | 7 |
  | DOC_YC_003 | 3 |
  | DOC_GOV_006 | 10 |
  | DOC_LH_001 | 73 |

  - 적재 단위: `RUN_20261105_B2_V2_EXP1` 34개 + `RUN_20261105_B2_V2_EXP2_LH001` 73개
- **검증**
  - 필수 payload 24개
  - payload text = 원문 슬라이스
  - point ID 집합 = uuid5(chunk_id)
  - 재적재 중복 0
  - 기존 34개 payload·벡터 해시 불변(2차 적재 후)
- 안전장치: 삭제 함수가 없다. 기존 컬렉션은 차원·거리 설정만 확인하고, 다르면 중단한다.
- 필터 가능 필드(`PAYLOAD_INDEX_FIELDS`): `policy_id`, `document_id`, `chunk_id`, `chunking_version`, `category`, `region`, `life_stages`, `verification_status`
- **한계: 전체 246개 문서(B2 기준 약 1,081청크)는 아직 색인하지 않았다.** 운영용 버전별 컬렉션과 alias 전환은 설계만 있다.

### D. Neo4j

- Neo4j 5.26.32 community / 드라이버 5.28.8. 데스크톱에 **노드 1,034 / 관계 3,615**를 적재했다(새 로더 사용).
- **노드 레이블 9종:**

  | 레이블 | 개수 |
  |---|---|
  | Policy | 237 |
  | Document | 246 |
  | Organization | 116 |
  | Region | 18 |
  | Category | 5 |
  | LifeStage | 6 |
  | TargetGroup | 11 |
  | ConditionGroup | 343 |
  | EligibilityCondition | 52 |
- **관계 타입 11종**
  - `ADMINISTERED_BY`, `AVAILABLE_IN`, `HAS_CATEGORY`, `APPLIES_TO_LIFESTAGE`, `TARGETS`
  - `SUPPORTED_BY`, `REQUIRES_CONDITION`, `HAS_ROOT_CONDITION_GROUP`, `MODIFIES_CONDITION_OF`
  - `CONTAINS_GROUP`, `CONTAINS_CONDITION`
- **인계 로더 결함** (`data/handoff`의 `load_neo4j.py`)
  - 레이블 없이 `policy_id OR document_id`로 매칭한다.
  - Document·ConditionGroup도 `policy_id`를 가지고 있어서, 관계가 3,615개 → **11,316개**로 부풀었다(오프라인 재현).
  - **이 스크립트는 실행하지 않는다.** 장반석님께 공유할 대상이다.
- **새 로더 설계**
  - 관계 타입별 (출발 레이블, 도착 레이블) + 각 레이블 고유 ID로만 `MERGE`/`MATCH`한다.
  - 값은 `$rows` 파라미터로 넘긴다. 레이블·타입은 허용 목록에 있는 것만 쓴다.
  - 500행 배치 트랜잭션이고, 건수가 다르면 롤백한다.
- **스키마**: 제약 9개 + 인덱스 4개 (`IF NOT EXISTS`). 인계 DDL 두 파일이 서로 달라서 `cypher_ddl.cypher` 기준으로 만들었다.
- **재적재 보호 (`preflight_database`)**
  - 비어 있지 않은 DB에는 기본적으로 적재를 거부한다.
  - `allow_existing=True`를 명시하면 진행한다. 이때도 기존 데이터가 입력의 부분집합인지 먼저 검증한다.
- **검증**
  - 스키마(SHOW CONSTRAINTS/INDEXES)
  - 노드 ID, 관계 (타입, 출발 ID, 도착 ID)의 누락·추가·중복·레이블 오류
  - 노드·관계 **속성값 원본 대조**: 모두 일치(`neo4j_property_check.json`)
  - 결과: 재실행 중복 0, 알림 분류 `needs_review` 0
- 한계: 로더는 null 속성을 `invalid_property`로 거부한다. 계약상 null을 허용하는 필드가 있지만, 현재 인계 데이터의 null은 0건이다.

### E. Hybrid 검색 (`database/hybrid_search.py`)

**흐름:**
1. 질문을 Ollama로 임베딩한다.
2. Qdrant에서 Top-K 원문 청크를 찾는다(필터 선택).
3. 그 청크들의 `policy_id`를 모은다(검색 순서 유지, 형식 검사).
4. Neo4j에서 정책 문맥을 읽는다(읽기 전용 세션, 파라미터화된 Cypher 2개).
5. `policy_id` 기준으로 결합한다.

**호출 방법:**

```python
from database.hybrid_search import HybridSearchSession, search_policy_context

# 질문을 여러 번 보낼 때 (연결 재사용, 권장)
with HybridSearchSession() as session:
    result = session.search_policy_context("신혼부부 전세자금 대출의 금리는?", k=5)
    result2 = session.search_policy_context("상담 전화번호", filters={"document_id": "DOC_LH_001"})

# 한 번만 부를 때 (호출 안에서 연결을 열고 닫음)
result = search_policy_context("질문", filters=None, k=5)
```

**반환 dict (주요 키):**

```text
{
  "query", "filters", "k", "collection", "query_template",
  "policies": [                       # best_rank 순
    {"policy_id", "policy_name", "best_rank", "best_score",
     "evidence": [ {rank, score, policy_id, document_id, chunk_id, text, char_start, char_end,
                    page_start, page_end, section_title, source_url, source_type, content_hash, ...} ],
     "graph_status": "FOUND" | "MISSING_IN_GRAPH",
     "graph": {policy, organizations, regions, categories, target_groups, life_stages, documents,
               modifies_condition_of, condition_modified_by,
               eligibility: {evaluated: False, note, condition_tree, requires_condition_ids,
                             tree_condition_ids, tree_matches_requires_condition,
                             unknown_logic_groups, structure_issues}},
     "evidence_document_check": {document_id: "PASS" | "MISMATCH" | "NOT_TESTED"},
     "data_quality": {warnings: [{code, level, message, ...}], review_status_counts, graph_text_fields}}
  ],
  "unlinked_hits", "missing_policy_ids", "invalid_policy_ids", "warnings",
  "latency_seconds": {query_embedding, qdrant_search, neo4j_context},
  "data_quality_summary": {code: 개수},
  "notes": {score, eligibility, unknown, extracted, gov24_rule, graph_text}
}
```

- **원문 근거와 그래프 조건은 다르다**
  - `evidence[].text`는 공식 원문 청크다. 답변 근거와 출처로 이것을 쓴다.
  - `graph.policy`의 `eligibility`·`benefit` 등은 수집 단계에서 추출한 텍스트이고, `condition_tree`는 인계 데이터의 구조다. 둘 다 원문 근거가 아니다.
  - `score`는 코사인 유사도일 뿐이며, 자격 충족 여부가 아니다.
- **조건 트리 보존:** ConditionGroup의 `logic`(AND/OR/NOT/UNKNOWN)과 계층을 그대로 돌려준다. 해석하거나 판정하지 않는다.
- **입력 검증 (DB 접속 전에 `HybridSearchInputError`)**
  - 빈 질문, 2,000자 초과
  - k가 1~200 밖
  - 허용되지 않은 필터 필드
  - 형식이 틀린 `policy_id`(`^POL_[A-Z0-9_]+$`)
- **백엔드 오류 `HybridSearchBackendError`**
  - `.backend`: `ollama`·`qdrant`·`neo4j`
  - `.kind`: `unavailable`·`timeout`·`auth`·`config`·`server`
  - 메시지에서 비밀번호·API 키·URL 사용자 정보를 지운다(`_safe_message`).
  - **코드 버그(Cypher 문법 오류, KeyError 등)는 감싸지 않고 원래 예외로 올린다.** 장애와 버그를 구분하기 위해서다.
- **타임아웃 (초):**

  | 대상 | 값 |
  |---|---|
  | Qdrant 요청 | 10 |
  | Neo4j 연결 | 5 |
  | Neo4j 연결 획득 | 10 |
  | Neo4j 조회 | 10 (`neo4j.Query(timeout=)`) |
  | Ollama 질의 임베딩 | 60 |

  - 근거: 데스크톱 실측값보다 넉넉하게 두되, 무한 대기는 막는다.
- **연결 소유권:** 세션이 **직접 만든** 연결만 닫는다. 밖에서 넘긴 `client`·`runner`는 닫지 않는다. 연결은 처음 쓸 때 만들고, `close()`는 여러 번 불러도 된다.
- **데이터 품질 경고 (`data_quality.warnings`)**
  - 표시만 하고, 순위·내용은 바꾸지 않는다.
  - `caution` 등급:
    - `UNKNOWN_CONDITION_GROUP`
    - `EXTRACTED_NOT_VERIFIED`
    - `GRAPH_CONTEXT_MISSING`
    - `CONDITION_STRUCTURE_ISSUE`
  - `suspected` 등급:
    - `SUSPECTED_HOMELESS_REQUIRED_WITH_HOUSEHOLD_OR`
    - `SUSPECTED_INCOME_ALL_BANDS_OR`
- **교차 검증 `verify_cross_db`**
  - 검사 7종: `point_id`, `chunk_id_document`, `policy_exists`, `document_exists`, `supported_by`, `document_policy_id`, `content_hash`
  - 데스크톱 결과: **107 × 7 PASS**
- **반복 호출 성능 (데스크톱 실측):** 함수를 매번 호출하면 약 0.162초, `HybridSearchSession`으로 재사용하면 약 0.026초가 걸린다.
- **스모크 결과 (5개 정책 범위):** 원문 근거 질문 5개에서 기대 정책 1위, 근거 청크 1위였다. 저장된 실험(`hybrid_experiment.json`)과 순위·청크·점수가 5/5 같았다. **성능 평가가 아니다.**

---

## 4. 테스트 상태

### 4-1. 2026-11-06 데스크톱 실측 (모두 실제 실행)

| 테스트 | 파일 (테스트 수) | 결과 |
|---|---|---|
| 단위 | `test_chunking.py` 65, `test_handoff_data.py` 24, `test_vector_store.py` 50, `test_neo4j_loader.py` 60, `test_hybrid_search.py` 58 | **257 PASS** |
| Neo4j 통합 | `tests/integration/test_neo4j_smoke.py` | **10 PASS** |
| Qdrant 통합 | `tests/integration/test_qdrant_smoke.py` | **9 PASS** |
| Hybrid 통합 | `tests/integration/test_hybrid_search_smoke.py` | **9 PASS** |

- 실행 전후 DB 건수가 같았다: Qdrant 107, Neo4j 1,034 / 3,615.
- 통합 테스트는 쓰기 메서드를 막고 실행한다.

### 4-2. 실행 명령 (PowerShell, 프로젝트 루트)

```powershell
# 단위 (서버 불필요)
.venv\Scripts\python.exe -m unittest discover -s tests/unit

# 통합 (스위치를 켜야 실제 서버에 접속, 없으면 NOT_RUN으로 skip)
$env:RUN_NEO4J_SMOKE="1";  .venv\Scripts\python.exe -m unittest tests/integration/test_neo4j_smoke.py -v
$env:RUN_QDRANT_SMOKE="1"; .venv\Scripts\python.exe -m unittest tests/integration/test_qdrant_smoke.py -v
$env:RUN_HYBRID_SMOKE="1"; .venv\Scripts\python.exe -m unittest tests/integration/test_hybrid_search_smoke.py -v
```

- `unittest discover -s tests`는 0건이다(`__init__.py` 없음). `-s tests/unit` 또는 파일 경로를 지정한다.
- 서버 접속이 안 되거나 데이터가 없으면 `DATA_NOT_READY`로 skip된다. **skip은 PASS가 아니다.**
- 필요한 환경 변수 이름(값은 `.env`에만): `QDRANT_URL`, `QDRANT_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `OLLAMA_BASE_URL`
  - Windows에서는 `localhost` 대신 `127.0.0.1`을 쓴다. `localhost`는 요청마다 약 2초씩 지연된다.

### 4-3. 검증된 것 vs 노트북에서 다시 확인할 것

| 구분 | 내용 |
|---|---|
| 데스크톱에서 검증 완료 | 위 테스트 4종, Neo4j 적재·속성 일치, 교차 검증 107×7, 스모크 질문 순위 재현 |
| 노트북에서 재확인 | 단위 257 PASS (서버 불필요 → 바로 가능) |
| 노트북에서 재확인 | Qdrant 통합 9: 노트북 Qdrant에 107 point가 남아 있으면 실행 가능. CPU 벡터라 점수는 미세하게 다를 수 있다 |
| 노트북에서 재확인 | Neo4j·Hybrid 통합: **노트북 Neo4j가 비어 있으면 `DATA_NOT_READY` skip이 정상**. 적재는 승인 없이 하지 않는다 |
| 미검증 | 전체 246문서 색인, 평가 질문 세트 기반 성능(Recall@K·MRR), RAG·조건 확인 모듈과의 실제 연결 |

---

## 5. 데이터 품질 위험 (상세: 이전 인수인계서 §12)

### 5-1. 두 가지 "정확성"을 구분한다

| 구분 | 의미 | 현재 |
|---|---|---|
| 저장·관계 무결성 | DB가 인계 파일과 같은가 | **통과** (실측) |
| 조건 의미 정확성 | 그래프 자격 조건이 실제 정책 자격과 맞는가 | **오류 의심 다수, 미검증** |

**원본 인계 데이터와 DB가 같다는 사실은 정책 조건이 정확하다는 뜻이 아니다.**

### 5-2. 정부24 조건 생성 규칙 오류 의심 (SUSPECTED, 공식 원문 검증 완료 아님)

조건 트리가 있는 정책 82개를 기준으로 집계했다. 같은 정책이 여러 항목에 겹치므로 합산하지 않는다.

| 항목 | 정책 수 | 근거 위치 |
|---|---|---|
| 무주택이 루트 AND 필수로 승격 (원천에서는 여러 가구유형 플래그 중 하나) | **46** | `scripts/build_handoff_package.py` 290·310행 |
| 소득 5구간 전부 OR → 의미상 "소득 제한 없음"인데 조건처럼 보임 | **45** | `scripts/build_clean_dataset.py` 1763행 |
| 가구 OR 그룹에 원천 `JA0410`(가구 "해당사항없음")이 빠짐 | **46** | `collectors/api/gov24_collector.py` 219행 |
| 원천에서 Y인 취업·경제활동 조건(예: 어업인)이 그래프에 없음 | **27** | `build_clean_dataset.py` 1733행 이후 |

- 대표 사례 **POL_GOV_006**(수산자원보호 직접지불제)
  - 그래프 조건: 연령 19~120 + 무주택 필수 + 소득 5구간 OR + 가구 7종 OR
  - 수집 원문에는 이 조건들에 대한 언급이 0회이고, 실제 요건(어업인 등)은 그래프에 없다.
- 위 코드 위치는 **장반석님 담당 파일**이다. 읽기만 했고 수정하지 않는다.

### 5-3. UNKNOWN 조건

- 루트 그룹 `logic=UNKNOWN`이고 조건이 0개인 정책: **155개** / 237개
- 그중 정책 텍스트에 조건 키워드(만 N세·소득·무주택·거주·혼인·장애)가 있는 정책: **122개**
  - 키워드 검색 기준이며, 실제 조건인지는 검증하지 않았다.
  - 예: POL_001, POL_003, POL_BOK_001
- "조건 0개" ≠ "자격 제한 없음"이다. 추천·판정에서 TRUE로 처리하지 말고 "공고 원문 확인 필요"로 안내한다.

### 5-4. 사용 원칙

- **자격 판정에 그래프 추출 조건을 무조건 사용하면 안 된다.**
  - `suspected` 경고가 있는 정책은 "공고 확인 필요"로 안내한다.
  - 자격 답변의 근거는 Qdrant 원문(`evidence`)을 우선한다.
- 공식 원문으로 검증한 사실이 아니라, **인계 파일·수집 텍스트·생성 코드 분석에 따른 의심**이다. 원천 JSON(`data/raw/gov24/items/*.json`)은 로컬에 없다.
- 데이터 담당 확인 전까지 확정하지 않는다. 인계 파일과 DB는 수정하지 않았다.

---

## 6. Git 협업 규칙 (최종 기준)

> 기존 문서에서 dev 병합을 "사용자 결정"으로 적은 부분은 **feature 브랜치로 dev 변경을 받는 것**을 말한다. **dev로 합치는 merge는 AI가 하지 않는다.** 아래가 최종 기준이다.

1. Issue와 feature 브랜치는 **사용자가 직접** 만든다. AI는 Issue 번호를 지어내거나 브랜치를 만들지 않는다.
2. 작업 브랜치: **`feature/5-hybrid-rag-db`**
3. PR 대상: **`dev`**
4. **AI가 dev를 직접 merge하지 않는다.**
5. **PR 제출 후 팀장님이 검토하고 merge한다.**
6. AI는 명시적 승인 없이 commit·push·PR 생성을 하지 않는다. rebase·reset·stash·cherry-pick·pull도 승인 후에만 한다.
7. 커밋 메시지는 **한국어**로 쓴다(제목 + 글머리표 본문).
8. **Co-Authored-By 줄은 넣지 않는다.**
9. `git add -A`는 쓰지 않는다. 검수한 파일 경로만 지정해 staging한다.
10. 팀원 담당 파일은 임의로 수정하지 않는다.
11. `.env`와 비밀정보는 Git에 올리지 않는다.
12. Docker 볼륨을 삭제하지 않는다. `docker compose down -v`, `volume prune`, `system prune`을 쓰지 않는다.
13. 모든 작업은 **완료 / 미완료 / 미검증(NOT_TESTED)**으로 구분해 보고한다. 실행하지 않은 것을 PASS로 쓰지 않는다.
14. 작업 순서: 분석 → 변경 범위 보고 → **사용자 승인** → 구현 → 설명 → 테스트 → 다음 단계 승인
15. 보고는 한국어로 한다. 사용자는 Python 초보자이므로 함수 입력·출력과 설계 이유를 쉬운 말로 설명한다.

---

## 7. 노트북 환경 (설치·초기화 전에 먼저 확인)

노트북 사양: i5-1135G7, **RAM 16GB(15.7GB)**, **Intel Iris Xe 내장 그래픽**. Ollama가 iGPU를 쓰지 않아 **100% CPU**로 돈다. **데스크톱 RTX 5070 성능을 기대하면 안 된다.**

노트북에는 2026-11-05에 만든 환경이 남아 있을 수 있다. **바로 설치·초기화하지 말고**, 아래를 읽기 전용으로 확인해 보고한다.

| 순서 | 확인 | 명령 예 (읽기 전용) | 주의 |
|---|---|---|---|
| 1 | Git 로컬 변경 | `git status`, `git stash list` | 변경이 있으면 건드리지 말고 보고 |
| 2 | feature 원격 차이 | `git branch --show-current`, `git log --oneline -5`, (승인 후) `git fetch origin`, `git rev-list --left-right --count HEAD...origin/feature/5-hybrid-rag-db` | 노트북 로컬은 `b1436ed`에 머물러 있을 가능성이 높다 |
| 3 | 안전하게 받을 수 있는지 | 로컬 변경 0이고 로컬이 원격의 조상이면 fast-forward 가능 | `git pull`·`git merge --ff-only`는 **승인 후**. 계획만 제시 |
| 4 | Ollama | `ollama --version`, `ollama list`, `ollama show qwen3-embedding:0.6b`, `ollama ps` | 1024차원 확인, PROCESSOR가 CPU인지 |
| 5 | Python | `.venv\Scripts\python.exe --version`, `pip list`, `pip check` | qdrant-client 1.12.2, neo4j 5.28.8, python-dotenv 1.2.4 |
| 6 | Docker | `docker ps -a`, `docker volume ls` | 기존 볼륨은 **삭제 금지** |
| 7 | Qdrant | 컬렉션 존재·point 수 (`count_points`) | 107 point가 남아 있는지. 덮어쓰지 않는다 |
| 8 | Neo4j | 노드·관계 수 읽기 (`experiments/neo4j_readonly_check.py`) | 11-05 노트북 상태처럼 비어 있는지 |
| 9 | 메모리 | `Get-CimInstance Win32_OperatingSystem` (여유 메모리) | Neo4j(힙 최대 1GiB + 페이지캐시 512MiB)와 Qdrant, Ollama, WSL을 함께 띄우면 빠듯하다 |

- **노트북에서 DB를 다시 적재하거나 기존 데이터를 덮어쓰지 않는다.**
  - Neo4j 적재(`neo4j_load_desktop.py`)와 Qdrant 재적재(`exp1/exp2` 스크립트)는 승인 대상이다. 기본 계획은 "하지 않음"이다.
- Neo4j가 비어 있으면 Neo4j·Hybrid 통합 테스트는 skip된다. 이 상태로 코드 리뷰·문서 작업은 할 수 있다.
- `.env`는 사용자가 직접 관리한다. AI는 변수 존재 여부와 호스트(127.0.0.1)만 확인하고 값을 출력하지 않는다.

---

## 8. 앞으로 남은 작업 (우선순위, 각 단계 사용자 승인 필요)

| 순위 | 작업 | 상태 | 비고 |
|---|---|---|---|
| 1 | 팀원에게 데이터 품질 문제 공유 (§5, 인계 로더 결함, DDL 충돌, URL 불일치 2건, 노이즈 청크 27건) | 미완료 | 주 대상 장반석님. 조건 확인(강민지)·RAG(나지인·박소희)에도 영향 |
| 2 | 최종 시연 검색 범위 확정: 현재 5개 정책 vs 전체 237개 정책 | **결정 대기** | 팀 결정. 임의로 확정하지 않음 |
| 3 | (2에서 전체로 결정되면) 246문서 B2 색인을 새 실험 컬렉션에 생성 | 미착수 | 데스크톱 GPU 기준 임베딩 수십 초(추정). 노트북 CPU는 매우 느림. 기존 107 컬렉션 보존 |
| 4 | `requirements.txt`·`.env.example` 공용 설정 협의 | 미완료 | 의존성 3개 추가, `.env.example`에 `OLLAMA_BASE_URL` 없음, 127.0.0.1 권장 안내. 공용 파일이라 팀 협의 후 |
| 5 | `docs/architecture/` 문서 작성 (산출물 02) | 미착수 | 현재 `.gitkeep`만 있음. 공용 위치 |
| 6 | RAG·조건 확인 담당과 인터페이스 계약 합의 | 미완료 | §3-E 반환 구조와 D-07 결과 계약 연결 |
| 7 | PR 전 최종 검수 | 미완료 | 사용자가 직접 이해·검토 (§9) |
| 8 | feature → `dev` PR 제출 | 미완료 | **사용자 승인 후**. 본문: 무엇을·왜·테스트·영향·범위(5개 정책)·데이터 품질 위험 |
| 9 | 팀장님 리뷰 후 **팀장님이 merge** | — | AI는 merge하지 않음 |

- 참고: origin/dev의 변경(문서 19개)은 이 브랜치와 경로가 겹치지 않는다. `git merge-tree` 모의 결과 충돌이 0건이었다. dev 변경을 feature로 받을지는 사용자가 결정한다.

---

## 9. PR 전 검토 자료 (사용자 검토 예정)

사용자(주민혁)는 PR 전에 작업을 직접 이해하고, 수정할 부분을 검토할 예정이다. **사용자가 충분히 이해하고 판단하기 전에는 PR을 자동으로 만들지 않는다.**

결정 대기 항목:

| 항목 | 현재 상태 | 결정 주체 |
|---|---|---|
| 청킹 전략 최종 확정 (A/B1/B2/C) | B2로 실험만 함. 평가 질문 세트 기반 비교 미실시 | DB + 검색·평가 (PRD §13) |
| `qwen3-embedding:0.6b` 최종 모델 여부 | 기준선 | 팀 (PRD §13) |
| 전체 정책 색인 여부 | 5개 문서만 색인 | 팀 (시연 범위와 연동) |
| 데이터 품질 오류 처리 | 의심만 기록, 경고 표시 | 데이터 담당 + 조건 확인 + DB |
| 팀원 인터페이스 계약 | 반환 dict 초안만 있음 | RAG·조건 확인 담당 (D-07) |
| 공용 파일 변경 | `requirements.txt`·`.env.example`·README 미반영 | 팀 |

검토하기 좋은 순서:
1. `database/hybrid_search.py`의 모듈 설명과 `search_policy_context`
2. `HybridSearchSession`
3. `assess_policy_data_quality`
4. `database/neo4j/load_graph.py`의 `preflight_database`·`verify_loaded_graph`
5. `database/chunking.py`의 B2 병합 규칙

---

## 10. 내일 노트북 Claude Code 시작 프롬프트 (그대로 복사)

```text
LifePolicy AI 프로젝트의 feature/5-hybrid-rag-db 브랜치 작업을 학원 노트북에서 이어간다.
모든 보고와 설명은 한국어로 한다 (코드·경로·명령어는 영문 유지).

1. 먼저 docs/development/HANDOFF_Issue5_Desktop_to_Laptop_20261106.md 를 읽어라.
   이 파일이 노트북에 아직 없으면 git log·status만 확인하고, 원격 feature 브랜치에서 안전하게 받는 계획만 제시한 뒤 멈춰라.
2. AGENTS.md, 그리고 실제 코드(database/, tests/)를 확인해라. 문서와 코드가 다르면 코드가 기준이다.
   최신 PRD·CODING_STYLE은 git show origin/dev:<경로> 로 읽기만 한다.
3. 로컬 Git 상태를 확인해라: 현재 브랜치, HEAD, 미커밋 변경, stash, 원격 feature 브랜치와의 차이.
   원격 변경을 받아야 하면 git fetch / git pull / merge --ff-only 등 "계획만" 제시하고 실행하지 마라.
4. 노트북 환경을 읽기 전용으로 점검해라: Ollama·모델, .venv 패키지, Docker 컨테이너·볼륨,
   Qdrant 컬렉션 point 수(기존 107개), Neo4j 노드·관계 수(비어 있는지), 여유 메모리.
   노트북은 RAM 16GB, Intel Iris Xe(CPU 임베딩)이다. 데스크톱 GPU 성능을 기대하지 마라.
5. 기존 데이터를 삭제·덮어쓰기·재적재하지 마라. Docker 볼륨 삭제 금지.
6. 현재 작업 상태를 요약하고, 미완료 작업과 권장 우선순위를 보고해라.
   107 point 결과를 전체 정책 추천 시스템의 검증 완료로 표현하지 마라.

금지 (내가 승인하기 전까지): 코드·문서 수정, Git 쓰기(commit·push·pull·merge·rebase·reset·stash·브랜치/Issue/PR 생성),
DB 쓰기, 패키지 설치, 모델 다운로드, Docker 컨테이너 실행·삭제, 비밀정보(.env 값) 출력, 팀원 담당 파일 수정.
dev 브랜치 merge는 하지 않는다 (PR 후 팀장님이 merge). 커밋 시 한국어 메시지, Co-Authored-By 금지, git add -A 금지.
점검 결과를 보고하고 멈춰라.
```
