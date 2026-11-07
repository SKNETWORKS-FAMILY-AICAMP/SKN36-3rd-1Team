# PR #16 신청 주소 교정 및 후보 검증

작성일: 2026-11-06 KST. 기준 브랜치: `feature/data-v1.4.0-candidate-audit`, 시작 커밋: `92dfad2`.

## 1. 결과와 범위

- 후보 정책 246건 중 기존 정책 **219건의 `official_apply_url`만 null로 교정**했다.
- 신청 주소가 있는 정책은 22건, null은 224건이다. 22건은 원천 신청 필드에 근거한 값이며, 현재 접수 화면을 직접 검증한 결과는 아니다.
- `source_url`, ID, 수집 시각, 기존 문서·관계·청크·문서 해시는 보존했다. 기존 정제본과 동결된 `data/handoff/`, `data/releases/v1/`도 수정하지 않았다.
- 수집기·정제 코드에도 같은 검토 함수를 적용했다. 참고 주소를 신청 주소로 올리거나, 신청 주소가 없을 때 출처 주소로 채우는 동작을 제거했다.
- `.gitattributes`에서 해시로 참조하는 증거 파일의 바이트와 패키지에 복사하는 로더의 LF 줄바꿈을 고정했다. Windows 재체크아웃에서도 참조 해시가 바뀌지 않도록 한 조치다.
- 후보용 Neo4j 검증 로더와 Qdrant payload 검증 도구를 준비하고 실제 임시 DB에서 검증을 통과했다. Neo4j는 검증 후 롤백했고 Qdrant는 임시 컬렉션을 정리했다. 마지막에 검증 컨테이너와 네트워크도 정리했다.
- 커밋·푸시 및 PR 설명 반영 이력은 PR #16의 Git 기록을 따른다. 머지·배포는 이번 작업 범위에 포함하지 않는다. DB 담당자의 브랜치나 화면 담당자의 코드는 수정하지 않았다.

## 2. 왜 163건보다 많이 바뀌었는가

| 기존 정책의 변경 원인 | 건수 | 처리 근거 |
|---|---:|---|
| 복지로·정부24 원천에 신청 주소 없음 | 156 | 복지로 XML의 신청방법 링크 없음 99건, 정부24 신청 URL 빈 값 57건 |
| 원래 출처 주소와 동일한 기타 공고·안내 | 7 | LH 1, 주택도시기금 5, 고용24 1 |
| 온통청년 신청 필드가 비어 참고 URL로 대체됨 | 47 | `aplyUrlAddr`는 비어 있고 `refUrlAddr1`만 있음 |
| 신청 필드에 들어 있지만 확인된 안내·공고 경로 | 7 | 대구 청년정책 안내 6, 한국콘텐츠진흥원 공고 1 |
| HTTP(S) 주소 형식이 아님 | 2 | 주소 앞의 프로토콜을 임의로 추정해 붙이지 않음 |
| 합계 | **219** | 전부 `official_apply_url`만 변경 |

기존 null 5건을 더하면 후보의 null은 224건이다. 전체 판정은
`data/releases/v1.4.0-candidate/application_url_review.json`, 변경 전후 값은
`release_delta_manifest.json`의 `updated_policy_properties`에 있다.

원천에서 추출한 신청 필드·참고 필드·원본 경로·원본 해시는
`data/metadata/application_url_evidence_v1.4.0.json`에 보존했다.
원본이 있는 환경에서는 빌더가 해시를 대조하며, 원본이 없는 환경에서는 이 추출 증거로 주소 판정을 재현한다.
증거에 없는 새 정책은 임의로 판정하지 않고 빌드를 중단한다.

## 3. 핵심 코드 설명

### 신청 주소와 참고 주소를 구분하기

기존 흐름은 아래와 같았다.

```python
aply_url = item.get("aplyUrlAddr") or item.get("refUrlAddr1") or ""
official_apply_url = aply_url or source_url
```

`or`는 앞 값이 비어 있으면 다음 값을 고른다. 일반적인 기본값 처리에는 편리하지만,
여기서는 **신청 주소가 없다는 사실을 참고·출처 주소로 가리는 문제**가 있었다.

개선한 흐름은 다음과 같다.

```python
raw_aply_url = (item.get("aplyUrlAddr") or "").strip()
aply_url = application_url_or_none(raw_aply_url, source_url)
```

1. 첫 줄: 신청 필드만 읽는다. `.strip()`은 앞뒤 공백을 없앤다. 원천 값은 별도로 보존한다.
2. 둘째 줄: HTTP(S) 주소인지, 출처와 같은지, 확인된 안내·공고 경로인지 검사한다.
3. 사용할 신청 주소가 없으면 Python의 `None`을 반환한다. JSON 파일로 쓰면 `null`이 된다.
4. `refUrlAddr1`은 이 함수에 넘기지 않는다. 참고 주소를 신청 주소로 승격시키지 않기 위해서다.

