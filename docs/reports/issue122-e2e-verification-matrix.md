# #122 최종 E2E 통합 검증표 (준비본)

2026-11-25 KST 작성. [#122](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/122)의 검증 항목 9개를 ID로 나누고, **이미 확인한 증빙을 연결**하고, **남은 항목의 입력·기대 결과·증빙 기준**을 정리했다.

> **이 문서는 최종 검증 결과가 아니다.** 이번 작업에서 서버·DB·브라우저 검증은 새로 하지 않았다. 아래 "현재 상태"는 기존 PR·보고서에 기록된 결과를 옮긴 것이다. 최종 판정은 잔여 기능이 반영된 dev에서 §4 순서대로 다시 실행한 뒤 이 표의 "최종 판정" 칸에 적는다.

## 1. 기준

| 항목 | 값 |
| --- | --- |
| 기준 dev | `66321e7` (PR #119 병합). PR #119·#124 증빙의 기준 `6b4fdc9`와 비교하면 변경은 `docs/reports/issue107-r3d-verification.md`와 `tests/unit/test_application_schedule.py` 두 파일뿐이다. 서비스 코드는 같아서 기존 증빙을 그대로 연결했다. |
| 데이터 | `dataset_version=1.4.0-candidate`, 정책 **249개**, manifest SHA-256 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` |
| 복원 번들 | `rag-final-v140-r3-57d90f23308a.zip` (SHA-256 `cfa1001d…0e1a`). Qdrant B2 1,249·B3 1,338 Point, Neo4j 1,147노드·2,702관계 ([ISSUE105_R3_RUNBOOK](../development/ISSUE105_R3_RUNBOOK.md) 머리말·§5.12) |
| 판정 기호 | **PASS** 기대 결과와 일치 · **FAIL** 실행했고 불일치 · **BLOCKED** 선행 기능·환경 때문에 실행 불가 · **미실행** 실행 가능하지만 아직 안 함 · **기존 증빙** 이전 기록 인용(최종 회차에 재확인) |

### 기존 증빙 출처

| 약칭 | 문서 | 기준 커밋 |
| --- | --- | --- |
| R119 | [PR #119](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/119) · [issue107-r3d-verification.md](issue107-r3d-verification.md) | `6b4fdc9` + PR #119 |
| R121 | [PR #121](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/121) · [ssr120-merge-verification](../development/ssr120-merge-verification/) | PR #121 병합 전 통합본 |
| R124 | [issue120-122-django-ssr-followup.md](issue120-122-django-ssr-followup.md) (이 검증표와 같은 [PR #124](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/124)) | `6b4fdc9` |
| R123 | [issue75-browser-regression-20261125.md](issue75-browser-regression-20261125.md) · #75 브라우저 회귀 중간 검증표 (PR #123에서 이 PR로 옮김) | `e853282` |
| R125 | [PR #125](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/125) (열림) · K-패스 목록 검색 별칭 | `1f43e62` |
| R112 | [PR #112](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/112) (열림) · 자녀 월령 Checker·Generator | `dabc7de` |

## 2. 선행 조건 상태

2026-11-25 GitHub 기준이다. 최종 검증 직전에 다시 확인한다.

| 선행 | 상태 | 최종 검증에 주는 영향 |
| --- | --- | --- |
| #105 최신 DB 번들 | 종료. r3d 독립 복원 PASS | 번들 기준은 확정. **팀 공유 환경의 FastAPI가 이 번들 DB에 붙는지는 미확인** (R124의 503) |
| #102 품질 플래그 | 열림 | E2E-05-4 BLOCKED |
| #39 자녀 월령 | 열림, PR #112 리뷰 중 | E2E-04-3 BLOCKED |
| #76 공식 자격기준 | 열림 (#76-A 미구현) | `official_requirements`는 계약상 `null` 유지가 기대 결과 |
| #78 제외 사유 | 종료. Checker 로직만 구현, Pipeline·API 연결은 후속 (ask_contract §8.3) | `exclusion_reasons`는 API에서 `null`. 연결 작업 이슈 번호 확인 필요 |
| #103 K-패스·FAQ·세종 | 열림, PR #125 리뷰 중(목록 검색만) | 상담 검색·FAQ·세종 항목 BLOCKED |
| #107 신청 일정 | 열림, 관련 PR #109·#110·#113·#118·#119 병합 | 일정 회귀 테스트·브라우저 증빙 있음 |
| #120 / PR #121 Django SSR | 종료·병합 | 1차 검증 R124. 실패 2건 후속 |
| #90 인천 연령 근거 | 열림 | E2E-05-5는 `needs_review` 유지가 기대 결과 |
| #116 새 clone 테스트 | 열림 | 기준 실패 목록을 먼저 고정해야 E2E-09 판정 가능 |

## 3. 검증표

### E2E-01 데이터 버전·정책 수·manifest 일치

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 01-1 | `GET /api/policies` (필터 없음) | HTTP 200, 정책 249건, `dataset_version=1.4.0-candidate`, manifest가 §1 값과 같음 | 응답 JSON의 `total`·`dataset_version`·manifest 필드 발췌, 실행 커밋 | **기존 증빙** R119·R124 (HTTP 200, 249건, manifest 일치) | |
| 01-2 | 복원 번들 `--verify-only` (읽기 전용) | PASS, Qdrant B2 1,249·B3 1,338, Neo4j 1,147·2,702, 교차 검증 통과 | `--verify-only` 보고서 JSON의 결과·건수 | **기존 증빙** RUNBOOK §5.12 (r3d verify 환경). 팀 공유 환경은 미확인 | |
| 01-3 | 상담용 FastAPI가 실제로 연결한 Neo4j·Qdrant 주소·컬렉션 | 01-2와 같은 DB·컬렉션. 다른 DB(빈 기본 컨테이너 등)를 보지 않음 | `/health` 응답과 서버 시작 로그(비밀값 제외) | **미실행** | |

### E2E-02 실제 Neo4j/Qdrant 검색 → Checker → Pipeline → FastAPI

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 02-1 | `POST /api/ask` 일반 상담 (가상 프로필, 질문 1건) | HTTP 200, `policy_checks` 1건 이상, 각 `policy_id`가 249개 목록에 있음 | 요청·응답 JSON (프로필은 가상값) | **기존 증빙** R119 (HTTP 200). R124 환경은 503 | |
| 02-2 | `POST /api/ask` + `selected_policy_id=POL_YC_007` | HTTP 200, 첫 정책이 `POL_YC_007`, 다른 정책 혼입 없음 | 요청·응답 JSON | R119 선택 상담 200 · **R124 FAIL** (503 `SEARCH_BACKEND_UNAVAILABLE`) | |
| 02-3 | 02-1을 같은 입력으로 2회 | 검색·생성 각 1회 호출, 정책 ID 목록 동일 | 서버 로그 또는 단위 테스트 결과 | **기존 증빙** R119 (모의 검색·생성기로 각 1회 호출 확인). 실DB 반복은 미실행 | |

> 02-2의 503은 검색 연결 문제로 보고 있다(R124). 원인 확인 담당은 R124에 적힌 대로 나지인 → 필요하면 DB 담당. 해결 전까지 02·03·04·05·07-3은 BLOCKED로 둔다.

### E2E-03 LLM 답변·자격 판정·정책 ID·문서/청크 출처 정합성

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 03-1 | 02-1·02-2 응답 | 답변의 인용 번호마다 `citations`에 같은 번호가 있고, 그 `policy_id`·`document_id`·`chunk_id`가 같은 정책의 문서·청크임 | 인용 번호 ↔ citation ↔ Neo4j `Policy-Document-Chunk` 대조표 | **BLOCKED** (02-2) | |
| 03-2 | 같은 응답 | 카드의 판정(`eligible`·`ineligible`·`needs_check`)이 Checker 결과와 같고, 답변 문장이 판정과 반대로 말하지 않음 | 응답의 `policy_checks[].status` ↔ 답변 문장 발췌 | **BLOCKED** | |
| 03-3 | 같은 응답 | `source_url`·`retrieved_at`·`content_hash`가 manifest 원문 값과 같음 | citation 1건 이상 원문 파일 대조 | **BLOCKED** | |
| 03-4 | 화면 | React 카드의 정책명·출처 링크가 응답과 같음 | 브라우저 스크린샷 + 응답 JSON | **기존 증빙** R119 (공식 근거·추가 확인 필요 판정 반영 확인) | |

### E2E-04 exclusion_reasons · official_requirements · child_age_months

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 04-1 | 제외 조건이 있는 정책(예: #102의 POL_GOV_016) 상담 | Pipeline 연결 전: `exclusion_reasons=null`, 답변 본문에서만 안내. 연결 후: `{field,label,reason}`이 Checker 사유와 같음 | 응답 JSON | **BLOCKED** (Pipeline 연결 미구현) | |
| 04-2 | 공식 원문 발췌가 있는 정책 상담·목록 | `official_requirements=null` 유지. 화면은 같은 자리에 `requirement_excerpts`를 '원문 발췌'로 표시. 값 환산 없음 | 응답 JSON + 카드 스크린샷 | **미실행** (실행 가능, 02 해결 필요) | |
| 04-3 | POL_GOV_050, `child_age_months` = 10 / 24 / 25 / 생략 / -1·1.5·"a" | 10·24 → eligible, 25 → ineligible, 생략 → needs_check, 잘못된 값 → 422(값 미노출). 조건은 `COND_CHILD_AGE_LE_24M`(LTE 24) | 요청별 응답 JSON, 422 본문 | **BLOCKED** (PR #112 미병합, API·화면 입력 미구현) | |

> **24개월 경계:** #39 본문의 테스트 시나리오 2는 24개월을 `ineligible`(24개월 미포함)로 적었지만, 이는 교정 전 내용이다. 릴리스 데이터 `data/releases/v1.4.0-candidate/neo4j/relationships.jsonl`에서 POL_GOV_050은 `COND_CHILD_AGE_LE_24M`(LTE 24)을 쓴다. `data/metadata/data_corrections_v1.4.0.json`에는 전주시 조례의 '24개월 이하' 문구를 따른다는 근거와 "반석 확정 2026-11-20, #39 A안"이 기록돼 있다. PR #112도 같은 기준이다. 따라서 24개월의 기대 결과는 `eligible`이다. #39 본문의 시나리오 2만 갱신하면 된다.

### E2E-05 K-패스 별칭·FAQ·세종 정책·인천 연령 근거

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 05-1 | 홈 목록 검색창에 PR #125의 6종 표기 | 6종 모두 `POL_STG_MOLIT_001` 표시 | 표기별 결과 스크린샷 또는 DOM 기록 | R123 FAIL(재현) → **R125에서 PASS** (PR 열림, 병합 후 재확인) | |
| 05-2 | 상담 질문에 같은 6종 표기 | 검색 결과·카드에 `POL_STG_MOLIT_001`, 정책명은 공식 명칭 | 응답 JSON | **BLOCKED** (#103 상담 검색 범위 + 02-2) | |
| 05-3 | #103의 FAQ 3건 관련 질문 | 추가된 FAQ 청크가 근거로 인용되거나, '제외'로 결정된 FAQ는 인용되지 않음 | FAQ별 반영 결과 기록 ↔ citation `chunk_id` | **BLOCKED** (#103 FAQ 미반영) | |
| 05-4 | #102의 보류 누락 37개 중 예시 (POL_GOV_008·POL_GOV_011·POL_GOV_016·POL_005) | 해당 항목이 '확인 필요'로 표시되고 '충족'으로 보이지 않음 | 응답 JSON의 조건별 판정 | **BLOCKED** (#102 미반영) | |
| 05-5 | POL_YC_065 (세종 위원회) 목록·상세·상담 | 정책 유지, '신청하기' 버튼 없음, '위원회별 청년위원 모집 일정은 공식 공고 확인 필요' 안내 | 목록·상세 스크린샷 | 신청 버튼 미노출은 **기존 증빙** R119. 안내 문구는 **BLOCKED** (#103) | |
| 05-6 | POL_STG_ICN_001 (인천 서구) 상담, 가상 나이 19·38·40 | #90 반영 전: 연령 조건 `needs_review`, 확정 판정 안 함. 반영 후: 근거 문서가 사업안내서이고 출처가 카드에 표시 | 응답 JSON + citation `document_id` | **미실행** (02 해결 필요) | |

### E2E-06 FastAPI GET /api/policies 및 POST /api/ask

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 06-1 | `GET /api/policies` 필터·정렬 조합 (지역·분야·접수 상태, 마감 임박순·이름순) | HTTP 200, 필터가 전체 249건 기준으로 동작, 검색·LLM 호출 없음 | `tests/integration/test_policy_catalog_api.py` 결과 + 응답 샘플 1건 | **기존 증빙** R119 (16건 통과). R124에서는 이 테스트가 출력 없이 멈춰 중단 | |
| 06-2 | `POST /api/ask` 계약 필드 (`selected_policy_id`, `policy_checks[].application_schedule`·`required_documents`·`requirement_excerpts`·`contacts`) | ask_contract v1.5 §8.3 형식과 일치, 없는 값은 `null` | 응답 JSON ↔ 계약 문서 대조 | **BLOCKED** (02-2) | |
| 06-3 | `POST /api/ask` 잘못된 요청 (필수값 누락·형식 오류) | 422, 입력값 미노출 | 응답 JSON | **미실행** | |

### E2E-07 Django SSR 정책 상세보기와 React 카드 새 탭

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 07-1 | 홈 목록 `POL_YC_007` → 상세보기 | 새 탭 `/policies/POL_YC_007/`, `window.opener=null`, 정책명·출처 일치 | DOM 기록 + 스크린샷 | **기존 증빙** R124 PASS | |
| 07-2 | 07-1 후 상세 탭 닫기 | 원래 카드가 펼쳐진 상태 유지 | 복귀 후 열린 `details` 수 | **R124 FAIL** (홈·관심 정책 두 경로) | |
| 07-3 | 실제 상담 카드 → 상세보기 → 복귀 | 상세 정책 = 카드 정책, 질문·답변·선택 정책이 이동 전후 같음 | 이동 전후 상태 비교 | R119 기존 상담 유지 확인. 질문·답변·선택 정책별 비교는 **BLOCKED** (02-2) | |
| 07-4 | 관심 정책·비교 화면 → 상세 | 관심: 저장·탭 유지. 비교: React 모달(Django 링크 추가는 팀 결정) | DOM 기록 | **기존 증빙** R124 PASS | |
| 07-5 | 일정 카드 → 상세 | 같은 정책 상세가 새 탭으로 열림 | DOM 기록 | **미실행** (R124) | |
| 07-6 | `python web_ssr/manage.py test policies --settings=config.settings_test` | 41건 통과 | 테스트 출력 | **기존 증빙** R119·R124 PASS | |

### E2E-08 404/503 · 누락 데이터 · 오래된 일정 · JS 비활성화 · 모바일

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 08-1 | 없는 정책 `/policies/POL_QA_NO_SUCH_POLICY_122/` | 404, '정책을 찾을 수 없어요', 복귀 링크 동작 | HTTP 상태 + 스크린샷 | **기존 증빙** R124 PASS | |
| 08-2 | 형식이 틀린 ID `/policies/pol_yc_007/`, `DEBUG=false` | 404, 내부 URL 패턴 미노출 | HTTP 상태 + 스크린샷 | `DEBUG=true`만 확인(R124). 운영 설정은 **미실행** | |
| 08-3 | Django 상위 API 중단 | 503, '정책 정보를 잠시 불러올 수 없어요', 내부 포트·예외 미노출. 복구 후 같은 URL 재시도 시 200 | HTTP 상태 + 스크린샷 | 503 화면 **기존 증빙** R124 PASS. 복구 후 재시도는 **미실행** | |
| 08-4 | FastAPI 검색 DB 중단 후 `POST /api/ask` · 릴리스 파일 손상 후 `GET /api/policies` | 503 `SEARCH_BACKEND_UNAVAILABLE` · 503 `POLICY_CATALOG_UNAVAILABLE`. 이전 캐시를 최신처럼 반환하지 않음 | 응답 JSON | 503 `SEARCH_BACKEND_UNAVAILABLE`은 R124에서 의도치 않게 재현. 의도된 장애 시험은 **미실행** | |
| 08-5 | 신청 일정 누락·충돌 정책 (기금대출 4건 등) | '일정 확인 필요', D-day·캘린더 버튼 없음 | 목록 스크린샷 | **기존 증빙** R119 (기금대출 4건 일정 미확인) | |
| 08-6 | KST 자정 전후, 탭 복귀, 저장 정책 재열람 | 접수 상태·D-day가 현재 날짜로 갱신됨 | 단위 테스트 + 브라우저 기록 | 단위 테스트 **기존 증빙** R119 (KST 자정 전후 4건). 브라우저의 자정 경과·탭 복귀는 **미실행** | |
| 08-7 | JS 비활성화로 `POL_STG_MOLIT_002` 상세 | 정책명·기관·신청 기간·제출서류·원문 링크가 HTML에 있음, 추가 요청 0건 | 응답 HTML 발췌 + 스크린샷 | **기존 증빙** R124 PASS | |
| 08-8 | 320·390px React·Django, 다크 모드 | 가로 넘침 0, 링크 높이 44px 이상. Django 다크 모드는 팀 결정에 따름 | 스크린샷 + 측정값 | 반응형 **기존 증빙** R121·R124 PASS. React 다크 R119 PASS. Django 다크 **R124 FAIL** (수용 기준 미정) | |

### E2E-09 기존 회귀 실패(#116)와 신규 실패 구분

| ID | 입력 | 기대 결과 | 증빙 기준 | 현재 상태 | 최종 판정 |
| --- | --- | --- | --- | --- | --- |
| 09-1 | 새 worktree, `.env` 없음, `python -m pytest tests/unit -q -rf` | 실패 목록이 #116 기준 목록과 같거나 줄어듦. **신규 실패 0** | 실패 테스트 node ID 목록 파일 (기준·최종 두 개) | #116 기준 53 failed + 20 errors (이슈 본문). RUNBOOK §5.5.3 기록은 55 failed + 20 errors로 환경에 따라 다름 → **기준 목록 미고정** | |
| 09-2 | 같은 worktree, 팀 `.env` 사용 | 09-1에서 환경 때문에 실패한 테스트가 통과 | 실패 목록 비교 | **미실행** | |
| 09-3 | `cd frontend && npm test` · `npm run build` | React 테스트 전부 통과(R119 기준 174건), 타입 검사·빌드 통과 | 테스트 출력 | **기존 증빙** R119·R125 PASS | |

## 4. 최종 회차 실행 순서

1. **기준 고정** — 최종 dev 커밋 SHA, 복원 번들 이름·SHA-256, manifest를 §1 표에 적는다. 선행 조건(§2)을 다시 확인하고 미반영 항목은 BLOCKED로 둔다.
2. **기준 실패 목록** — 09-1을 먼저 실행해 실패 node ID를 파일로 남긴다. #116이 먼저 고쳐지면 그 결과를 기준으로 쓴다.
3. **DB 연결 확인** — 01-2 `--verify-only`, 01-3 연결 대상 확인. 여기서 막히면 02 이후를 진행하지 않는다.
4. **API** — 01-1 → 06-1 → 02 → 06-2·06-3 → 03 → 04 → 05 순서. 요청·응답 JSON을 ID별 파일로 저장한다.
5. **화면** — 05-1 → 07 → 08. 스크린샷 파일명은 `E2E-07-2_390.png`처럼 ID로 시작한다.
6. **장애** — 08-3·08-4는 장애용 인스턴스(R124처럼 빈 포트를 상위 API로 둔 Django 등)로만 시험하고 정상 인스턴스는 건드리지 않는다.
7. **정리** — "최종 판정" 칸을 채우고, FAIL·BLOCKED마다 재현 절차와 후속 이슈 번호를 단다.

명령은 R124 "실행 명령과 승인 조건" 절과 RUNBOOK §5.12를 그대로 쓴다. Docker 컨테이너 시작, DB 복원·초기화는 담당자 승인 뒤에만 한다(AGENTS.md §5).

## 5. 증빙 기록 규칙

- 요청·응답 샘플의 프로필은 가상값만 쓴다. 실제 개인정보, API 키, 비밀번호, `.env` 값은 증빙 파일·스크린샷·로그에 남기지 않는다.
- 응답 샘플에는 실행 커밋, 실행 시각(KST), 데이터 manifest를 함께 적는다.
- "기존 증빙"은 최종 회차에서 다시 실행해 PASS·FAIL로 바꾼다. 다시 못 한 항목은 이유를 적고 "기존 증빙"으로 남긴다.
- 실행하지 않은 항목을 PASS로 적지 않는다.

## 6. 팀 결정이 필요한 항목

| 항목 | 관련 | 결정할 내용 |
| --- | --- | --- |
| `exclusion_reasons` Pipeline 연결 | #78 후속 | 담당 이슈 생성 여부, #122 완료 조건에 넣을지 |
| Django 상세 다크 모드 | #120 · R124 | 승인 기준에 포함할지 |
| 상세 탭 복귀 시 카드 펼침 유지 | #120 · R124 | 이번 범위에서 고칠지 |
| 비교 화면 Django 링크 | #120 · R124 | 추가할지 |
| #116 기준 실패 목록 | #116 | 53건(이슈)과 55건(RUNBOOK) 중 어느 환경을 기준으로 할지, 또는 #116 수정 후로 미룰지 |
