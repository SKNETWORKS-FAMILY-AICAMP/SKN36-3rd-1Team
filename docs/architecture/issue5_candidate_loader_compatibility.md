# Issue #5 — v1.4.0 후보 로더 호환성 및 안전성 검증

확인일: 2026-11-06 (KST). 작업 브랜치 `feature/5-hybrid-rag-db`, 시작 HEAD `cb55cb7542560210ae41a4fd05be25c461a89c63`.
후보 데이터는 Git 객체에서 읽었으며 작업 트리의 `data/handoff/`나 DB에 적용하지 않았다.

## 1. 검증한 후보와 계약

- 원격 후보 브랜치: `feature/data-v1.4.0-candidate-audit`.
- `git ls-remote`로 확인한 원격 HEAD와 로컬 참조가 모두 `0a091268d2bfb0a781e992e28427d733870ebfa0`이다.
- 경로: `data/releases/v1.4.0-candidate/`.
- manifest 버전: `1.4.0-candidate`, 상태 `CANDIDATE / PENDING_DB_REVIEW`.
- manifest SHA-256: `f1fa3861dd425d9b9b34e2d748a12eade1861474b66b65557009f8bca7b3767d`.
- manifest 내 파일 20개: Git 원본 바이트의 크기·SHA-256 및 JSONL 건수 일치. LF 정규화 후에도 일치하며, CRLF 복사본을 LF로 정규화하는 검사도 통과했다. 원본 파일/매니페스트는 수정하지 않았다.

| 항목 | 기존 v1.3.2 파일 | 후보 v1.4.0 |
|---|---:|---:|
| Policy | 237 | 246 |
| Document | 246 | 261 |
| Organization | 116 | 123 |
| Region / Category / LifeStage / TargetGroup | 18 / 5 / 6 / 11 | 동일 |
| ConditionGroup | 343 | 352 |
| EligibilityCondition | 52 | 52 |
| 전체 노드 | 1,034 | 1,074 |
| 전체 관계 | 3,615 | 3,703 |
| 벡터 인계 청크 | 557 | 599 |

후보의 노드/관계 ID·중복·양끝 참조, 문서→정책, 관계→근거문서 및 벡터→정책/문서·해시 연결 검사가 통과했다.
이는 파일과 메모리 구조 검사 결과이며 현재 DB의 실제 건수를 뜻하지 않는다.

계약 근거는 후보 커밋의 `docs/data/data_contract.md` §2.1 및 `data/handoff/data_contract.md`다.
두 문서의 2026-11-06 개정에 `Policy.official_apply_url`이 선택 속성으로 명시돼 있다.
실제 신청 주소가 확인되지 않으면 null을 유지하고, 서비스 안내 페이지는 `source_url`에 둔다.
`Document.law_article`, `effective_date`, `version`도 선택 속성으로 명시돼 있다.
현재 Issue #5 브랜치의 계약 문서는 이전 내용이므로 후보 개정과 차이가 있다. 공통 계약 파일은 수정하지 않았다.

## 2. 31건 null 오류의 원인과 현재 후보 차이

기존 로더는 `_is_neo4j_property_value(None)`이 false여서 선택 속성도 전부 `invalid_property`로 거부했다.
Git의 원래 로더 코드를 메모리에 읽어 후보 두 버전에 대해 재현했다.

| 필드 | 과거 후보 `074d6383c9295f97255d34cbfa0b335365307989` | 현재 후보 `0a09126` |
|---|---:|---:|
| Policy.official_apply_url | 5 | 224 |
| Document.law_article | 9 | 9 |
| Document.effective_date | 9 | 9 |
| Document.version | 8 | 8 |
| 합계 | **31** | **250** |

현재 후보는 신청 주소 대체를 제거하면서 URL null이 늘었다. 따라서 현재 오류를 31건으로 고정해 보고하면 안 된다.
수정 로더에서는 두 후보 모두 입력 검사 오류 0건이다.

## 3. 로더 변경

`database/neo4j/load_graph.py`:

