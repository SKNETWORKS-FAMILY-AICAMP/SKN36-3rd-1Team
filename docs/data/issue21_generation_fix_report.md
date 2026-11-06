# Issue #21 생성 규칙 수정 결과
> **[과거 결과 — 2026-11-07 갱신]** 이 문서는 `0645f90 생성 규칙 1차 수정` 기준의 이전 단계 결과다. 현재 검토 기준 후보는 `data/candidates/issue21-v1.4.0-20261107-rag/`이며, 루트 UNKNOWN 수치(245 등)와 일부 판단은 이후 수정으로 바뀌었다. 최신 결과는 `docs/development/ISSUE21_FINAL_VERIFICATION_20261107.md` 참조.


기준일: 2026-11-06 / 브랜치: `feature/21-eligibility-data-fix`

생성 규칙과 테스트를 수정했다. 데이터 버전 생성, 전체 수집, DB 적재는 실행하지 않았다.
신규 테스트는 통과했지만 기존 전체 품질 검증에는 아래 선행 실패가 있으므로
이번 단계 완료 조건 중 **기존 검증 전체 통과는 충족하지 못했다**.

## ① 수정 파일과 함수

| 파일 | 함수 및 변경 |
|---|---|
| `collectors/api/gov24_collector.py` | `structure_ja_conditions`: JA0410 해당사항없음 보존 |
| `collectors/api/youthcenter_collector.py` | `normalize_youthcenter_item`: N을 제한 없음으로 확정하지 않고 `age_limit=null`, 검토 필요로 보존. 원래 연령·상세 지역 코드 보존. `map_zip_codes_to_regions`: 누락·미등록 코드를 전국으로 추론하지 않음 |
| `scripts/build_clean_dataset.py` | `extract_lifecycles_and_attributes`: 소득 구간 전체와 정확한 구간명 보존, 취업 구조화 키 불일치 수정. `infer_region_from_text`: 명시된 시군구 보존 |
| 동일 파일 | `get_structured_conditions`: 정제본의 `support_conditions.JA0410`이 남아 있으면 누락 복구. `build_condition_edges`: 정부24 취업·경제활동, 온통청년 연령·소득 관계 생성. 누락된 연령 경계에 0/99를 임의 대입하지 않음 |
| 동일 파일 | `rebuild_policy_conditions`, `rebuild_condition_relationships`: 정제 데이터를 읽은 객체에서 조건만 재구성. 입력 객체·기타 관계·식별자·출처 보존. 소득 메타데이터와 검토 상태 갱신 |
| `scripts/build_handoff_package.py` | `build_condition_groups`: 무주택을 가구 그룹으로 통합, 미확인 JA 결합은 UNKNOWN. 빈 NOT 대신 제외 문장 전체를 가진 UNKNOWN 그룹 생성. 온통청년 `excluded_participants`도 보존 |
| 동일 파일 | `main`: import 시 쓰기 제거, 기존 정제본으로 조건 재구성 가능. 기본 출력은 `data/candidates/issue21-handoff`, 이미 있으면 중단 |
| `tests/unit/test_eligibility_generation.py` | 가상 `sample_only` 단위 사례 및 실제 정제 데이터 읽기 전용 회귀 검사 19개 |
| 본 보고서 | 변경·검증·미해결 사항 기록 |

`build_clean_dataset.main`은 `--output-dir`을 명시해야 하며 `data/candidates/` 아래 새 디렉터리만 허용한다.
전체 원본 기반 main과 인계 패키지 main은 이번에 실행하지 않았다. 정제본 재구성은 순수 함수로 검증했다.
새 패키지를 배포하거나 기존 DB 소비 코드가 UNKNOWN을 올바르게 처리한다고 보증하는 단계는 아니다.

## ② 검증 결과

