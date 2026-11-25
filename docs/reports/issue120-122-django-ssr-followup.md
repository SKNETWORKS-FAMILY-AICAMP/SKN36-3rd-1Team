# Django 정책 상세 페이지 잔여 통합 검증 · #120·#122

2026-11-25 KST · 검증 담당 범위는 React 진입 경로와 Django 상세 페이지다. **#122 전체 E2E 승인은 보류한다.** 기존 R3D 실상담 증빙은 유효한 기준 기록으로 인용했다. 이번 환경의 실상담 요청은 검색 단계에서 503을 반환했다.

## 1차 검증 상태와 후속 작업

**Django 상세 1차 검증 완료 · 실제 상담 연동 재검증 대기.** #120은 종료 상태이며, #122의 전체 E2E 승인은 보류한다.

| 상태 | 후속 담당 | 범위 |
| --- | --- | --- |
| 1차 검증 완료 | 강민지 | 홈·관심 정책의 상세 새 탭 연결과 정책 ID·이름·공식 원문 일치, 320px·390px 레이아웃, JavaScript 비활성화 상태의 SSR, 404·503 안내와 정상 화면 복귀를 확인했다. 다음 회차에서 기존 증빙을 재사용한다. |
| React 수정 및 회귀 검증 | 강민지 | 상세 탭을 닫고 복귀할 때 펼친 카드가 닫히는 문제를 수정한 뒤 홈과 관심 정책 두 경로를 다시 확인한다. 관심 정책 저장과 탭 선택도 함께 확인한다. |
| 실제 상담 재검증 대기 | 나지인과 연동 확인 후 강민지 | `POST /api/ask`의 503 `SEARCH_BACKEND_UNAVAILABLE`을 재현했다. 나지인과 FastAPI의 R3D 검색 연결을 확인하고, 필요하면 DB 담당과 협의한다. 상담이 성공하면 상세 새 탭을 열기 전후의 질문·답변·선택 정책을 비교한다. |
| 팀 논의 대기 | 팀 | Django 상세 다크 모드 지원과 정책 비교 화면의 Django 상세 링크 추가 여부를 결정한다. |

## 기준과 실행 환경

- 시작 브랜치는 `feature/82-llm-checker-integrated-eval` (`d01c632`). PR #121 병합 커밋이 현재 HEAD에 포함되지 않았음을 `git merge-base --is-ancestor 6b4fdc9 HEAD`의 종료 코드 1로 확인했다. 추적 파일 수정이 없는 기존 격리 폴더 `.codex-dev-integration-6b4fdc9`의 detached HEAD를 사용했다. 로컬 `origin/dev`와 동일한 전체 SHA는 `6b4fdc96b154b47194d0460c19be23f70d779546`이다.
- GitHub 이슈 본문 기준 #120은 종료, #122는 열려 있다. #120은 공통 `PolicyCard`의 새 탭 링크와 실제 상담·목록·일정 카드 연동을 요구한다. #122는 최신 데이터·전체 서비스·오류 및 SSR 검증을 요구한다. PR #121의 변경 파일과 병합 커밋을 확인했다. [#120](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/120) · [#122](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/122) · [PR #121](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/121)
- Windows의 Chrome 158 헤드리스와 DevTools Protocol, 기존 Vite 5.4.11, Django 5.2.19, FastAPI를 사용했다. React `19413`, FastAPI `19410`, Django `19411`을 로컬에 띄웠다. 장애용 Django `19412`만 비어 있는 포트 `19414`를 상위 API로 지정했다. Chrome 프로필은 임시로 생성했다.
- 새 목록 API의 HTTP 200 응답은 정책 **249건**, `dataset_version=1.4.0-candidate`, `evaluated_on=2026-11-25`, `source_kind=release_files`, manifest `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da`였다. PR #119의 manifest와 일치한다. 상세는 실제 FastAPI 목록 응답을 사용했다. 실행 중인 R3D 검증용 컨테이너와 설치된 Ollama 모델은 확인했으며, 이번 상담에서 R3D 검색과 LLM 성공은 확인하지 못했다.

## 브라우저 결과

