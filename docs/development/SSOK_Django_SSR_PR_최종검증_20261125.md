# SSOK Django SSR PR 최종 검증 (#120)

> 작성: 2026-11-25 · 대상 브랜치 `feature/120-django-policy-detail-ssr` · PR 대상 `dev`
> 이번 단계에서는 검증만 했다. **코드 수정 0건**, commit·push·PR·이슈 작업 없음, 사용자 서버(8000/8001/8120/8121/5120) 종료 없음.

## A. 작업 기준

| 항목 | 값 |
|---|---|
| 작업 폴더 | `C:\dev\project\SKN36-3rd-1Team\django-ssr-120` (git worktree, 공통 저장소 `SKN36-3rd-1Team/.git`) |
| 현재 브랜치 | `feature/120-django-policy-detail-ssr` |
| 현재 HEAD | `dc4f07c9fac5a869accdae739285793dc6f1df9b` — feat(#120): Django 정책 상세 SSR 및 React 카드 연동 (2026-11-25 01:40) |
| 원격 브랜치 | `origin/feature/120-django-policy-detail-ssr` = `dc4f07c` (**이미 push됨**, ahead 0 / behind 0) |
| 최신 `origin/dev` | `b5c2fd3f89413b89c265cb5d525d22c6149f13bc` — Merge PR #118 (2026-11-25 `git fetch` 직후 확인, 변동 없음) |
| 브랜치 vs dev | 브랜치만 1커밋, dev만 0커밋 → fast-forward 관계 |
| 데이터 | `1.4.0-candidate`, 정책 249건, manifest `b3130722…04f3da`, `source_kind=release_files`, 일정 기준일 2026-11-25 |
| 실행 환경 | Windows 11, Python 3.12.10(작업 폴더 `.venv`), Django 5.2.19, FastAPI 0.165.0, Node 24.20.0, Chrome + Playwright 1.65.2 |

### 미커밋 상태 (작업 시작 시 기록, 그대로 보존)

- Staged: 없음 / Unstaged(추적 파일 수정): 없음
- Untracked: `docs/development/SSOK_Django_SSR_코드검증_최적화_결과_20261125.md`, `docs/development/ssr120-verification/`(67개, 이전 검증 증거)
- 이번에 추가한 untracked: 이 보고서, `docs/development/ssr120-final-verification/`(캡처 10장 + `final-results.json`)

> **중요**: 요청서에는 "Django 변경이 아직 미커밋"이라고 되어 있지만, 실제로는 Django·React 변경 31개 파일이 `dc4f07c`로 **이미 커밋·push되어 있다**. 미커밋인 것은 문서·검증 증거뿐이다.

## B. 변경 파일 전체 (`origin/dev...HEAD`, 31개 · +1,561 / −1)

### 신규 (27)

| 파일 | 기존 역할 | 이번 변경 내용 | 변경 이유 | 영향 범위 |
|---|---|---|---|---|
| `web_ssr/manage.py` | 없음 | Django 명령 진입점 | Django 실행·테스트 | web_ssr 한정 |
| `web_ssr/requirements.txt` | 없음 | `Django==5.2.19`, `requests>=2.31.0` | 의존성 분리·고정 | 별도 설치(루트 requirements 불변) |
| `web_ssr/.env.example` | 없음 | DJANGO_*·SSR_* 변수명(값 없음) | 설정 안내 | 없음 |
| `web_ssr/README.md` | 없음 | 구조·실행·테스트·SSR 확인 방법 | 문서 | 없음 |
| `web_ssr/conftest.py` | 없음 | 루트 pytest에서 Django 테스트 설정 | 루트 `pytest` 실행 시 오류 방지 | web_ssr 테스트 |
| `web_ssr/config/__init__.py` | 없음 | 패키지 표시 | — | — |
| `web_ssr/config/settings.py` | 없음 | DB 없는 최소 설정, `web_ssr/.env`만 읽음, DEBUG 기본 false, SECRET_KEY 필수(운영), ALLOWED_HOSTS·HTTPS 옵션, FastAPI·React 주소, 타임아웃, 캐시 60초 | 안전한 기본값 | web_ssr 한정 |
| `web_ssr/config/settings_test.py` | 없음 | 테스트 전용(가짜 FastAPI, 캐시 0) | 결정적 테스트 | 테스트 |
| `web_ssr/config/urls.py` | 없음 | `policies/` include, handler404/500 | URL Routing | web_ssr |
| `web_ssr/config/wsgi.py` | 없음 | WSGI 진입점 | 운영 서버용 | 없음 |
| `web_ssr/policies/__init__.py`, `apps.py` | 없음 | 앱 등록 | Django 앱 | — |
| `web_ssr/policies/urls.py` | 없음 | `<policy_id:policy_id>/` → `policy_detail` | URL Routing | web_ssr |
| `web_ssr/policies/converters.py` | 없음 | `^POL_[A-Z0-9_]+$`, ≤128자, fullmatch | 잘못된 ID를 View 전 404 | web_ssr |
| `web_ssr/policies/views.py` | 없음 | GET 전용 View, 200/404/503, 접수 상태 표시 규칙(React와 동일), React 링크 | View | web_ssr |
| `web_ssr/policies/services.py` | 없음 | FastAPI 목록 전체 조회·검증, `by_id` 정확 일치, 60초 캐시(KST 날짜 키), 동시 갱신 1회, redirect 차단, 안전 URL | 데이터 연동 | web_ssr |
| `web_ssr/policies/templates/policies/base.html`, `detail.html`, `error.html` | 없음 | 공통 뼈대, 상세 본문, 404/503/500 화면 | Template·SSR | web_ssr |
| `web_ssr/policies/static/policies/detail.css`, `detail.js` | 없음 | React 토큰 기반 스타일, 서류 더보기/접기 | 디자인·작은 상호작용 | web_ssr |
| `web_ssr/policies/tests/__init__.py`, `fixtures.py`, `test_services.py`, `test_views.py`, `test_regressions.py` | 없음 | 39개 테스트(가상 sample_only 데이터, HTTP mock) | 회귀 방지 | 테스트 |
| `frontend/src/components/PolicyDetailLink.test.tsx` | 없음 | 링크 위치·주소·새 탭·숨김 조건 테스트 | React 회귀 방지 | 테스트 |

### 수정 (4)

| 파일 | 기존 역할 | 이번 변경 내용 | 변경 이유 | 영향 범위 |
|---|---|---|---|---|
| `frontend/src/components/TurnView.tsx` | 공통 `PolicyCard` | `.pfoot`의 관계 지도 앞에 `정책 상세보기 ↗` 링크(+6/−1) | Django 상세 진입점 | PolicyCard 사용 3곳(둘러보기·상담 결과·최근 일정). 환경변수 없으면 변화 없음 |
| `frontend/src/lib/route.ts` | 해시 라우트 헬퍼 | `policyDetailHref()` 추가(+21) | 주소 생성·검증 | 신규 함수만 |
| `frontend/.env.example` | 프론트 변수명 | `VITE_POLICY_DETAIL_BASE_URL=` | 링크 스위치 | 없음 |
| `README.md` | 프로젝트 안내 | Django SSR 실행 한 단락 + 폴더 표 한 줄(+5) | AGENTS §5(의존성 추가 시 README) | 문서 |

변경하지 않은 영역(확인): `backend/`(FastAPI·RAG·Checker·LLM), `database/`, `ai/`, `data/`, `docker-compose*.yml`, 루트 `requirements.txt`·`.env.example`.

## C. 발견한 문제와 수정 결과

| 우선순위 | 문제 | 원인 | 수정 방법 | 검증 결과 |
|---|---|---|---|---|
| — | 이번 검증에서 **수정이 필요한 결함 없음** | — | 코드 변경 없음 | 아래 테스트·브라우저 결과 |
| 참고 | 요청서 전제(“미커밋”)와 실제 상태 불일치 | 기능이 이미 `dc4f07c`로 커밋·push됨 | 보고만 함 | §A |
| 참고(P3) | `HEAD /policies/…/`가 405 | `@require_GET`(요청서의 “GET만 허용”을 그대로 따름) | 변경 안 함. 운영 헬스체크가 HEAD를 쓰면 `require_safe`로 바꾸는 것을 팀과 결정 | 405 확인 |
| 참고(P3) | 실제 서버에서 POST는 405가 아니라 403 | CSRF 미들웨어가 View보다 먼저 거부 | 변경 안 함(차단이라는 결과는 같음) | 403 확인, 테스트 클라이언트는 405 |
| 기록 | 검증 스크립트 오류 1건(관심 저장 버튼 문구가 바뀌어 locator 실패) | 스크립트 문제(앱 아님) | `aria-pressed` locator로 변경 후 재실행 | 통과 |

이전 단계에서 적용된 최적화·방어 로직 유지 확인: 동시 캐시 갱신 1회(Lock + Future), `by_id` 인덱스, 정확한 ID 일치, 응답 타입 검증, 페이지별 manifest·버전·기준일·총 건수 일치, `allow_redirects=False`, 기준일 지난 일정 “일정 재확인 필요”, 잘못된 URL 404.

## D. 최신 dev 병합 호환성

- **텍스트 충돌**: 없음. `git merge-tree --write-tree origin/dev HEAD` 결과 트리 `7c1834f…`가 HEAD 트리와 **완전히 동일**(exit 0). `origin/dev`가 HEAD의 조상이라 fast-forward.
- **의미적 충돌**: 없음. dev에 Django 커밋 이후 새 커밋이 0개이므로 FastAPI 계약·`PolicyCard`·라우팅·환경변수가 바뀐 곳이 없다.
  - 정책 API 필드(실측): `agency, application_schedule, categories, official_apply_url, policy_id, policy_name, reception_status, regions, required_documents, requirement_excerpts, source_url` — services.py가 쓰는 필드와 일치
  - 정책 ID 249건 모두 `^POL_[A-Z0-9_]+$`·128자 이하, 중복 0
  - React 해시 라우트(`#/`, `#/life`, `#/map`)와 Django `/policies/` 경로 겹침 없음
  - `VITE_POLICY_DETAIL_BASE_URL`은 dev에 없는 신규 변수 → 충돌 없음
- **최신 데이터 호환성**: Django는 DB(Neo4j·Qdrant)를 **직접 읽지 않는다**. FastAPI `GET /api/policies`가 저장소의 릴리스 파일(`data/releases/v1.4.0-candidate`)을 읽어 주는 값을 쓴다. 따라서 DB 적재 상태(민혁님 작업)와 무관하게 릴리스 파일 기준 249건을 표시한다.
- **실제 검증 방식**: merge-tree로 병합 결과 트리 확인 + 병합 결과와 동일한 HEAD 트리에서 전체 테스트·실제 서버·브라우저 검증. 기준선은 별도 임시 worktree(`origin/dev`, 검증 후 제거)에서 전체 pytest를 새로 실행.
- **추가 조치**: 없음. PR 생성 직전에 `git fetch` 후 dev가 다시 움직였다면 이 절을 다시 확인한다.
- **검증하지 못한 부분**: 실제 `/api/ask`(유료 LLM·DB 필요) 연동, 운영 배포 환경.

## E. 전체 테스트 결과 (이번 실행에서 새로 측정)

| 항목 | origin/dev 기준선 | 브랜치(=병합 결과) | 판정 |
|---|---|---|---|
| Django `manage.py test policies` | (해당 없음) | **39 통과** | ✅ |
| Django via pytest `web_ssr` | — | **39 통과** (27 subtests) | ✅ |
| FastAPI 정책 계약 `tests/integration/test_policy_catalog_api.py` | — | **16 통과** | ✅ |
| React `vitest` | — | **174 통과** (19 files) | ✅ |
| TypeScript `tsc --noEmit` | — | 오류 0 | ✅ |
| React 빌드 `npm run build` | — | 성공(청크 크기 경고만, 기존과 동일) | ✅ |
| 기존 전체 `pytest tests` | 93 failed / 1775 passed / 49 skipped / 23 errors | **93 failed / 1775 passed / 49 skipped / 23 errors** | ✅ 신규 실패 **0** (고유 실패 ID 목록 완전 일치) |
| `git diff --check origin/dev HEAD` | — | 통과 | ✅ |

- 기존 실패는 모두 데이터·DB·임베딩 환경 테스트(`test_full_index` 15, `test_vector_store` 13, `test_load_neo4j_r2_isolated` 13, `test_hybrid_profiles` 10, `test_retrieval_run` 8, 기타)로 #116 범위이며 Django 변경과 무관.
- 이전 Codex 검증의 98 failed / 30 errors와 수치가 다른 것은 실행 환경(가상환경·설치 패키지·로컬 원본 데이터 유무) 차이다. 같은 환경에서 기준선과 브랜치를 비교한 이번 결과가 판단 근거다.

## F. 실제 브라우저 테스트 결과 (Chrome + Playwright, 격리 포트)

테스트 서버: FastAPI `18100`, Django `18101`, 장애용 Django `18102`(FastAPI를 닫힌 포트로 지정), React `15100`. 완료 후 직접 띄운 서버만 종료.

| 구분 | 결과 |
|---|---|
| React CSR | 첫 HTML은 `<div id="root"></div>`만, 정책명 없음 / 브라우저가 `GET :18100/api/policies?limit=500&offset=0` 호출 / “정책 249개” 표시 |
| React 기존 기능 | 지역+“접수 중” 필터 2개, 카드 펼침, 신청 기간·신청 기관 행, 관심 저장 ☆→★→☆(`aria-pressed`), 관계 지도 `#/map?policy=POL_YC_007` 렌더, 생애주기 렌더, 상담(예시 모드) 응답 표시 |
| 상세 링크 | 버튼 순서 `정책 상세보기 ↗ → 관계 지도 → 원문 보기 ↗ → 관심 정책 저장`, `href=…/policies/POL_YC_007/`, `target=_blank`, `rel=noopener noreferrer`, 새 탭 `window.opener === null`, 클릭 후에도 원래 카드 펼침 유지 |
| Django SSR `POL_YC_007` | 200, Doc 응답에 정책명·기관·신청 기간 포함, Fetch/XHR 0건. 이 정책은 데이터에 자격 발췌·서류가 없어 “확인된 … 없어요” 안내 표시(지어내지 않음) |
| Django SSR 긴 서류 `POL_STG_MOLIT_002` | 200, Doc 응답에 정책명·기관·기간·자격 원문·서류 포함, Fetch/XHR 0건 |
| JavaScript OFF | 정책명 표시, 서류 전부 펼쳐짐, 접기 버튼 숨김 |
| 서류 더보기/접기 | 초기 `더보기/false` → 클릭 `접기/true` → Enter `더보기/false` → Space `접기/true` |
| 반응형 | Django·React 모두 1440/768/390/320px 가로 넘침 없음, Django 버튼 겹침 없음 |
| HTTP | 정상 200, 없는 정책 404, 소문자·형식 오류 404, `/` 없음 301, POST 403, HEAD 405, FastAPI 다운 503(본문에 내부 포트·Traceback·변수명 없음) |

캡처: `docs/development/ssr120-final-verification/` — `react_card_1440.png`, `react_card_390.png`, `django_POL_YC_007_1440.png`, `django_POL_STG_MOLIT_002_1440.png`, `django_768.png`, `django_390.png`, `django_320.png`, `django_js_off.png`, `django_404.png`, `django_503.png`, 원자료 `final-results.json`.

## G. 성능 및 API 호출 (실측)

| 측정 | 결과 |
|---|---|
| 같은 상세 페이지 5회 연속 요청 | FastAPI 목록 호출 **1회**(나머지 캐시 적중) |
| 캐시 만료(62초 대기) 후 동시 10요청 | 모두 200, FastAPI 호출 **1회**(동시 갱신 합치기 동작) |
| 브라우저 측 추가 요청 | Django 페이지는 Fetch/XHR 0건 |

응답 시간 개선 수치는 이번에 측정하지 않았으므로 성능 향상을 주장하지 않는다. 캐시는 프로세스 메모리(LocMem)라 프로세스가 여러 개면 프로세스마다 1회씩 호출된다.

## H. 남은 위험 및 팀 협의 사항

1. **PR 기준 브랜치**: `CODING_STYLE.md` §17은 “main 확정·dev 삭제”라고 적혀 있지만 실제 머지는 `dev`로 진행 중(최근 PR #118도 dev). 이번 PR은 `dev` 대상이 맞아 보이며, 문서 갱신은 팀에 요청 필요.
2. **배포 미정**: 운영에는 `DJANGO_SECRET_KEY`·`DJANGO_ALLOWED_HOSTS`·`DJANGO_SECURE_HTTPS=true`, WSGI 서버(gunicorn/waitress 등), `collectstatic` 후 `/ssr-static/` 제공(현재 WhiteNoise 없음), 같은 도메인 프록시 시 `/policies/`·`/ssr-static/` 라우팅이 필요하다. 배포 의존성은 아직 추가하지 않았다.
3. **엄격한 데이터 검증의 트레이드오프**: 목록 중 한 건이라도 정책 ID 형식이 깨지거나 manifest가 페이지마다 다르면 모든 상세 페이지가 503이 된다(틀린 정보를 보여 주지 않기 위한 의도된 동작). 데이터 담당자와 공유 필요.
4. **CSS 토큰 복제**: `detail.css`는 React 라이트 테마 토큰 사본. 디자인 변경 시 함께 수정. 다크 테마 미지원.
5. **링크 노출 범위**: 공통 `PolicyCard` 3곳에만 노출. 생애주기·관계 지도·정책 비교 화면에는 없음.
6. **HEAD 405**: 헬스체크가 HEAD를 쓰면 조정 필요(§C).
7. **증거 폴더 정리**: 이전 검증 폴더 `ssr120-verification/`(67개, 3.0MB, 스크립트·XML 포함)는 PR에 넣지 않는 것을 권장.

## I. 최종 판정

**READY WITH NOTES**

- 근거(통과): 최신 dev와 병합 결과가 HEAD와 동일(충돌·의미적 충돌 없음), Django 39·FastAPI 계약 16·React 174 통과, 타입 검사·빌드 성공, 기존 전체 테스트 신규 실패 0, 실제 브라우저 SSR/CSR·JS OFF·반응형·404/503·캐시 동작 확인, 비밀정보 미포함, `git diff --check` 통과.
- 주의 사항(READY FOR PR이 아닌 이유): 기존 전체 테스트 실패 93/23(#116)이 남아 있어 “전체 green”이 아님, PR 기준 브랜치 문서 불일치, 운영 배포 설정 미정. 기능 PR 자체를 막는 문제는 아니다.