- 노드 종류별 허용 목록에 있는 선택 문자열 속성만 null을 허용한다. 누락도 허용하며 숫자·bool·list·dict로 대체된 선택 문자열은 거부한다.
- 필수 Graph 투영 속성의 누락·null·잘못된 타입을 차단한다. ID, 출처 URL, 정책명, 문서 정책 ID·제목·해시 등에 대한 부정 테스트를 포함했다.
- 정제본의 중첩 `requirements`/`eligibility_attributes` 등은 Graph 투영 필드가 아니다. 전체 processed 레코드 검증과 Graph 노드 검증을 혼동하지 않는다.
- `_node_row`는 허용된 null을 파라미터 속성에서 제외한다. 빈 문자열·가짜 URL로 바꾸지 않는다. `SET += null`로 기존 속성을 삭제하는 효과도 피한다. 기존 DB에 다른 값이 남아 있다면 속성 검증에서 불일치로 보고하며 자동으로 지우지 않는다.
- 관계 `raw_value`, `condition_type`, `doc_type`, `evidence_type`, `law_article`을 보존한다. 선택 문자열로 처리해 누락/null은 저장 속성에서 제외하고, 값이 있으면 문자열만 허용한다. 관계 타입/방향/조건 의미는 바꾸지 않는다.
- 후보의 신규 문서 관계 15건에 있는 `doc_type`/`evidence_type`/`law_article`도 적재·속성 비교에서 조용히 버리지 않도록 했다. 근거는 후보의 `schema_extension_compatibility.md`와 `data_contract_change_proposal.md` 및 실제 인계 값이다.

후보 전체를 `FakeGraphRunner` 메모리에 적재하는 테스트에서 1,074 노드 / 3,703 관계 및 속성 비교가 통과했다.
**이 테스트는 실제 Neo4j의 트랜잭션·서버 동작 검증이 아니다.**

## 4. 빈 AND / needs_review / raw_value

`database/hybrid_search.py`:

- 루트 또는 중첩 AND 그룹이 비어 있으면 `EMPTY_AND_GROUP` 경고와 `hold_auto_eligibility=true`를 반환한다.
- 정책·그룹·조건·관계·근거 노드의 `needs_review`를 검사해 `NEEDS_REVIEW` 경고로 보류한다. 그룹의 검토 상태도 condition_tree에 보존한다.
- UNKNOWN 그룹은 기존 보류 계약을 유지한다. condition_tree의 AND/OR/NOT/UNKNOWN 값 자체를 재작성하지 않고 `evaluated=false`를 유지한다.
- 명시적 소득 무관 메타데이터는 그대로 반환하며 자동으로 UNKNOWN으로 바꾸지 않는다.

Issue #21의 `raw_value`는 생성 관계에 JSON 문자열로 존재하고 조건 노드의 `value`에도 보존됐다.
이 작업은 Issue #5 로더의 관계 속성 허용 목록에서 빠지던 `raw_value`/`condition_type`을 보존한다.
가상 관계의 실제 파라미터·메모리 저장 결과·속성 재검증 테스트가 통과했다.

## 5. 남아 있는 데이터 의미 문제

후보의 기존 52개 조건 노드와 기존 343개 그룹은 v1.3.2와 동일하다. Issue #21 수정이 자동 반영된 후보가 아니다.

| 정책/패턴 | 현재 후보 상태 |
|---|---|
| POL_GOV_006 | 조건 14개, 소득 5구간 OR와 가구 OR 유지 |
| POL_GOV_011 | 조건 14개, 소득 5구간 OR 유지. 13~18세와 본문 12~24세 충돌은 해결하지 않음 |
| POL_GOV_048 | 지역 충청남도, 상세 천안시 미반영 |
| POL_YC_054 | 지역 전북특별자치도, 조건 관계 0개 / UNKNOWN |
| POL_YC_002 | 지역 전국, 조건 관계 8개. Issue #21의 연령·소득 추가 관계 미반영 |
| POL_005 | 자식 없는 `GRP_POL_005_NOT_EXCL` 유지 |

후보 그룹은 UNKNOWN 164 / AND 82 / OR 105 / NOT 1개다.
생성 규칙·공식 원문 충돌은 소비자 코드에서 정답을 추측해 고치지 않았다.
신규 manifest를 실제 적용할 때 기존 v1.3.2 정적 의미 경고 목록은 해시가 달라 `stale`이 될 수 있다.
새 후보용 원문 대조 경고 목록을 별도로 검증해야 한다.

## 6. 1차 테스트 결과와 환경 차단 (복구 전 기록)

