# SSOK Django SSR PR #121 — 최신 dev 통합 검증 (2026-11-25)

> 검증만 수행. **코드 수정 0건**, commit·push·PR 수정·merge 없음. 사용자 서버(8000/8001/8120/8121) 종료 없음.

## A. Git 기준

| 항목 | 값 |
|---|---|
| 작업 폴더 / 브랜치 | `C:\dev\project\SKN36-3rd-1Team\django-ssr-120` / `feature/120-django-policy-detail-ssr` |
| PR HEAD | `75afe4500997a110f9b5d14d2c332412ab66af5f` (= `origin/feature/120-…`, push 완료) |
| 최신 `origin/dev` | `59f1dab4bdbb37f33faa71f5654cb11581c90b7b` — Merge PR #111 (`git fetch` 직후 확인) |
| merge base | `b5c2fd3` |
| 커밋 차이 | PR만 3개(`dc4f07c`, `5972db7`, `75afe45`) / dev만 25개(#105·#110 DB 복원 R3) |
| 변경 파일 | PR 33개, dev 38개, **겹치는 파일 0개** |
| 작업 폴더 상태 | staged·unstaged 없음. untracked: 이전 검증 문서·폴더 3개(보존) + 이번 보고서·`ssr120-merge-verification/` |

**실제 병합 재현 방식**: 별도 임시 worktree(`../pr121-merge-verify`, `origin/dev` detached)에서 `git merge --no-commit --no-ff 75afe45` 실행 → 충돌 0, 결과 인덱스 트리가 `git merge-tree --write-tree origin/dev HEAD` 결과와 동일. 이 병합 상태에서 전체 테스트·서버·브라우저 검증 후 `git merge --abort`, worktree 제거(커밋 없음).

**의미적 충돌**: 없음. dev 변경 38개는 `database/full_index.py`, `tests/unit/*`, `tests/integration/test_final_v140_smoke.py`, `scripts/`, `docs/`, 복원 데이터뿐이며 FastAPI API(`backend/api`), `frontend/`, `requirements.txt`, `.env.example`, 릴리스 데이터, pytest 설정은 건드리지 않았다. 병합 후 정책 API 필드·정책 ID·React `PolicyCard` 구조가 그대로임을 실행으로 확인(아래).

## B. 변경 파일

이번 통합 검증에서 **수정한 파일 없음**. PR #121의 기존 변경(33개)은 그대로이며, 직전 리뷰 반영 커밋 `75afe45`의 세 파일(`web_ssr/conftest.py`, `web_ssr/config/settings.py`, `web_ssr/policies/tests/test_local_env.py`)과 `README.md`도 최신 dev 위에서 정상 동작했다.

새로 생긴 파일(미커밋): 이 보고서, `docs/development/ssr120-merge-verification/`(캡처 10장 + `final-results.json`).

## C. 테스트 결과 (최신 dev 통합 상태에서 새로 측정)

| 항목 | 최신 dev 단독 | dev + PR #121 병합 | 판정 |
|---|---|---|---|
| Django `manage.py test policies` | — | **41 통과** | ✅ |
| Django `pytest web_ssr` (수집 / 실행) | — | **41 수집 / 41 통과** | ✅ |
| FastAPI 정책 계약 `test_policy_catalog_api.py` | — | **16 통과** | ✅ |
| React vitest | — | **174 통과** (19 files) | ✅ |
| TypeScript `tsc --noEmit` | — | 오류 0 | ✅ |
| React `npm run build` | — | 성공 | ✅ |
| 루트 `pytest --collect-only` (Django 설치 venv) | 2,237 | **2,278** (= 2,237 + Django 41) | ✅ |
| 루트 `pytest --collect-only` (Django 미설치 venv) | 2,237 | **2,237**, 수집 오류 0, 제외 사유 1줄 출력 | ✅ |
| Django 미설치 `pytest web_ssr` | — | 제외 사유 출력, no tests ran | ✅ (의도된 동작) |
| 전체 pytest | `pytest tests`: 58 failed / **2,110 passed** / 63 skipped / 20 errors | 루트 `pytest`: 58 failed / **2,151 passed** / 63 skipped / 20 errors | ✅ 신규 실패 **0** (고유 실패 ID 목록 동일, web_ssr 실패 0) |
| `git diff --check` (병합 인덱스) | — | 통과 | ✅ |

- Django 미설치 환경: scratchpad 임시 venv(Python 3.12.10, 루트 `requirements.txt`만 설치, Django 없음, pytest 9.2.0). 기존 `.venv`는 변경하지 않음.
- 기존 실패 58/20은 dev 단독에서도 동일하게 발생하는 데이터·DB·임베딩 환경 테스트(#116 범위). 이전 측정(93/23)보다 줄어든 것은 dev의 #105/#110 머지 효과이며 이번 PR과 무관.

### `.env` · README 실행 순서 (병합된 `web_ssr/`의 임시 복사본에서 재현)

| 확인 | 결과 |
|---|---|
| README 2단계: `.env`가 없을 때만 `.env.example` 복사 | 원본과 동일하게 복사됨 |
| 그대로 복사(DJANGO_DEBUG 빈 값) | `ImproperlyConfigured: DJANGO_DEBUG=false이면 DJANGO_SECRET_KEY를 설정해야 합니다.` — 안전하게 거부(README 3단계 안내와 일치) |
| `DJANGO_DEBUG=true`만 변경 | `SSR_FASTAPI_BASE_URL='http://127.0.0.1:8000'`, `SSR_REACT_BASE_URL='http://127.0.0.1:5173/'`, `ALLOWED_HOSTS=['127.0.0.1','localhost']` — 빈 값이 기본값을 덮어쓰지 않음 |
| OS 환경 변수 우선 | `SSR_REACT_BASE_URL=http://os.example/`가 `.env`보다 우선 |
| 잘못된 값(`SSR_FASTAPI_TIMEOUT_SECONDS=abc`) | `ImproperlyConfigured: …는 숫자여야 합니다.` — 기동 전 명확히 거부 |
| 비밀값 노출 | `.env`의 테스트용 가짜 `DJANGO_SECRET_KEY`가 `manage.py check` 출력·서버 요청 로그(31건)에 0회 등장 |
| README 흐름으로 띄운 Django | 이 서버(18301)로 아래 브라우저 검증 전체 수행, 정상 |

## D. 실제 브라우저 검증 (Chrome + Playwright, 병합 코드)

테스트 서버: FastAPI 18300, Django 18301(README 흐름 `.env`), 장애용 Django 18302(닫힌 포트를 FastAPI로 지정), React 15300. 완료 후 직접 띄운 서버만 종료.

| 구분 | 결과 |
|---|---|
| 데이터 | 249건, `1.4.0-candidate`, manifest `b3130722…`, 모든 ID `^POL_[A-Z0-9_]+$`, 중복 0, 필드 11개가 services.py 기대와 일치 |
| React CSR | 첫 HTML은 빈 `#root`, 정책명 없음 / 브라우저가 `GET :18300/api/policies?limit=500&offset=0` 호출 / 249건 표시 |
| React 기존 기능 | 지역+“접수 중” 필터, 카드 펼침, 신청 기간·기관, 관심 저장 ☆→★→☆(`aria-pressed`), 관계 지도 `#/map?policy=POL_YC_007`, 생애주기, 상담(예시 모드) 정상 |
| 상세 링크 | 순서 `정책 상세보기 ↗ → 관계 지도 → 원문 보기 ↗ → 관심 정책 저장`, `href=…/policies/POL_YC_007/`, `_blank`, `noopener noreferrer`, 새 탭 `opener=null`, 원래 탭·카드 유지. (환경변수 미설정·예시 정책·잘못된 URL 숨김은 React 단위 테스트로 확인) |
| Django SSR `POL_YC_007` | 200, Doc 응답에 정책명·기관·신청 기간, Fetch/XHR 0건. 데이터에 자격 발췌·서류가 없어 “확인된 … 없어요” 안내 |
| Django SSR 긴 서류 `POL_STG_MOLIT_002` | 200, 정책명·기관·기간·자격 원문·서류가 HTML에 포함, Fetch/XHR 0건 |
| JavaScript OFF | 내용 표시, 서류 전부 펼침, 접기 버튼 숨김 |
| 서류 더보기/접기 | `더보기/false` → 클릭 `접기/true` → Enter `더보기/false` → Space `접기/true` |
| 반응형 | Django·React 1440/768/390/320px 가로 넘침 없음, Django 버튼 겹침 없음 |
| HTTP | 200 / 없는 정책 404 / 소문자·형식 오류 404 / `/` 없음 301 / POST 403 / HEAD 405 / FastAPI 다운 503(본문·로그에 내부 포트·Traceback 없음) |
| 캐시 | 캐시 만료 후 동시 10요청 → 모두 200, FastAPI 호출 **1회**. (연속 5회 요청 측정은 직전 준비 요청으로 캐시가 이미 채워져 0회) |

캡처: `docs/development/ssr120-merge-verification/` (`react_card_1440.png`, `react_card_390.png`, `django_POL_YC_007_1440.png`, `django_POL_STG_MOLIT_002_1440.png`, `django_768.png`, `django_390.png`, `django_320.png`, `django_js_off.png`, `django_404.png`, `django_503.png`, `final-results.json`).

## E. 남은 문제

1. **기존 실패 58 failed / 20 errors**: dev 단독과 동일(#116, 데이터·DB·임베딩 환경). 이번 PR과 무관.
2. **배포 설정 미정**: 운영 `DJANGO_SECRET_KEY`·`DJANGO_ALLOWED_HOSTS`·`DJANGO_SECURE_HTTPS`, WSGI 서버, `collectstatic`과 `/ssr-static/` 제공, 같은 도메인 프록시.
3. **의도된 엄격 검증**: 목록 데이터 형식이 깨지면 상세 페이지 전체 503.
4. **팀 협의**: `CODING_STYLE.md`의 기준 브랜치(main) 표기와 실제 운영(dev) 불일치, HEAD 405(헬스체크 사용 시), 다크 테마 미지원.
5. **검증하지 못한 부분**: 실제 `/api/ask`(유료 LLM·DB), 운영 배포 환경. 병합 직전 dev가 다시 움직이면 재확인 필요.
6. **미커밋 문서**: 이전 검증 산출물(`SSOK_Django_SSR_코드검증_최적화_결과_20261125.md`, `ssr120-verification/`, `ssr120-final-verification/`)은 PR에 넣지 않는 것을 권장.

## F. 최종 판정

**READY TO MERGE**

최신 `origin/dev`(`59f1dab`)와 실제 병합한 코드에서 충돌·의미적 충돌이 없고, Django 41·FastAPI 계약 16·React 174 통과, 타입 검사·빌드 성공, Django 설치/미설치 양쪽 pytest 수집 정상, `.env`·README 흐름 정상, 실제 브라우저 SSR/CSR·JS OFF·반응형·404/503·캐시 확인, 기존 대비 신규 실패 0건. 남은 기존 실패(#116)와 배포 설정은 이 PR 병합을 막는 문제가 아니다.