`review_application_url()`은 `(주소, 사유)` 두 값을 반환한다. 수집기는 주소만 쓰고,
후보 빌더는 사유까지 기록한다. 같은 검사 기준을 두 곳에 따로 작성하지 않아 이후 수정 누락을 줄인다.
복지로처럼 링크가 여러 개면 `first_application_url()`이 순서대로 검사한다.

이 검사는 모든 기관의 모든 안내 경로를 아는 목록이 아니다. 새 경로와 남은 22건의 실제 신청 가능 여부는 별도 확인이 필요하다.

### 관계가 엉뚱한 노드에 연결되지 않도록 하기

이전 로더는 라벨 없이 여러 속성의 `OR` 조건으로 노드를 찾았다.
Document와 ConditionGroup도 `policy_id`를 가질 수 있어 Policy와 동시에 매칭될 수 있었다.
후보용 로더는 관계 종류별로 고정한 노드 종류와 기본 키를 사용한다.

```cypher
UNWIND $rows AS row
MATCH (src:Policy {policy_id: row.source_id})
MATCH (tgt:Document {document_id: row.target_id})
MERGE (src)-[r:SUPPORTED_BY]->(tgt)
SET r += row.properties
```

1. `UNWIND`: 전달한 목록을 한 행씩 처리한다.
2. 첫 `MATCH`: Policy 종류의 노드만 정책 기본 키로 찾는다.
3. 둘째 `MATCH`: Document 종류의 노드만 문서 기본 키로 찾는다.
4. `MERGE`: 같은 연결이 있으면 재사용하고 없으면 만든다. 반복 적재로 중복되는 일을 막는다.
5. `SET`: 근거 문서·위치·검수 상태 등 속성을 넣는다. 값은 문자열 조합이 아니라 파라미터로 전달한다.

노드 종류·관계 이름은 코드의 고정 목록에서만 고른다. 기대 건수는 과거의 237건으로 고정하지 않고 후보 파일에서 계산한다.
Neo4j는 null 속성을 보관하지 않으므로 파일의 선택 속성 null은 허용하고, 조회 대조에서는 없는 속성으로 비교한다.
필수 속성 null이나 저장할 수 없는 중첩 딕셔너리는 적재 전에 거절한다.

주요 검사는 입력을 한 번씩 읽는 방식이다. 파일 크기에 비례한 해시 검사와 노드·관계·청크 수에 비례한 검사를 수행한다.
복잡한 최적화나 새 패키지를 추가하지 않았다.

## 4. 실행한 검증

| 검사 | 결과 | 증거 |
|---|---|---|
| 동결 인계 패키지 | 13/13 통과 | `python scripts/verify_handoff_package.py` |
| 후보 파일·필수 속성·자료형·관계 끝점 종류·UUID·Graph/Vector 연결·해시 | 통과 | `data/metadata/candidate_offline_verification.json` |
| 회귀 테스트 | 13개 통과 | `python -m unittest discover -s tests -p "test_candidate_release.py" -v` |
| 원본을 수정한 수집기로 읽기 전용 재처리 | 복지로 99, 정부24 65, 온통청년 65건의 신청 주소 판정 일치 | `data/metadata/candidate_collector_replay.json` |
| 문서 본문 해시·원본 경로 | 261건 일치, 원본 누락 0 | `data/metadata/candidate_source_verification.json` |
| 청크 내용과 원문 본문 대조 | 599건 포함 확인, 불일치 0 | 같은 source 보고서, 공백·줄바꿈 차이만 정규화 |
| 연속 재빌드 | 핵심 파일 18개 해시 일치 | `data/metadata/candidate_rebuild_hashes.json` |
| Python 구문 검사 | 변경한 Python 파일 통과 | `ast.parse` |
| 공백·패치 검사 | 통과 | `git diff --check` |
| 커밋 대상 바이트 해시 | handoff·후보 패키지 불일치 0 | Git index의 실제 바이트로 매니페스트 전수 대조 |
| Windows 체크아웃 줄바꿈 검사 | 증거 파일 참조 해시·복사 로더 일치 | `core.autocrlf=true`의 Git 필터 결과 대조 |
| 실제 Neo4j 적재 | **PASS — 노드 1,074·관계 3,703의 2회 적재 및 전체 속성 대조, 롤백 후 노드 0** | `data/metadata/candidate_neo4j_live_verification.json` |
| Neo4j 제약조건·인덱스 | **PASS — DDL 13개, 유일성 제약조건 9개·보조 인덱스 4개, ONLINE 및 적용 후 재적재 검사 통과** | `data/metadata/candidate_neo4j_schema_verification.json` |
| 실제 Qdrant payload 적재 | **PASS — 599개 ID·payload의 2회 저장 및 전체 값·정책별 필터 건수 대조** | `data/metadata/candidate_qdrant_live_verification.json` |
| 임베딩·의미 검색·React 버튼 | **NOT_TESTED** | 담당자 환경·모델·화면 연동 필요 |