| 검사 | 결과 |
|---|---|
| 신규 후보/선택 null/필수 오류/관계/안전 보류 검사 | **12/12 통과** |
| 기존 Neo4j 로더 | **60/60 통과** |
| 기존 Hybrid 검색 | **31 통과 / 27 건너뜀**, 실패 없음 |
| 두 검색 프로필 | **9 통과 / 8 환경 오류** |
| 전체 색인 | **5 통과 / 10 환경 오류** |
| 수정 전 전체 단위 검사 | 366개: **312 통과 / 27 환경 오류 / 27 건너뜀** |
| 수정 후 전체 단위 검사 | 378개: **324 통과 / 27 환경 오류 / 27 건너뜀**, assertion 실패 0 |

환경 원인: `.venv`는 Python 3.13.15를 가리키지만 해당 인터프리터가 없고, 실행 가능한 번들 Python은 3.12다.
기존 `cygrpc.cp313-win_amd64.pyd`를 3.12에서 import할 수 없다. 코드의 표면 오류는 qdrant-client 미설치지만
실제 원인은 gRPC 바이너리/인터프리터 불일치다. 패키지 설치, 테스트 skip 추가, 더미 라이브러리 주입으로 우회하지 않았다.
환경 오류 위치는 full_index 10, hybrid_profiles 8, retrieval_run 3, vector_store 5, Qdrant 테스트 클래스 초기화 1개다.
107 Point 및 전체 색인의 실제 Qdrant 메모리 동작과 두 프로필의 검색 실행은 이 환경에서 완전 검증하지 못했다.
실제 Neo4j/Qdrant/Ollama smoke 테스트와 라이브 DB 건수 조회는 미실행이다.

실행 환경과 주요 명령:

```powershell
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages;$PWD;$PWD/tests/unit"
$python = 'C:/Users/minhyeokju/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $python -X utf8 -m unittest test_candidate_loader_compatibility test_neo4j_loader -v
& $python -X utf8 -m unittest discover -s tests/unit -q
```

구문 검사와 `git diff --check`도 실행한다. 전체 회귀 통과로 표기하지 않는다.

## 7. 보존·협의·커밋 상태

- 변경 대상: `database/neo4j/load_graph.py`, `database/hybrid_search.py`, `tests/unit/test_neo4j_loader.py`, 신규 `tests/unit/test_candidate_loader_compatibility.py`, 본 보고서.
- 원본 데이터·후보 Git 객체·실험 JSON·Issue #21 코드 변경 없음. `0645f90`도 별도 브랜치에 그대로 보존된다.
- 실제 DB 접속/쓰기/삭제/컨테이너 기동·모델 다운로드·OpenAI API 호출 없음. 따라서 Qdrant 5개 컬렉션 및 Neo4j 기존 1,034/3,615를 변경하는 작업은 하지 않았다. 실제 수치 재측정은 하지 않았다.
- merge/rebase/cherry-pick/브랜치 전환/push/PR 없음. 환경 복구 후 최종 검증 결과와 커밋 판단은 §8에 기록한다.
- 후보 데이터의 의미 문제 및 아래 계약을 합의하기 전 실제 데이터 적재 승인으로 간주하면 안 된다.

후속 우선순위:

1. Python 환경 복구 및 Qdrant·전체 색인·두 프로필 단위 회귀는 §8에서 완료. 실제 서버 smoke 검증은 별도 실행 범위다.
2. 후보의 2026-11-06 nullable 개정을 공통 기준 계약에 반영할 범위와 적용 버전을 확인. 후보 자체 상태는 여전히 PENDING_DB_REVIEW다.
3. `raw_value`/`condition_type`의 선택 문자열 계약을 데이터 담당과 공식 문서로 정리. 현재는 Issue #21의 실제 생성 형식에 맞춰 보존하며 임의 객체·숫자를 허용하지 않는다.
4. 후보에 남아 있는 조건 의미 오류와 manifest에 연결된 정적 경고 목록을 별도 검증. 실제 적재는 이번 범위 밖이다.

## 8. Python 환경 복구 및 최종 회귀

### 정확한 원인과 복구 방식

