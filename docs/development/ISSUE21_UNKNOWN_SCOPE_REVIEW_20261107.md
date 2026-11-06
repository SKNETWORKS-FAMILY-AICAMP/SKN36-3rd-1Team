# Issue #21 UNKNOWN 범위 검수 및 수정
> **[과거 결과 — 2026-11-07 갱신]** 이 문서는 `issue21-v1.4.0-20261107-scoped` 기준의 이전 단계 결과다. 현재 검토 기준 후보는 `data/candidates/issue21-v1.4.0-20261107-rag/`이며, 루트 UNKNOWN 수치(245 등)와 일부 판단은 이후 수정으로 바뀌었다. 최신 결과는 `docs/development/ISSUE21_FINAL_VERIFICATION_20261107.md` 참조.


확인일 2026-11-07. `feature/21-eligibility-data-fix`, HEAD `a77c13c` 유지, 커밋 없음.
이전 후보와 미커밋 작업을 보존했다. DB/API 접근·브랜치 전환·merge/rebase·push·PR·모델 다운로드 없음.

## 1. UNKNOWN 246개의 원인과 타당성

현재 데이터에는 정책 전체의 판정을 보류할 이유가 없는 완전한 자격식이 없다. 다만 이전 생성기는 한 조건의 불확실성을 독립된 다른 하위 그룹의 논리에까지 전파하는 결함이 있었다.

| 분류 | 정책 수 | 실제 원인 |
|---|---:|---|
| 조건 관계가 없는 UNKNOWN 루트 | 108 | 기타 107개 및 서울 청년수당 1개. 구조화 조건 부재. 기존 문장만으로 자격식을 추측하지 않음 |
| 조건 관계가 있는 UNKNOWN 루트 | 137 | 정부24 72개: JA 코드·결합 의미 미확인. 온통청년 65개: 소득 OTHER/원값 해석과 연령 필드 검토 필요 |
| AND 루트 + UNKNOWN 제외 자식 | 1 | POL_005. 비정형 제외 문장을 아직 구조화하지 못함 |
| 합계 | 246 | UNKNOWN 루트 245개, 자식만 UNKNOWN인 정책 1개 |

원래 v1.4.0의 UNKNOWN 포함 정책 164→246 증가분 82개는 정부24 65개, 온통청년 16개, POL_005의 제외 조건 1개다. 루트 증가분은 81개이고 나머지 1개는 UNKNOWN 제외 자식이다.

출처별 UNKNOWN 정책은 원래 v1.4.0 대비 정부24 7→72, 온통청년 50→66, 기타 107→108이다. 이전 final 후보와 이번 scoped 후보 사이의 정책 수는 동일하다.

정부24 모든 JA Y를 제한 없음으로 바꾸지 않았다. 원래 루트의 AND가 잘못됐다는 공식 판정도 하지 않았다. 해당 루트는 기존 생성기가 가정한 결합이며, JA 의미·선택/필수 여부를 확인하기 전 전체 자동 자격 판정은 보류한다. 온통청년도 소득·연령·추가 문장이 미확정인 상태에서 전체 루트를 확정하지 않았다.

실제 과잉 전파는 **온통청년 7개 정책의 OR 하위 그룹 8개**에서 발견됐다. 기존 OR가 있던 학력 6개, 특화 1개, 취업 1개가 다른 소득/연령의 검토 상태 때문에 UNKNOWN으로 바뀌었다. 해당 원자 관계 자체에는 검토 표시가 없었으므로 기존 OR 구조를 보존하도록 수정했다. 공식 정책 전체의 자격을 새로 확정한 뜻은 아니다.

복원한 그룹:

- POL_YC_002: EDU, SP
- POL_YC_007, POL_YC_009, POL_YC_010, POL_YC_017, POL_YC_063: EDU
- POL_YC_049: JOB

## 2. needs_review 246개의 원인