재빌드 검사에서는 빌드 일시가 들어가는 `manifest.json`, `CHANGELOG.md`, `release_delta_manifest.json`의 바이트 동일성은 요구하지 않았다.
데이터·로더·주소 판정 파일은 일치했고, 변경 목록의 내용은 회귀 테스트로 따로 대조했다.
Graph Document 파일에는 본문이 없으므로, 로더의 `document_text_hashes_checked: 0`은 별도 source 검증 보고서의 본문 검사 261건과 구별해야 한다.
`candidate_offline_verification.json` 및 `candidate_qdrant_verification.json`의 DB `NOT_TESTED`는 `--apply` 없이 실행한 사전 검사 기록이다. 실제 DB 판정은 위의 `*_live_verification.json`을 따른다.

초기 테스트 준비 중 파서 인수 형식 오류와 sandbox 임시 폴더 쓰기 권한 오류가 있었다.
저장소 안의 `sample_only` XML 픽스처를 읽는 방식으로 고친 뒤 최종 테스트가 모두 통과했다.
공용 포맷터·정적 타입 검사기는 팀에서 확정하지 않아 실행하지 않았다.

현재 후보 규모는 정책 246, 문서 261, 청크 599, 그래프 노드 1,074, 관계 3,703이다.
동결 handoff manifest의 SHA-256은 계속 `9cdc54322b269bb245f9d0a99602171bf0937087ca57ee11432b5c778378bb85`이다.

`data/releases/v1/manifest.json`이 참조하는 정부24 파일 두 개(`gov24_public_services.jsonl`, `gov24_merged_services.jsonl`)는 기존 PR의 대용량 파일 제외 결정으로 Git에 없다. 이번 로컬 검증에서는 복원된 파일로 해시가 일치했다. Git에 있는 v1 파일의 해시도 일치하지만, 새 체크아웃에서 v1 전체·원본 본문·회귀 테스트를 재현하려면 Git 제외 원본과 해당 두 파일을 별도로 복원해야 한다. 이를 "저장소에 모든 검증 입력이 포함됨"으로 해석하면 안 된다. 동결 파일이나 매니페스트를 바꾸거나 100MB 초과 파일을 추가하지 않았다.

## 5. 실제 DB 검사 절차

사용자가 임시 Docker DB 준비를 허용하고 `.env`의 접속 정보를 직접 설정했다. 인증 연결 확인 후 실제 DB 검증을 실행했다. 비밀번호·키를 만들거나 대신 쓰거나 출력하지 않았다.
기존 개발용 구성 대신 `docker-compose.verify.yml`과 별도 프로젝트 이름을 사용한다.

실제 검증 환경은 Neo4j **5.26.32**, Qdrant **1.12.4**이다. 사용한 이미지 태그·다이제스트는 `data/metadata/candidate_db_environment.json`에 있다.
컨테이너는 `lifepolicy-candidate-review-neo4j-1`, `lifepolicy-candidate-review-qdrant-1`만 기동했다. 검증 후 전용 Compose 프로젝트를 `down --volumes`로 정리했고, 해당 프로젝트 컨테이너가 남지 않은 것을 확인했다. 다운로드한 이미지는 캐시에 남아 있다.

`.env`에 필요한 설정:

```dotenv
NEO4J_URI=bolt://127.0.0.1:17687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<사용자가 직접 정한 비밀번호>
QDRANT_URL=http://127.0.0.1:16333
QDRANT_API_KEY=<사용자가 직접 정한 긴 인증문자열>
```

아래 명령으로 실제 검증을 수행했다. Compose 설정 전문은 비밀번호를 포함할 수 있어 출력하지 않았다.

```bash
docker compose -p lifepolicy-candidate-review -f docker-compose.verify.yml up -d
python scripts/load_candidate_neo4j.py --apply --http-url http://127.0.0.1:17474 --report data/metadata/candidate_neo4j_live_verification.json
python scripts/verify_candidate_qdrant.py --apply --url http://127.0.0.1:16333 --report data/metadata/candidate_qdrant_live_verification.json
docker compose -p lifepolicy-candidate-review -f docker-compose.verify.yml down --volumes
```

