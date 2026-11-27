# Issue #122 조건부 연령 근거 보강 개발 보고서

2026-11-27 KST · `feature/122-generator-semantic-analysis` · PR 검수용

| 기준 | 값 |
| --- | --- |
| 개발 시작 HEAD | `51bc4ae32dfcc0c7add15316ecc737f3b6ce3612` |
| 구현 커밋 | `3f43feefff4c31c535faaf41c31fcc4ea6933e96` |
| 브랜치 상태 | PR 생성 전. 문서 작성 시 로컬 HEAD와 추적 중인 원격 브랜치 참조가 구현 커밋에서 일치 |
| 검증 대상 | 커밋 전 작업 트리에서 실행한 실LLM 증빙의 변경 파일 6개 SHA-256이 구현 커밋의 작업 트리와 일치 |

## 1. 작업 요약

상담 답변에 필요한 연령 근거가 검색 후보에는 있었지만 Generator 입력에서 빠지는 문제를 개선했다. 기존 상위 5개 근거를 보존하고, 이미 채택된 정책의 누락된 연령 근거가 조건과 문서 검증을 통과하면 요청당 최대 1개를 추가한다. 담당 범위는 RAG 근거 선택, Pipeline 연결, API 계약 및 관련 테스트다.

실DB+Mock 검증에서 국민내일배움카드의 핵심 제외 근거 포함률은 **0/8 → 8/8**, 기존 근거 보존은 **95/95**였다. 실제 LLM은 4건 모두 연령 제외 규정을 올바르게 설명했다. 원문 인용과 신청 절차까지 평가한 답변 품질은 **PASS 1건, 부분 충족 3건**이었다.

