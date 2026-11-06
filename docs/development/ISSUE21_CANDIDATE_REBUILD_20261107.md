# Issue #21 자격 조건 재구성 결과
> **[과거 결과 — 2026-11-07 갱신]** 이 문서는 `issue21-v1.4.0-20261107-final` 기준의 이전 단계 결과다. 현재 검토 기준 후보는 `data/candidates/issue21-v1.4.0-20261107-rag/`이며, 루트 UNKNOWN 수치(245 등)와 일부 판단은 이후 수정으로 바뀌었다. 최신 결과는 `docs/development/ISSUE21_FINAL_VERIFICATION_20261107.md` 참조.


확인일: 2026-11-07. 브랜치 `feature/21-eligibility-data-fix`, HEAD `a77c13c`.
입력 데이터 커밋 `0a09126`, 생성 규칙 커밋 `0645f90` 모두 유지.
DB/API 접근, 원문 재수집, 임베딩, 모델 다운로드, 브랜치 변경, 커밋·push·PR 수행 없음.

## 1. 검토 대상과 재현

최종 검토 대상은 **`data/candidates/issue21-v1.4.0-20261107-final/`**이다.
이는 `REVIEW_ONLY_NOT_FOR_DB` 상태이며 배포·DB 적재 승인본이 아니다.

주요 산출물:

- `processed/policies.jsonl`: 246개 정책의 복원 가능한 상세 요건 및 검토 상태.
- `processed/condition_relationships.jsonl`: 조건 그룹 추가 전의 관계.
- `neo4j/`: 기존 비조건 노드 보존, 조건 노드·그룹·지역 및 관계 재구성.
- `qdrant/payload_chunks.jsonl`: ID·본문 보존, 소득·지역·검토 메타데이터 갱신.
- `policy_changes.jsonl`: 246개 정책별 조건 관계·그룹 관계·지역 관계·조건 노드·메타데이터 전후 기록.
- `comparison.json`: 지표, 사용한 생성 코드의 SHA-256, 입력 manifest 검사, 본문 검사, 한계.
- `age_conflict_candidates.jsonl`: 명시된 연령 범위의 충돌 후보와 근거 문장.
- `validation.json`, `manifest.json`: 보존 대상 해시, 계약·참조·DAG 검사, 최종 파일 18개의 체크섬.

실행에 사용한 명령(PowerShell, 저장된 순수 Python 의존성만 사용):

```powershell
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages;$PWD;$PWD/tests/unit"
$issue21Python = 'C:/Users/minhyeokju/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $issue21Python -X utf8 scripts/rebuild_eligibility_candidate.py --output-dir data/candidates/issue21-v1.4.0-20261107-final
```

Python 3.12.14를 사용했다. 위 PYTHONPATH는 저장된 requests/bs4/pypdf를 가져오기 위한 이 작업의 환경 설정이다. gRPC/Qdrant 바이너리는 불러오지 않았다. 패키지를 설치·업그레이드하지 않았다.
재현 시에는 `--output-dir data/candidates/issue21-replay-새이름`처럼 **아직 없는 경로**를 사용한다. 이미 존재하는 출력이나 기존 릴리스 경로는 쓰기 전에 거부한다.

입력은 기존 정책 정제본 237개, staging 정책 9개, 정부24 교차 확인 자료 7개, 저장된 온통청년 정제본의 raw_attributes, 최신 후보의 정책·문서·청크·관계이다. 원본 API/PDF는 재수집하지 않았다. 저장된 값으로부터 복원할 수 없는 값은 새로 채우지 않았다.

실행 중 만든 `issue21-v1.4.0-20261107`, `issue21-v1.4.0-20261107-review`, `issue21-v1.4.0-reconstructed-20261107`은 중간 생성물이다. **비교·검토·후속 적재에는 사용하지 않는다.** 최종본에서 문서별 메타데이터 보존, 지표 산식, API 근거 문서 선택 및 검증을 보완했다. 중간 생성물은 보존했다.

## 2. 건수와 불변성

| 항목 | 기존 후보 | 신규 후보 |
|---|---:|---:|
| 정책 | 246 | 246 |
| 문서 | 261 | 261 |
| 청크 | 599 | 599 |
| 전체 노드 | 1,074 | 1,153 |
| 전체 관계 | 3,703 | 4,482 |
| 원자 조건 노드 | 52 | 81 |
| 조건 그룹 노드 | 352 | 391 |
| 지역 노드 | 18 | 29 |
| REQUIRES_CONDITION | 735 | 1,105 |