| 이슈 · 경로 | 입력 및 기대 결과 | 실제 결과·증빙 | 판정 |
| --- | --- | --- | --- |
| #120·#122 홈 정책 목록 → 상세 | `POL_YC_007` 카드를 펼쳐 상세보기. 같은 정책·출처가 새 탭에 표시 | 새 탭 `/policies/POL_YC_007/`, `target=_blank`, `window.opener=null`. 양쪽 정책명 ‘글로벌첨단전략산업 기술경영 전문인력 양성사업’과 온통청년 주소 끝 ID `20261101005400214681` 일치 | 통과 |
| #120 홈 복귀 | 상세 탭을 닫은 뒤 원래 카드가 펼쳐진 상태 | 원래 React 탭과 목록은 유지됐다. 탭 복귀 후 로딩 완료를 기다렸으나 열린 `details`가 없었다 | 실패 |
| #120·#122 관심 정책 → 상세 | 같은 정책을 저장 → 관심 정책 탭 → 상세보기. 같은 정책·출처이며 저장 유지 | 정책 ID·이름·출처 일치. 복귀 시 관심 탭 `aria-pressed=true`, 로컬 저장 ID `POL_YC_007` 유지 | 통과 |
| #120 관심 정책 복귀 | 저장한 카드를 열어 상세로 갔다가 기존 카드가 펼쳐진 상태로 복귀 | 목록 재표시 후 열린 카드가 없었다. 저장과 탭 선택은 유지됐다 | 실패 |
| #120·#122 정책 비교 | ‘글로벌첨단전략산업 기술경영 전문인력 양성사업’, ‘공학교육혁신센터 지원’을 비교. Django 상세 진입 유무와 정책·출처 확인 | 두 요약 카드 모두 ‘상세 보기’가 React 모달을 열고 ‘공식 공고 ↗’를 별도 표시한다. 첫 정책의 모달 제목과 공고 URL은 위 Django 상세와 일치했다. 모달 내 Django 링크 0개. #120의 공통 카드 계약은 충족했다. 비교 화면 Django 진입은 두 이슈의 명시적 필수 항목에 포함되어 있지 않다 | 통과 |
| #120·#122 실제 상담 카드 | 상세 새 탭 및 원래 상담 보존 | PR #119는 동일한 `dev` 병합본에서 실제 상담 카드를 열어 기존 상담이 유지됨을 확인했다. 원래 탭에 근거 출처가 남았고 `POL_010` Django 상세 HTTP 200 및 SSR 메타가 확인됐다. 질문·답변·선택 정책을 각각 비교한 값은 기존 보고서에 없다 | 기존 증빙 인용 |
| #120·#122 이번 실제 선택 상담 | 홈에서 `POL_YC_007`의 ‘내 조건으로 확인하기’로 상담 요청. 성공하면 상세 이동 전후 질문·답변·선택 정책 비교 | 브라우저 `POST /api/ask`에 정확한 `selected_policy_id=POL_YC_007`이 들어갔으나 HTTP 503 `SEARCH_BACKEND_UNAVAILABLE`. 검색 실패 원인은 이번 검증에서 좁히지 못했다 | 실패 |
| #120·#122 질문·답변·선택 정책별 복귀 검사 | 위 선택 상담의 성공 응답에서 각 상태의 이동 전후 비교 | 성공 응답과 실제 상담 카드가 생성되지 않아 현 실행에서 비교할 수 없었다. 홈/관심 카드의 펼침 검사는 별도로 수행했다 | 미실행 |
| #120 일정 카드 | 일정 카드에서 동일 정책 상세 진입 | 이번 검증은 요청된 홈 목록·상담·관심·비교 경로에 집중했다. 일정 카드 상세는 별도 클릭하지 않았다 | 미실행 |
| #120·#122 상세 모바일 | `POL_STG_MOLIT_002`, 390px 및 320px. 본문 가로 넘침·잘림, 공식 링크 포커스·조작 확인 | HTTP 200. 문서 스크롤 너비는 각각 375px·305px, 측정 대상 제목·본문·링크의 가로 경계 이탈 0건. 공식 링크 높이 44px, 포커스와 중앙 히트 테스트 통과. 클릭 시 정부24 새 탭 탐색 확인 | 통과 |
| #120·#122 상세 다크 모드 | 390px에서 브라우저 다크 선호 설정 시 상세도 어두운 테마 표시 | `matchMedia`는 다크를 감지했으나 계산된 `color-scheme=light`, 배경 `rgb(248, 248, 243)`로 라이트와 동일했다. #120은 PC·모바일 일관성을 요구한다. 다크 지원을 승인 기준으로 삼을지는 팀 결정이 필요하다 | 실패 |
| #120·#122 없는 정책 | `/policies/POL_QA_NO_SUCH_POLICY_122/`에서 404와 복귀 | HTTP 404, ‘정책을 찾을 수 없어요’. 복귀 링크로 React 목록 렌더 확인, 정상 ID는 200 | 통과 |
| #120·#122 형식이 틀린 ID | `/policies/pol_yc_007/`에서 404 | HTTP 404. 로컬 `DEBUG=true`에서 Django 기본 디버그 안내와 URL 패턴이 표시됐다. 정상 ID는 200. 운영 설정에서의 브라우저 화면은 별도 확인 필요 | 통과 |
| #120·#122 상위 API 연결 실패 | 장애용 Django에서 `POL_YC_007`을 열어 503과 복귀 | HTTP 503, ‘정책 정보를 잠시 불러올 수 없어요’. 사용자 화면에 내부 포트·예외 없음. 복귀 링크로 정상 React 목록이 로드됐고 정상 Django 주소는 200. 장애 인스턴스에서 동일 URL 재시도 후 복구되는 흐름은 미측정 | 통과 |
| #120·#122 JavaScript 비활성화 | `POL_STG_MOLIT_002` 상세 URL 직접 열기. 응답 HTML의 주요 정보 및 화면 표시 | HTTP 200. 문서 응답과 렌더 DOM에 ‘청년 전세보증금반환보증 보증료 지원’, ‘국토교통부 청년주거정책과’, 신청 기간 ‘상시 접수’, ‘접수 중’, 제출서류 1블록, 정부24 원문 링크 포함. 제목·서류 표시, 토글 버튼 숨김, 추가 Fetch/XHR 0건 | 통과 |