- 수정 전 최초 재현 테스트 5개: 실패 4개, JA0410 누락 오류 1개.
- 기존 HEAD의 DAG 생성 부분을 파일 쓰기 없이 메모리에서 실행: GOV_011 소득 OR 및 빈 NOT 1개 재현.
- 수정 후 `python -m unittest discover -s tests/unit -v`: **19개 통과**.
- 변경 Python 파일 5개 `python -m py_compile ...`: 통과.
- `git diff --check`: 통과.
- 실제 정제 정책 237개 재구성: 관계 2,537→2,812개, DAG 그룹 368개, 전체 Graph 관계 4,190개. 전부 메모리 내 결과.
- 정책당 루트 1개, 그룹 ID 중복 없음, 조건/그룹 참조 유효, 빈 NOT 없음, 입력 데이터 불변, 반복 재구성 멱등성 검사 통과.
- 기존 저장소의 `tests/`에는 실행 가능한 테스트가 없고 `.gitkeep`만 있었다. 기존 회귀 대상으로 아래 검증 스크립트 함수를 실행했다.

| 기존 검증 | 결과 |
|---|---|
| `scripts.verify_data_quality.run_verification()` | 9/10 통과. 원본 파일 246개 누락으로 실패 |
| `scripts.verify_handoff_package.run_handoff_verification()` | False. 원본 246개 누락 및 기존 `data/handoff/README.md` 체크섬 불일치 1건 |

두 함수는 CLI 보고서 쓰기 경로를 거치지 않고 읽기 전용으로 호출했다. 이 데이터는 수정 전부터 변경 없는 파일이다.
패키지 설치·API 호출·모델 다운로드·Docker 기동·DB 접속은 하지 않았다.
린트 도구 실행 및 실제 DB 소비자의 UNKNOWN 평가 연동은 미검증이다.

실행 환경: 기존 `.venv/Scripts/python.exe`가 삭제된 Python313 경로를 가리켜 실행되지 않았다.
설치 없이 다음 기존 Python과 가상환경 패키지로 실행했다.

```powershell
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages;$PWD"
& 'C:/Users/minhyeokju/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -X utf8 -m unittest discover -s tests/unit -v
```

## ③ 실제 데이터 전후 예시

아래는 `data/processed/` 기존 레코드를 읽어 비교한 결과이며 기존 파일을 갱신한 결과가 아니다.

| 정책 | 이전 | 수정 함수 적용 결과 |
|---|---|---|
| POL_GOV_001 | 소득 속성 `기준중위소득_50%이하` 하나 | 원래 적용 구간 0~50%, 51~75%, 76~100%, 101~200% 모두 보존 |
| POL_GOV_011 | 관계 14개, 소득 5구간 OR, 무주택은 루트 직결, JA0410 및 취업 관계 누락 | 관계 21개. 해당사항없음 1개와 취업·경제활동 6개 복구. 무주택은 가구 그룹 소속. 루트·소득·가구·취업 결합은 UNKNOWN |
| POL_YC_002 | 관계 8개, 연령·소득 관계 누락 | 관계 10개. `COND_AGE_18_29`와 소득 원값 관계 추가. 확인 전 UNKNOWN |
| POL_005 | 자식 없는 NOT 제외 그룹 | 원래 제외 문장 전체를 가진 UNKNOWN 제외 그룹 |

GOV_011의 구조화 연령 13~18세와 선정기준 본문 12~24세는 모두 그대로 보존했다.
어느 범위가 정답인지 선택하지 않았으며, 정책 조건 전체가 UNKNOWN으로 보류된다.

## ④ 공식 확인·팀 합의 대기

- JA0201~JA0205 전체 Y의 의미, JA 코드들의 실제 AND/OR 의미, JA0410의 평가 의미.
- GOV_011의 연령 충돌 해결과 원천 정정 여부.
- 온통청년 `age_limit_yn=N` 의미. 원본 소득 금액의 단위·비교 방식 및 복잡한 기타 소득 문장.
- 시군구 코드 공식 매핑. 코드 자체는 보존하지만 공식 매핑 없는 지명은 생성하지 않는다.
- 이미 정제 단계에서 사라졌고 잔존 원값도 없는 지역·소득·연령은 복원하지 않는다.
- 정부24 JA 조건 전체와 새 온통청년 연령·소득 조건은 보수적으로 검토 대상으로 둔다. 확인 후 필요한 범위만 판정 가능하게 좁혀야 한다.