정책·문서·청크·Point ID 집합 유지. 문서 노드 JSONL은 바이트 단위 동일하다.
청크는 region, eligibility_attributes 중 income_level, eligibility_review_status 외의 모든 필드를 동일하게 유지했다. 문서별 law_article/source와 기존 정상 속성도 보존했다.
정책 그래프는 region, eligibility_review_status 외 모든 기존 속성이 동일하다. 확인된 신청 URL 22개 및 null 224개를 유지했다.
조건·그룹·지역 외의 관계도 원본과 동일하다. AVAILABLE_IN 건수는 247개로 동일하며 대상 지역 일부만 바뀌었다.

기존 data 및 미추적 실험 파일 등 156개 파일을 실행 전후 SHA-256으로 대조했고 변화가 없다. v1.3.2 인계본과 v1.4.0 릴리스는 수정하지 않았다. 원본 문서 본문 261개는 저장된 자료로 해시를 다시 비교했으며 불일치 0개, 본문에 포함되지 않은 청크 0개다.

## 3. 의미적 변화

| 지표 | 전 | 후 |
|---|---:|---:|
| 소득 5구간을 OR로 판정하는 정책 | 45 | 0 |
| 소득 5구간 관계를 보존하는 정책(논리 무관) | 45 | 52 |
| 무주택을 AND 그룹에 직결한 정책 | 47 | 0 |
| JA0410 관계가 있는 정책 | 0 | 56 |
| UNKNOWN 그룹이 하나라도 있는 정책 | 164 | 246 |
| 루트 UNKNOWN 정책 | 164 | 245 |
| 빈 NOT 그룹 | 1 | 0 |
| 명시적으로 needs_review가 기록된 정책 | 0 | 246 |
| needs_review 조건 노드 | 0 | 63 |
| needs_review 직접 조건 관계 | 0 | 1,071 |
| 상세 지역을 이번 규칙으로 복원한 정책 | 0 | 22 |
| 명시 연령 범위 충돌 후보 | 2 | 2 |

기존 needs_review 0은 해당 검토 표시가 없었다는 뜻이며, 검토할 문제가 없었다는 뜻이 아니다.
UNKNOWN 출처 분포는 정부24 7→72, 온통청년 50→66, 기타 107→108이다. 출처는 원천 source_type 및 구조화 요건으로 분류했다.
정부24 취업 조건이 추가된 정책 32개, 온통청년 연령 조건이 추가된 정책 37개, 온통청년 소득 조건이 추가된 정책 65개다.

기존 5구간 OR는 각 구간 중 하나만 맞으면 소득 분기를 충족시키는 의미였다. 모든 JA가 Y일 때 이 의미가 공식적으로 확인되지 않았으므로 관계를 남기고 그룹 논리를 UNKNOWN으로 변경했다. 신규 정부24 7개도 같은 방식으로 보존하여 5구간 자체는 52개 정책에 있다. 그룹 ID에 `_OR_`가 남아 있어도 판정 의미는 **logic 필드**를 따른다.
무주택 역시 독립 필수 조건으로 승격하지 않고 가구 그룹에 포함했다. 이를 무주택 제한 없음으로 확정하지 않았다.
UNKNOWN 246개는 전 정책 자동 판정을 보류해야 한다는 결과다. 관계 증가나 검토 표시 증가를 정답률 개선으로 해석할 수 없다. 유일한 AND 루트도 UNKNOWN 제외 하위 그룹을 갖는다.

## 4. 대표 사례

관계 수는 정책에서 원자 조건으로 향하는 REQUIRES_CONDITION 기준이다.