이전 `324 통과 / 27 오류 / 27 건너뜀`은 pytest가 아닌 **unittest** 결과였다.
당시 실행 파일은 번들 Python **3.12.14**였고, `PYTHONPATH`로 기존 `.venv/Lib/site-packages`를 가져왔다.
기존 `.venv/pyvenv.cfg`는 현재 없는 `C:/Users/minhyeokju/AppData/Local/Programs/Python/Python313/python.exe`와
Python **3.13.15**를 가리킨다. 기존 `grpcio`의 실제 바이너리는 `cygrpc.cp313-win_amd64.pyd`다.
따라서 Python 3.12에서 gRPC import가 실패했고, qdrant-client 래퍼가 이를 단순 미설치 오류로 표시했다.
NumPy와 pydantic_core도 cp313 wheel이어서 기존 site-packages를 혼용하는 방식은 사용할 수 없다.
활성화된 가상환경과 PATH상의 pytest 명령은 없었고, 기존 환경에도 pytest 배포판은 없었다.
다른 프로젝트의 기존 가상환경도 사라진 uv Python 경로를 참조해 재사용할 수 없었다.

기존 `.venv`를 수정·삭제하지 않고, Git 제외 경로 **`venv/issue5-py312`**에 별도 venv를 생성했다.
가상환경 활성화에 의존하지 않고 그 안의 Python으로 `-I -m pip`와 `-I -m pytest`를 실행했다.
`-I`는 외부 PYTHONPATH/사용자 site-packages 혼입을 차단한다. 확인된 site-packages는 새 venv 내부뿐이다.
pip 대상은 새 venv의 `Lib/site-packages`이며, `pip check`는 **No broken requirements found**였다.

| 구성 | 복구 환경 |
|---|---|
| Python | 3.12.14, `venv/issue5-py312/Scripts/python.exe` |
| pip / pytest | 25.0.1 / 8.3.5 |
| grpcio / grpcio-tools | 1.85.1 / 1.85.1, cp312 Windows wheel |
| qdrant-client / neo4j | 1.12.2 / 5.28.8 |
| numpy | 2.5.4, cp312 Windows wheel |
| pydantic / pydantic_core | 2.14.3 / 2.50.4, core는 cp312 wheel |
| httpx / requests / python-dotenv | 0.28.1 / 2.34.2 / 1.2.4 |

애플리케이션 의존성 버전은 기존 환경의 설치 메타데이터와 동일하게 고정했다. pytest만 기존에 없어서
3.12와 호환되는 8.3.5를 별도로 고정했다. 공용 requirements 및 시스템 Python은 변경하지 않았다.
첫 복구 실행에서 python-dotenv 누락으로 1개 실패·7개 skip이 발생했으며, 기존과 같은 1.2.4를
격리 환경에 설치한 후 모두 실행됐다. 테스트 기준이나 skip 조건을 수정하지 않았다.

추가로 신규 후보 테스트를 `-I` 단독 실행하면 프로젝트 import 경로가 없어 수집이 실패하는 문제를 확인했다.
기존 테스트들과 같은 프로젝트 루트 경로 설정을 넣어 단독 실행도 12/12 통과하도록 했다.

### 최종 결과

전체 실행: **381 passed / 0 failed / 0 errors / 36 skipped**.
단위 테스트 **381개 전부 통과**, 단위 테스트 skip **0개**다.

| 주요 범위 | 통과 | 실패/오류 | skip |
|---|---:|---:|---:|
| 후보 데이터·null·메타데이터·판정 보류 신규 검사 | 12 | 0 | 0 |
| Neo4j 로더 | 60 | 0 | 0 |
| Qdrant/vector store | 50 | 0 | 0 |
| Hybrid Search | 58 | 0 | 0 |
| 0.6B/4B 검색 프로필 | 17 | 0 | 0 |
| 전체 색인 | 15 | 0 | 0 |
| 나머지 단위 테스트 | 169 | 0 | 0 |
| 라이브 Hybrid 프로필 smoke | 0 | 0 | 8 |
| 라이브 Hybrid 검색 smoke | 0 | 0 | 9 |
| 라이브 Neo4j smoke | 0 | 0 | 10 |
| 라이브 Qdrant smoke | 0 | 0 | 9 |