문서 충돌: `docs/data/data_contract.md` §3의 N=제한없음, `preprocessing_rules.md`의 17개 시도 한정 규칙은
이번 사용자 지시와 충돌한다. 이번 지시를 우선했다. 공통 계약 문서는 수정하지 않았으므로
데이터·DB·조건 판정 담당의 계약 정리가 필요하다. UNKNOWN 비교 연산자를 가진 미확인 조건은
다운스트림에서 숫자로 파싱하거나 충족으로 간주하지 않도록 연동 검증이 필요하다.

## ⑤ 다음 우선순위

1. 공식 명세 및 GOV_011 공고로 미해결 의미 확인.
2. UNKNOWN 처리와 원값 보존 방식에 대한 소비자 연동 검사 및 계약 합의.
3. 새 후보 디렉터리에만 생성 후 Graph/Vector 메타데이터와 기존 인계 스크립트의 고정 건수 등을 검증.
4. 원본 복원 및 기존 README 체크섬 불일치 원인 확인. 기존 릴리스는 보존.

## ⑥ Git·데이터 상태

- 시작 및 최종 확인 브랜치: `feature/21-eligibility-data-fix`.
- 코드 4개 수정, 테스트 2개와 본 보고서 신규 생성. 기능 단위 커밋 대상이며 push·PR·merge·rebase는 수행하지 않는다.
- `git diff --name-only -- data` 출력 없음. 기존 데이터 버전 및 DB 변경 없음.
- 기존 미추적 `docs/development/experiments/` 및 지정 중간 결과 파일은 수정·삭제하지 않음.
- 전체 후보 패키지·DB를 만들지 않았으므로 DB 완료나 전체 데이터 검증 완료로 간주하면 안 된다.

## 후속 마무리 검증 — 최종 상태

이 절은 위 1차 검증 이후의 변경 및 최종 테스트 결과다.

### 생성 영역 수정

- `rebuild_policy_conditions`: 기존 광역지역과 기관명의 상세 지역이 일치하고 자격 원문에도 같은 시군구가 명시될 때 상세 지역을 보존한다. 기관명만으로 지역을 확정하지 않으며 `region_review_needed=true`로 남긴다.
- `rebuild_condition_relationships`: 복구한 지역의 `AVAILABLE_IN`도 동일한 상세 지역으로 갱신한다. 인계 생성기의 Region 노드 구성은 해당 관계를 사용한다.
- Qdrant 인계 페이로드의 `region`도 재구성한 정책 값을 사용한다. 기존 청크 파일은 수정하지 않는다.
- 온통청년 소득 원값은 코드 의미와 비교 방식 확인 전 `OTHER` / `operator=UNKNOWN`으로 보존한다. 무관·기타를 금액 조건으로 승격하지 않는다.
- YC_002 소득 코드 기대값을 실제 정제본의 `0043001`, 라벨 `무관`으로 수정했다. 이전 `0043003` 기대값은 테스트 오류였다.

천안시 원문은 POL_GOV_048의 자격 설명에 주민등록·거주 요건으로 남아 있다. 기존 `충청남도`가 후보 객체에서 `충청남도 천안시`로 보존되며, 정책·지역 관계·Region 노드·벡터 인계 메타데이터의 동일 값 사용을 검사했다. 실제 데이터 파일에는 쓰지 않았다.

### 최초 실패 5건 분류

| 테스트명 (`test_` 이하) | 이전 결과 | 최종 분류/결과 |
|---|---|---|
| `blocker_cheonan_region_is_rebuilt_from_preserved_source` | 기대 천안시 / 실제 충청남도 | 생성 영역 수정, 통과 |
| `blocker_new_relationship_fields_are_loaded_losslessly` | 로더 변환 결과의 `raw_value` 접근 시 KeyError | 생성·인계에는 존재. Issue #5 속성 허용 목록에서 누락. 명시적 assertIn 실패로 유지 |
| `blocker_empty_and_group_must_hold` | 기대 보류 true / 실제 false | Issue #5 소비자 문제, 실패 유지 |
| `blocker_review_needed_alone_must_hold` | 기대 보류 true / 실제 false | Issue #5 소비자 문제, 실패 유지 |
| `blocker_other_income_is_not_typed_as_amount` | YC_002에 0043003을 잘못 기대 | 실제 0043001로 수정. 원값 OTHER 보존 확인, 통과 |

