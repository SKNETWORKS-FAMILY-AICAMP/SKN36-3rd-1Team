# #122 후속 — Checker·Pipeline·API 판정 전달 추가 검증 (2026-11-27)

## 1. 검증 목적

PR #148(실제 R3D·LLM 판정 전달 기록)에서 **미검증으로 남은 시나리오만** 최신 dev에서 추가 검증한다.
#40 기능(UNKNOWN AND 범위 보류, PR #94)과 #78 제외 사유 전달은 이미 병합되어 있으므로 다시 구현하지 않았다.
서비스 코드(backend·database·frontend)는 수정하지 않았다.

## 2. 기준 환경

| 항목 | 값 |
|---|---|
| 작업 브랜치 | `test/122-checker-pipeline-api-consistency-followup` (신규, origin/dev에서 생성) |
| Worktree | `C:\dev\project\SKN36-3rd-1Team\issue122-consistency-followup` (신규) |
| 기준 dev SHA | `02659e223d1f30d43158f591bd8bc9a4ca9e0824` (PR #139 병합, 2026-11-27 21:16 KST) |
| 데이터 릴리스 | `data/releases/v1.4.0-candidate`, manifest SHA256 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da` |
| Python | 기존 프로젝트 `.venv` (패키지 설치·모델 다운로드 없음) |
| 실제 R3D | Qdrant `127.0.0.1:56756`, Neo4j `127.0.0.1:56787`, Ollama `127.0.0.1:11434`, 프로필 `b2_06b_v140`. 노드 1147·관계 2702·B2 Point 1249, 실행 전후 동일 |
| 실제 LLM | `gpt-5.4-mini` (기존 OpenAI 설정), 3회 호출, 오류 0, timeout 45초, 출력 상한 1600 tokens |

PR #148(`test/122-r3d-llm-e2e-consistency-latest`, head `0587000`)은 Open·미병합 상태이며 이 브랜치에 포함하지 않았다.
PR #148 브랜치·보고서·증빙 JSON은 읽기만 했고 수정하지 않았다.

## 3. 기존 증빙 (과거 결과 — 이번 실행 수치에 합산하지 않음)

| 출처 | 상태(2026-11-27 GitHub 조회) | 이번 작업에서의 사용 |
|---|---|---|
| #40 | Closed (2026-11-21) | 재종료하지 않음. Mock 19/19 PASS는 과거 증빙으로만 인용 |
| PR #94 | Merged (`df6503d`, 2026-11-21) | 소득 UNKNOWN 필수 AND 예외 규칙을 그대로 검증 기준으로 사용 |
| #102 / PR #131 | Merged (2026-11-25) | 정적 품질 플래그(릴리스 재생 계층에서 동일 로더 사용) |
| PR #139 | Merged (2026-11-27, 현재 dev에 포함) | child_age_months는 이번 범위 밖(중복 검증 안 함) |
| PR #148 | Open, head `0587000` (GOV059 리뷰 재확인 중) | 결과를 **확정된 최신 결과로 취급하지 않음**. 미검증 목록만 참고 |
| #122 | Open | 완료 기준 일부만 다룸(§11) |

PR #148이 보고한 값(참고): R3D Smoke 13 PASS, R3D+Mock 11 PASS, 실LLM 3건 중 판정 의미 2 PASS/1 FAIL(GOV042/45세),
Checker→Pipeline→API 상태 배열 불일치 0건. PR #148이 미검증으로 남긴 항목: 명확한 eligible 양성 사례, 확정 NOT 제외 사유 전달,
전체 품질 보류 전용 사례, 다중 정책 인용 혼용, 선택 OR 불충족, Condition ID 추적.

## 4. 검증 계층 구분

| 기호 | 계층 | 데이터 | DB/LLM 호출 |
|---|---|---|---|
| F | Fixture | 가상 정책(sample_only). 실제 정책 기준 아님 | 없음 |
| R | 릴리스 파일 재생 | v1.4.0-candidate JSONL의 실제 조건 트리 + 정적 품질 플래그, 근거 청크만 가상 | 없음 (실DB 검증 아님) |
| D | 실제 R3D | Qdrant·Neo4j·Ollama 읽기 전용 검색 | §8 |
| L | 실제 LLM | gpt-5.4-mini | §8 |

모든 F·R 요청은 `FastAPI TestClient → run_ask_pipeline → 실제 check_eligibility → Generator 대역` 경로로 흐른다.
Generator 대역이 받은 `policy_checks`를 Pipeline 단계 관측값으로 사용했다.

## 5. 신규 검증 시나리오와 결과 (F·R)

파일: `tests/integration/test_issue122_consistency_followup.py` (오라클: `tests/integration/issue122_followup_oracles.py`)

각 사례에서 단계별로 비교한 값: Policy ID, 개별 조건 행(field·status·순서), 조건 그룹 행, 사유 원문,
종합(마지막 eligibility 행), exclusion_reasons(Pipeline=API, 종합 ineligible 아니면 null), 보류 안내, Citation 문서 소속.

| # | 시나리오 | 계층 | 정책 / 입력 | Checker | Pipeline | API | 결과 |
|---|---|---|---|---|---|---|---|
| 1 | 필수 AND 명확한 불충족 | F | 가상 AND[19~39] / 45세 | ineligible | ineligible | ineligible, 제외사유 null | PASS |
| 1 | 명확한 충족(양성) | F | 가상 AND[19~39] / 30세 | eligible | eligible | eligible, 보류 안내 없음 | PASS |
| 1 | 필수 AND 불충족·충족 | R | POL_GOV_024 AND[19~39] / 45세·30세 | ineligible·eligible | 동일 | 동일 | PASS |
| 2 | 선택 OR 일부 불충족 | F | AND[19~39, OR[19~24, 특수대상]] / 30세 | needs_check (OR 하위 age 행 ineligible 보존) | 동일 | 동일 | PASS |
| 2 | 소득 UNKNOWN + 선택 OR 전부 불충족 | F | AND[19~64, UNKNOWN소득, OR[19~24, 60~64]] / 30세 | needs_check (OR 불충족으로 종합 확정 안 함) | 동일 | 동일 | PASS |
| 2 | OR(소득) 보류 / 필수 나이 불충족 | R | POL_GOV_030 / 20세·30세 | needs_check·ineligible | 동일 | 동일 | PASS |
| 3 | 소득 UNKNOWN + 필수 나이 불충족 | F | AND[19~39, UNKNOWN소득] / 45세 | ineligible (income 행 needs_check) | 동일 | 동일 | PASS |
| 3 | 소득 UNKNOWN + 나이 충족 | F | 동일 / 30세 | needs_check | 동일 | 동일 | PASS |
| 4 | 전체 품질 보류(affects=all) | F | NOT[40세 이상] + GLOBAL_HOLD / 45세 | needs_check, 확정 행 0 | 동일 | 동일, 제외사유 null (대조군은 ineligible+사유) | PASS |
| 4 | 소득 UNKNOWN + 전체 보류 경고 | F | AND[19~39, UNKNOWN소득] + all 경고 / 45세 | needs_check (#40 예외 비활성) | 동일 | 동일 | PASS |
| 4 | Pipeline 신뢰 경계 | F | 보류 정책에 확정 ineligible+사유를 주입 | ineligible(주입값) | needs_check, 사유 null | needs_check, 사유 null | PASS |
| 4 | 전체 품질 보류 | R | POL_BOK_004 AND[15~40], affected all / 45세 | needs_check | 동일 | 동일 | PASS |

R 계층 주의: 릴리스 파일 재생은 정적 품질 플래그만 반영한다. 실제 R3D 검색은 검색 시점 지역 경고를 추가로 붙이므로
POL_GOV_024·POL_GOV_030의 실제 서비스 종합값은 §8처럼 needs_check다. R 계층의 ineligible·eligible은 정적 데이터 기준 전달 일관성 결과다.
| 5 | NOT 제외 조건 해당 | F | AND[19~120, NOT[OR[75세 이상]]] / 80세 | ineligible + 사유 1 | 동일 사유 | 동일 사유 | PASS |
| 5 | NOT 제외 조건 비해당 | F | 동일 / 30세 | eligible | 동일 | 동일, 사유 null | PASS |
| 6 | 종합 needs_check + 사유 null | F | NOT[OR[75세 이상, 미확정 대상]] / 30세 | needs_check | 동일 | 동일, 키 유지·값 null | PASS |
| 7 | 다중 정책 5건 동시 | F | eligible·ineligible·#40보존·needs_check·제외확정 / 45세 | 정책별 상이 | 정책별 동일 | 정책별 동일, 사유는 제외확정 정책에만 | PASS |
| 7 | 다중 정책 4건 | R | GOV_024·GOV_042·BOK_004·GOV_030 / 45세 | i·i·n·i | 동일 | 동일, 사유 0 | PASS |
| 8 | Citation 정책 소속 | F | 5개 정책 각 문장 인용 | — | — | 재번호 후 [n]→해당 정책 문서, 문의처·source_url 자기 정책 | PASS |
| 8 | 다른 정책 출처로 자격 단정 | F | "가상필수나이정책은 신청 자격이 있습니다 [eligible 정책 출처]" | — | — | 지칭 정책 판정(ineligible)으로 교정 | PASS |
| 9 | 일치하는 ineligible 안내 | F | "포함되지 않아 신청 대상이 아닙니다" | ineligible | ineligible | 원문 유지, 의미 일치 | PASS |
| 9 | ineligible에 자격 있음 단정 | F | "신청 자격이 있습니다" | ineligible | ineligible | INELIGIBLE 안내로 교정 | PASS |
| 9 | **ineligible을 '확정 불가'로 안내** | F | 소득 UNKNOWN + 45세, "전체 신청 자격은 확정할 수 없습니다" | ineligible | ineligible | ineligible, **답변 원문 그대로 유지** | **FAIL (XFAIL 기록)** |
| 10 | 근거 불명확 조건 보수 안내 | F | AGE EQUAL "청년" / 30세 + "신청 자격이 있습니다" | needs_check(원문 확인) | 동일 | 동일, ELIGIBILITY 안내로 교정 | PASS |

판정 상태 전달 불일치(Checker·Pipeline·API 구조값): **0건**.
판정 의미 불일치(답변 안내): **1건** — 시나리오 9 '확정 불가' 안내.

### 의미 불일치 상세 (시나리오 9)

- 구조값: Checker·Pipeline·API 모두 종합 ineligible, 나이 행 ineligible, 소득 행 needs_check, exclusion_reasons null.
- 답변: "다만 소득 기준은 추가 확인이 필요해 전체 신청 자격은 확정할 수 없습니다 [1]"이 최종 API answer에 그대로 남는다.
- 원인: `backend/rag/pipeline.py`의 `_claims()`는 `HEDGED_CLAIM`(추가·확인 등)이 있으면 단정이 아닌 것으로 보고 교정 대상에서 뺀다.
  확정 ineligible을 '미확정'으로 낮추는 유보 문장은 현재 교정 규칙의 대상이 아니다.
- PR #148의 실LLM GOV042/45세 관측과 같은 유형이다. 이번에는 Fixture 재생으로 **최신 dev(02659e2)에서도 동일 경로가 남아 있음**을 확인했다.
  실제 LLM이 이 문장을 다시 생성하는지(재현 빈도)는 Fixture로 판단할 수 없다.
- 테스트는 기대값을 완화하지 않고 `unittest.expectedFailure`로 기록했다. 서비스 수정 후 통과하면 unexpected success로 드러나므로 데코레이터를 제거한다.
- 서비스 코드 수정은 이번 범위(검증)에서 하지 않았다. 후속 담당 판단 필요(§10).

### 단계별 비교 중 확인한 계약상 한계

- **Condition ID**: 현재 `ConditionResult`(Checker 출력·API 스키마)에 condition_id가 없다. 행 순서·field로 ID를 추정하지 않았다.
  Condition ID 단위 추적은 **검증 불가(계약 미지원)**로 기록한다. 새 API 구조는 추가하지 않았다(팀 합의 유지).
- **eligible 의미**: Checker eligible은 확인 가능한 조건 충족일 뿐 전체 자격 보장이 아니다. Pipeline은 LLM의 '자격 충족' 단정을
  `PARTIAL_ELIGIBLE_NOTICE`로 낮춘다. 오라클은 eligible 정책의 '추가 확인 필요' 안내를 불일치로 보지 않는다.
- **접수 상태**: Fixture의 `상시신청`은 현재 dev에서 reception unknown으로 판정되어 "현재 접수 여부는 공식 공고에서 확인해 주세요."가 덧붙는다(#103 규칙).
  판정 보류 안내(`HOLD_NOTICE_TEXT`)와 다르며 이번 판정 비교 대상이 아니다.
- **실데이터 재현 불가 시나리오**: 릴리스 249개 정책 오프라인 스캔 결과 NOT 그룹은 POL_005 1건뿐이고 품질 보류 상태라
  확정 NOT 제외 사유는 실데이터로 재현할 수 없다. OR 하위 조건도 소득·특수대상 등 Checker가 needs_check로만 판정하는 유형이라
  '선택 OR 경로의 확정 불충족'은 실데이터로 재현할 수 없다. 두 시나리오는 F 계층 결과만 있다.

## 6. 회귀 테스트 (최신 dev 02659e2 + 신규 테스트, 이번 실행)

| 테스트 | PASS | FAIL | SKIP | XFAIL | 비고 |
|---|---:|---:|---:|---:|---|
| 신규 `test_issue122_consistency_followup.py` | 23 | 0 | 0 | 1 | subtests 9 PASS. XFAIL=시나리오 9 의미 불일치 |
| 대상 묶음(#40·조건 그룹·Checker·Pipeline·API·#78·#39 + 신규) | 339 | 0 | 1 | 1 | subtests 177 PASS. SKIP=gov_048 원본(gitignore) 부재 |
| 전체 `tests/unit tests/integration` | 3388 | 0 | 130 | 1 | ERROR 0, subtests 17870 PASS, 4분 10초 |

대상 묶음: `test_unknown_and_scoped_hold`, `test_unknown_and_scope_safety`, `test_issue40_alias_consistency`, `test_issue40_r2_fullscan`,
`test_issue40_r2_realdata`, `test_checker_quality_regression`, `test_eligibility`, `test_issue78_exclusion_reasons`(unit),
`test_ask_api`, `test_rag_pipeline`, `test_issue78_exclusion_reasons_api`, `test_issue39_child_age_api`, 신규 파일.

### 기존 dev 대비 신규 실패

- 신규 실패 **0건**. 이 브랜치의 변경은 테스트·manual·보고서 파일 추가뿐이며 서비스 코드 diff가 없다.
- 전체 회귀에서 FAIL·ERROR 0. PR #148 보고서(dev 8c40858)의 31 FAIL/7 ERROR는 당시 환경(임시 git exit128 등) 요인으로 분류됐으며 이번 환경에서는 재현되지 않았다.
  `test_regenerated_doc_gov_048_...`은 현재 dev에서 원본 부재 시 SKIP 처리된다(실패 아님, 미검증).
- 실행 명령(Worktree 루트):

```powershell
& 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe' -B -m pytest tests/integration/test_issue122_consistency_followup.py -q -rxX
& 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe' -B -m pytest tests/unit tests/integration -q
```

## 7. 실데이터(오프라인) 후보 선정 근거

릴리스 JSONL로 249개 정책을 `database.hybrid_search`와 같은 트리·품질 계산으로 재구성해 Checker를 나이 7개 값으로 실행했다(DB 미사용).
보류 없는 정책 22개, 확정 제외 사유가 나온 정책 0개. 선정:

- POL_GOV_024 청년 사업자 임대료 지원: AND[AGE 19~39], 보류 없음 → 필수 AND 불충족(45세)과 eligible 양성(30세).
- POL_BOK_004 청년내일저축계좌: AND[AGE 15~40], affected_attributes에 all → 전체 품질 보류(45세).
- POL_GOV_030 위기청소년 특별지원: AND[AGE 9~24] + OR[소득 3종], 보류 없음 → OR 보류(20세)·필수 나이 불충족(30세).
- PR #148에서 이미 다룬 GOV042·GOV039·POL_005·GOV050·GOV011·GOV059·GOV016은 반복하지 않았다.
  GOV007 17세는 PR #148에서 Mock으로만 실행됐으므로 이번에 실제 LLM 사례로 추가했다(§8).
- 위 선정은 정적 플래그 기준이다. 실DB에서는 GOV_024·GOV_030에 검색 시점 지역 경고가 붙어 종합이 needs_check였다(§8.1).

## 8. 실제 R3D·LLM 검증

작업 시작 시 R3D가 꺼져 있었고(Docker 미실행) 컨테이너를 임의로 기동하지 않았다. 사용자가 직접 기동한 뒤 읽기 전용으로 실행했다.

- recorder: `tests/manual/verify_issue122_followup.py` (결과 exclusive-create, 전후 노드·관계·Point 수 비교, 예외는 타입만 기록)
- runner: `tests/manual/run_issue122_followup_readonly.ps1` (기존 DPAPI 인증 파일을 프로세스 환경 변수로만 읽고 종료 시 복원)
- 증빙: `docs/reports/issue122-followup-r3d-mock-20261127.json` (SHA256 `41ec6326…a0a3`, Mock Generator 7건),
  `docs/reports/issue122-followup-r3d-llm-20261127.json` (SHA256 `8ed76099…bca38`, 실제 LLM 3건)
- DB 변경: 없음. 두 실행 모두 `counts_unchanged=true`(노드 1147·관계 2702·Point 1249). 건수 비교는 payload 전체 불변 증명은 아니다.
- LLM 사례 변경: 최초 승인안은 GOV024 45세·30세 + 다중 1건이었다. 실DB에서 GOV024가 region 경고로 종합 needs_check여서
  확정 ineligible의 안내 의미를 볼 수 없으므로, 사용자 승인을 받아 GOV024 30세를 POL_GOV_007 17세로 바꿨다(총 3회 유지).
  r8(GOV007 17세)은 LLM 실행에서만 돌렸고 Mock 실행 파일에는 없다(Mock 파일 생성 후 사례를 추가했다).
- 인증 정보 검사: 생성·변경 파일 7개에서 실제 DPAPI 인증값 일치 0건, `sk-`/Bearer 패턴 0건, 사용자 경로 0건.
  `.env` 값 대조에서 NEO4J_PASSWORD·QDRANT_API_KEY가 일치했으나, 해당 `.env` 값이 인증 파일명(`*.clixml`)이라 runner의 파일명과 겹친 오탐으로 확인했다.

### 8.1 Checker·Pipeline·API 비교 (D 계층)

| 사례 | 정책 / 입력 | 품질 보류(affected) | Checker | Pipeline | API | 행·사유 일치 | 제외 사유 | 인용 소속 |
|---|---|---|---|---|---|---|---|---|
| r1 | POL_GOV_024 / 45세 | region | needs_check (age 행 ineligible) | 동일 | 동일 | 일치 | null | 일치 |
| r2 | POL_GOV_024 / 30세 | region | needs_check (age 행 eligible) | 동일 | 동일 | 일치 | null | 일치 |
| r3 | POL_BOK_004 / 45세 | all (UNKNOWN 그룹) | needs_check (age 행도 보류) | 동일 | 동일 | 일치 | null | 일치 |
| r4 | POL_GOV_030 / 20세 | region | needs_check | 동일 | 동일 | 일치 | null | 일치 |
| r5 | POL_GOV_030 / 30세 | region | needs_check (age 행 ineligible) | 동일 | 동일 | 일치 | null | 일치 |
| r6 | 다중: 청년 임대료·월세 / 45세 | GOV_024 region, POL_009 all | 둘 다 needs_check | 동일 | 동일 | 일치 | null | 문단별 자기 정책 문서 |
| r7 | 다중: 장애인 자립 / 30세 | GOV_016·BOK_002·BOK_037·BOK_060 | 4건 모두 needs_check | 동일 | 동일 | 일치 | null | 일치 |
| r8 | POL_GOV_007 / 17세 | income (소득 UNKNOWN) | **ineligible** (#40 보존) | 동일 | 동일 | 일치 | null | 일치 |

D 계층 상태 전달 불일치: **0건**(8사례, 정책 13건). 모든 사례에서 HTTP 200, 정책 ID·행 배열·사유 원문·종합·제외 사유가 세 단계에서 같았다.

**새로 확인한 사실(팀 판단 필요)**: 실제 검색은 대부분 정책에 `SEARCH_REGION_REQUIRES_REVIEW`·`SUSPECTED_REGION_GRANULARITY_LOST`
(affects=region, blocks=true)를 붙인다. 현재 Checker는 #40 소득 예외 외의 범위 보류가 하나라도 있으면 모든 eligibility 행을 보류하므로,
**필수 AND 나이 불충족(GOV_024 45세, GOV_030 30세)도 종합 needs_check**가 된다. 나이 행 자체는 ineligible로 남는다.
이는 합의된 규칙(B·C)보다 보수적인 동작이며 규칙 위반은 아니다. 다만 '지역만 보류인 경우 명확한 필수 AND 불충족을 종합 ineligible로 둘지'는
PRD/팀 합의 사항이므로 이번 작업에서 바꾸지 않았다. 그 결과 실데이터에서 확정 ineligible을 재현할 수 있는 사례는 소득 UNKNOWN 예외 경로(GOV_007·GOV_042)와
지역 경고가 없는 일부 정책(GOV_011 등, PR #148)으로 좁아진다.

### 8.2 실제 LLM 의미 검증 (L 계층, gpt-5.4-mini 3회)

| 사례 | 구조 종합 | LLM 안내 요지 | 오라클 | 판단 |
|---|---|---|---|---|
| r1 GOV_024 45세 | needs_check (age 행 ineligible, region 보류) | "만 45세는 연령 기준에 맞지 않아 이 정책의 대상이 아닙니다", 접수 여부는 공고 확인 | 불일치 없음 | 나이 행과는 일치. 구조 종합(보류)보다 단정적 — 참고 관찰 |
| r6 다중 | GOV_024 needs_check / POL_009 needs_check(전체 보류) | GOV_024 "지원 대상이 아닙니다"[1], POL_009 "신청 자격을 충족한다고 보기 어렵습니다"·마감[2–5] | 해당 없음(다중) | 문단별 인용이 자기 정책 문서에만 연결(혼용 0). 접수 상태 unknown·closed와 일치. 두 정책 모두 구조 종합보다 단정적 — 참고 관찰 |
| r8 GOV_007 17세 | **ineligible** | "연령 요건은 충족하지 못합니다", 소득 세부 기준은 추가 확인, 접수 중(open과 일치) | 불일치 없음 | PASS. 종합 ineligible을 '전체 자격 확정 불가'로 낮추지 않음. 다만 '신청 대상이 아님'을 명시하지는 않음 |

- 실제 LLM 판정 의미 불일치(확정 ineligible → 미확정, 또는 보류 → 지원 가능 단정): **0건 / 3건**.
- Pipeline 교정 발생: 0건(세 사례 모두 원시 Generator 답변 = 최종 API 답변).
- 참고 관찰 2건(r1·r6): 구조 종합은 needs_check인데 LLM이 공식 원문 나이 기준과 입력 나이를 비교해 '대상 아님'으로 안내했다.
  r1은 Checker 나이 행(ineligible)과 일치하고, POL_009는 전체 품질 보류 상태에서 원문 기준을 인용한 유보 표현이다.
  §5 E의 정의(확정 판정을 미확정으로 낮춤)에 해당하지 않아 불일치로 세지 않았다. 지역 보류 정책의 안내 수준은 8.1의 팀 판단과 함께 정할 사항이다.
- 1회씩만 관측했다. 반복 안정성과 시나리오 9 결함(Fixture XFAIL)의 실제 재현 빈도는 미검증이다.

## 9. 다른 PR과 중복되는 범위

| PR/이슈 | 겹치는 부분 | 처리 |
|---|---|---|
| PR #148 | 실DB recorder 구조(TestClient + Checker/Generator 캡처), 실행기 인증 처리 | 미병합이므로 재사용 불가. 신규 시나리오 전용으로 별도 파일 작성. 같은 정책·나이 사례는 반복하지 않음 |
| #78 테스트 | NOT 제외 사유 전달 | 기존은 단일 정책 경계값 중심. 이번은 다중 정책 혼재·전체 보류 대조·신뢰 경계를 추가 |
| #80 조건 그룹 검증 | AND·OR·NOT 조합 | 그룹 계약 자체는 다루지 않고 단계 간 전달만 확인. 새 API 구조 추가 없음 |
| #79·#18·#90 | 연령·URL·출처 | URL·출처 원문 검증은 하지 않음. Citation은 정책 소속만 확인 |
| PR #139 | child_age_months | 범위 밖 |
| PR #156 (Open) | 실제 LLM 증거, 청크 계보 | 변경 파일 4개, 이번 파일과 경로 중복 없음 |
| PR #154 (Open) | React 월령 입력 | 변경 파일 10개, 중복 없음 |

2026-11-27 GitHub 조회 시 Open PR은 #148·#154·#156 3건이며, 세 PR의 변경 파일 중 이번 신규 파일과 같은 경로는 없다.

## 10. 미해결 사항

| 문제 | 구분 | 담당 후보 |
|---|---|---|
| 종합 ineligible을 LLM이 '전체 자격 확정 불가'로 안내해도 Pipeline이 교정하지 않음 (시나리오 9, PR #148 GOV042 동일 유형) | 서비스 후속 | #122 후속 / Pipeline 담당 (#40 의미 전달) |
| Condition ID가 Checker·API 출력에 없어 ID 단위 추적 불가 | 계약 결정 필요 | 팀 합의 (새 API 구조 추가 금지 합의와 충돌하므로 임의 추가 안 함) |
| 확정 NOT 제외·선택 OR 확정 불충족은 실데이터 재현 불가 | 데이터 | 데이터 담당 (POL_005 품질 보류 해제 여부 등) |
| 지역 범위 경고(region)만 있어도 필수 AND 나이 불충족이 종합 needs_check가 됨 (GOV_024·GOV_030 실DB) | 팀 합의 필요 | PRD §13 / #40 후속 판단 (규칙 임의 변경 안 함) |
| 지역 보류 정책에서 LLM이 원문 나이 기준으로 '대상 아님'을 안내하는 수준 (r1·r6 참고 관찰) | 안내 정책 | #122 / Generator 담당 |
| GOV059 OPEN_CLAIM 오탐, POL_005 문장부호 | PR #148 리뷰 범위 | PR #148·#152 (이번 작업에서 다루지 않음) |
| Django·React·브라우저·장애·모바일 E2E | #122 잔여 | #122 |

## 11. #122 완료 기준 충족 여부

**최종 판단: CONDITIONAL.** 판정 상태 전달 불일치 0건(F·R·D 전 계층), 실제 LLM 의미 불일치 0/3,
Fixture 기준 알려진 의미 결함 1건(시나리오 9, 서비스 미수정)과 팀 합의 필요 사항 1건(지역 보류 시 종합 판정)이 남는다.

**#122 완료 기준: 미충족.** 이번 작업은 #122 체크리스트 중 'LLM 답변·자격 판정·정책 ID·문서/청크 출처 정합성'과 'exclusion_reasons' 항목의
판정 전달 부분만 보강한다. Django SSR·React·404/503·모바일 등은 다루지 않았으므로 `Closes #122`를 쓰지 않는다.