남은 36개는 `RUN_NEO4J_SMOKE`, `RUN_QDRANT_SMOKE`, `RUN_HYBRID_SMOKE`를 0으로 둔
명시적 opt-in 서버 검사다. 사유는 모두 `NOT_RUN: RUN_…=1 일 때만 실제 서버에 접속한다`이며,
패키지 미설치나 바이너리 오류 때문에 건너뛴 단위 테스트는 없다. 이 작업은 서버 접속·모델 호출 없이
메모리 Qdrant/FakeGraphRunner/가짜 embedder로 검증했다. 라이브 서버 smoke는 완료로 표시하지 않는다.

이전 unittest 378개와 현재 pytest 단위 381개의 차이는 Qdrant 테스트 클래스 초기화 실패가 이전에는
클래스 오류 1건으로 집계된 데 따른 수집/집계 차이다. 테스트를 삭제하거나 정답을 바꾸지 않았다.

### 계약 및 보존 최종 확인

- 후보 `0a09126`의 2026-11-06 계약은 `official_apply_url`과 문서 `law_article`/`effective_date`/`version`을 Nullable로 명시한다. 현재 브랜치의 구 계약과 차이는 그대로 보고하며 계약 문서를 임의 수정하지 않았다.
- `raw_value`/`condition_type`은 후보 계약에 정식 필드 명세가 아직 없다. Issue #21이 생성한 문자열 표현을 선택 메타데이터로 보존하고, 누락/null을 허용하며 객체·숫자 등 잘못된 타입을 차단하는 현재 구현을 검증했다. 이것을 팀의 최종 계약 확정으로 간주하지 않는다.
- 새 후보 전용 문서 속성 및 관계 메타데이터는 기존 데이터에 필수로 요구하지 않는다. 기존 v1.3.2 검사와 후보 검사가 모두 통과했다.
- 후보의 과거 31개 및 현재 250개 null 거부 재현, 수정 후 검사 통과, 필수 필드 누락/null/타입 차단, 빈 AND와 needs_review 보류, 관계 값 보존이 모두 재검증됐다.
- 데이터·평가 v0 질문/정답·Issue #21 코드·실험 중간 JSON은 변경하지 않았다. 실서버 DB 접속·쓰기·삭제, OpenAI 호출, 모델 다운로드 없음. DB 건수를 재측정한 결과는 아니다.
- 환경 자체, pip 설치 기록, pytest XML 및 복구용 파일은 `venv/` 안에 두어 커밋에서 제외한다. 기능 변경 5개 파일만 경로를 지정해 커밋 대상으로 삼는다.
- 환경 차단과 기능 회귀는 해소돼 로컬 기능 커밋이 가능하다. 실제 후보 데이터 배포/적재 승인 및 PR에는 후보의 의미 오류, 공통 계약 개정 적용, 선택 관계 속성 합의와 라이브 검증 범위를 따로 명시해야 한다.

재실행 명령(프로젝트 루트):

```powershell
$python = 'venv/issue5-py312/Scripts/python.exe'
$env:RUN_NEO4J_SMOKE = '0'
$env:RUN_QDRANT_SMOKE = '0'
$env:RUN_HYBRID_SMOKE = '0'
& $python -I -X utf8 -m pip check
& $python -I -X utf8 -m pytest tests -q -ra --tb=short
& $python -I -X utf8 -m pytest tests/unit/test_candidate_loader_compatibility.py -q
```

독립 환경의 전체 resolved 버전 목록(재구성 시 아래 블록을 격리된 환경의 requirements 파일로 사용):

```text
annotated-types==0.8.0
anyio==4.16.0
certifi==2026.10.23
charset-normalizer==3.5.2
colorama==0.4.6
grpcio==1.85.1
grpcio-tools==1.85.1
h11==0.16.0
h2==4.4.1
hpack==4.2.0
httpcore==1.0.9
httpx==0.28.1
hyperframe==6.1.0
idna==3.20
iniconfig==2.3.1
neo4j==5.28.8
numpy==2.5.4
packaging==26.3
pluggy==1.7.0
portalocker==2.10.1
protobuf==7.36.2
pydantic==2.14.3
pydantic_core==2.50.4
pytest==8.3.5
python-dotenv==1.2.4
pytz==2026.6
pywin32==312
qdrant-client==1.12.2
requests==2.34.2
setuptools==84.2.1
typing-inspection==0.4.5
typing_extensions==4.16.0
urllib3==2.8.0
```