지역 수정 직후 발생한 `missing_target=22`는 테스트가 새 지역 관계에 기존 18개 Region 노드를 결합한 결과였다. 현재 테스트는 실제 인계 생성기의 Region 구성 코드를 실행하여 새 노드를 검증한다. 검사 기준·참조 무결성 검증은 완화하지 않았다.

### raw_value 전달 및 Issue #5 후속

1. 생성: `build_condition_edges`가 소득 코드·라벨·최소/최대금액·기타설명을 JSON 문자열 `raw_value`로 관계에 제공한다.
2. 인계: 관계는 `raw_value`를 포함한 채 JSONL로 직렬화된다. 조건 노드는 같은 문자열을 기존 `value` 필드에 보존한다. 이 경로의 JSON 직렬화/역직렬화 테스트 통과.
3. 소비: Issue #5 `cb55cb7542560210ae41a4fd05be25c461a89c63`의 `_relationship_row`는 `target_name`, `evidence_document_id`, `evidence_location`, `review_status`만 보존한다. 따라서 관계의 `raw_value` 및 `condition_type`은 누락된다. 조건 노드의 `value` 보존과는 별개다. 관계 추가 필드의 적재 계약 합의 및 보존 처리를 Issue #5에서 진행해야 한다.
4. 빈 AND, 조건·정책의 `needs_review`만 있는 경우에도 자동 판정 보류가 필요하다. 현재 소비자는 이 두 경우 `hold_auto_eligibility=false`다. 다만 검색 함수의 `evaluated=false`는 유지되므로 이 결과를 적격 확정으로 보고해서는 안 된다.

소비자 코드는 Git 객체에서 선택한 원본 함수만 메모리로 읽었다. 해당 브랜치 파일과 DB에는 변경이 없다.

### 최종 테스트

- 기존 생성 규칙 테스트: **19/19 통과**.
- 기존 추가 호환성 테스트: **11개 중 8개 통과, Issue #5 전용 3개 실패**. 실패 테스트는 `Issue5ConsumerBlockerTests` 클래스로 분리했으며 삭제·skip·expectedFailure 처리를 하지 않았다.
- 추가 회귀 검사: **3/3 통과**. 상세 지역 재구성 멱등성/기관명만으로 추론하지 않음, 소득 원값 인계 JSON 보존, 벡터 인계 지역 일치 확인.
- Issue #21 관련 합계: **30개 통과**. 전체 discovery는 **33개 중 30개 통과, 3개 실패**이며 전체 통과로 보고하지 않는다.
- 변경 Python 파일 구문 검사와 `git diff --check` 실행. 최종 실행 결과는 커밋 전 확인한다.

```powershell
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages;$PWD;$PWD/tests/unit"
$python = 'C:/Users/minhyeokju/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $python -X utf8 -m unittest test_eligibility_generation test_issue21_consumer_compatibility.ConsumerCompatibilityTests test_issue21_consumer_compatibility.GenerationFollowupTests -v
& $python -X utf8 -m unittest test_issue21_consumer_compatibility.Issue5ConsumerBlockerTests -v
```

기존 원본 246개 누락은 `origin/dev` 임시 추출본에서도 재현됐다. README 매니페스트의 SHA-256 `6c462cbe…`는 Git의 LF 원본과 일치하지만 현재 Windows CRLF 작업 파일은 `df419307…`로 다르다. 내용 변경에 의한 신규 회귀가 아니다. 데이터나 검사 기준을 변경해 통과시키지 않았다.

후속 브랜치 복귀 전에는 이 기능 커밋과 작업 트리 상태를 확인한다. 지정 실험 JSON은 커밋 대상에서 제외하며 사용자가 직접 브랜치를 전환한다. Issue #5의 소비자 3건과 전체 패키지 생성/실DB 검증은 후속 작업으로 남는다.
