# LifePolicy AI — Issue #5 (Hybrid RAG DB) 작업 인수인계서

> 작성: 2026-11-05, 노트북 Claude Code 세션 → 데스크톱 새 세션
> 담당: 주민혁 (임베딩·DB 담당) / 브랜치 `feature/5-hybrid-rag-db`
> **이 문서를 읽은 뒤 반드시 실제 코드·데이터를 다시 확인하고 작업한다.** 문서 내용과 코드가 다르면 코드가 기준이다.
> 비밀값(.env 값, API 키, 비밀번호)은 이 문서에 없다. 출력·커밋하지 않는다.

---

## 0. 새 세션이 가장 먼저 할 일

1. `AGENTS.md`, `docs/product/PRD.md`, `docs/development/CODING_STYLE.md`, `README.md` 읽기
   - 이 feature 브랜치에는 **origin/dev의 최신 문서 변경(PR #6·#7·#8)이 아직 병합되지 않았다.** 최신 규칙·역할은 `git show origin/dev:<경로>`로 읽기만 한다. 병합은 사용자 승인 후.
2. `git branch --show-current`, `git status`, `git log --oneline -5`로 상태 확인
3. 아래 §4의 파일 11개와 `docs/development/experiments/`가 있는지 확인 → 없으면 **작업 중단하고 사용자에게 보고**
4. §8 "데스크톱 복구 순서"대로 환경 구성 → §9 기준으로 검증
5. 검증이 끝나면 §11 "다음 작업"으로 진행 (각 단계 사용자 승인 필요)

### 0-1. 데스크톱 Claude Code 첫 프롬프트 (사용자가 붙여 넣을 문안)

```text
LifePolicy AI 프로젝트의 feature/5-hybrid-rag-db 브랜치에서 작업을 이어간다.
먼저 docs/development/HANDOFF_Issue5_hybrid_rag_db_20261105.md 와 AGENTS.md를 읽고,
실제 코드(database/, tests/)와 Git 상태를 확인해라. 문서와 코드가 다르면 코드가 기준이다.

이번 단계는 데스크톱 환경 점검·복구까지만 승인한다.
1. 설치 상태 확인: Git, Python 3.13, Docker Desktop/Compose, Ollama(qwen3-embedding:0.6b), GPU(nvidia-smi), RAM
2. 없는 프로그램·패키지·모델은 설치 대상과 정확한 명령을 먼저 보고하고 승인 후 설치한다.
3. .venv 생성 후 requirements.txt + qdrant-client==1.12.2 neo4j==5.28.8 python-dotenv==1.2.4 설치, pip check
4. .env는 내가 직접 작성한다. 너는 값이 아니라 변수 존재 여부와 호스트(127.0.0.1)만 확인한다.
5. 단위 테스트 165개 실행 결과를 PASS/FAIL/NOT_TESTED로 보고한다.

금지: commit·push·merge·rebase·브랜치/Issue 생성, Docker 볼륨 삭제, DB 쓰기, 비밀값 출력, 승인되지 않은 파일 수정.
Qdrant 107개 point 재생성과 Neo4j 적재는 다음 단계에서 별도로 승인한다.
점검 결과와 노트북 대비 차이를 보고하고 멈춰라.
```

---

## 1. 프로젝트 개요

- **서비스:** 생애주기 기반 정부 정책 추천 + 근거 기반 질의응답 (LifePolicy AI, SKN36 3차 LLM/RAG 프로젝트)
- **필수 구조:** Hybrid RAG = Qdrant(의미 검색) + Neo4j(자격 조건·관계 탐색), 공통 키 `policy_id`·`document_id`
- **통합 브랜치:** `dev` (PR 대상도 `dev`, CODING_STYLE 갱신 기준)
- **역할 (origin/dev의 PRD 기준, 2026-11-05 갱신 — 이 브랜치에는 아직 미병합)**
  - 반석: 데이터 수집·정제·인계 (`collectors/`, `scripts/`, `data/`) — **수정 금지 영역**
  - 주민혁: 임베딩·DB (Qdrant·Neo4j 적재, 검색 인터페이스, 산출물 02, A/B 비교 설정·인덱스 재현 지원)
  - 나지인·박소희: LLM·프롬프트·RAG (`backend/rag/`, `ai/`), 산출물 04
  - 강민지: 사용자 조건 확인(`backend/eligibility/`)·산출물 03·발표 (UI는 4차, React 확정)
- 3차는 터미널·노트북 최소 실행으로 시연. 웹 UI는 4차.

---

## 2. 협업 규칙 (사용자가 반복해서 지시한 것 — 반드시 지킬 것)

1. **작업 순서:** 분석 → 변경할 파일·범위 보고 → **사용자 승인** → 구현 → 코드 설명 → 테스트 → 다음 단계 승인
2. 승인된 파일만 수정·생성한다. 새 파일·폴더 생성도 승인 대상.
3. **commit·push·PR·merge·rebase·stash·reset·checkout 금지** (사용자 승인 시에만). Issue·브랜치는 사용자가 직접 만든다. Issue 번호를 지어내지 않는다.
4. 커밋 메시지는 **한국어**, **Co-Authored-By 추가 금지** (사용자 지시)
5. 팀원 코드·인계 파일(`data/handoff/` 등) 수정 금지. 문제는 보고 → 담당자에게 공유.
6. 패키지 설치, Docker 실행, DB 접속·쓰기, 모델 다운로드는 사용자 승인 후.
7. 기존 데이터·Docker 볼륨 삭제 금지 (`docker compose down -v`, `volume prune`, `system prune` 금지). 삭제 함수·DETACH DELETE 구현 금지.
8. 테스트 결과는 **PASS / FAIL / NOT_TESTED**로 구분. 실행하지 않은 것을 PASS로 쓰지 않는다. 실측·추정·미검증을 구분해서 보고한다.
9. 정책 사실·결과를 지어내지 않는다. 근거 없으면 `unknown`/`needs_review`. 가상 데이터는 `sample_only` 표시.
10. `.env` 값·API 키·비밀번호는 절대 출력하지 않는다(존재 여부·길이·호스트만 확인).
11. 사용자는 Python 초보자 → 함수별 입력·출력과 핵심 코드를 쉬운 말로 설명. 강사님 코드 리뷰 대비 설계 이유를 기록.
12. 미확정 항목(PRD §13: 임베딩·LLM 모델 최종 선택, 청킹 규칙 최종안 등)을 임의로 확정하지 않는다. `qwen3-embedding:0.6b`는 **기준선(baseline)**일 뿐이다.
13. A/B 비교는 한 번에 변수 하나, 같은 데이터·질문·Top-K. 지표: Recall@K, MRR, 지연, 임베딩 시간, 비용.

---

## 3. Git 상태 (노트북 기준, 2026-11-05)

- 브랜치 `feature/5-hybrid-rag-db`. 분기 시점 `9aa8e03` (= PR #4 병합 시점)
- 2026-11-05 노트북에서 사용자 승인 후 **코드 11개 + 이 인수인계서 + `docs/development/experiments/`를 이 브랜치에 커밋하고 push**했다. (커밋 해시는 `git log`로 확인)
- `origin/dev`는 이 브랜치보다 9커밋 앞서 있다 (`a8e85a0`, PR #6·#7·#8: 문서 19개만 변경, 코드·데이터 파일 변경 0, Neo4j·Qdrant 데이터 체크섬 동일). 경로 겹침 0 → 충돌 없이 병합 가능 예상. **dev 병합은 아직 하지 않았다(사용자 결정).**
- PR은 만들지 않았다. PR 대상은 `dev`.
- **Git에 없는 것:** `.env`(비밀값, 사용자가 직접 작성), `.venv`(새로 생성), Docker 볼륨(Qdrant는 재생성, Neo4j는 빈 DB였음)

### 3-1. 실험 자료

- `docs/development/experiments/README.md` — 파일별 DB 쓰기 여부, 재생성 순서, 노트북 실측 요약
- `exp1_load_search.py`·`exp2_lh001.py` (**DB 쓰기 있음**, 승인 후 실행) → Qdrant 107개 재생성 경로
- `exp1_report.json`·`exp2_report.json` — 노트북 실측 결과 (데스크톱 결과와 비교 기준)
- `exp2_diagnose.py`·`latency_check.py`·`neo4j_readonly_check.py` — 읽기 전용 점검

---

## 4. 구현 파일과 핵심 함수 (모두 신규 파일)

| 파일 | 역할 | 핵심 함수 |
|---|---|---|
| `database/handoff_data.py` | 인계 데이터 로드·검증 | `load_documents(verify_hash=True)`(sha256(text)==content_hash), `load_payload_chunks`, `load_neo4j_nodes/relationships`, `NODE_SPECS`·`RELATIONSHIP_SPECS`·`LABEL_ID_FIELDS`(레이블·관계 허용 목록), `match_relationships_offline`, `verify_manifest_checksums`(raw/lf/crlf), `select_chunks_for_embedding(exclude_noise=False)`, `REVIEWED_NOISE_CHUNKS`(27건, 미합의), `get_verified_page_number`, `load_policy_metadata`(policy_id→정책 메타 조인) |
| `database/chunking.py` | B1/B2 구조 청킹 + 구조 인식 | `chunk_documents(docs, strategy="B1"/"B2")`, `chunk_document`, `merge_short_chunks`(B2), `split_long_span`, `_segments_from_starts`, `build_chunk`, `make_chunk_id`(`CHK_{버전}_{문서}_C001`), `make_point_id`(uuid5 NAMESPACE_DNS), `verify_coverage`, `get_excluded_metadata`, `analyze_structure`(structured/partial/unstructured, LLM 미호출), `score_heading_detection` |
| `database/embedding.py` | Ollama 임베딩 | `build_embedding_text(chunk, meta, rule)` 규칙 `policy_section_body`(v1)/`policy_section_body_v2`(권장)/`body_only`, `describe_embedding_input`, `ollama_embed_batch`(`truncate:false`, 개수·차원·NaN 검증, `metrics_sink`), `make_ollama_embedder`, `resolve_ollama_base_url`(인자→환경변수→.env→기본값), `build_query_text(question, query_template)`(지시문 기본값 없음), `summarize_ollama_metrics` |
| `database/qdrant/store.py` | Qdrant 저장·검색 (실험 컬렉션 전용) | `assert_experiment_collection`(`^exp_[a-z0-9_]{1,60}$`만, `lifepolicy_chunks` 거부), `build_points`/`build_point_payload`(필수 payload 24개), `get_client`(.env 자동 로딩, `:memory:`), `resolve_connection_settings`, `ensure_collection`(없을 때만 생성, 차원·거리 불일치 시 중단), `upsert_points`, `count_points`, `list_point_ids`, `search_chunks` — **삭제 함수 없음** |
| `database/vector_index.py` | 최소 적재·검색 흐름 | `prepare_chunks(docs, "A"/"B1"/"B2")`, `run_minimal_index`(재적재 중복 검사 포함), `run_smoke_search`, `make_run_info`, `DEFAULT_SAMPLE_DOCUMENT_IDS` |
| `database/neo4j/load_graph.py` | 안전한 Neo4j 로더 (아직 실DB 적재 안 함) | `read_graph_input`, `validate_graph_input`(오류 11종), `build_schema_statements`(제약 9+인덱스 4, IF NOT EXISTS), `build_node_merge_query`/`build_relationship_merge_query`(레이블+고유ID, 파라미터), `build_load_plan`(검증 실패 시 계획 생성 안 함), `load_graph`, `Neo4jDriverRunner`(execute_write 배치 트랜잭션, 건수 불일치 시 롤백), `snapshot_counts`, `check_vector_payload_links` |
| `tests/unit/test_handoff_data.py` | 24개 | |
| `tests/unit/test_chunking.py` | 65개 (B1/B2·구조 인식) | B1/B2 출력 해시 고정 테스트 포함 |
| `tests/unit/test_vector_store.py` | 50개 (임베딩·Qdrant·.env) | Qdrant 메모리 모드 4개 포함 |
| `tests/unit/test_neo4j_loader.py` | 26개 | 가짜 실행기(메모리 그래프) |
| `tests/integration/test_qdrant_smoke.py` | 9개, `RUN_QDRANT_SMOKE=1`일 때만 | 읽기 전용, 기대 107 point |

**B1/B2 출력 해시 (불변 기준):**
B1 `13b7927e…d2c7` / `91d97538…6fe1`, B2 `534d0275…e67a` / `6dbf5cf2…9916` (전체 값은 test_chunking.py·test_vector_store.py 상수)

---

## 5. 설계 결정과 이유 (포트폴리오·코드 리뷰용)

1. **청킹 A/B1/B2/C 비교 설계** — A: 기존 750자/80 고정 창(557개). B1: 원문 필드·제목 경계(1,300개). B2: B1의 100자 미만 청크를 허용된 의미 그룹끼리만 병합(1,081개). C(LLM Section title/start_text, 강의 방식): 미구현, 원문 대조 검증 설계만.
   - 모든 청크 `text == 원문[char_start:char_end]` (원문 불변), 제외 구간(페이지 표지·출처 URL·최종수정일시)은 따로 기록 → "누락 0자" = 청크·제외 구간·공백 어디에도 속하지 않는 글자 0.
   - PDF 페이지는 추출 단계 표지(`--- [Page N] ---`)에서만 가져옴(추측 금지).
   - B2 병합 허용 짝: 기본정보+지원내용, 기본정보+자격, 신청+문의, 신청+유의, 문의+유의. 자격 청크에 다른 주제 섞지 않음. MIN_CHUNK_CHARS=100 근거는 B1 분포 실측.
2. **구조 인식 분리(analyze_structure)** — 청킹 전에 제목 후보 → 줄 단위 검사(※·빈 제목·문장형·대괄호+본문·목차 점선·40자 초과) → 순서 검사(목록·목차·하위 항목·중복 제목) → 판정. 새 숫자 임계값 없이 논리 조건 + 기존 CHUNK_SIZE만 사용. 246건: structured 243 / partial 1 / unstructured 2. **structured가 제목 정확도를 입증하지 않음**(메뉴·요약 상자·첨부 라벨 오인 사례 존재).
3. **임베딩 입력 v2** — 저장 text와 임베딩 입력 분리. v1은 B 청크의 83%에서 섹션명이 두 번 들어감 → v2는 본문 "줄 시작"에 이미 있는 제목 요소·선언된 정책명만 생략(문장 속 언급은 유지). 규칙 이름이 payload `embedding_input_rule`에 남아 기존 벡터와 섞이지 않음.
4. **Qdrant 안전장치** — 실험 컬렉션 이름만 허용, 삭제 함수 없음, point_id=uuid5(chunk_id)로 재적재 중복 0, 기존 컬렉션은 설정만 확인. 운영 전환은 버전별 컬렉션 + alias 전환 설계(미구현).
5. **Neo4j 인계 로더 결함** — `load_neo4j.py`가 레이블 없이 `src.policy_id = … OR src.document_id = …`로 매칭 → Document·ConditionGroup도 policy_id를 가져 POL_001이 노드 5개에 매칭 → 관계 3,615 → **11,316개**로 부풀음(오프라인 재현). 새 로더는 관계 타입별 (출발 레이블, 도착 레이블) + 각 레이블 고유 ID로만 MATCH, 값은 `$rows` 파라미터, 레이블·타입은 허용 목록, 검증 실패 시 아무것도 쓰지 않음, 배치(500행) 단위 트랜잭션.
6. **.env 로딩** — python-dotenv `override=False`, 별도 파서 미구현, 오류 메시지에 비밀값 없음.

---

## 6. 발견한 오류·문제와 해결 과정

| 문제 | 원인 | 해결 |
|---|---|---|
| Windows에서 `localhost` 요청마다 약 2초 지연 | `localhost`→`::1` 먼저 시도, 서비스는 IPv4만 대기 → IPv6 거부까지 ~2.03초 | `.env`의 QDRANT_URL·OLLAMA_BASE_URL·NEO4J_URI를 `127.0.0.1`로. 실측: Ollama 1건 2.23→0.18초, Qdrant 2.05→0.007초, Neo4j 연결 2.24→0.03초 |
| `.env`의 OLLAMA_BASE_URL이 무시될 수 있음 | embedding.py가 .env를 직접 안 읽음 | `resolve_ollama_base_url` 추가 |
| LH_001 임베딩 시간 초과 | CPU에서 16개 배치가 60초 초과 | 호출 인자 batch 4, timeout 180 (코드 기본값 유지) |
| B1 lone `\r` 줄바꿈 미인식 | 복지로·정부24 원문 `\r` 1,140개 | LINE_BREAK 패턴 |
| B1 HTML 번호 제목에 줄바꿈 포함("1.\n혼인 요건") | `\s`가 줄바꿈 매칭 | 임베딩 v2에서 `starts_a_line`으로 처리 |
| 인계 DDL 두 파일 충돌 | `cypher_ddl.cypher`(ID 기준 13개) vs `schema_constraints.cypher`(name 기준, TargetGroup 없음) | 새 로더는 cypher_ddl 기준. 반석님 공유 필요 |
| 인계 문서·코드 불일치 | PR #4 보고서 "semantic section" 주장 vs 실제 750/80 고정 창 코드 | 반석님 공유 필요 |
| 출처 URL 불일치 2건 | DOC_MOLIT_002, DOC_MOEL_001 (plus.gov.kr vs www.gov.kr, 같은 svcId) | 반석님 공유 필요 |
| `unittest discover -s tests` 0건 | tests/·tests/unit/에 `__init__.py` 없음 | 당분간 파일 경로 지정 또는 `discover -s tests/unit` |

---

## 7. 실제 실험 결과 (노트북, 실측)

**환경:** i5-1135G7 4C/8T, RAM 15.7GB, Intel Iris Xe(내장, Ollama가 iGPU 사용 안 함 → 100% CPU, num_ctx 4096)

**Qdrant 실험 컬렉션 `exp_b2_qwen3emb06b_v1`** (1024, Cosine, B2, `qwen3-embedding:0.6b`, `policy_section_body_v2`)
- 1차: DOC_NHUF_003 14 / BOK_001 7 / YC_003 3 / GOV_006 10 = 34 point, `index_run_id=RUN_20261105_B2_V2_EXP1`
- 2차: DOC_LH_001 73 point 추가 → **총 107**, `RUN_20261105_B2_V2_EXP2_LH001`, 기존 34개 payload·벡터 해시 불변, 재적재 중복 0, PDF 페이지 73개 모두 표지와 일치(1~25쪽)
- 임베딩 속도(서버 측정): 1차 34개 69.98초, 11,953토큰(약 171 tok/s, batch 16) / 2차 73개 245.5초, 31,834토큰(116~136 tok/s, batch 4). 모델 로딩 약 2.4~2.6초. L2 norm 1.0(정규화된 벡터).
- 질의 임베딩 0.155~0.41초, 검색 0.009~0.019초 (127.0.0.1 기준)

**검색 결과 요약 (질문 = 원문 근거로 작성, 성능 평가 아님)**
- 1차 4문서 4질문 × (지시문 없음 / 모델카드 지시문 `Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}`): 기대 문서 8/8 1위, 근거 청크 7/8 1위 (GOV_006 지시문 있음에서 2위)
- 2차 PDF 6질문(정책명 생략 5개): P2·P4는 근거 1위, P1 근거 2위, P5 3·4위, **P3(예비신혼부부 혼인신고) 근거 Top-5 밖(15·20위)**, **P6(상담 전화번호, 범위 없는 질문) LH 문서 Top-5 밖**(A)
- 원인 분석(가설): P3는 741자 청크 안 ①~⑤ 목록 중 한 줄 → 희석, P6는 정책 범위 없는 일반 질문 → 필터 필요, LH 부록(14~25쪽) 청크가 마지막 제목을 물려받는 B 구조 인식 한계
- 지시문 B는 점수만 전반적으로 높고 근거 순위는 같거나 나쁨 — 표본이 작아 결론 없음

**Neo4j:** 5.26.32 community 컨테이너 실행·인증 확인, **DB 비어 있음(노드 0·관계 0·제약 0, LOOKUP 인덱스 2)**, 로더 MERGE 쿼리 20개 `EXPLAIN` 문법 통과, 페이지 캐시 512MiB·힙 최대 1GiB, **실제 적재는 아직 안 함**.

**인계 데이터:** 노드 1,034 (Policy 237, Document 246, Organization 116, Region 18, Category 5, LifeStage 6, TargetGroup 11, ConditionGroup 343, EligibilityCondition 52), 관계 3,615 (11타입), 양끝 누락 0·중복 0, Qdrant payload 557·107과 policy_id/document_id/content_hash 연결 불일치 0.

---

## 8. 데스크톱 복구 순서

**노트북 기준 버전:** Windows 11, Python 3.13.15, Git 2.55.0, Docker Engine 29.6.2 / Compose v5.3.1 (WSL 2.7.11),
Ollama 0.40.14, Qdrant 서버 v1.12.4 / qdrant-client 1.12.2, Neo4j 5.26.32 community / neo4j 드라이버 5.28.8,
python-dotenv 1.2.4, VS Code 1.145.0 + Claude Code 확장

각 항목은 **확인 → 없으면 설치 대상·명령을 먼저 보고 → 승인 후 설치 → 버전이 다르면 호환성 확인** 순서로 진행한다.
설치되어 있지 않다는 이유로 기능·테스트를 생략하지 않는다. 시스템 설정 변경·주요 버전 변경·삭제는 사용자 승인 후.

| 순서 | 확인 명령 | 없을 때 (승인 후, ID는 `winget search`로 재확인) |
|---|---|---|
| Git | `git --version` | `winget install --id Git.Git -e` |
| Python 3.13 | `py -3.13 --version` | `winget install --id Python.Python.3.13 -e` |
| Docker | `docker version`, `docker compose version`, `wsl --version` | `winget install --id Docker.DockerDesktop -e` (WSL2·재부팅 필요할 수 있음) |
| Ollama | `ollama --version`, `ollama list` | `winget install --id Ollama.Ollama -e` → `ollama pull qwen3-embedding:0.6b` |
| GPU | `nvidia-smi`, `Get-CimInstance Win32_VideoController` | 드라이버 변경은 승인 후 |

```powershell
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team.git
cd SKN36-3rd-1Team
git checkout feature/5-hybrid-rag-db
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt qdrant-client==1.12.2 neo4j==5.28.8 python-dotenv==1.2.4
.venv\Scripts\python.exe -m pip check
# .env: .env.example을 복사해 사용자가 값 입력. 주소는 127.0.0.1 사용
#   QDRANT_URL=http://127.0.0.1:6333 / NEO4J_URI=bolt://127.0.0.1:7687 / OLLAMA_BASE_URL=http://127.0.0.1:11434 (example에 없음 → 추가)
.venv\Scripts\python.exe -m unittest discover -s tests/unit            # 기대 165 PASS
ollama show qwen3-embedding:0.6b                                         # embedding length 1024
docker volume ls                                                         # 기존 볼륨 확인 (삭제 금지)
docker compose up -d qdrant ; docker compose ps                          # Qdrant만 먼저
# (승인 후) docs/development/experiments/README.md 순서로 107 point 재생성
$env:RUN_QDRANT_SMOKE="1"; .venv\Scripts\python.exe -m unittest tests/integration/test_qdrant_smoke.py -v   # 기대 9 PASS
docker compose up -d neo4j ; docker compose ps                           # 빈 DB 확인 (experiments/neo4j_readonly_check.py)
ollama ps                                                                # PROCESSOR: GPU/CPU 확인
```

- 노트북 성능과 비교 측정(§7 수치, `experiments/*.json` 기준)
- Windows에서는 `localhost` 대신 `127.0.0.1`을 쓴다 (localhost는 요청마다 약 2초 지연, §6)

## 9. 복구 완료 기준

- 단위 165 PASS (`.venv\Scripts\python.exe -m unittest tests/unit/test_chunking.py tests/unit/test_handoff_data.py tests/unit/test_vector_store.py tests/unit/test_neo4j_loader.py`)
- B1/B2 해시 불변, B1 1,300 / B2 1,081 청크
- Qdrant `exp_b2_qwen3emb06b_v1`: 1024/Cosine, 107 point, ID = uuid5(chunk_id) 집합 동일, 문서별 14/7/3/10/73, payload text = 원문, 재적재 중복 0, 통합 테스트 9 PASS
- 같은 질문 10개(1차 4 + 2차 6)를 다시 검색해 노트북 결과와 순위 비교 (벡터 값은 하드웨어에 따라 미세하게 다를 수 있음 → 순위 차이는 기록)
- Neo4j: 5.26 계열, 드라이버 5.28.8 연결, DB 비어 있음 확인 (적재는 §11 승인 후)

## 10. 미완료 작업

- Neo4j 로더 보강(승인 대기): `verify_schema`(SHOW CONSTRAINTS 이름+레이블+속성+유형 검증), `verify_relationship_endpoints`·`verify_node_ids`(원본과 (타입, 출발 ID, 도착 ID) Counter 비교 → 누락·추가·중복·레이블 오류), 빈 DB 확인, `make_driver(notifications_min_severity="OFF")`, 읽기 세션
- Neo4j 실제 적재 1,034/3,615 + 재실행 중복 0 확인 + `tests/integration/test_neo4j_smoke.py`
- Qdrant–Neo4j 교차 검증 `verify_cross_db`, 하이브리드 검색 `search_policy_evidence(query, filters=None, k=5)`, `get_policy_graph_context`
- A/B1/B2 청킹 비교 실험(평가 질문 세트 — 검색·평가 담당과 합의), 질의 지시문 A/B
- C안(LLM 섹션) — 패키지·모델 선택 승인 필요
- 의존성 명세: requirements.txt에 qdrant-client·neo4j·python-dotenv 미반영(공용 파일, 승인 필요), `.env.example`에 OLLAMA_BASE_URL 없음
- 반석님 공유: README §6-2의 `python load_neo4j.py` 안내(결함 로더), DDL 충돌, 27개 노이즈 청크 합의, URL 불일치 2건, `\r`·HTML 엔티티, manifest CRLF/LF
- 산출물 02, 학습용 노트북 3개

## 11. 다음 작업 우선순위 (각 단계 승인 필요)

1. 데스크톱 환경 복구 + §9 검증 + 성능 비교 보고 (Qdrant 107 point 재생성 포함)
2. (사용자 결정) origin/dev 병합 → 테스트 재실행 (노트북 작업 커밋·push는 완료)
3. Neo4j 로더 보강(load_graph.py·test_neo4j_loader.py) → 실제 적재 → 재실행 검증 → Neo4j 통합 테스트
4. 교차 검증 + 하이브리드 검색 인터페이스
5. 평가 질문 세트 + A/B1/B2 비교 (문서 20~30건 규모 제안)