재구성기는 UNKNOWN 그룹이 하나라도 있는 정책에 eligibility_review_status=needs_review를 기록한다. 현재 246개 모두 위의 실제 공백/불확실성이 있어 정책 단위 보류를 유지했다. 테스트를 통과시키거나 숫자를 줄이려고 표시를 제거하지 않았다.

소득 검토는 온통청년 65개에 존재한다. 연령은 N 37개와 Y이지만 정규화된 양쪽 경계가 없는 28개로 나뉜다. Y 28개도 실제 검토 플래그가 True이므로 이번 변경으로 임의로 정상 처리하지 않았다. 추가 자격 문장 38개, 비정형 제외 문장 7개, 지역 검토 플래그 84개도 있다. 이 이유들은 중복된다.

정책 검토와 개별 관계 검토는 다르게 처리했다. 기존에 extracted/verified인 직접 관계 34개를 그룹 멤버로 복사할 때까지 다른 조건의 needs_review를 덮어씌우던 부분을 수정했다. 그 관계 34개와 OR 그룹 연결 8개의 검토 범위를 복원했다. 조건이 없는 UNKNOWN 루트 108개의 연결에는 오히려 needs_review를 명시하여 기존 정책 표시와 일치시켰다.

완전히 정상인 가상 테스트 정책은 AND/OR 및 extracted 상태를 유지한다. 지역 검토만으로 다른 자격식의 연산자를 지우지 않는다. 기존의 needs_review는 사유를 확인하지 않고 제거하지 않는다.

정책별 근거는 새 후보 uncertainty_audit.jsonl에 root_logic, unknown_child_ids, review_reasons, 기존 AND/OR 그룹, 보류/미표시 원자 관계를 구분해 기록했다. 미표시 관계는 source에서 검토 표시가 없다는 뜻이며 정책 전체가 적격이라는 뜻이 아니다.

## 3. 수정 코드와 재현 테스트

- scripts/build_handoff_package.py / build_condition_groups
  - 정책 전체의 uncertain 플래그 대신 해당 그룹 멤버 및 실제 JA 유형을 기준으로 하위 그룹의 논리를 결정.
  - 구조화 객체가 있다는 사실과 지역 검토만으로 정상 조건을 UNKNOWN으로 바꾸지 않음.
  - 그룹 멤버의 review_status는 정책별 REQUIRES_CONDITION 근거 관계에서 가져옴. 공유 조건 노드나 다른 분기의 상태를 전파하지 않음.
  - 조건 미제공 설명에서 근거 없는 ‘전 국민 개방형’ 가능성 표현 제거. UNKNOWN 루트 연결에도 검토 표시.
- scripts/build_clean_dataset.py / build_condition_edges
  - Y + 명시적인 정상 검토 플래그 + 완전하고 순서가 정상인 숫자 범위일 때만 연령 관계의 기존 출처 상태 유지.
  - N, 누락·역전·기존 검토 범위는 계속 보류. 실제 후보 Y 28개는 모두 경계 누락이므로 데이터 판정 변화 없음.
- scripts/rebuild_eligibility_candidate.py
  - with_policy_review_status로 정상 조건까지 needs_review로 기본 처리하던 잠재 경로 제거. 실제 데이터의 검토 표시는 모두 보존.
  - 정책별 불확실성 감사 파일, 자식만 UNKNOWN인 정책 수, 그룹별 logic 수, 검토 사유별 집계 추가.
- tests/unit/test_issue21_unknown_scope.py 신규: 10개 회귀 검사.

핵심 재현 검사 5개는 HEAD의 수정 전 함수를 Git AST로 읽어 실행했을 때 모두 실패했고, 수정 후 통과했다. 새 검사 10개 전체 통과. 검사 기대값이나 과거 소비자 참조는 완화하지 않았다. 테스트용 가상 정책은 sample_only로 표시했다.

## 4. 새 후보와 검증

