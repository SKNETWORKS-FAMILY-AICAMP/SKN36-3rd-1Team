# Issue #43 — 최종 DB 재적재 및 독립 인계 검증 (2026-11-10)

## 독립 브랜치 기준 결과

현재 worktree는 feature/43-final-db-reload-handoff / a44bf08이다.
#41의 A/B 소스·신규 4B 구성·구성 목록 테스트 변경은 포함하지 않았다.
재적재 문서와 통합 스모크 두 파일의 최초 복사 SHA-256이 원본과 일치함을 확인했다.
기존 #41 파일·Docker 데이터·297개 4B Point는 보존했고 A/B 평가는 실행하지 않았다.

| #43 독립 검증 범위 | 결과 |
|---|---|
| v1.4.0·기본값·검색·복원 안전 테스트 | 49개 통과 |
| 레거시·후보 호환·Neo4j·색인·검색·청킹·평가 | 319개 통과 |
| 후보 릴리스 전체 | 25개 중 23개 통과, 1 failure / 1 error |
| 실제 최종 DB Hybrid/checker·Neo4j | 18개 통과 |

합계 **411개 중 409개 통과 / 1 failure / 1 error**다.
레거시 test_hybrid_profiles_smoke 클래스 skip 1건은 별도로 기록한다.
원래 415개 집계는 #41 변경을 포함한 혼합 작업 트리 이력이며 현재 결과와 혼용하지 않는다.

남은 failure는 원본 파일 누락으로 verify_sources가 FAILED를 반환한 건이다.
error는 Git 제외 파일 data/releases/v1/gov24_public_services.jsonl 누락이다.
원래 작업 폴더의 handoff/증거 파일 CRLF 바이트 해시 오류는 새 worktree에서 재현되지 않았다.
데이터 수정으로 통과시킨 것이 아니며 승인 데이터·원본 증거는 변경하지 않았다.

POL_005, POL_GOV_006, POL_GOV_011, POL_009의 실제 Hybrid/checker 연결과
전체 1,138개 Point의 일곱 가지 교차 DB 검사가 통과했다.
POL_GOV_006의 레거시 suspected 경고 기대값을 최신 데이터에 강제하지 않았다.

## 팀원용 패키지와 깨끗한 복원 검증

[팀원용 안내](ISSUE43_RESTORE_GUIDE.md), [Compose](issue43_restore.compose.yaml),
[검증 기록](ISSUE43_RESTORE_VALIDATION_20261110.json)을 함께 검토한다.
복원 도구는 scripts/restore_final_v140.py, 안전 테스트는 tests/unit/test_restore_final_v140.py다.

최신 정합화 0.6B 단일 snapshot SHA-256:
552edbf61b65d364f3e90eb6760527a29ce11b01196dc41e1c736f0e809c762f.
이 snapshot은 기존 컨테이너가 아닌 검증된 적재 후 백업의 새 임시 복제본에서 추출했다.
PR29 ZIP을 최신 최종본으로 표시하지 않았다. 전체 벡터 digest는 기존 기록과 동일하다.

패키지 소스를 새 폴더에 실제 압축 해제하고 새 프로젝트의 빈 DB에서
사전 검사→복원→읽기 전용 재검증을 수행했다. 모두 PASS이며 추가 metadata 수정은 0건이다.
정책 246 / 문서 261 / 원본 청크 599 / B2 1,138 / Neo4j 1,004·2,431 / REQUIRES_CONDITION 136을 확인했다.
전체 벡터 불변, 전체 예상 payload, Graph ID/속성, 실제 정책·문서·SUPPORTED_BY·해시 연결이 일치했다.
복원·검증 도구의 임베딩 호출은 0회다. 실제 스모크는 0.6B 질의만 사용했다.
압축 해제 소스에서도 스모크 18개+안전 테스트 8개 총 26개가 통과했고 레거시 클래스는 별도 skip이었다.

기존 .env는 worktree·패키지·압축 해제 폴더에 복사하지 않았다.
검증에 필요한 인증값만 읽어 자식 프로세스 환경으로 전달하고 출력하지 않았다.
ZIP에는 .env·비밀값·4B 데이터·모델이 없으며 대용량 파일은 Git 추적하지 않는다.

