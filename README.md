# LifePolicy AI — 생애주기 맞춤형 정부 지원 정책 추천 챗봇

> SKN 36기 부트캠프 3차 단위프로젝트 (교과목: LLM)
> 상태: **데이터 수집·전처리 코드 및 DB 인계 파일 준비됨 — DB 적재·검색·조건 확인·RAG 통합은 미완료** (2026-11-05)

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
- [ ] Neo4j 정책 관계 적재·조회
- [ ] Qdrant 원문 청크 임베딩·검색
- [ ] Graph + Vector 결합 조회
- [ ] 사용자 입력 검증 + 연령·지역·신청기간 조건 확인
- [ ] LLM 근거 기반 답변 + 출처 표시
- [ ] 평가 질문 10개 및 결과 기록

> 3차 발표는 **React 화면 한 장**으로 질문과 선택 조건을 입력하고 답변·공식 출처를 보여줍니다. 터미널·노트북은 시연 백업이며 정식 웹 UI 개발은 4차 범위입니다.

## 기술 스택

| 영역 | 기술 | 상태 |
|---|---|---|
| Backend | Python, FastAPI | 제안 |
| LLM·RAG | LangChain, LangGraph | 제안 (LLM 모델 미확정, PRD D-06) |
| Graph DB | Neo4j | 제안 (PRD D-02) |
| Vector DB | Qdrant | 제안 (PRD D-02) |
| Embedding | 한국어 지원 모델 | 미확정 (PRD D-04) |
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
├── ai/                 # embeddings/, prompts/, workflows/ (LangGraph), evaluation/
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
| React 시연 UI / 발표 | 강민지 | `frontend/` | 03 개발 소프트웨어 실행·시연 정리 (화면 연동·검증, 전원 실행법 취합), 발표 |
| 공통 변경 | 전원 (PR 리뷰 필수) | `README.md`, `.env.example`, `AGENTS.md`, `docs/api/` (공통 API 계약) | — |

검색·평가와 LLM·RAG의 세부 코드 소유권은 아직 합의 전입니다 (PRD D-08).

## 개발 환경 설정

> 데이터 수집·전처리용 의존성은 `requirements.txt`에 정의되어 있습니다. DB·임베딩·RAG 의존성은 각 담당자가 기술 확정 후 추가합니다.

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
| `OPENAI_API_KEY` | LLM API 키 (모델 확정 전) |
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

2026-11-06 임시 Neo4j에서 노드 1,074개·관계 3,703개의 적재·재적재·속성 대조와 DDL 13개 적용 검사를 통과했습니다.
임시 Qdrant에서도 599개 청크의 ID·payload 저장 검사를 통과했습니다. 검증 후 데이터와 컨테이너를 정리했으며, 임베딩·의미 검색·서비스 통합 완료를 뜻하지 않습니다.

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

## 데이터 원칙 (요약)

- 공식 출처의 원문만 실데이터로 사용합니다. 가상 정책을 실데이터처럼 만들지 않습니다.
- `raw/`와 `processed/`를 분리하고 `source_url`, `retrieved_at`, `content_hash`를 보존합니다.
- `data/samples/`의 파일은 **형식 설명용 가상 예시**입니다.
- 세부 규칙: [docs/data/data_schema.md](docs/data/data_schema.md), [docs/data/preprocessing_rules.md](docs/data/preprocessing_rules.md)