관련 자료는 통합 검증 이슈, 기존 실DB·LLM 비교 기준, 신청 링크·접수·나이 결론 보정, 제외 사유 전달 PR이다. [Issue #122](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/issues/122) · [PR #148](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/148) · [PR #152](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/152) · [PR #135](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/pull/135)

## 2. 기존 오류와 원인 분석

| 사례 | 확인한 구조화 판정과 답변 | 원인 분석 |
| --- | --- | --- |
| `POL_GOV_042`, 45세 | Checker와 Generator 입력의 나이·필수 AND 종합은 `ineligible`. 소득 등 일부 요건은 `needs_check`. 저장 Generator 답변은 나이 미충족을 설명하면서 전체 자격은 확정할 수 없다고 안내했다. | 종합 미충족은 전달됐다. 일반 자격 보정은 유보 표현을 직접 단정으로 분류하지 않고, 나이 보정은 방향이 이미 맞는 나이 문장을 유지했다. 저장 답변을 현재 경로에 재생해도 문제 문장이 남았다. 당시 모델 원시 응답은 확보하지 못했다. |
| `POL_005`, 76세 | 과거 동일 요청 2건에서 API는 종합 `ineligible`과 연령 제외 사유를 반환했지만 본문은 나이 기준에 포함된다는 방향으로 설명했다. 한 답변은 연령 제한 근거가 확인되지 않았다고 했다. | 현재 R3D에서 검색한 C003이 기존 상위 5개에 들지 못했다. 별도로 취업 조건 품질 보류가 적용되어 Checker 종합이 `needs_check`이고 `exclusion_reasons=null`이었다. 현재 상태에서 저장 답변 2건을 재생해도 잘못된 설명이 남았다. |

국민내일배움카드의 B2 문서 `DOC_WORK24_001`에서 **C003**은 지원 제외 목록의 제목과 ‘75세 이상인 사람’ 항목을 담고 있다. **C004**에는 제외 목록 후반과 지원 절차가 이어지고, **C001**에는 ‘국민 누구나’라는 일반 안내가 있다. 직접 연령 질문에서 C003은 검색 6위였고 기존 Generator 입력은 C004·C001·C008·C007·C005였다. 따라서 제외 목록의 머리말과 연령 항목이 빠지는 첫 단계는 검색 결과를 최종 context로 자르는 단계였다.

현재 Checker의 76세 나이 행 `eligible`은 NOT 아래에서 **제외 범위에 포함됨**을 나타낸다. 취업 조건 품질 경고 `EMPLOYMENT_NOT_MODELED`가 적용되면서 종합 판정은 보류됐고, 확정 종합 미충족에 필요한 제외 사유 수집 조건도 충족하지 못했다. 기존 나이 보정은 NOT/OR에 확정 연령 제외 사유가 없으면 방향을 단정하지 않으므로 이 입력에서는 작동하지 않았다. 과거의 확정 제외 사유가 포함된 checks로 같은 보정을 실행했을 때는 두 문장 모두 교정됐다.

과거 실패 기록의 기준은 `befe2d0`이고 현재 검증은 `51bc4ae` 위의 미커밋 구현으로 진행했다. 과거 실행의 모델 식별자·설정과 실제 품질 플래그 로딩 상태는 미확정이다. 과거와 현재의 판정 차이는 관찰됐지만 그 차이가 발생한 역사적 원인과 모델이 특정 문구를 선택한 이유는 확인하지 못했다. [과거 API 요청·응답](assets/issue122-befe2d0/ask-api.json) · [브라우저 회귀 기록](issue75-browser-regression-20261125.md) · [통합 검증표](issue122-e2e-verification-matrix.md)

## 3. 개선 대안과 설계 결정

| 대안 | 장점 | 비용과 위험 |
| --- | --- | --- |
| context 상한 일괄 확대 | 기존 선택 로직으로 더 많은 후보 확보 | 관측 표본의 9위 C003까지 포함하려면 상한을 9 이상으로 올려야 한다. 다른 질문에도 입력 길이와 관련 없는 본문이 늘어난다. |
| 기존 근거 교체 | 상한 5개 유지 | 기존 신청 방법 등 다른 근거를 잃을 수 있다. 교체 위치에 따라 출처 번호의 의미가 바뀐다. |
| 후보 재정렬 | 질문 목적에 따라 중요한 조건 근거를 앞세울 수 있음 | 기존 정책별 균형, 마감 정책 후순위와 출처 순서에 영향을 줄 수 있다. |
| Graph 조건과 원문 대조 | 정책·문서·조건까지 연결해 보강 후보를 좁힐 수 있음 | Graph에는 조건의 근거 문서가 있지만 청크 연결은 없다. 본문 경계와 포함·제외 문맥을 별도로 대조해야 한다. |
| 조건부 최대 1개 추가 | 기존 순서와 번호를 유지하고 누락된 경우에만 추가 | 해당 요청의 입력은 최대 6개로 늘고, 여러 정책이 보강을 필요로 해도 한 요청에서 1개만 추가한다. |

최종안은 **Graph 조건과 기존 검색 후보를 대조한 뒤 1개까지 추가**하는 방식이다. 원래 정책 순서와 신청 절차 근거를 유지하면서 누락된 조건을 보존할 수 있고, 추가 검색·적재 없이 적용할 수 있다.

## 4. 실제 구현

| 변경 파일 | 역할 |
| --- | --- |
| [backend/rag/context_builder.py](../../backend/rag/context_builder.py) | 연령 조건과 인용 가능한 원문을 대조하고 누락 근거를 1개까지 추가 |
| [backend/rag/pipeline.py](../../backend/rag/pipeline.py) | 기존 `select_contexts` 직후, 근거 기반 파생 필드 계산 전에 보강 호출 |
| [docs/api/ask_contract.md](../api/ask_contract.md) | v1.8의 선정 상한과 출처 번호·인용 계약 기록 |
| [tests/unit/test_condition_evidence_completion.py](../../tests/unit/test_condition_evidence_completion.py) | 경계, 문맥, 문서·정책 제한, 중복, 입력 보존 및 선택 순서 검증 |
| [tests/integration/test_condition_evidence_api.py](../../tests/integration/test_condition_evidence_api.py) | 실제 Generator 검증 경로와 FastAPI 재번호, Checker 결과 및 파생 필드 검증 |
| [frontend/src/components/TurnView.sixSources.test.tsx](../../frontend/src/components/TurnView.sixSources.test.tsx) | 출처 6개의 링크와 제목 렌더링 검증 |

변경 전 흐름은 **검색 → 인용 후보 정렬 → Checker → 기본 5개 선정 → Generator → 인용 조립**이다. 변경 후에는 기본 선정과 Generator 사이에서 `complete_condition_evidence`가 같은 후보 목록을 사용한다.

1. 명시적 연령·자격·신청 가능 질문에 적용한다. 신청 방법·절차 질문은 유효한 나이 입력이 있을 때 대상이 된다. 제3자 연령이나 연령과 무관하다고 밝힌 질문은 제외한다.
2. 이미 채택된 정책의 `AGE/BETWEEN`만 사용한다. Checker의 유효 나이·구간과 상한 센티널 규칙을 재사용하고, 연령 제한을 확정할 수 없는 `0~120`은 보강에서 제외한다.
3. Graph 관계의 `evidence_document_id`와 후보 문서 ID가 일치하고 해당 문서의 검증 결과가 `PASS`여야 한다. 이미 채택된 근거에 같은 조건이 있는지도 동일 정책·문서 범위에서 확인한다.
4. 원문의 숫자와 이상·이하·초과·미만 방향을 구간으로 변환하고, 지원 대상 또는 제외 대상 문맥이 Graph의 위치와 일치하는지 검사한다. 불명확한 구간, 제3자·우대·서류·상담 문맥과 불완전한 조건 트리는 보수적으로 건너뛴다.
5. 채택 정책 순서와 후보 순위에 따라 첫 적합 청크를 추가한다. 기존 본문·순서·`source_id`는 유지한다. 기본 5개가 채워졌다면 새 번호는 6이다. 중복 청크는 제외하며 `limit=0`이면 기본 선택을 그대로 반환한다.

Checker는 보강 전에 계산하며 조건 배열·종합 판정·품질 경고·제외 사유 생성 규칙은 기존 코드를 따른다. 추가 청크의 원문에서 명시된 서류·문의처 등 파생 정보를 보완할 수 있으나, 실DB 21건에서는 비교한 파생 필드 차이가 없었다.

v1.8은 기본 context 5개와 조건부 최대 6개를 명시한다. API 응답 구조는 유지하고 실제 사용한 근거만 `citations`로 내보낸다. 예를 들어 Generator가 1번과 6번만 인용하면 API는 이를 1번과 2번으로 연결한다. FastAPI 및 React 6개 출처 표시를 테스트했다. Django 정책 상세는 별도의 `GET /api/policies`를 사용한다는 점을 코드에서 확인했다.

## 5. 코드 리뷰와 추가 보완

| 리뷰 항목 | 재현과 조치 |
| --- | --- |
| 연령 변환 경계 | `120세 초과`가 무상한 구간의 하한 121로 남았다. 변환 후 하한을 Checker 유효 범위로 검증했다. `0세 미만`은 기존에도 거부됐고, `119세 초과`와 `120세 이상`은 유효한 경계로 유지했다. |
| 섹션 문맥 | 제외 목록 뒤 참고사항·기타 안내 등에 이전 제외 문맥이 남았다. 별도 섹션의 시작에서 문맥을 초기화한다. |
| 한 줄에 복수 연령 | `19~34세, 별도 안내는 60세`에서 범위만 읽던 사례가 재현됐다. 범위 밖에 별도의 세 단위 숫자가 있으면 거부한다. |
| 제3자·상담·불확실 표현 | 동반인·가족, 상담·안내 서비스, 확인 필요 등 재현된 오매칭을 거부한다. 반대 방향이나 명시적 부정은 기존 거부 동작을 확인했다. |
| 마감 정책 우선순위 | 양쪽 정책 모두 보강 후보가 있는 경우는 순수 함수 테스트로 확인했다. 접수 중·마감 혼합, 마감 정책만 있는 경우, 정책명 직접 질문 예외는 실제 Pipeline에서 확인했다. 기존 선택 순서가 보존되어 우선순위 코드는 그대로 사용했다. |

수정 전 가상 픽스처에서 하위 실패 13개를 재현했다. 수정 후 해당 단위 범위는 **14 passed, 56 subtests passed**, 최종 Python 관련 범위는 **191 passed, 1 skipped, 577 subtests passed**였다. 통합 테스트는 6번 근거 본문, 인용 재번호, 판정과 출처를 단언하며 HTTP 200은 보조 확인으로 사용했다.

## 6. 검증 환경과 결과

데이터셋은 `data/releases/v1.4.0-candidate`, manifest SHA-256은 `b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da`다. R3D verify는 Neo4j `127.0.0.1:56787`, Qdrant `127.0.0.1:56756`을 사용했다. 검색 프로필은 `b2_06b_v140`, alias는 `exp_b2_qwen3emb06b_v140_v1`과 `exp_b3_qwen3emb06b_v140_v1`이다.

| 검증 시점과 범위 | 결과 |
| --- | --- |
| 구현 단계, 리뷰 최종 보완 전 실DB+Mock | 읽기 전용 21/21건 및 B2 청크·인용 경로 21/21건 통과. POL005 핵심 근거 0/8 → 8/8, 기존 근거 95/95 보존. 판정과 파생 필드 차이 0건. |
| 구현 단계, 추가 본문과 함수 시간 | POL005 8건 각 737자, 다중 정책 1건 294자로 총 6,190자 추가. 실DB 21건의 보강 함수 시간은 최소 0.088ms, 중앙값 1.686ms, 최대 4.401ms. 검색·임베딩·LLM 시간은 제외한 표본 측정이다. |
| 리뷰 최종 보완 후 저장 실검색 재생 | 보완 직전 구현과 최종 context·checks·API 결과 **19/19 동일**. POL005 8/8 보강과 기존 근거 19/19건 보존. 이 비교에서는 실DB를 다시 검색하지 않았다. |
| 리뷰 최종 보완 후 Python | **191 passed, 1 skipped, 577 subtests passed**. 기존 정부24 원본 `449000000108.json` 부재로 1건 skip. |
| 구현 단계 React 및 빌드 | **198 passed, 22개 테스트 파일**, TypeScript 타입 검사와 Vite 빌드 통과. 데이터 번들 크기 경고가 있었다. 이후 프론트엔드 변경이 없어 기존 결과를 재사용했다. |

실DB+Mock 21건과 최종 실LLM 4건 각각의 전후 건수는 Neo4j 정책 **249**·노드 **1,147**·관계 **2,702**, Qdrant B2 **1,249**·B3 **1,338**로 일치했다. Neo4j 읽기 전용 실행기와 Qdrant 읽기 메서드 제한을 사용했다. 건수 일치는 전체 데이터의 불변을 증명하는 검사는 아니다.

## 7. 실제 LLM 검증

공식 OpenAI API에 `gpt-5.4-mini`를 요청했고 네 응답 모두 `gpt-5.4-mini-2026-03-17`을 반환했다. PR #148의 비교 설정을 따라 최대 출력 1,600토큰, 타임아웃 45초, temperature 0, JSON 객체 형식을 적용했다. 실제 호출과 HTTP 200은 각 4회, 자동 재시도 및 fallback은 0회다. 공통 요청은 `selected_policy_id=POL_005`, 가상 지역 서울, 소득 null, 이력 `[]`이다.

| 사례 | 질문과 가상 나이 | 실제 최종 답변 핵심 | 판정 |
| --- | --- | --- | --- |
| L01 | 국민내일배움카드의 나이 기준을 충족하나요? · 76세 | 75세 이상은 제외 대상이고 76세는 그 범위에 포함되며 전체 자격은 추가 확인이 필요하다고 설명했다. 함께 나열한 생계급여 수급자 제외는 C004에 있는데 해당 청크를 인용하지 않았다. | 부분 충족 |
| L02 | 만 76세도 국민내일배움카드를 신청할 수 있나요? · 76세 | 76세가 원문의 제외 범위에 해당한다고 C003으로 설명했다. 구조화 종합 판정은 보류로 유지됐다. 연령 기준에 따른 결론 뒤의 최종 확인 문구는 더 명확히 연결할 여지가 있다. | PASS |
| L03 | 국민내일배움카드 신청 방법을 알려주세요. · 76세 | 모델과 Generator는 고용24 온라인 제출과 고용센터 방문을 안내했다. 최종 답변은 링크 보정에서 온라인 신청 절차가 빠졌고, 직접 링크 미확인 안내가 중복됐다. 방문 경로와 75세 이상 제외 설명은 남았다. | 부분 충족 |
| L04 | 국민내일배움카드의 나이 기준을 충족하나요? · 74세 | 74세는 75세 이상 제외 범위에 해당하지 않으며 전체 자격은 추가 확인이 필요하다고 설명했다. 함께 제시한 재학생 제외 조건의 근거 C004는 인용하지 않았다. | 부분 충족 |

| 사례 | C003 검색 순위 → Generator 번호 → 최종 API 인용 | 입력 / 출력 / 합계 토큰 | LLM / API 시간 |
| --- | --- | --- | --- |
| L01 | 6 → 6 → [2] | 8,927 / 225 / 9,152 | 3.77초 / 7.37초 |
| L02 | 7 → 6 → [3] | 8,927 / 208 / 9,135 | 3.65초 / 4.11초 |
| L03 | 9 → 6 → [5] | 8,923 / 410 / 9,333 | 3.20초 / 3.76초 |
| L04 | 6 → 6 → [4] | 8,929 / 280 / 9,209 | 2.93초 / 3.42초 |

네 사례 모두 기존 상위 5개를 보존하고 C003을 6번에 추가했다. 종합 `needs_check`, 품질 보류와 `exclusion_reasons=null`이 유지됐다. C003의 최종 인용은 모두 `DOC_WORK24_001`의 원문 URL로 연결됐으며 B2 청크·문서·번호 검증은 4/4 통과했다. 원시 응답과 Generator 반환의 답변 본문은 4/4 일치했다. 본문을 바꾼 Pipeline 보정은 L03의 신청 링크 보정이었다. **연령 제외 규정 설명은 4/4 정확했고 전체 답변 품질은 PASS 1건, 부분 충족 3건이다.** [고용24 원문 출처 URL](https://www.work24.go.kr/hr/h/a/1100/selectIssuGudn.do)

과거 L01과 같은 입력에서 관찰한 오안내가 이번 답변에서는 해소됐다. 코드와 품질 상태가 다르고 과거 모델 설정이 확정되지 않아 이 비교는 동일 조건 A/B 실험이 아니다. 관찰된 차이를 근거 보강만의 인과적 효과로 귀속할 수 없으며, 네 사례로 전체 정확도나 모델 안정성을 추정하지 않았다.

## 8. 남은 문제와 담당자 인계

| 담당 | 검토할 사항 |
| --- | --- |
| 소희 | GOV042에서 종합 `ineligible`과 답변의 전체 자격 미확정 표현이 충돌하는 문제. |
| 소희 | POL005 L01·L04의 부수 제외 사유를 해당 청크에 연결하는 인용 검증. L02는 연령 제외 결론과 추가 확인의 대상이 서로 분명하게 읽히는지 검토. |
| 지인 | L03의 링크 보정이 직접 신청 URL 확인과 온라인 신청 방법 설명을 구분하도록 검토. 온라인 절차를 보존하고 안내 중복을 방지하는 회귀 사례 필요. |
| 팀 | API v1.8의 최대 6개 근거 및 인용 호환성 검수. |

이번 PR 범위는 조건부 연령 근거 선정과 연결 계약 개선이다. **Issue #122 전체 종료는 후속 통합 검증 범위로 남는다.**

## 9. 한계와 후속 과제

- `AGE/BETWEEN`만 지원한다. 복합 표현과 새로운 섹션·주체 표현은 보수적인 문맥 검사에서 누락될 수 있다.
- Graph 조건 또는 검증을 통과한 근거 문서 연결이 없거나, 필요한 청크가 기존 검색 후보에 없으면 보강할 수 없다.
- 추가 청크의 전달과 답변 생성은 별도 단계다. 실제 모델 4회에서 확인한 결과를 다른 정책이나 표현에 일반화할 수 없다.
- Python은 관련 단위·통합 범위, React는 테스트와 빌드 범위를 검증했다. Python 전체 테스트, 현재 구현의 브라우저 E2E·Django 런타임, 공식 사이트의 최신 원문 재수집은 이번 검증 범위에 포함되지 않았다.

## 10. 실행 명령과 증빙 위치

아래는 검증에 사용한 명령이다. 이번 보고서 작성에서는 기존 실행 결과와 구현 커밋을 대조했다. 구현 변경 파일 6개는 4절에 정리했다.

```powershell
git show --stat 3f43fee
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = '.;tests;tests/unit;tests/integration'
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider `
  tests/unit/test_condition_evidence_completion.py tests/integration/test_condition_evidence_api.py `
  tests/unit/test_context_builder.py tests/unit/test_official_requirements.py `
  tests/integration/test_rag_pipeline.py tests/integration/test_issue78_exclusion_reasons_api.py `
  tests/unit/test_age_verdict_alignment.py tests/integration/test_age_verdict_api.py -q -rs
```

프론트엔드 실행 위치는 `frontend/`다.

```powershell
npm.cmd run test -- --cache=false
npm.cmd run build -- --outDir ../.cache/issue122-generator-semantic-analysis/frontend-build
```

실DB 재현은 지정 R3D verify 대상의 manifest·alias·건수를 먼저 확인하고 읽기 전용 세션에서 실행한다. 아래 로컬 스크립트는 승인된 암호화 인증 파일을 프로세스 환경에서 사용한다. 출력 경로는 신규 이름으로 지정한다. 실제 LLM 실행은 별도 승인 범위에서만 수행한다.

```powershell
& .cache/issue122-generator-semantic-analysis/resume-readonly.ps1 `
  -ScriptName verify_implementation.py -OutputName implementation-live-recheck.json
.venv/Scripts/python.exe -B .cache/issue122-generator-semantic-analysis/verify_implementation.py `
  --offline --out .cache/issue122-generator-semantic-analysis/review-offline-recheck.json
```

| 로컬 증빙 | 내용 |
| --- | --- |
| `implementation-live-reviewed.json`, `implementation-summary.json` | 실DB+Mock 21건, 청크 경로, 건수, 추가 문자와 시간 |
| `review-reproduction-before.txt`, `review-reproduction-after.txt`, `review-regression-tests.txt` | 수정 전 재현과 수정 후 관련 테스트 |
| `review-offline-19.json`, `review-final-verification.json` | 최종 보완 전후 저장 검색 결과 비교 |
| `implementation-frontend-tests-six.txt`, `implementation-frontend-build.txt` | React, TypeScript와 빌드 |
| `context-selection-live-llm-resumed-20261127.md`, `context-selection-live-llm-results-20261127.json` | 사례별 최종 답변, 원시 모델 응답, Generator·Pipeline·API 단계와 사용량 |

표의 증빙 및 재현 스크립트는 작성자의 로컬 `.cache/issue122-generator-semantic-analysis/`에 있으며 **GitHub 제출 파일에 포함되지 않는다.** 검수에 필요한 수치, 실제 답변 요지, 인용 연결과 실패 조건은 본문에 수록했다. 구현 커밋은 여섯 변경 파일과 관련 단위·통합·React 테스트를 포함한다. [구현 커밋 3f43fee](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/commit/3f43feefff4c31c535faaf41c31fcc4ea6933e96)
