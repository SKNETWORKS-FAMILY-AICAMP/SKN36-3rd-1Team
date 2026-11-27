# #79·#18·#90 교정 정책 API·URL·출처 최종 검증

2026-11-27 KST. **최종 CONDITIONAL.** 서비스 코드·데이터·DB 변경 없음. 테스트·수동 검증 스크립트·증거만 추가했다.
Related to #79, #18, #90, #122 (이 보고서는 세 이슈의 종료 근거가 아니다).

## 1. 검증 목적과 범위

교정된 연령·신청 URL·근거 문서가 Checker → Pipeline → Generator → FastAPI 응답까지 그대로 전달되는지 확인한다.
PR #140(53정책 독립 연령 기대값·58청크·저장 답변 재생)과 PR #152(인천 18세 결론 보정·보정 문구 겹침)가 이미 확인한 범위는 **다시 만들지 않고**, 최신 dev에서 재실행만 했다. 새 테스트는 그 뒤에도 비어 있던 범위만 다룬다.

## 2. 기준

| 항목 | 값 |
|---|---|
| 최신 dev | `02659e223d1f30d43158f591bd8bc9a4ca9e0824` (PR #139 병합, 2026-11-27 21:16 KST) |
| 브랜치 / worktree | `test/79-18-90-policy-source-e2e` / `C:\dev\project\SKN36-3rd-1Team\issue79-18-90-policy-source-e2e` (origin/dev에서 새로 생성) |
| 데이터 릴리스 | `1.4.0-candidate`, `manifest.json` SHA-256 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` (PR #140 기록과 같음) |
| 릴리스 규모(이번 실측) | 정책 249, 문서 273, 청크 672, 조건 105, 관계 2,702 |
| R3D verify DB | **BLOCKED(환경)** — Qdrant `127.0.0.1:56756`·Bolt `127.0.0.1:56787` 연결 거부, 실행 중인 컨테이너 없음. 컨테이너 기동은 승인 대상이라 실행하지 않음 |
| 실제 LLM | `gpt-5.4-mini` 7회, 동결 릴리스 문맥 + TestClient (R3D 검색 아님) |
| Python | 기존 루트 checkout의 `.venv` 사용, 패키지 설치 없음 |

GitHub 상태(2026-11-27 조회): PR #89·#130·#140·#152 **병합**(각 병합 커밋 `e764518`·`c1569bc`·`3541a64`·`51bc4ae`, 모두 최신 dev에 포함). PR #148 **열림·미병합**. 이슈 #79·#18·#90·#122 모두 **열림**.

## 3. 재사용한 기존 증빙

| 증빙 | PR·커밋 | 날짜 | 데이터 | 당시 결과 | 최신 dev `02659e2`에서 |
|---|---|---|---|---|---|
| 53정책 독립 연령 기대값·346 경계 | #140 `d948819` | 11-27 | 1.4.0-candidate `b3130722…` | 93 PASS | 테스트 **재실행 PASS**(아래 §9). 기대값 fixture 그대로 |
| 58 B2 청크·273문서 해시 (실제 R3D) | #140 `b19faf6` | 11-26 | 같음 | 58/58 일치 | R3D 미기동으로 **재실행 안 함**. 대신 릴리스 전체 672청크 계보를 새로 검사(§7) |
| 실제 LLM 7건 저장 답변 재생 | #140 `d948819` | 11-26 응답 | 같음 | 출처 7/7 | 재생 테스트 **재실행 PASS**. 새 LLM 호출 아님 |
| 문서 단위 인용 11건 | #130 `c1569bc` | 11-25 | R3D | 문서 단위 PASS | 재실행 안 함(R3D). 문서 ID·URL이 같은 릴리스라 문서 단위로만 인용 |
| 인천 18세 결론 보정·보정 문구 반복 제거 | #152 `a4f7c4d`·`fad8742` | 11-27 | 같음 | 3,269 PASS | 관련 테스트 **재실행 PASS**. 단 새 반복 사례 1건 발견(§6, D-8) |
| 인천 원문 열람(사업안내문·조례 19~39, 정부24 API 요약만 18~39) | #152 matrix §14 | 11-27 | — | 화면 열람 | 재열람 안 함. 릴리스 장부 기록과 대조만 함 |

과거 PASS를 이번 실측으로 바꿔 적지 않았다. 재실행한 것은 "재실행 PASS", 재실행하지 않은 것은 그대로 구분했다.

## 4. #79 연령

### 4-1. 범위 정리

릴리스에서 조건 트리에 신청자 `AGE`가 있는 정책은 **93개**이고, 연산자는 모두 `BETWEEN`(단위 세)이다. **LT/LTE/GT/GTE AGE는 릴리스에 없다.** 자녀 월령 LTE24(#39, POL_GOV_050)는 `CHILD_AGE`라 이 집합에 들어가지 않으며 기존 #39 테스트로 확인했다.

| 구분 | 정책 수 | 근거 |
|---|---:|---|
| PR #140 독립 기대값 fixture | 53 | 교정 장부 승인 ID (기존) |
| 이번: 원문 문구로 기대값 확인 | 10 | 근거 문서 청크의 문구를 직접 확인 (`OFFICIAL_AGE`) |
| 이번: 무상한 0~120 | 23 | 나이로 거절하지 않는지만 확인 |
| 이번: 지원 제외(NOT) | 1 | POL_005, 기존 `test_age_verdict_api.py` |
| 이번: 신청자 기준으로 쓸 수 없는 후보 | 6 | §4-3 |

### 4-2. 원문 기대값 10정책 (새로 검증)

각 정책마다 ① 데이터 값 = 원문 기대값, ② 원문 문구가 연결된 근거 문서 청크에 실제로 있음, ③ `_condition`이 하한-1·하한·하한+1·상한-1·상한·상한+1·미입력에서 기대값과 같음, ④ 공개 Checker에서 범위 밖·미입력이 개별·종합 모두 eligible이 아님, ⑤ 하한·상한·경계 밖·미입력 API 응답이 Checker보다 확정 쪽으로 바뀌지 않고 종합 ineligible이 아니면 `exclusion_reasons=null`(#78)을 확인했다.

| 정책 | 원문 문구 | 기대 | 결과 |
|---|---|---|---|
| POL_GOV_002 | 만 65세 이하 | 0~65 | PASS |
| POL_GOV_005 | 18세 이상 ~ 40세 미만 | 18~39 | PASS |
| POL_GOV_007 | 만 18세 이상 | 18~ | PASS |
| POL_GOV_011 | 만 12세 ~ 만 24세 | 12~24 | PASS |
| POL_GOV_016 | 만 19세 이상 | 19~ | PASS |
| POL_GOV_017 | 만 14세~64세 | 14~64 | PASS |
| POL_GOV_023 | 만 6세 이상부터 만 65세 미만 | 6~64 | PASS |
| POL_GOV_024 | 19세 ~ 39세 | 19~39 | PASS |
| POL_GOV_030 | 9세 이상에서 24세 이하 | 9~24 | PASS |
| POL_GOV_063 | 65세 이상 | 65~ | PASS |

오류 주입 확인: 실제 Checker `_condition`에 "상한 포함→미포함", "상한 초과→eligible" 오류를 넣으면 이 테스트가 각각 7·9건 실패한다(상한 없는 3정책은 첫 오류와 무관해 정상). Checker 출력을 기대값으로 복사하지 않았다.

### 4-3. 신청자·수혜자 나이와 OR 조건 후보 — **데이터 담당 확인 필요**

PR #89는 수혜자 나이 27건을 신청자 나이와 분리했지만, 아래는 교정 장부에 연령 교정 기록이 없는 원 데이터 그대로다.

| 정책 | 데이터 | 원문 | 현재 판정 | 테스트 |
|---|---|---|---|---|
| POL_GOV_042 발달재활서비스 | AGE 0~17 | 만18세 미만 장애아동·만9세 미만 예견아동(수혜자) | 보호자 45세 → 나이 ineligible, **종합 ineligible** | xfail strict |
| POL_GOV_022 가정위탁 양육보조금 | AGE 0~17 | 18세 미만 아동을 보호 중인 위탁가정, 만24세까지 연장 | 보호자 45세 → 나이 ineligible(종합은 품질 보류로 needs_check) | xfail strict |
| POL_GOV_059 세종 청년 행정인턴 | AGE 19~39 필수 | 19~39세 청년 **또는** 세종시 소재 대학 재학생 | 18세 → 종합 ineligible | xfail strict |
| POL_GOV_036 결식아동 급식 | AGE 0~17 | 18세 미만 결식우려 아동 | 보호자 신청 여부 확인 필요 | 보고만 |
| POL_GOV_029 방과후 아카데미 | AGE 10~16 | 초4~중3(학년 기준) | 환산값 | 보고만 |
| POL_GOV_049 열린교육바우처 | AGE 6~16 | 초·중학생(학년), 근거 청크에 나이 없음 | 환산값 | 보고만 |

**#122 연결:** PR #148이 "GOV_042 45세 종합 ineligible을 LLM이 미확정으로 안내 → 의미 불일치"로 기록한 건의 원인은 Generator가 아니라 이 데이터일 가능성이 높다. 이번 실제 LLM도 같은 입력에서 "45세는 해당하지 않아 보이지만 자녀 나이 확인 전에는 최종 판단 불가"로 답했다(§8). xfail은 데이터가 고쳐지면 XPASS로 실패해 테스트를 정리하도록 했다(strict).

## 5. #90 인천 POL_STG_ICN_001

| # | 확인 항목 | 결과 |
|---|---|---|
| 1 | 현재 연령 조건 | `COND_AGE_19_39`, 값 `19~39`, `REQUIRES_CONDITION`·`CONTAINS_CONDITION` 두 관계 |
| 2 | evidence_document_id | 두 관계 모두 `DOC_STG_ICN_001_NOTICE_2026`, evidence_location에 청크 `DOC_STG_ICN_001_NOTICE_2026_P01_C01` |
| 3 | 공식 사업안내문 Document·Chunk | Document 있음(doc_type notice, `youth.incheon.go.kr …msg_seq=491`, content_hash·raw_source_sha256 64자). 청크 1개, 정책·문서·URL 일치, 본문에 "19~39세" |
| 4 | 검색 결과 노출 | 실제 Hybrid는 R3D BLOCKED로 이번 미실행. 동결 문맥 실제 LLM 3건 모두 NOTICE_2026을 인용(§8). PR #140 실제 Top-10 포함 기록은 재사용 |
| 5 | 정부24 요약 구분 | `DOC_STG_ICN_001_GOV24`(gov24_api) 본문에는 19~39가 없고, 장부 `source_discrepancies`에 "정부24 지원조건 코드 18~39세" 차이 보존 |
| 6 | Qdrant·Neo4j 연결 | 릴리스 페이로드와 그래프 사이 정책·문서·URL 일치. **실제 R3D 재대조는 BLOCKED** |
| 7 | Checker 18·19·39·40·미입력 | 개별 `_condition`: ineligible·eligible·eligible·ineligible·needs_check. 공개 나이 행·종합은 모두 needs_check, exclusion_reasons=null |
| 8 | API 연령 안내 | 18·40세 나이 행 사유 "만 N세가 포함되지 않습니다 + 데이터 품질 경고로 자동 판정을 보류" |
| 9 | 실제 LLM | 18세: "18세 포함" 오안내 **없음**, 다만 19~39 공고 기준을 말하지 않고 "확정하기 어렵다"로만 답함(충돌 설명 부족). 40세: PR #152 보정 문장 "만 40세는 연령 기준(만 19~39세)에 포함되지 않고" 확인. 미입력: 19~39·18~39 차이와 경계 확정 불가를 설명 |
| 10 | review_status·보류 | 연령 관계 `verified`. 종합 보류 원인은 `UNKNOWN_CONDITION_GROUP`(나머지 조건)과 `EXCLUSION_NOT_MODELED`(2025년 선정자 제외)이며 **needs_review가 아님** |

**#90 판단:** 이슈 본문 체크리스트의 데이터 작업(공고 Document/Chunk 추가, evidence 연결, 18~39 차이 기록, verified 해제)은 릴리스 장부 `pending_followups.POL_STG_ICN_001`("해결(2026-11-20) … verified로 해제")과 실제 릴리스 값에서 **반영된 것으로 확인된다.** 그러나

- 이슈 체크박스·데이터 담당자 승인 기록이 GitHub에 없다.
- 공고 원본(`data/raw/official/…hwpx`)은 Git 제외 파일이라 이번에 해시를 재계산하지 않았다.
- 조례 문서에 청년 정의(제2조 19~39세)가 없다(제9·12조만 수록).
- 실제 R3D DB에 같은 값이 적재됐는지 이번에 확인하지 못했다(PR #140 11-26 실측은 verified였음).
- matrix §14는 "나이 조건 관계는 검수 대기"라고 적었지만 릴리스 값은 verified다. 문서 정정이 필요하다.

따라서 **#90은 데이터 반영 확인, 이슈 종료는 데이터 담당 승인 대기**로 둔다. review_status·DB는 바꾸지 않았다.

## 6. #18 공식 신청 URL

### 6-1. 신청 URL 장부

| 분류 | 정책 | 근거 |
|---|---|---|
| 접수 화면 확인(live true) | POL_GOV_057, POL_GOV_065, POL_STG_EX_001 | `application_url_review.json` manual_review_verified_apply_page |
| 화면 확인, 신청서 미확인 | POL_YC_006 | 기간 밖이라 '신청하기' 버튼까지만 확인(live false) |
| **원천 값 그대로, 접수 화면 미확인** | POL_GOV_059 `https://www.sejong.go.kr`(시청 첫 화면), POL_YC_009 `https://ceedin.knu.ac.kr`(기관 첫 화면), POL_STG_SEOUL_001 | reason `application_field_present`, live false |
| 신청 URL null | 나머지 242 | — |

- 신청 URL이 원문 `source_url`이나 같은 정책 근거 문서 URL과 같은 경우 0건(249 전수).
- API는 `official_apply_url`을 릴리스 값 그대로 전달한다(10정책 검사). **live 확인 여부 플래그는 API·Generator에 전달되지 않는다.** 그래서 POL_GOV_059의 시청 첫 화면은 "신청 URL"로 그대로 나간다. 이번 실제 LLM은 "일반 신청 링크로 보기 어렵다"고 스스로 피했지만 구조적 보장은 없다 → 데이터·API 담당 확인 항목.

### 6-2. 8가지 시나리오 (TestClient, 실제 generator 검증 경로, LLM 출력 고정)

| # | 시나리오 | 정책 | 결과 |
|---|---|---|---|
| 1 | 신청 URL 있음 | POL_GOV_065 | 온라인 신청 안내 **유지** PASS |
| 2 | 신청 URL null | ICN | 웹 신청 단정 → "직접 신청 링크는 확인되지 않았어요." PASS |
| 3 | 공고 URL만 | ICN(NOTICE_2026) | "신청 공고는 홈페이지에서 확인" 유지 + 접수 합의 문구 PASS |
| 4·5 | 기관 홈페이지만 / 신청+공고 둘 다 | POL_GOV_059 | 값 그대로 전달, 새 주소 생성 없음 PASS. 단 §6-1의 데이터 확인 필요 |
| 6 | 다른 정책 URL 인용 | ICN에 GOV_065 주소 | 짧은 URL은 보정 PASS. **112자 실제 URL은 보정 안 됨 → FAIL(xfail)** |
| 7 | 방문만 가능 | POL_GOV_016("온라인/우편 접수 불가 (현장 방문 접수만 가능)") | 온라인 신청 단정 보정 PASS. 릴리스에 "방문 신청 불가" 원문 정책은 없음(기존 #103 합성 테스트로 확인) |
| 8 | 접수 상태 unknown | ICN | "상시 신청 가능" 제거·합의 문구 PASS. **링크 단정과 함께 나오면 접수 합의 문구 2회 → FAIL(xfail)** |

**D-6 원인:** `backend/rag/application_links.py`의 `LINK_CLAIM`이 `(?:홈페이지|https?://|온라인).{0,100}(?:신청|접수)`로 URL 시작부터 '신청'까지 100자 이내만 본다. POL_GOV_065 실제 주소는 112자라 문장이 신청 주장으로 잡히지 않는다. 다른 정책의 긴 신청 주소가 null 정책 안내에 섞여도 그대로 응답된다(#103 "정책 간 신청 링크 혼용 금지" 위반 가능). 최신 dev에도 있던 동작이고 이번 변경으로 생긴 것이 아니다.

**D-8 원인:** `align_application_links`가 접수 문구를 붙일지 정할 때 원래 답변에 합의 문구가 없으면 붙인다. 그 뒤 `ensure_unknown_reception_notice`가 "상시 신청 가능" 절을 합의 문구로 바꿔 같은 문구가 두 번 남는다. PR #152 §14 "같은 정책 보정 문장은 한 번" 후속.

## 7. 문서·청크·Citation

| 항목 | 결과 |
|---|---|
| 릴리스 전체 청크 계보 | 672청크 ID 중복 0, 273문서 모두 청크 있음. 모든 청크의 policy_id = 문서 policy_id, source_url = 문서 source_url, `SUPPORTED_BY(정책→문서)` 존재 — **PASS(신규, 전수)** |
| 복수 정책 표시 번호 재배치 | Context [5]·[2] → 표시 [n] → citations[n-1]의 문서·URL이 원래 Context 문서·청크와 같음 — PASS. citations 배열을 뒤집는 오류 주입 시 실패 확인 |
| 정책명과 인용 문서 불일치 | "승촌보 캠핑장 이용요금 감면은 … [1]"에서 [1]이 인천 조례인데 HTTP 200, 그대로 응답 — **FAIL(xfail)**. Generator `_validate_model_response`와 Pipeline 어디에도 문장 정책명↔인용 정책 대조가 없다(pr-prep/23의 `1b9fa52` 검증은 dev에 없음) |
| 공개 Citation 계약 | `title,url,document_id,page_number`만 있음. chunk_id·retrieved_at·content_hash 없음 → E2E-03-3 **BLOCKED(계약)**, 계약 변경하지 않음 |
| 카드 source_url | 인천은 조례 URL(첫 채택 문맥). 같은 정책 문서라 혼용은 아님, 답변 인용 문서와 다를 수 있음(기존 설계, matrix §13-2와 같음) |

인용 번호가 같다고 같은 문서로 보지 않고, Generator가 받은 Context의 source_id → 문서 → 청크로 되짚어 비교했다.

## 8. 실제 LLM (gpt-5.4-mini, 7회, 동결 릴리스 문맥)

증거: [llm-live-frozen.json](assets/issue79-18-90-e2e-20261127/llm-live-frozen.json). 실행 스크립트 `tests/manual/verify_issue79_18_90_llm_frozen_release.py`. 키 값은 기록하지 않았다(파일에 `sk-` 0건 확인). Neo4j·Qdrant 실검색이 아니므로 "실제 R3D E2E"로 세지 않는다.

| 시나리오 | HTTP | 자동 판정 | 의미 판정(사람) |
|---|---|---|---|
| 인천 18세 | 200 info | 신청 URL null 유지, 타 정책 URL 0, 합의 문구 2종 있음, 인용 같은 정책 | 18세 포함 오안내 없음 PASS / 19~39 공고 기준 미언급 → 충돌 설명 부족(관찰). 링크 미확인 문장이 LLM 문장과 보정 문장으로 두 번 |
| 인천 40세 | 200 info | 같음 | 나이 결론 PASS(#152 보정 동작). 링크·접수 안내가 보정 문장 + LLM 문장으로 반복(관찰) |
| 인천 미입력 | 200 info | 같음 | 19~39·18~39 차이 설명 PASS. 반복 관찰 |
| 세종 행정인턴 링크 | 200 answered | 값 보존(시청 첫 화면), 타 정책 URL 0 | 첫 화면을 신청 링크로 안내하지 않음 PASS. 단 API 종합 **eligible**(25세, 지역 미입력) — 세종 주민등록·재학 조건이 모델링되지 않은 데이터 문제(관찰) |
| 세종 위원회 링크 | 200 info | 합의 문구 2종, 상시 단정 없음 | PASS |
| 방문만 가능(GOV_016) | 200 answered | 온라인 단정 없음 | PASS(방문 접수 안내 유지) |
| 보호자 45세(GOV_042) | 200 info | API 나이·종합 ineligible | LLM은 "자녀 나이 확인 전 최종 판단 불가" — API와 의미 불일치. §4-3 데이터 문제와 같은 원인 |

잘못된 연령 확정 안내 0/7, null URL을 신청 주소로 안내 0/7, 정책 간 URL·출처 혼용 0/7. 보정 문장 반복 3/7(인천)은 Pipeline 문구 정리 후속이다.

## 9. 테스트 결과

| 실행 | 결과 |
|---|---|
| PR #140·#152·#103·#39·#78·#40·공식 요건·정책 목록 관련 31개 파일 (최신 dev, 수정 전) | **701 passed, 1 skipped, 333 subtests passed** (21.4초) |
| 신규 `tests/integration/test_issue79_18_90_policy_source_e2e.py` | **62 passed, 6 xfailed** (약 2초) |
| 신규 테스트 오류 주입 5종 (테스트 프로세스 안에서만) | 상한 미포함 7 FAIL · 상한 초과 eligible 9 FAIL · 링크 보정 제거 4 FAIL · 접수 보정 제거 1 FAIL · 인용 순서 뒤집기 1 FAIL — 모두 검출 |
| xfail 6건 `--runxfail` | 6건 모두 의도한 assertion에서 실패(예외·환경 오류 아님) |
| 전체 `pytest tests/unit tests/integration` (신규 파일 포함) | **3,427 passed, 130 skipped, 6 xfailed, 실패 0**, 17,861 subtests passed (225초). 실행 도중 신규 파일의 신청 URL 장부 테스트에 검수 파일 assertion을 더했고, 최종본은 따로 62 passed·6 xfailed 확인 |
| `py_compile` (신규 2파일) | PASS |
| 실제 LLM | 7/7 HTTP 200 (§8) |
| 실제 R3D | **BLOCKED(환경)** |

실패 분류: 신규 코드 오류 0. 기존 dev에서도 있는 동작 3(D-6 긴 URL, D-8 접수 문구 반복, 정책명↔인용 불일치). 데이터 품질 확인 필요 3+3(§4-3 xfail 3, 보고만 3) + 신청 URL 미확인 3. 공식 근거 미확인: 학년 환산 2. 환경: R3D 미기동.

## 10. #122 E2E 항목 전달

| ID | 이번 판정 | 근거 |
|---|---|---|
| E2E-03-2 (판정↔답변) | **CONDITIONAL** | 구조화 판정 전달 PASS(53+10정책·인천). 실제 LLM 인천 연령 오확정 0/3. 남은 것: GOV_042 의미 불일치는 데이터(§4-3), 인천 18세 충돌 설명 부족 |
| E2E-03-3 (source_url·retrieved_at·content_hash) | **BLOCKED(계약)** + 릴리스 계보 PASS | 672청크 전수 계보 PASS. 공개 Citation에 chunk/hash/timestamp 없음 |
| E2E-05-6 (인천) | **PASS(데이터 반영)** / #90 종료는 승인 대기 | 근거가 2026 공고 문서이고 카드·답변 인용에 나타남. 실제 R3D 재대조는 BLOCKED |
| E2E-06-2 (POST 계약 필드) | **PASS(TestClient)** | 신청 URL null·값 보존, exclusion_reasons null 규칙, 접수 상태. 실제 HTTP 서버 미실행 |

## 11. 이슈별 결론

- **#79:** 신청자 AGE 93정책 전부를 분류했고 63정책(53+10)은 독립 기대값으로 Checker·API까지 PASS. 수혜자 나이·OR 조건 후보 3건(xfail)과 학년 환산 2건 확인 필요 → **미완료(데이터 확인 대기)**. 이슈 본문의 최종 승인 체크도 미체크.
- **#18:** 신청 URL null 보존·원문 URL 복사 0·8 시나리오 중 6 PASS. 미확인 신청 URL 3건(특히 세종 시청 첫 화면), 긴 URL 보정 누락, live 플래그 미전달 → **미완료**.
- **#90:** 데이터 반영은 릴리스에서 확인, 데이터 담당 승인·R3D 적재 확인·조례 제2조 없음 → **미완료(승인 대기)**.
- **#122 전달:** 가능. 위 §10 표와 xfail 6건을 그대로 넘긴다.

## 12. 미해결 사항 (담당별)

**데이터(반석·민혁)**
1. POL_GOV_042·022(·036): 수혜 아동 나이를 신청자 AGE에서 분리할지(SPECIAL_TARGET 등) 결정.
2. POL_GOV_059: "청년 또는 세종시 소재 대학 재학생" OR과 세종 거주 조건 모델링. 현재 25세 지역 미입력도 종합 eligible.
3. POL_GOV_029·049: 학년 기준을 나이로 환산해 자동 판정해도 되는지.
4. 신청 URL 미확인 3건(GOV_059·YC_009·STG_SEOUL_001) 접수 화면 확인(#18 남은 신청 주소).
5. #90: 승인 기록, 공고 원본 해시 재확인, 조례 제2조 수록 여부, R3D 적재 값 확인. matrix §14 "검수 대기" 문구 정정.

**API·Pipeline(지인·민지)**
1. `LINK_CLAIM` 100자 제한으로 긴 타 정책 신청 주소가 보정되지 않음(D-6, 재현 테스트 있음).
2. 접수 합의 문구 2회(D-8), 실제 LLM에서 링크·접수 문장 반복 3/7.
3. `live_application_page_verified=false` 신청 URL을 API·Generator에 구분해 전달할지 결정(계약 변경 필요 시 팀 합의).

**LLM·Generator(소희)**
1. 문장의 정책명과 인용 정책 대조 검증이 dev에 없음(재현 테스트 있음).
2. 인천 18세: 공고 19~39 기준을 함께 말하도록 할지(현재는 오확정 없이 "확정 어려움").

## 13. 재현 명령

```powershell
Set-Location -LiteralPath 'C:\dev\project\SKN36-3rd-1Team\issue79-18-90-policy-source-e2e'
$py = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe'
$env:GIT_CONFIG_COUNT = '0'
& $py -m pytest tests/integration/test_issue79_18_90_policy_source_e2e.py -q -p no:cacheprovider -rxX
& $py -m pytest tests/unit tests/integration -q -p no:cacheprovider
# 외부 호출(승인 필요): 실제 LLM 7회
& $py tests/manual/verify_issue79_18_90_llm_frozen_release.py --env-file <.env 경로> --output <새 JSON 경로>
```

`GIT_CONFIG_COUNT=0`은 PR #140 보고서에 적힌 실행 환경의 Git 설정 변수 문제를 피하려고 이 프로세스에만 넣었다.