| 정책 | 관계 전→후 | 실제 변경과 남은 검토 |
|---|---:|---|
| POL_GOV_006 | 14→16 | JA0410·어업인 추가, 소득 5구간 보존, 무주택 필수 승격 제거. 루트·소득·가구 UNKNOWN. 수산 직불금 원문에는 교육·법령 준수 등 별도 요건도 있어 새 관계만으로 완전한 자격식이라고 볼 수 없음. |
| POL_GOV_011 | 14→21 | JA0410·취업경제활동 6개 추가. 구조화 13~18세와 본문 12~24세 모두 보존하고 needs_review. 어느 범위를 정답으로 선택하지 않음. |
| POL_GOV_048 | 14→15 | JA0410 추가. 기관명과 자격 본문에 함께 명시된 천안시를 근거로 충청남도→충청남도 천안시. 정책·지역 관계·Vector 지역 일치. 지역도 검토 상태 유지. |
| POL_YC_054 | 0→2 | 18~45세 및 소득 raw_value 추가. 0043003/기타/중위소득 150%이하 원문 보존, 숫자 상한으로 확정하지 않음. 전북특별자치도→전북특별자치도 진안군. N 연령 플래그와 제외 문장은 UNKNOWN. |
| POL_YC_002 | 8→10 | 18~29세 및 소득 raw_value 추가. 실제 코드 0043001/무관, 원본 0/0 금액 문자열 보존. 소득 제한 없음·금액 0원으로 확정하지 않음. N 연령 플래그 및 제외 조건 UNKNOWN. |

온통청년 소득 65개 관계의 raw_value는 JSON 문자열, condition_type은 OTHER, review_status는 needs_review로 보존된다. 조건 노드의 value와 관계 raw_value도 일치한다. 정부24 JA 원값은 상세 정책의 requirements.support_conditions 등에 남기고, 원본에 없는 raw_value 속성을 임의로 만들어 붙이지 않았다.

## 5. 미확정 조건과 계약 충돌

- 모든 JA Y의 실제 의미와 조건 사이 결합 의미는 공식 확인 대기. JA0410도 원값을 보존했으며 제한 없음으로 해석하지 않음.
- age_limit_yn=N 의미는 확정하지 않음. 현재 계약 §3.2의 N/0~0=연령 제한 없음 규정과 이번 사용자 지침이 충돌한다. 사용자 지침에 따라 age_limit=null 및 검토 상태를 보존했고 계약은 수정하지 않음.
- GOV_011 연령 충돌은 미해결. 추가로 POL_STG_MOLIT_001의 구조화 19~120세와 본문 19~34세도 검토 대상. 집계 2개는 명시 범위 대조로 발견한 후보 수이며, 전체 의미 오류 전수 확정 건수가 아님.
- POL_STG_SEOUL_001 정책 official_id=20260317005400211516, 연결된 DOC_STG_SEOUL_001_YC official_id=20261018005400213994. 저장된 YC 자료는 후자에 있다. 연결을 추정해 복원하지 않고 기존 값 및 UNKNOWN을 유지함. 데이터 담당 확인 필요.
- 신규 NTS 정책 등 구조화 요건이 없는 항목은 UNKNOWN. 공식 근거 없이 소득·시군구·자격을 추가하지 않음.
- Graph/Payload 계약 검증 통과와 완전한 정제 원본 계약 충족은 구분해야 함. 기존 projection에 없는 필수 수집 필드·원본 파일은 임의로 채우지 않았으며 검토 후보로 남김.

이번 확인은 저장소에 보존된 원문·API 정제본·코드 정의를 대상으로 했다. 공식 사이트를 새로 조회하거나 정책의 최신성을 재확인한 결과가 아니다.

## 6. 테스트 및 품질 결과

전체 53개: **47 통과 / 5 실패 / 1 오류 / 건너뜀 0**.

| 분류 | 결과 |
|---|---|
| 기존 생성 규칙 | 19/19 통과 |
| 신청 URL | 4/4 통과 |
| 원래 호환성 11개 | 8 통과 / 3 실패 |
| 추가 GenerationFollowup | 3/3 통과 |
| 이번 후보 재구성 회귀 | 7/7 통과 |
| 나머지 기존 candidate 검사 | 6 통과 / 2 실패 / 1 오류 |

호환성 파일에는 현재 14개가 있다. 요청한 원래 11개와 추가 3개를 구분했다. 실패는 cb55cb7에 고정된 Issue #5 Git 객체를 대상으로 한다. 현재 Issue #5 HEAD의 수정 동작을 재검증한 결과로 주장하지 않는다.

소비자 실패 3개(검사 유지, 코드 수정·우회·expectedFailure 없음):

- test_blocker_empty_and_group_must_hold: 빈 AND가 자동 보류되지 않음.
- test_blocker_new_relationship_fields_are_loaded_losslessly: 로더가 raw_value를 버림.
- test_blocker_review_needed_alone_must_hold: needs_review만으로 자동 보류되지 않음.

기존 candidate 실패·오류 3개:

- test_chunk_texts_and_document_hashes_match_saved_bodies: 본문·청크는 일치하지만 참조 원본 255개가 없어 전체 status=FAILED.
- test_frozen_manifests_match_actual_files: 동결 handoff에서 CRLF 바이트 체크섬 불일치. 확인된 파일은 LF 정규화 및 HEAD Git blob이 기록 체크섬과 일치. v1 릴리스에는 Git에 없는 원본 파일 2개도 있음.
- test_review_is_repeatable_from_captured_evidence: POL_001 URL 증거 경로 data/handoff/neo4j/nodes_policy.jsonl의 CRLF 바이트 해시가 달라 ValueError. LF 및 HEAD blob은 증거 해시와 일치. URL 자체를 수정한 결과가 아님.

기존 10대 품질 검사도 9/10이며 원본 누락 검사 실패다(237개 정책/246개 문서 정제본 대상). 기존 파일은 실행 전후 동일하므로 위 기존 실패는 이번 재구성으로 유발된 데이터 변경이 아니다. 바이트 검사 결과를 임의로 통과 처리하지 않았다.

새 후보는 manifest 18개 체크섬·파일 크기·레코드 수, 그래프 속성 자료형/필수·nullable, 관계 종류·끝점·중복·근거 문서, 문서/정책/청크 연결, Point UUIDv5, DAG 비순환 및 정책당 루트 1개 검사를 통과했다. 전체 원본 확보를 요구하는 검사는 원본 누락 때문에 여전히 통과하지 못한다.

실행한 테스트 명령:

```powershell
& $issue21Python -X utf8 -m unittest tests.test_candidate_release test_eligibility_generation test_issue21_candidate_rebuild test_issue21_consumer_compatibility -v
```

상세 테스트 로그·요약 및 줄바꿈/원본 문제 감사 자료는 `data/candidates/issue21-validation-20261107/`에 있다.
변경 Python 파일 4개의 py_compile 구문 검사 및 git diff --check도 통과했다. Git의 LF→CRLF 안내는 있었으나 diff 공백 오류는 없었다.

## 7. 코드·Git 및 보존 범위

- scripts/rebuild_eligibility_candidate.py 신규: 별도 후보 재구성·비교·보존·검증.
- scripts/build_handoff_package.py: 기존 main의 조건 노드 생성을 순수 build_condition_nodes 함수로 추출하여 재사용. 기존 분류 규칙 유지.
- tests/unit/test_issue21_consumer_compatibility.py: AST로 main 일부를 추출하던 helper 대신 같은 production 함수 호출. 기존 기대값·소비자 참조·실패 검사 유지.
- tests/unit/test_issue21_candidate_rebuild.py 신규: 덮어쓰기 차단, URL/ID 보존, 잘못된 원천 ID 자동 매칭 금지, YC_002 코드, 연령 충돌 보고, 지표, 문서별 청크 메타데이터 보존.
- 이 보고서와 별도 후보/검증 산출물 신규.

기존 실험 폴더는 수정·삭제하지 않았다. Issue #5 브랜치 및 DB에 접근·변경하지 않았다. 현재 DB 건수를 새로 조회한 것은 아니며, 기존 건수가 같다고 실측한 것으로 보고하지 않는다. HEAD 유지, 커밋 없음.

## 8. 다음 우선순위와 데이터 담당 검토

1. 서울 청년수당 official_id 정합성, 두 연령 충돌, JA 전체 Y 및 JA0410/N 플래그의 공식 의미 검토. 정답 확인 전 자동 적격식으로 승격하지 않기.
2. 데이터 담당과 needs_review·UNKNOWN 및 추가 관계 메타데이터 계약을 검토하고, 246개 전체 보류 중 구조화 가능한 정책을 원문 근거별로 줄이기. 미확인과 명시적인 제한 없음을 구분하기.
3. 원본 255개 복원 및 Git 줄바꿈/동결 checksum 재현 절차 합의. 기존 동결본을 임의로 고치거나 기준을 완화하지 않기.
4. Issue #5 최신 HEAD에서 이 최종 후보를 별도 dry-run/mocks로 소비자 검증. 현재 고정된 과거 소비자 실패 3개와 분리하기.
5. 담당자 검토 후 후속 릴리스의 완전한 메타데이터·계약·manifest 승인 절차 진행. 이번 작업에서는 DB 적재나 PR을 생성하지 않음.