- Neo4j: 비어 있는 DB에서만 실행한다. 한 트랜잭션에서 적재·조회 대조·재적재·재대조한 뒤 기본적으로 롤백한다. 기존 노드 삭제 쿼리는 사용하지 않는다. 스키마 제약조건·인덱스 DDL은 적용하지 않는다.
- DDL은 별도 검사에서 후보의 `cypher_ddl.cypher`를 적용했다. 9개 종류의 기본 키 제약조건과 보조 인덱스 4개를 확인하고, ONLINE 상태를 기다린 뒤 같은 적재 검사를 다시 통과했다. 로더 결과의 `schema_constraints_applied: false`는 로더 자체가 DDL을 적용하지 않았다는 뜻이며, 별도 DDL 검증 실패를 뜻하지 않는다.
- Qdrant: 기존 컬렉션이 있으면 거절한다. 새 검증 컬렉션에 ID와 payload를 두 번 넣고, 전체 payload와 정책별 필터 건수를 비교한다. 이 실행에서 만든 컬렉션만 정리한다.
- Qdrant에 가상 임베딩을 넣지 않는다. [공식 FAQ의 벡터 없는 point 저장](https://qdrant.tech/documentation/faq/) 방식으로 payload만 검사한다. 임베딩 모델·차원은 아직 별도 결정 대상이다.
- Neo4j는 기존 프로젝트의 5.26 이미지에 맞춰 [HTTP 명시적 트랜잭션](https://neo4j.com/docs/http-api/current/)을 사용한다. 운영 연결 방식 변경이나 DB 담당자 로더의 대체를 확정한 것은 아니다.

## 6. 남은 검증과 팀 확인

1. 임시 DB의 실제 적재·재적재 검증은 PASS다. 운영 DB에 데이터를 남겨 적재하거나 운영 서비스와 연동한 결과는 아니다.
2. 민혁님 브랜치의 로더에도 선택 속성 null 처리·관계별 라벨 지정·후보 기대 건수가 반영됐는지 확인해야 한다. 이번에 준비한 것은 후보 검증 도구다.
3. 임베딩 모델·차원 합의 후 실제 벡터 적재, Graph+Vector 검색, 평가 질문을 검증해야 한다.
4. 화면은 신청 주소가 null일 때 신청 버튼을 숨기고, 원문 보기에는 `source_url`을 사용해야 한다. 프론트엔드 코드는 이번 범위에서 변경하지 않았다.
5. 남은 신청 주소 22건의 최종 이동 경로·현재 접수 가능 여부를 확인해야 한다. 값이 있다는 것만으로 현재 접수 중이거나 자격을 충족한다는 뜻은 아니다.
6. 신규 정책 9건의 조건 그룹은 여전히 `UNKNOWN`이다. 자격 조건 구조화·정책 내용 최신성 전체 검수는 하지 않았다.
7. PR 설명은 "인계 원본 보존, 후보에서 기존 정책 219건의 신청 주소 교정"으로 표기한다. 실제 임시 DB 적재 검증과 미검증인 임베딩·의미 검색을 구분한다.

## 7. 변경·생성 파일

- 공통 안내·줄바꿈: `README.md`, `docker-compose.verify.yml`, `.gitattributes`
- 수집기: `collectors/api/youthcenter_collector.py`, `collectors/api/gov24_collector.py`, `collectors/api/bokjiro_collector.py`
- 정제: `collectors/preprocessing/application_urls.py`(신규), `collectors/preprocessing/normalize.py`, `scripts/build_clean_dataset.py`
- 후보 생성: `scripts/build_incremental_release_candidate.py`, `scripts/review_candidate_application_urls.py`(신규)
- 검증 도구: `scripts/load_candidate_neo4j.py`, `scripts/verify_candidate_qdrant.py`, `scripts/verify_candidate_sources.py`(신규)
- 테스트: `tests/test_candidate_release.py`, `tests/fixtures/bokjiro_no_apply_sample_only.xml`(신규)
- 후보 산출물: `data/releases/v1.4.0-candidate/neo4j/nodes_policy.jsonl`, `neo4j/load_neo4j.py`, `manifest.json`, `release_delta_manifest.json`, `CHANGELOG.md`, `application_url_review.json`(신규)
- 증거·검증 결과: `data/metadata/application_url_evidence_v1.4.0.json`, `candidate_offline_verification.json`, `candidate_qdrant_verification.json`, `candidate_collector_replay.json`, `candidate_source_verification.json`, `candidate_rebuild_hashes.json`(신규)
- 실제 DB 검증 기록: `data/metadata/candidate_neo4j_live_verification.json`, `candidate_neo4j_schema_verification.json`, `candidate_qdrant_live_verification.json`, `candidate_db_environment.json`(신규)
- 문서: `docs/data/gov24_field_mapping.md`, `release_candidate_v1.4.0_audit.md`, `v1.4.0_candidate_fix_report.md`, `official_source_crosscheck_report.md` 및 이 보고서