새 검증 자산:
- snapshot 추출 컨테이너 issue43-snapshot-source-20261110 / 볼륨 issue43_snapshot_source_20261110, 포트 46337.
- 빈 DB 복원 Compose 프로젝트 issue43-clean-restore-20261110,
  Qdrant 56336 / Neo4j HTTP 57474·Bolt 57687 및 새 볼륨 3개.
검증 후 새 임시 컨테이너만 중지하고 볼륨을 보존한다. 기존 최종 DB는 계속 실행한다.

검증 범위는 같은 Windows PC의 새 소스 폴더·빈 DB 볼륨이다.
다른 실제 PC/OS의 설치·네트워크·메모리 및 실제 LLM HTTP/UI E2E는 미검증이다.
원본 누락 자료 복원은 별도 담당 작업이며 테스트를 위해 데이터를 바꾸지 않는다.

<details>
<summary>최초 최종 재적재 기록 — 아래 415개 집계는 #41 혼합 작업 트리의 과거 이력</summary>

# 최종 v1.4.0 DB 재적재 — 2026-11-10

## 범위와 Git 보호

팀 우선순위 변경에 따라 Issue #41 4B 색인/A/B 평가를 보류하고 최종 데이터 적재를 수행했다.
브랜치는 `feature/41-embedding-ab-eval`을 유지했다. PR #35와 #37이 병합된
`a44bf08dcd30515bbaaf73fc258350f1e9bcdfc1`까지 fast-forward했다.
upstream 변경 파일과 #41 미커밋 파일이 겹치지 않음을 먼저 확인했다.

미커밋 6개 파일과 패치를 `.cache/final-db-reload-20261110-01/before-dev/`에 백업했다.
반영 직후 6개 파일 SHA-256이 모두 백업과 같음을 확인했다. 이후 #41 실행 문서에 보류 상태만 추가했다.
커밋·Push·PR·새 브랜치 생성은 하지 않았다. 승인 데이터와 질문/정답은 직접 수정하지 않았다.

## 데이터 및 실제 적재 결과

- 데이터: `data/releases/v1.4.0-candidate`.
- Manifest LF SHA-256: `49246d1bda0624f6a10d19b252713f6ffb69375e07321fa615f594a44c698389`.
- 정책 246개 / 문서 261개 / 릴리스 원본 청크 599개 / B2 청크 1,138개.
- Neo4j 실제 노드 **1,004개**, 관계 **2,431개**, `REQUIRES_CONDITION` **136개**.
- 스키마 제약/인덱스 13개 문장 적용. 빈 DB 확인 후 기존 `database.neo4j.load_graph`로 적재했다.
- 노드 ID·관계 양끝 ID 집합·전체 노드/관계 속성과 파일 입력의 일치를 검증했다.
- Qdrant 0.6B B2: **1,138 Point / 1,024차원 / Cosine**.
- 전체 Point ID·본문·위치·해시·모델·입력 규칙·문서별 category/life_stages 등 예상 payload 일치.
- 0.6B 벡터를 재생성하지 않았다. 기존 정합화된 A/B 볼륨을 읽기 전용 복제하여 사용했다.
- 전체 벡터 digest `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df` 불변.
- 실제 Neo4j 정책/문서 노드와 전체 Qdrant Point의 ID·content_hash·SUPPORTED_BY 연결 검사 통과.

기존 Qdrant/Neo4j 볼륨과 이전 조사·A/B 볼륨을 수정하거나 삭제하지 않았다.
A/B 볼륨 복제 전후 파일 해시와 복제본 초기 해시가 일치했다.
복제 전 TAR `ab-paused-before-final.tar` SHA-256:
`65d82b55cdb6fe130d01fe1780024533975f0815f9fab98f78783870d1cd035d`.

검증 후 새 최종 컨테이너 두 개만 정상 종료해 적재 후 일관된 TAR 백업도 만들고 다시 기동했다.
로컬 `FINAL_SHA256SUMS`에 다음 체크섬을 기록했다.

- `final-qdrant-postload.tar`: `671f6be7ded4d54a2ca04861c6dcf85af51f0ec60079ab37f137915536ecbed1`.
- `final-neo4j-postload.tar`: `377b7f83377e67c64915a54eb66a91ee8b50a720ee629a338ff7df0052083a3f`.

## 최종 전용 환경과 연결 전환

프로젝트 라벨은 `lifepolicy-final-20261110`이다. Compose 파일을 실행한 것이 아니라
설치된 이미지와 Docker CLI로 명시 생성했다. 구성 내 비밀값은 `.env`에서 환경변수로만 전달했다.