검토 대상: **data/candidates/issue21-v1.4.0-20261107-scoped/**
결정성 확인용 동일 재생성본: data/candidates/issue21-v1.4.0-20261107-scoped-replay/
이전 issue21-v1.4.0-20261107-final은 그대로 보존했다.

| 지표 | 이전 final | 이번 scoped |
|---|---:|---:|
| 정책 / 문서 / 청크 | 246 / 261 / 599 | 246 / 261 / 599 |
| 노드 / 관계 | 1,153 / 4,482 | 1,153 / 4,482 |
| UNKNOWN 루트 정책 | 245 | 245 |
| UNKNOWN 자식만 있는 정책 | 1 | 1 |
| UNKNOWN 포함 정책 | 246 | 246 |
| needs_review 정책 | 246 | 246 |
| UNKNOWN 그룹 | 390 | 382 |
| OR 그룹 | 0 | 8 |
| AND 그룹 | 1 | 1 |
| needs_review 원자 조건 노드 / 직접 관계 | 63 / 1,071 | 63 / 1,071 |

수치 변화의 핵심은 불확실성과 무관한 하위 OR 및 관계 상태의 보존이다. 관계 증가나 UNKNOWN 정책 수 감소를 개선 지표로 삼지 않았다. 빈 NOT 0개, 무주택 필수 AND 승격 0개, 상세 지역 복원 22개, 연령 충돌 후보 2개는 유지됐다.

검증 통과:

- 두 독립 프로세스의 산출물 **20개 모두 바이트 단위 SHA-256 동일**. manifest 자체도 동일.
- manifest가 선언한 19개 파일의 체크섬·크기·JSONL 레코드 수 통과.
- 정책·문서 ID, Point/청크 ID 및 본문 보존. 문서 노드 JSONL 바이트 동일, 저장된 문서 본문 261개 해시 일치, 청크 599개 본문 포함 검사 불일치 0.
- 신청 URL 22개 및 null 224개 유지. 문서 선택 속성 null과 문서별 기존 청크 메타데이터 유지.
- 현 데이터 팀 Graph/Payload 계약 검증, 관계 종류·중복·끝점·근거 문서 참조, DAG 비순환, 정책별 루트 1개, Graph/Vector 참조 통과.
- 기존 data 및 실험 자료, 이전 후보를 포함한 **236개 기존 파일 SHA-256 불변**.

원본 경로 255개 누락은 여전하므로 원본 파일 실존까지 요구하는 품질 검사는 전체 통과가 아니다. Graph 투영 계약 검사 통과를 원본 수집 계약 전체 충족으로 주장하지 않는다.

재현(PowerShell):

```powershell
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages;$PWD;$PWD/tests/unit"
$issue21Python = 'C:/Users/minhyeokju/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $issue21Python -X utf8 scripts/rebuild_eligibility_candidate.py --output-dir data/candidates/새로운검증경로
& $issue21Python -X utf8 -m unittest tests.test_candidate_release test_eligibility_generation test_issue21_candidate_rebuild test_issue21_consumer_compatibility test_issue21_unknown_scope -v
```

기존 경로는 생성기가 거부한다. Python 3.12.14와 이미 저장된 순수 Python 의존성을 사용했고 설치·환경 삭제·공용 requirements 변경은 없다.

## 5. 대표 정책 7개

아래 ‘보존된 값’은 저장된 근거의 명확한 원값·기존 구조를 뜻한다. 실제 자격의 최종 정답을 의미하지 않는다.

| 정책 | 보존된 값·구조 | 불확실·추가 검토 | 이번 변화 |
|---|---|---|---|
| POL_GOV_006 | 소득 5구간, JA0410, 어업인, 가구 원값. 직접 조건 16개 유지 | JA 전체 Y, 소득/가구/취업의 결합, 원문 교육·준수 의무를 포함한 완전한 자격식 | 조건 삭제 없음. 루트·소득·가구 UNKNOWN 유지 |
| POL_GOV_011 | 구조화 13~18세 및 본문 12~24세, 취업 코드 등 21개 관계 유지 | 어느 연령이 정답인지, JA 조건의 결합 | 충돌을 그대로 보존, 루트·소득·가구·취업 UNKNOWN 유지 |
| POL_GOV_048 | 충청남도 천안시, 근거 문장 및 정책/지역 관계/Vector 일치, 조건 15개 | 지역 적용 범위 최종 검토와 JA 해석 | 지역·조건 값 불변. 지역 검토가 독립 정상 조건 논리를 오염시키지 않도록 규칙 수정 |
| POL_YC_054 | 전북특별자치도 진안군, 원값 18~45세, 소득 0043003/기타/중위소득 150%이하, 조건 2개 | N의 의미, 소득의 정형 표현, 비정형 제외 조건 | 값·지역·UNKNOWN 유지. 숫자 상한·제한 없음으로 확정하지 않음 |
| POL_YC_002 | 소득 0043001/무관, 금액 0/0 문자열, 원값 18~29세, 조건 10개 | N, 소득 단위/무관 의미의 판정 적용, 제외 조건 | EDU/SP를 UNKNOWN→기존 OR로 복원. 루트·소득·연령 검토·제외 UNKNOWN 유지 |
| POL_STG_MOLIT_001 | 구조화 19~120세 및 본문 19~34세, 관계 20개 유지 | 구조화와 상세 문서 범위 충돌 | 양쪽 모두 보존. 정답 선택·나이 덮어쓰기 없음 |
| POL_STG_SEOUL_001 | 정책 official_id=20260317005400211516, DOC_STG_SEOUL_001_YC official_id=20261018005400213994, 신청 URL | 원천 정책 동일성 및 정책 ID 교정 여부 | ID 추정 매칭·조건 복원 보류, 조건 0개·UNKNOWN 유지 |

일곱 정책의 전체 전후 정책·그룹·직접 관계는 감사 폴더 representative_cases.jsonl에 기록했다.

## 6. 기존 53개 실패·오류 분류

기존 53개: **47 통과 / 5 실패 / 1 오류 / 건너뜀 0**, 수정 전 결과와 동일.
신규 10개: **10 통과**. 합계 63개: **57 통과 / 5 실패 / 1 오류 / 건너뜀 0**.
19개 생성 규칙, 4개 신청 URL, 기존 재구성 회귀 7개는 모두 통과했다.
관련 Python 6개 파일의 py_compile 및 git diff --check도 통과했다. Git의 LF→CRLF 안내는 있었지만 공백 오류는 없었다.

| 테스트명 | 실제 실패/오류 | 원인·수정 여부 |
|---|---|---|
| CandidateIntegrityTests.test_chunk_texts_and_document_hashes_match_saved_bodies | AssertionError: FAILED != PASS | 저장된 본문과 청크는 일치하나 참조 원본 255개 누락. 환경 차단. 원본 재수집·우회 없음 |
| CandidateIntegrityTests.test_frozen_manifests_match_actual_files | SHA-256 1ce335ff… != 787fbd9f… | 첫 실패는 data/handoff/neo4j/cypher_ddl.cypher의 CRLF/LF. 기존 Git blob/LF 값은 기록값과 일치. 동결본·검사 수정 없음 |
| Issue5ConsumerBlockerTests.test_blocker_empty_and_group_must_hold | AssertionError: False is not true | cb55cb7 과거 소비자가 빈 AND를 보류하지 않음. #21 수정 대상 아님 |
| Issue5ConsumerBlockerTests.test_blocker_new_relationship_fields_are_loaded_losslessly | raw_value not found in loaded properties | cb55cb7 과거 로더의 속성 허용 목록 누락. 생성·인계의 65개 원값은 보존. #21 수정 대상 아님 |
| Issue5ConsumerBlockerTests.test_blocker_review_needed_alone_must_hold | AssertionError: False is not true | cb55cb7 과거 소비자의 needs_review 판정 누락. #21 수정 대상 아님 |
| CandidateIntegrityTests.test_review_is_repeatable_from_captured_evidence | ValueError: 신청 주소 증거의 원본 해시 불일치: POL_001 | 증거 경로 data/handoff/neo4j/nodes_policy.jsonl의 CRLF 바이트 해시. LF 및 HEAD blob은 증거 해시와 일치. URL·증거·검사 변경 없음 |

위 6건 중 #21의 신규 생성 코드 회귀로 확인된 항목은 없다. 생성 결함은 별도 재현 검사에서 확인해 고쳤다. 실패를 skip/expectedFailure로 숨기거나 과거 소비자 참조를 바꿔 기존 53개 결과를 통과로 만들지 않았다.

상세 로그, traceback, 재현 실패, 결정성·보존 감사는 **data/candidates/issue21-unknown-validation-20261107/**에 있다. 기존 환경 체크섬 분류는 보존한 issue21-validation-20261107/environment-audit.json에서도 확인할 수 있다.

## 7. 공식 확인 및 데이터 담당 검토

- JA 전체 Y, JA0410, 적용 코드 사이의 실제 AND/OR 의미. 조건 일부의 정상 OR 복원이 전체 정책 적격을 뜻하지 않음.
- N 연령 플래그의 의미. 현재 계약 §3.2의 N=제한 없음 표현과 이번 사용자 지침이 충돌하므로 사용자 지침대로 보류. 계약 수정 없음.
- 두 연령 충돌 및 서울 청년수당 official_id 불일치. 원문·ID 추정 교정 없음.
- 명확한 기존 하위 OR와 정책 전체의 UNKNOWN을 소비자가 구분하는지, 관계별 검토 범위 및 공유 조건 노드의 검토 상태를 어떻게 해석할지 데이터/DB 담당 검토 필요.
- 정상 자격식을 확정하려면 미구조화 추가 자격 38개·제외 문장 7개와 조건 없는 정책의 원문을 근거로 단계별 구조화 필요. 원본 255개 복원 및 줄바꿈 재현 절차도 별도 해결.

이번 단계에서 공식 사이트·API를 새로 확인하지 않았다. 저장된 자료·기존 계약·코드로 확인한 결과와 공식 해석 미확정 사항을 구분했다.

## 8. 최신 Issue #5 검증 준비와 Git

04a9edc의 함수를 Git에서 읽고 필요한 AST 정의만 실행했다. 브랜치 병합/전환, database 모듈의 네트워크 초기화, 실제 DB 클라이언트 생성은 하지 않았다.

- 과거에 실패한 안전성 3개를 **별도 04a9edc 실행**으로 검사: 3/3 통과.
- 새 scoped 후보 전체를 최신 validate_graph_input에 전달: 오류 0, 1,153노드/4,482관계 통과.
- mock runner로 246개 정책의 트리/직접 관계 일치, 구조 오류 없음, evaluated=False 및 판정 보류 246개 확인.
- 최신 _relationship_row에서 raw_value 65개 무손실 보존 확인.

따라서 최신 #5에서도 과거 3개 결함이 남아 있다고 주장하지 않는다. 이 후보는 최신 로더의 전체 오프라인 사전 검증에 사용할 준비가 됐다. 다만 자동 적격 판정·공식 릴리스·DB 적재 승인 상태는 아니며, 실제 DB와 전체 검색 프로필 통합 검증은 이번 단계에서 미실행이다.

현재 변경 파일: build_clean_dataset.py, build_handoff_package.py, rebuild_eligibility_candidate.py, 기존 소비자 테스트 helper(전 단계 변경 유지), 기존 재구성 회귀 파일(전 단계 신규 유지), 신규 unknown_scope 테스트, 보고서 및 후보/감사 산출물. 다른 담당 브랜치·기존 데이터·실험 자료는 보존했다. #5 HEAD 04a9edc 유지. 커밋하지 않았다.
