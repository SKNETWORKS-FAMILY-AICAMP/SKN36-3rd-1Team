# LifePolicy AI — 생애주기 맞춤형 정부 지원 정책 추천 챗봇

> SKN 36기 부트캠프 3차 단위프로젝트 (교과목: LLM)
> 상태: **초기 구조만 생성됨 — 기능 구현 전** (2026-11-04)

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
- [ ] 공식 정책 원문/API 데이터 수집·전처리
- [ ] Neo4j 정책 관계 적재·조회
- [ ] Qdrant 원문 청크 임베딩·검색
- [ ] Graph + Vector 결합 조회
- [ ] LLM 근거 기반 답변 + 출처 표시
- [ ] UI (질문/답변/출처/로딩/오류)
- [ ] 평가 질문 10개 및 결과 기록

## 기술 스택

| 영역 | 기술 | 상태 |
|---|---|---|
| Backend | Python, FastAPI | 제안 |
| LLM·RAG | LangChain, LangGraph | 제안 (LLM 모델 미확정, PRD D-06) |
| Graph DB | Neo4j | 제안 (PRD D-02) |
| Vector DB | Qdrant | 제안 (PRD D-02) |
| Embedding | 한국어 지원 모델 | 미확정 (PRD D-04) |
| Frontend | React 기반 | 프레임워크 미확정 |
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
│   └── eligibility/    #   연령·지역 등 명시 규칙 판정 엔진
├── frontend/           # React 기반 웹 UI
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
├── refer/              # 팀 참고 자료 원본 (PRD·스타일 원본, 강의 실습 zip)
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
| 검색 / 평가 | 나지인, 박소희 | 검색 관련 코드, `ai/evaluation/`, `tests/evaluation/` | 05 테스트 계획 및 결과 보고서 |
| LLM / RAG | 나지인, 박소희 | `backend/rag/`, `ai/prompts/`, `ai/workflows/` | 04 RAG-LLM 및 DB 연동 코드 |
| UI / 운영 / 발표 | 강민지 | `frontend/` | 03 개발 소프트웨어 실행·시연 |
| 공통 변경 | 전원 (PR 리뷰 필수) | `README.md`, `.env.example`, `AGENTS.md`, `docs/api/` (공통 API 계약) | — |

검색·평가와 LLM·RAG의 세부 코드 소유권은 아직 합의 전입니다 (PRD D-08).

## 개발 환경 설정

> 의존성 목록(`requirements.txt` 또는 `pyproject.toml`)은 아직 없습니다. 라이브러리 버전 확정 후 추가합니다.

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

아직 테스트 코드는 없습니다.

## 데이터 원칙 (요약)

- 공식 출처의 원문만 실데이터로 사용합니다. 가상 정책을 실데이터처럼 만들지 않습니다.
- `raw/`와 `processed/`를 분리하고 `source_url`, `retrieved_at`, `content_hash`를 보존합니다.
- `data/samples/`의 파일은 **형식 설명용 가상 예시**입니다.
- 세부 규칙: [docs/data/data_schema.md](docs/data/data_schema.md), [docs/data/preprocessing_rules.md](docs/data/preprocessing_rules.md)