| 항목 | 자산/주소 |
|---|---|
| Qdrant 컨테이너 | lifepolicy-final-qdrant-20261110 |
| Qdrant 볼륨 | lifepolicy_final_20261110_qdrant |
| Qdrant URL | http://127.0.0.1:46336 |
| Neo4j 컨테이너 | lifepolicy-final-neo4j-20261110 |
| Neo4j 데이터 볼륨 | lifepolicy_final_20261110_neo4j_data |
| Neo4j 로그 볼륨 | lifepolicy_final_20261110_neo4j_logs |
| Neo4j Bolt | bolt://127.0.0.1:47687 |
| Neo4j HTTP | http://127.0.0.1:47474 |

Qdrant 1.12.4를 digest로 고정했고, Neo4j는 설치된 `neo4j:5.26-community` 이미지 ID로 고정했다.
이미지 ID와 연결 정보는 로컬 `assets.json`에 기록했다. 메모리는 Qdrant 512 MiB,
Neo4j 1,280 MiB 제한이며 Neo4j heap 128~256 MiB / page cache 128 MiB로 구성했다.
모든 공개 포트는 127.0.0.1에만 바인딩했다.

`.env`의 QDRANT_URL·NEO4J_URI·NEO4J_HTTP_URL 세 주소만 위 최종 주소로 전환했다.
비밀값과 다른 모든 설정이 동일함을 검증했다. 검색 프로필은 변경하지 않았으며 기본은 `b2_06b_v140`이다.
기존 프로세스는 환경을 다시 읽도록 재시작해야 한다. 새 `.env` 기본 주소로 실제 스모크가 통과했다.
이전 주소만 포함한 복귀 참고 정보는 로컬 `endpoint-transition.json`에 저장했다(비밀값 없음).

## 4B 보존 상태

원래 A/B 컨테이너 `issue41-ab-run01`은 중지 상태이고 볼륨 `issue41_ab_run01_storage`는 보존했다.
4B 실제 Point는 **297개**, 누락은 **841개**이며 전체 부분 payload 검증을 통과했다.
복제 과정에서 최종 Qdrant에도 같은 부분 컬렉션이 보존되어 있다. 두 환경의 전체 부분 벡터/payload
digest가 동일하다. 최종 검색은 이 부분 컬렉션을 사용하지 않는다.
4B 모델·색인은 재시작하지 않았으며 A/B 평가 결과는 없다.
향후 #41 재개는 최종 환경이 아닌 명시적인 A/B 주소에서 누락분만 이어 적재한다.

## 검증 결과

Python 3.13.15에서 다음 최종 검증을 수행했다.

| 범위 | 결과 |
|---|---|
| #41·v1.4.0 핵심 | 53개 통과 |
| 후보 로더 호환·Neo4j 로더·색인·검색·청킹·평가·릴리스 어댑터 | 319개 통과 |
| 실제 최종 v1.4.0 Hybrid/checker 스모크 | 8개 통과 |
| 실제 Neo4j 전체 속성·ID·관계·스키마 스모크 | 10개 통과 |
| 기존 후보 릴리스 테스트 | 25개 중 22개 통과, 2 failures / 1 error |

중복 재실행을 제외한 위 범위는 **415개 중 412개 통과 / 2 failures / 1 error**다.
기존 `test_hybrid_profiles_smoke.py`는 별도로 실행했지만 v1.3.2의 0.6B/4B-instruct 프로필을
요구하므로 `FullIndexError`를 DATA_NOT_READY로 처리해 클래스 단위 skip이었다.
이는 서버 연결 실패가 아니라 데이터셋/프로필 불일치이며, v1.4.0 통과로 세지 않았다.
기존 레거시 테스트 기대값은 바꾸지 않고 `test_final_v140_smoke.py`를 별도 추가했다.

새 스모크의 초기 두 계약 가정 오류(조건 그룹 간 관계 타입, checker 최상위 status 존재 여부)는
실제 계약인 `CONTAINS_GROUP`, `conditions[].status`/`reception_status`로 수정 후 재실행해 통과했다.
DB·승인 데이터는 이를 위해 변경하지 않았다.

특별 확인 정책:

- `POL_005`: NOT 아래 OR 제외 그룹, 제외 조건 13개 및 `COND_AGE_75_120` 포함.
- `POL_GOV_006`: 날짜 미확정·신청기간 설명 보존, 최신 데이터에서 레거시 정적 플래그 미적용.
  기존 스모크 122~124행의 suspected 경고 기대값은 동결 레거시에 해당하며 최신 데이터에 강제하지 않았다.
- `POL_GOV_011`: 실제 `REQUIRES_CONDITION` 연령이 `COND_AGE_12_24`.
- `POL_009`: 실제 시작/종료일과 별도 신청기간 설명이 릴리스와 일치.
- 4개 정책 모두 정책 필터를 이용한 실제 임베딩→Qdrant→Neo4j→checker 연결 통과.
  이는 연결·근거 검사이며 검색 품질 A/B 평가가 아니다. 테스트 프로필은 sample_only다.

기존 후보 테스트 3건은 #41/최종 적재 결함과 분리한다.
원본 파일 누락으로 원문 존재 검사 실패 1건, handoff의 CRLF 원시 바이트 해시 실패 1건,
신청 URL 증거 파일의 CRLF 바이트 해시 오류 1건이다. 기존 조사에서 LF 정규화 시 해시 일치를 확인했다.
본문 261개 content_hash 및 599개 청크 본문 대조는 일치한다.
테스트를 통과시키려고 승인 데이터·원본 증거 파일을 바꾸지 않았다.

## 재검증 명령과 남은 작업

저장소 루트와 새 `.env` 기본 주소를 사용한다. 값 전체를 출력하지 않는다.

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
$env:RUN_FINAL_V140_SMOKE='1'
$env:RUN_NEO4J_SMOKE='1'
.\.venv\Scripts\python.exe -B -m unittest tests.integration.test_final_v140_smoke tests.integration.test_neo4j_smoke -v
```

남은 작업은 원본 누락/CRLF 테스트 정책 합의, 실제 서비스 프로세스 재시작 및 HTTP/UI E2E 확인이다.
이번에는 실제 OpenAI LLM 호출이나 웹 UI E2E까지 검증하지 않았다.
Issue #41은 별도 지시 후 A/B 환경의 4B 누락 841개를 이어 적재하고 전체 정합성 통과 후 예비 평가한다.

</details>


## PR45 review package r1 ? latest distribution (2026-11-10)

Supersedes the earlier distribution for new restores; historical results below/above remain historical.

- ZIP: `rag-final-v140-pr45-review-worktree-20261110-r1.zip` (local artifact: `.cache/rag-final-v140-pr45-review-worktree-20261110-r1.zip`)
- ZIP SHA-256: `5b053525a13443b4afc0bc7650bf65c9c943429ebdf9ee96437ab248f260e5e8`
- Restore script SHA-256: `5433a24e5fb679e714f42dfe652c10e7a97d9c1643bbf8cb82acad2183f30de1`
- Source: uncommitted Issue #43 worktree based on `3b9da1bfef528e85d3a5f29907c2a1b478b38444`; this is not a new commit SHA.
- Extract `source-pr45-review-worktree.zip` from the bundle. Use this extracted source and its `scripts/restore_final_v140.py`, not the earlier source archive or another checkout.
- Independent ZIP extraction: six internal checksums matched; extracted restore safety tests 24/24 passed on Python 3.13.15. Both `--apply` and `--verify-only` passed against new empty DB volumes.
- Actual counts: Neo4j 1,004 nodes / 2,431 relationships / 136 REQUIRES_CONDITION; Qdrant 1,138 points / 1,024 dimensions / Cosine. Cross-DB PASS, 1,138/1,138; full graph properties verified; vector/protected payload unchanged; embedding calls zero.
- Vector digest: `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df`. Metadata updates zero because the snapshot is already aligned.
- Evidence: [PR45_PACKAGE_VALIDATION_20261110.json](PR45_PACKAGE_VALIDATION_20261110.json). This distribution-side evidence was recorded after packaging; it does not change the sealed ZIP or its checksums.
- Review items 1?5 are included in both script copies. The old ZIP and `.sha256` remain unchanged. The earlier statement that the review ZIP had not yet been rebuilt is historical and superseded by this entry.
- No secrets, `.env`, models, 4B vectors, local caches or test DB volumes are included. `.env.example` contains configuration names only. Dependencies must already be installed; another PC/OS and fresh dependency installation remain unverified.