## 재현과 기존 증빙

- **펼친 카드 닫힘** — 홈에서 정책을 펼쳐 ‘정책 상세보기 ↗’를 누른 뒤 새 탭을 닫는다. 관심 정책 탭에서도 동일하다. `PolicyExplorer.tsx`는 포커스·가시성 복귀에 목록을 갱신하며 `loading` 상태에 카드 목록을 제거한다. 이 흐름이 네이티브 `details` 상태가 사라지는 관찰과 일치한다. 이전 PR #121 검증에서 기록한 카드 펼침 유지와 달리 이번 Chrome 탭 복귀 시나리오에서는 두 경로에서 닫힘이 재현됐다.
- **다크 모드** — Chrome에서 viewport 390px, `prefers-color-scheme: dark`를 적용하고 위 상세 URL을 열면 된다. `web_ssr/policies/static/policies/detail.css`는 `color-scheme:light`와 라이트 배경을 지정한다. 기존 390px React 다크 검증과 Django 모바일 반응형 기록을 각각 확인했다. [PR #119 R3D 보고서](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/blob/8229620fd4ee72284d7a7af362eb1258e5b50cab/docs/reports/issue107-r3d-verification.md) · [PR #121 브라우저 증빙](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/tree/6b4fdc96b154b47194d0460c19be23f70d779546/docs/development/ssr120-merge-verification)
- **실제 상담 503** — 홈에서 `POL_YC_007`을 펼쳐 ‘내 조건으로 확인하기’를 누른다. 질문은 ‘글로벌첨단전략산업 기술경영 전문인력 양성사업 지원 대상이랑 신청 방법, 필요한 서류 알려줘’였다. `/health`의 초기 `ready=false`만으로 장애를 판정하지 않았으며, 실제 브라우저 POST의 오류 코드로 판정했다. 기존 R3D 컨테이너는 실행 중이지만 이번 서버에서 확인한 설정으로 해당 번들 검색을 성공시키지 못했다. 전용 Neo4j·Qdrant 포트에 TCP 접속은 가능했으나 현재 실행 환경에는 R3D 검증용 인증 설정이 제공되지 않았다. 팀의 기존 R3D 설정을 제공받으면 같은 브라우저 요청으로 재검증할 수 있다.
- **기존 상세 보조 증빙** — PR #121 병합 전 통합 보고서에는 누락 데이터 안내, 320/390/768/1440px 반응형, 제출서류 조작, 404/503과 기존 대비 추가 전체 회귀 실패 0건이 기록돼 있다. 이번 전체 회귀는 실행 범위에 포함하지 않았다. [PR #121 브라우저 증빙](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/tree/6b4fdc96b154b47194d0460c19be23f70d779546/docs/development/ssr120-merge-verification)

## 실행 명령과 승인 조건

- 격리 폴더에서 기존 가상환경으로 `python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 19410 --env-file <저장소 루트 .env>`와 `python web_ssr/manage.py runserver 127.0.0.1:19411 --noreload`를 실행했다. 기존 `.r3d-ssr-deps`를 사용하고 `SSR_FASTAPI_BASE_URL`과 `SSR_REACT_BASE_URL`을 격리 포트로 설정했다. `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 19413 --strictPort`에 `VITE_ASK_API_URL`, `VITE_POLICY_API_URL`, `VITE_POLICY_DETAIL_BASE_URL`을 연결했다. Chrome CDP에서 새 탭, viewport, 스크립트 비활성화, 네트워크 응답을 관찰했다.
- `python web_ssr/manage.py test policies --settings=config.settings_test` **41건 통과**. HTTP mock을 사용하는 Django 회귀 테스트다.
- `python -m pytest tests/integration/test_policy_catalog_api.py -q`는 결과 출력 없이 대기해 중단했다. 목록 API의 HTTP 200 및 데이터 대조는 위 브라우저 실행에서 별도로 확인했다.
- #122 완료에는 팀이 승인하는 최신 dev·R3D 번들에서 실제 Neo4j/Qdrant → Checker → Pipeline → FastAPI → React → Django를 다시 확인하고, 답변·판정·정책 ID·문서/청크 출처를 대조한 증빙이 필요하다. #102 품질 플래그, #39·#76·#78 필드, #79·#90·#103 정책 시나리오, #107 일정과 오래된 일정 표시, #116 기준 실패 대비 신규 회귀 여부도 최종 표에 기록해야 한다.
- 팀 결정이 필요한 항목은 비교 화면의 Django 링크 추가 여부, 상세 페이지 다크 테마 수용 기준, 탭 복귀 시 카드 펼침 상태 유지 우선순위다. 운영 배포에서 형식 오류 404의 사용자 화면과 상위 API 복구 후 같은 URL의 재시도는 별도 확인 대상으로 남겼다.
