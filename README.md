# LifePolicy AI — 생애주기 맞춤형 정부 지원 정책 추천 챗봇

> SKN 36기 부트캠프 3차 단위프로젝트 (교과목: LLM)
> 상태: **최종 데이터(v1.4.0) Neo4j·Qdrant 적재 검증 완료 — RAG·API 통합(#23)과 화면 연동은 진행 중** (2026-11-10)

사용자가 나이·지역·취업·혼인·가구·소득 상태를 입력하면, 공식 정책 원문을 근거로 관련 정부·지자체 지원 정책을 찾고
**조건 충족 여부와 추가 확인 사항을 구분**하여 신청 절차·필요 서류·공식 링크·출처를 제시하는 한국어 RAG 질의응답 서비스입니다.

- 기획 문서: [docs/product/PRD.md](docs/product/PRD.md)
- 코딩 규칙: [docs/development/CODING_STYLE.md](docs/development/CODING_STYLE.md)
- AI 코딩 도구 규칙: [AGENTS.md](AGENTS.md)
- 일정·역할: Notion WBS (PRD 상단 링크)

> 학습용 MVP이며 행정기관의 최종 자격 심사나 법적·금융적 조언을 대체하지 않습니다.

## 진행 현황

PRD §3.1 필수 MVP 기준입니다. 완료된 항목만 체크합니다.

- [x] 초기 폴더 구조 및 공통 설정 파일
- [x] 정책 정제 데이터 및 Graph·Vector DB 인계 파일 준비
- [ ] 원본 파일 별도 전달·복원 및 로컬 존재 확인
- [ ] 원본 배치 후 최종 인계 패키지 검증 통과
- [x] Neo4j 정책 관계 적재·조회 (#45 최종 재적재 검증)
- [x] Qdrant 원문 청크 임베딩·검색 (B2 청킹, qwen3-embedding 0.6B)
- [x] Graph + Vector 결합 조회
- [x] 사용자 입력 검증 + 연령·지역·신청기간 조건 확인
- [x] LLM 근거 기반 답변 + 출처 표시
- [x] 평가 질문·검색 실험 결과 기록 (55문항 실행·52문항 채점, Gold 최종 합본은 별도)

> 3차 발표는 **React 세 화면(상담하기·생애주기·정책 관계 지도)**으로 질문과 선택 조건을 입력하고 답변·공식 출처를 보여줍니다. 터미널·노트북은 시연 백업이며 정식 웹 UI 개발은 4차 범위입니다.

## 기술 스택

| 영역 | 기술 | 상태 |
|---|---|---|
| Backend | Python, FastAPI | 구현 완료 |
| LLM·RAG | Python RAG Pipeline, OpenAI API | 3차 시연 모델 `gpt-5.4-mini` 채택 ([PR #56 비교 결과](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/56)) |
| Graph DB | Neo4j | 최종 DB 적재·검증 완료 |
| Vector DB | Qdrant | 최종 DB 적재·검증 완료 |
| Embedding | Ollama · Qwen3 Embedding 0.6B | 기본 검색 프로필에 적용 |
| Frontend | React | 3차 최소 시연·4차 정식 웹 UI |
| 데이터 처리 | Python, Pandas, XML/HTML/PDF 파서 | 파서 라이브러리 미확정 |
| 테스트 | pytest | 제안 |
| 개발 환경 | Python venv, Docker Compose | 제안 |

## 폴더 구조

```text
3차/
├── backend/            # FastAPI 서버
│   ├── api/            #   라우터·요청/응답 스키마
│   ├── core/           #   설정·로깅·공통 유틸
│   ├── models/         #   Pydantic 도메인 모델 (정책·문서·청크 등)
│   ├── services/       #   정책 조회·답변 조합 비즈니스 로직
│   ├── rag/            #   검색 결과 결합·LLM 호출
│   ├── graph/          #   Neo4j 연동
│   └── eligibility/    #   사용자 입력 검증, 연령·지역·신청기간 조건 확인
├── frontend/           # 3차 React 최소 시연 화면과 4차 웹 UI
├── data/
│   ├── raw/            # 원본 (youth_policy/, lh/, other/) — Git 기본 제외
│   ├── processed/      # 정제 결과 (policies/documents/relationships.jsonl)
│   ├── metadata/       # 출처·API 등록부
│   ├── samples/        # 형식 설명용 가상 예시 (실데이터 아님)
│   └── schemas/        # JSON Schema 등 데이터 계약 정의
├── collectors/         # 수집기 (api/, html/, pdf/) 및 preprocessing/
├── database/           # neo4j/ (Cypher·제약조건), qdrant/ (컬렉션 설정), migrations/
├── ai/                 # embeddings/, prompts/, workflows/ (확장용), evaluation/
├── tests/              # unit/, integration/, evaluation/
├── scripts/            # 적재·재실행 유틸리티
├── docs/               # product/, development/, architecture/, data/, api/, reports/
├── .env.example
├── docker-compose.yml  # 개발용 Neo4j + Qdrant
└── AGENTS.md
```

## 팀별 담당 영역

역할은 Notion WBS 기준이며, 폴더 경계는 협업을 위한 **기본 제안**입니다.

| 역할 | 담당 | 주요 작업 위치 | 산출물 |
|---|---|---|---|
| 데이터 / API / README | 장반석 | `data/`, `collectors/`, `docs/data/` | 01 수집 데이터 및 전처리 문서 |
| 임베딩 / DB | 주민혁 | `database/`, `ai/embeddings/`, `backend/graph/`, `docs/architecture/` | 02 시스템 아키텍처 |
| 검색 / 조건 확인 / 평가 | 나지인, 박소희 | 검색 관련 코드, `backend/eligibility/`, `ai/evaluation/`, `tests/evaluation/` | 05 테스트 계획 및 결과 보고서 |
| LLM / RAG | 나지인, 박소희 | `backend/rag/`, `ai/prompts/`, `ai/workflows/` (조건 결과 기반 답변·추가 질문) | 04 RAG-LLM 및 DB 연동 코드 |
| PM · React 시연 UI · 발표 | 강민지 | 프로젝트 일정·진행 관리, 팀 협업 및 GitHub 작업 흐름 관리, `frontend/` | 03 개발 소프트웨어 실행·시연 정리 (화면 연동·검증, 전원 실행법 취합), 발표 |
| 공통 변경 | 전원 (PR 리뷰 필수) | `README.md`, `.env.example`, `AGENTS.md`, `docs/api/` (공통 API 계약) | — |

RAG/API와 Checker·Generator의 파일별 담당은 [API 계약 §0](docs/api/ask_contract.md)을 기준으로 합니다.

## 로컬에서 전체 실행하기

1. [DB 복원 가이드의 가상환경 준비 절차](docs/development/ISSUE43_RESTORE_GUIDE.md)에 따라 Python 가상환경을 준비하고 `requirements.txt`의 의존성을 설치합니다.
2. 같은 가이드에 따라 복원 ZIP으로 Qdrant·Neo4j 데이터를 복원하고 Ollama를 준비합니다. 저장소 루트의 `.env`에 복원 환경과 일치하는 DB 접속 정보, Ollama 주소, 답변 생성 API 설정을 입력합니다.
3. 가상환경을 활성화한 상태로 저장소 루트에서 백엔드를 실행합니다.

   ```powershell
   python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --env-file .env
   ```

   백엔드 실행 후 [상태 확인](http://127.0.0.1:8000/health)에서 API 기동과 `ready` 값을 확인합니다. 실제 검색·답변 생성·출처 표시는 5단계의 상담 요청으로 확인합니다.

4. 새 터미널에서 [프론트 README](frontend/README.md)의 설치 절차를 따릅니다. `frontend/.env.local`에서 `VITE_ASK_API_URL=http://127.0.0.1:8000/api/ask`로 설정하고, `frontend` 폴더에서 실행합니다.

   ```powershell
   npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
   ```

5. [프론트 화면](http://127.0.0.1:5173/)에서 “직업 훈련이나 교육비 지원은 어디서 찾아요?”를 보냅니다. 화면에 답변과 출처가 표시되고 백엔드 로그에 `POST /api/ask … 200 OK`가 찍히는지 확인합니다.

백엔드 없이 예시 모드로 시연할 때는 프론트 README에 따라 VITE_ASK_API_URL을 비우고 npm run dev를 실행합니다.

## 개발 환경 설정

> 데이터 수집·전처리용 의존성은 `requirements.txt`에 정의되어 있습니다. DB·임베딩·RAG 실행 의존성도 같은 파일에서 설치합니다.

### 1. Python 가상환경

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux / Git Bash
source .venv/bin/activate
```

### 2. 환경변수

```bash
cp .env.example .env
```

`.env`에 실제 값을 입력합니다. `.env`는 `.gitignore`로 제외되며 절대 커밋하지 않습니다.

`requirements.txt`의 `python-dotenv`는 `.env` 로딩에 필요합니다. 검색·로더·색인은 `LIFEPOLICY_DATASET_DIR` 미설정 시 승인된 `data/releases/v1.4.0-candidate/`를 사용합니다. 명시한 경로가 우선이며, v1.3.2 호환 검사는 `data/handoff`를 지정합니다. 잘못된 경로·manifest는 오류로 중단합니다.

데이터셋 선택은 DB 접속 주소를 바꾸지 않습니다. `NEO4J_URI`·`QDRANT_URL`은 해당 데이터셋이 적재된 본인의 환경으로 설정합니다.

기존 v1.3.2 기반 단위 테스트는 입력을 명시해 실행합니다(신규 기본값 검사는 별도 프로세스에서 환경변수를 제거해 검증).

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/handoff'
python -m unittest discover -s tests/unit -p 'test_*.py'
```

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 답변 생성 API 키 |
| `YOUTH_POLICY_API_KEY` | 온통청년 Open API 인증키 (승인 필요) |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j 접속 정보 (로컬 사용자명은 `neo4j`) |
| `QDRANT_URL` / `QDRANT_API_KEY` | Qdrant 접속 정보 |

### 3. 로컬 DB 실행 (개발용)

```bash
docker compose up -d
```

- Neo4j Browser: http://localhost:7474
- Qdrant Dashboard: http://localhost:6333/dashboard

`NEO4J_PASSWORD`, `QDRANT_API_KEY`가 `.env`에 없으면 기동되지 않도록 구성했습니다. 데이터는 Docker named volume에 저장되며 `docker compose down -v`로 삭제됩니다.

### 4. 테스트

```bash
pytest
```

후보 데이터 검증에는 추가 설치 없이 Python 기본 테스트 도구를 사용할 수 있습니다.

```bash
python -m unittest discover -s tests -p "test_candidate_release.py" -v
python scripts/load_candidate_neo4j.py
python scripts/verify_candidate_qdrant.py
python scripts/verify_candidate_sources.py
```

위 명령은 파일·계약·참조·해시를 검사하며 실제 DB에는 쓰지 않습니다.
전체 회귀 테스트와 원본 본문 검사는 Git 제외 원본 및 v1 정부24 대용량 파일 2개를 복원한 환경에서 실행합니다. 새 체크아웃의 후보 파일 사전 검증은 `load_candidate_neo4j.py`와 `verify_candidate_qdrant.py`로 별도 수행할 수 있습니다.
실제 DB 검사와 신청 주소 교정 범위는 [후보 검증 보고서](docs/data/pr16_application_url_review.md)를 참고하세요.

최종 데이터의 실제 적재 검증 결과는 아래 [최종 데이터와 사용 안내](#최종-데이터와-사용-안내)를 참고하세요.

### 후보 릴리스 재생성 및 DB 검사

```bash
python scripts/build_incremental_release_candidate.py
# Neo4j: 비어 있는 검증 DB에서 적재·재적재·속성을 대조한 뒤 롤백
python scripts/load_candidate_neo4j.py --apply --http-url http://127.0.0.1:7474
# Qdrant: 새 임시 컬렉션에서 ID·payload를 대조한 뒤 컬렉션 정리
python scripts/verify_candidate_qdrant.py --apply --url http://127.0.0.1:6333
```

- DB 검사 전 `.env`에 `NEO4J_USER`, `NEO4J_PASSWORD`, `QDRANT_API_KEY`를 설정합니다. 접속 값은 로그에 출력하지 않습니다.
- Neo4j에 기존 노드가 있거나 Qdrant 검증 컬렉션이 이미 있으면 쓰기를 거절합니다. 기존 DB를 초기화하지 않습니다.
- Qdrant 검사는 **벡터 없는 ID·payload 저장 검사**입니다. 임베딩 생성·유사도 검색은 별도 검증이 필요합니다. 모델·차원은 임의로 확정하지 않습니다.
- 후보는 기존 정책의 신청 주소를 교정하며 `data/handoff/` 인계 원본은 보존합니다. 변경 전후 값은 후보의 `release_delta_manifest.json`에 기록합니다.

## 최종 데이터와 사용 안내

3차 최종 데이터는 `data/releases/v1.4.0-candidate/`입니다(2026-11-10, PR #45 기준).

| 항목 | 값 |
|---|---|
| 정책 / 근거 문서 / 청크 | 246 / 261 / 599 |
| Neo4j 노드 / 관계 | 1,004 / 2,431 (`REQUIRES_CONDITION` 136) |
| Qdrant | B2 청킹 1,138 Points, `qwen3-embedding:0.6b`, 1,024차원, Cosine (`exp_b2_qwen3emb06b_v140_v1`) |
| 검증 | 실제 적재 후 노드·관계 속성 대조, Neo4j–Qdrant 교차 검증 1,138/1,138, 빈 DB 복원 검증 통과 |

v1.3.2(정책 237, 노드 1,034, 관계 3,615)보다 노드·관계가 적은 것은 공식 원문에 근거가 없는 조건을 교정으로 지웠기 때문입니다.

### 어떤 파일을 보나요

| 목적 | 위치 |
|---|---|
| 검색·적재에 쓰는 최종 데이터 | `data/releases/v1.4.0-candidate/` (`LIFEPOLICY_DATASET_DIR` 미설정 시 기본값) |
| 필드 정의와 해석 규칙 | [docs/data/data_contract.md](docs/data/data_contract.md) |
| 원문 대조 교정의 전후 값·근거 | `data/metadata/data_corrections_v1.4.0.json`, [docs/data/data_corrections_v1.4.0_review.md](docs/data/data_corrections_v1.4.0_review.md) |
| 팀원용 DB 복원 | [docs/development/ISSUE43_RESTORE_GUIDE.md](docs/development/ISSUE43_RESTORE_GUIDE.md) (복원 ZIP은 Git 밖에서 별도 전달) |
| RAG 인계 | [docs/development/ISSUE5_V140_RAG_HANDOFF_20261107.md](docs/development/ISSUE5_V140_RAG_HANDOFF_20261107.md) |
| v1.3.2 기준선 | `data/handoff/` (고정본, 수정하지 않음) |

### 데이터를 쓸 때 주의할 점

- **자격 판정은 checker가 해석합니다.** 신청기간(`application_start`·`application_end`·`application_is_ongoing`)과 원문 확인 결과(`income_source_review`·`household_source_review`)는 checker가 읽고, RAG pipeline은 판정 결과만 사용합니다.
- **모르는 조건은 "확인 필요"로 안내합니다.** 조건 그룹이 `UNKNOWN`인 정책은 자격을 확정하지 않습니다. 자격 설명에 신청자 연령 요건이 있지만 구조화된 나이 조건이 누락된 경우, 연령 요건을 추가로 확인하도록 안내합니다.
- **신청 주소(`official_apply_url`)가 기록된 정책은 22건이며, 실제 접수 화면 연결 여부는 별도 확인이 필요합니다.** 정책 상세는 `source_url`로 안내합니다.
- **정책 정보는 수집 시점 기준입니다.** 신청기간·금액·자격은 바뀔 수 있으므로 답변에 공식 원문 확인 안내를 함께 둡니다.

### 데이터를 고칠 때

1. `data/handoff/`와 릴리스 파일을 직접 고치지 않습니다. `data/metadata/data_corrections_v1.4.0.json`에 공식 원문 근거와 함께 교정 항목을 추가합니다.
2. `python scripts/build_incremental_release_candidate.py`로 릴리스를 다시 만들고, `python -m unittest discover -s tests -p "test_candidate_release.py"`로 검사합니다.
3. 노드·관계 수가 바뀌면 관련 테스트 기대값을 함께 고치고, DB 담당에게 재적재를 요청합니다. 청크 본문이 바뀌면 재임베딩이 필요하므로 먼저 알립니다.

## 데이터 원칙 (요약)

- 공식 출처의 원문만 실데이터로 사용합니다. 가상 정책을 실데이터처럼 만들지 않습니다.
- `raw/`와 `processed/`를 분리하고 `source_url`, `retrieved_at`, `content_hash`를 보존합니다.
- `data/samples/`의 파일은 **형식 설명용 가상 예시**입니다.
- 세부 규칙: [docs/data/data_schema.md](docs/data/data_schema.md), [docs/data/preprocessing_rules.md](docs/data/preprocessing_rules.md)
