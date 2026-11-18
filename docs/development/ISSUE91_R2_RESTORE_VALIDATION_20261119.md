# Issue #91 — 최신 DB 정합화와 r2 복원 번들 검증 기록 (2026-11-19)

수치의 원본은 같은 폴더의 [ISSUE91_R2_RESTORE_VALIDATION_20261119.json](ISSUE91_R2_RESTORE_VALIDATION_20261119.json)이며,
이 JSON은 `.cache/issue91-r2-20261119/`의 실행 보고서에서 직접 생성했다. 비밀번호·API 키·DPAPI 파일은 넣지 않았다.
검증 환경은 한 대의 Windows 11 PC, Python 3.13.15이다. 다른 PC·OS에서의 설치는 검증하지 않았다.

## 1. 목표와 배경

PR #87(검토된 노이즈 청크 27건 적재 제외)과 PR #89(연령·소득·출처 데이터 교정)가 `dev`에 병합되면서
v1.4.0-candidate 릴리스가 바뀌었다. Issue #91의 목표는 최신 릴리스와 일치하는 Qdrant B2 컬렉션과 Neo4j 그래프를
만들고, 팀원이 같은 상태를 복원할 수 있는 r2 번들을 만들어 독립 환경에서 복원까지 검증하는 것이다.

## 2. 병합 이후 데이터 변경

| 항목 | r1 기준(`3b9da1b`) | r2 기준(`027f40b`) |
|---|---|---|
| 정책 / 문서 / 릴리스 청크 | 246 / 261 / 599 | 246 / 262 / 600 |
| B2 Point | 1,138 | 1,139 |
| Neo4j 노드 / 관계 / REQUIRES_CONDITION | 1,004 / 2,431 / 136 | 1,126 / 2,668 / 211 |
| manifest(LF) SHA-256 | `49246d1b…` | `d48ef11b599466b394bef15e275be26b24be9dfde1e74771c86b8e3d384acf50` |

- B2 청크 변화(`61e259e`): 신규 문서 `DOC_BOK_031_MOE_2026`(교육부 2026 안내서 PDF 305쪽) → 신규 Point 1개.
  `DOC_BOK_054`는 릴리스가 지정한 추출본으로 바뀌어 `content_hash`가 바뀌었고, B2 청크 3개(C002~C004)의 본문이 바뀌었다.
- 그래프 변화는 PR #89의 조건 교정(대상자·자녀 연령, 가구·소득, 출처)이며 Neo4j는 전체를 새로 적재했다.
- PR #87의 노이즈 제외(27건)는 릴리스 청크 600 → 적재 대상 573에 적용되며 B2 1,139와는 별개다(검토 대상 해시 불변 확인).

## 3. r1 번들을 그대로 쓸 수 없었던 이유

1. 복원 도구 기준이 1,139 Point·manifest `d48ef11b…`로 바뀌어 r1 번들(1,138 Point, manifest `49246d1b…`)은 검사에서 거부된다.
2. 같은 ID의 Point 5개(DOC_BOK_054)는 `content_hash`·위치·본문이 달라 `alignment_plan`의 보호 필드 검사에 걸린다.
3. r1 ZIP 원본은 이 PC에서 찾지 못했다.

## 4. pr29 snapshot 재사용 판단

- 이전 작업의 B2 컬렉션 덤프(`venv/issue31_20261109/exp_b2_qwen3emb06b_v140_v1.after.json`)의 vector_digest가
  r1 검증 기록 `f5ec8c25…`와 정확히 같았다.
- 덤프 벡터를 Cosine 컬렉션에 upsert하면 Qdrant가 다시 정규화해 float32 값이 바뀐다(인메모리 실측). 그래서 upsert 시드는
  채택하지 않고 pr29 번들의 snapshot(`b2-v1.12.4.snapshot`, `4f7a67ea…`)을 새 격리 Qdrant에 복원했다.
- 복원 직후 실제 Qdrant의 전체 vector_digest가 `f5ec8c25…`로 r1과 같았다(벡터 비트 보존 확인).

## 5. 벡터 1,135개 보존과 신규 4개 임베딩

- 분류: 재사용 1,135(그중 payload 동일 1,000, `category`·`life_stages`만 갱신 133, `content_hash`·위치 갱신 2),
  새 임베딩 4(DOC_BOK_054 C002~C004 본문 변경 3 + `CHK_B2_DOC_BOK_031_MOE_2026_C001` 신규 1), 제거 0.
- 임베딩 입력 규칙(`policy_section_body_v2`: 정책명·섹션명·본문)은 인덱싱 시점 이후 코드 변경이 없고, 재사용 1,135개의
  입력 문자열은 현재 규칙으로 다시 만든 값과 같다.
- 절차(`scripts/update_b2_from_snapshot.py`): payload 135개 교체(`overwrite_payload`, 벡터 불변) → 3개 삭제
  → 기존 `execute_full_index`를 검증한 같은 클라이언트로 실행해 없는 4개만 임베딩(누적 4개 상한).
- 재사용 1,135개 부분 digest `6c6672ca…`는 갱신·삭제·임베딩 전후 모두 같았다.
- 모델: `qwen3-embedding:0.6b`, Ollama 모델 digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`, 1024차원.
  - 신규 4개: Ollama **0.40.16**에서 생성, 실행 ID `RUN_FULL_A_B2_V140_20261119_015749`.
  - 재사용 1,135개: 원래 실행 ID `RUN_FULL_A_B2_V140_20261107_173008`. 당시 Ollama 버전은 **직접 기록이 없다**
    (#41 문서상 로컬 0.40.15로 추정). 모델 digest가 같아도 Ollama 버전이 다르면 수치가 같다고 보장하지 않는다.
- 최종 Qdrant: 1,139 Point, 전체 vector_digest `8577c3c8698fcb891fb35544eac01ec02066fb5c2ef0313d15b6a26e576da3d3`.

## 6. Neo4j 전체 그래프 재적재

`scripts/load_neo4j_r2_isolated.py`로 격리 Neo4j(5.26.32 community, Bolt 56487)에 기존 `load_graph(verify=True)`를 실행했다
(빈 DB 확인 → 스키마 → 노드 → 관계, 배치 128). 노드 1,126·관계 2,668·REQUIRES_CONDITION 211,
`verify_loaded_graph`(건수·스키마·ID·관계 양끝·속성값) 통과.

## 7. 두 DB 교차 검증

기존 `hybrid_search.verify_cross_db`와 `full_index.verify_full_index`(원문 슬라이스)를 읽기 전용으로 실행했다.
7개 항목(point_id, chunk_id_document, policy_exists, document_exists, supported_by, document_policy_id, content_hash)
모두 1,139 PASS, MISSING·MISMATCH·NOT_TESTED 0. 검증 전후 Qdrant 벡터·payload digest와 Neo4j 건수가 같았다.

## 8. snapshot과 ZIP

- snapshot: 격리 Qdrant에서 생성·다운로드. 서버 checksum과 로컬 SHA-256 일치 `2e07c17ad529882fc638af172018cccf61e9477240d6f1630b2099e70640bca0`,
  14,889,472 bytes. 생성 전후 컬렉션 fingerprint 불변.
- 번들(`scripts/build_restore_bundle_v140.py`): 평평한 6개 파일(snapshot, `source-<sha12>.zip`, `compose.yaml`,
  `restore_final_v140.py`, `package.json`, `SHA256SUMS`). `.partial`로 압축 → 압축 내용 전체 재검증(SHA256SUMS·
  `check_bundle`) → 최종 이름. source ZIP은 `git archive`(`core.autocrlf=false`)라 커밋 blob과 바이트 동일하다.

## 9. 확정 커밋과 번들 출처

| 항목 | 값 |
|---|---|
| 소스 커밋 | `027f40b556c5408a2f0b9547acef4964254e37b8` |
| 최종 ZIP | `rag-final-v140-r2-027f40b556c5.zip`, 24,270,102 bytes |
| ZIP SHA-256 | `dd28f8cd14c242bda2e6d5697d5cbf23725e6b7dee7b72f4781830429c579197` |
| source ZIP | `source-027f40b556c5.zip`, SHA-256 `fba51dba…1965`, 528개 파일 blob 일치 |
| snapshot / vector_digest | `2e07c17a…bca0` / `8577c3c8…a3d3` |

ZIP은 Git에 넣지 않는다(로컬 `.cache/issue91-r2-20261119/bundle-r2/`).

## 10. 독립 환경 복원과 결과

- 환경: Compose 프로젝트 `issue91-r2-verify-20261119`(압축을 푼 source의 `issue91_r2_verify.compose.yaml`),
  Qdrant `127.0.0.1:56556`, Neo4j Bolt `127.0.0.1:56587`, 전용 볼륨 3개, 검증 전용 인증값.
- 래퍼(`scripts/restore_r2_isolated.py`, 압축을 푼 source): Docker 소유(프로젝트·서비스·이미지·127.0.0.1 바인딩·전용 볼륨),
  키 없는 Qdrant 401, 틀린 Neo4j 비밀번호 거부, 버전, 빈 서버, 번들·소스 일치를 확인하고 fingerprint를 대조한 뒤
  기존 `restore_final_v140.restore()`를 호출했다.
- preflight 종료 코드 0(fingerprint `8200109a…d483`), `--apply` 종료 코드 0.
- 결과: 복원 `PASS`, payload 갱신 0, 임베딩 0, vector_digest `8577c3c8…`(복원 직후·verify-only·별도 조회 3회 일치),
  Neo4j 1,126/2,668/211, Policy 246, Document 262, `verify_loaded_graph` 통과, `verify_cross_db` 7개 항목 각 1,139 PASS,
  `verify_full_index` 통과(슬라이스 불일치 0).
- snapshot 업로드는 2xx(복원 도구의 `raise_for_status` 통과)이며 정확한 상태 코드는 기록되지 않았다.

### Hybrid 검색·Checker 스모크 (복원된 독립 DB, 같은 PC)

- 실행: 압축을 푼 source에서 `python -B -m unittest tests.integration.test_final_v140_smoke tests.integration.test_neo4j_smoke -v`.
  DB·Ollama·데이터셋 변수를 모두 검증용 값(Qdrant 56556, Neo4j 56587, Ollama 11434, source의 v1.4.0 릴리스)으로 명시했고,
  source에는 `.env`가 없어 다른 DB 주소가 섞이지 않았다. 두 모듈 모두 읽기 전용이다.
- 결과: **19개 실행, PASS 19 · FAIL 0 · SKIP 0**(`Ran 19 tests … OK`), 프로세스 종료 코드 0(실행 세션 출력으로 확인).
  - `test_real_hybrid_and_checker_for_four_policies`: POL_005·POL_GOV_006·POL_GOV_011·POL_009에서 실제 질문 임베딩
    (`qwen3-embedding:0.6b`) → Qdrant → Neo4j 문맥 → 규칙 기반 checker 판정까지 통과(프로필 `b2_06b_v140`, 1024차원).
  - `test_after_school_guide_is_citable`: 신규 문서 `DOC_BOK_031_MOE_2026`(교육부 2026 안내서 PDF 305쪽)이 인용 후보로 검색됨.
  - 교차 검증 1,139 PASS, 교정 데이터 4건, Neo4j 스모크 10개(건수·스키마·ID·관계 양끝·속성·읽기 전용 세션) 통과.
- 실행 전후 불변: 래퍼 `--verify-only` fingerprint `276e8883…`, Qdrant 1,139 Point·ID 집합·vector_digest `8577c3c8…`·
  payload digest `6bdb5a73…`, Neo4j 1,126/2,668과 레이블별 건수가 모두 같았다. 원본 #91 DB fingerprint도 같았다.

## 11. 최초 후보 ZIP과 CLI 종료 코드 버그

첫 후보 `rag-final-v140-r2-fa37b8db47f8.zip`(`37bd6d1d…`)은 정적 검증을 통과했지만 `--build` 성공 시 CLI가 1을 반환했다.
원인은 `main()`의 `{"status": "PASS", **result}`에서 결과의 `status=BUILT`가 PASS를 덮어쓴 것이다. `027f40b`에서
`cli_success()`로 전체 상태(`status=PASS`)와 작업 상태(`result_status=BUILT`)를 나눴고, 최종 ZIP 생성의 실제 종료 코드는 0이다.
첫 후보 ZIP은 보존하되 배포하지 않는다.

## 12. 검증 범위와 제한

검증함: 위 1~11, 단위 테스트(아래), 같은 Windows PC의 별도 Docker 프로젝트·새 볼륨 독립 복원,
복원 DB의 Hybrid 검색·checker 스모크 19개 PASS(§10), 원본 #91 DB 불변.

미검증·제한:
- 다른 PC·OS 설치, 이미지를 처음 내려받는 환경(이 PC는 로컬 이미지 사용).
- 실제 HTTP API → LLM(OpenAI) → 프론트엔드 전체 E2E. 스모크는 검색·checker까지만 확인했다.
- 후속 이슈(#90 등)의 신규 데이터는 r2에 반영되지 않았다.
- 재사용 1,135개 벡터의 생성 당시 Ollama 버전(기록 없음).
- `restore_final_v140.restore()`는 두 DB에 걸친 원자적 처리가 없다. 실패 시 새 DB에 부분 자산이 남고 자동 재실행은 없다.
- 기존 `restore_final_v140.py`를 래퍼 없이 실행하면 환경 변수의 다른 loopback DB로 접속할 수 있다(빈 DB 검사가 마지막 방어선).
- 저장소 전체 단위 테스트(`tests`)는 1,141 passed / 56 failed / 20 errors / 45 skipped이다. 실패·오류는 이번 작업 이전부터
  있던 목록(레거시 데이터셋 실행 조건 58건, 이 PC에 없는 원본 파일 5건 등)이며 이번 Issue로 인한 신규 실패는 0건이다.
  위 스모크 19개 PASS와는 별개의 결과다.

## 13. 기록 위치 (로컬, Git 제외 `.cache/issue91-r2-20261119/`)

| 단계 | 보고서 |
|---|---|
| 빈 서버·snapshot 복원·갱신·임베딩 | `01-dry-empty.json`, `02-restore.json`, `03-dry-restored.json`, `06-apply.json`, `07-dry-ready.json`, `08-index.json`, `10-dry-complete.json`, `10-new-points.json` |
| Neo4j 적재·검증 | `21-neo4j-dry-empty.json`, `22-neo4j-load.json`, `23-neo4j-verify.json`, `23-neo4j-label-counts.json` |
| 교차 검증 | `24-cross-db.json`, `24-*-dry-pre.json`, `25-*-dry-post.json` |
| snapshot·번들 | `31-create-snapshot.json`, `bundle-r2/snapshot-record.json`, `50-build-027f.json`, `bundle-r2/*.report.json` |
| 독립 복원 | `61-extract.json`, `62-verify-preflight.json`, `64-verify-preflight-recheck.json`, `64-verify-apply.json`, `65-verify-db-state.json` |
| Hybrid·checker 스모크 | `71-smoke-stderr.log`(unittest 결과), `70-verify-only-pre.json`, `72-verify-only-post.json`, `70-digests-pre.json`, `72-digests-post.json` |
| 원본 불변 | `60/63/64/65-orig-*-*.json`, `72-orig-*-after-smoke.json`, Docker 전후 목록 `*-before-*`·`*-after-*` |

## 14. 팀원 복원 절차

팀원 PC는 [ISSUE91_R2_TEAM_LOCAL_DB_GUIDE.md](ISSUE91_R2_TEAM_LOCAL_DB_GUIDE.md)(설치부터 백엔드 연결까지),
작성자 검증 절차는 [ISSUE91_R2_VERIFY_RUNBOOK.md](ISSUE91_R2_VERIFY_RUNBOOK.md)를 따른다. 요약:
1. ZIP과 `.sha256`을 받아 SHA-256 `dd28f8cd…9197`을 확인하고 새 폴더에 푼다. `source-027f40b556c5.zip`도 새 폴더에 푼다.
2. 압축을 푼 source의 `docs/development/issue91_r2_verify.compose.yaml`로 새 프로젝트를 띄운다(전용 인증 변수, `config --quiet`).
3. 압축을 푼 source의 `scripts/restore_r2_isolated.py`로 preflight → `--apply --confirm-write --expect-fingerprint` → `--verify-only`.
4. 기대값: §10과 같음. 실패하면 재실행하지 말고 새 프로젝트·새 볼륨으로 다시 시작한다.

`restore_final_v140.py`를 직접 실행하는 이전 절차([ISSUE43_RESTORE_GUIDE.md](ISSUE43_RESTORE_GUIDE.md))는 기록으로 남아 있다.

## 15. 기존 DB·볼륨 보존

- 모든 쓰기는 새 Compose 프로젝트(`issue91-r2-qdrant-*`, `issue91-r2-neo4j-*`, `issue91-r2-verify-*`)의 새 볼륨에만 했다.
  공유 DB(`skn36-3rd-1team`, `lifepolicy-*`)에는 접속하지 않았다.
- 스크립트는 loopback + 고정 포트만 허용하고(공유 포트 목록 거부), 서버 버전·컬렉션 구성·Docker 소유를 확인하며,
  dry-run fingerprint가 같을 때만 쓴다. `.env`·`QDRANT_URL`·`NEO4J_URI`는 대상 결정에 쓰지 않는다.
- `down -v`, `volume rm`, `prune`, 컬렉션 삭제는 쓰지 않았다. 원본 #91 DB fingerprint
  (Qdrant `fb1a1000…7235`, Neo4j `54094d52…25ff`)는 독립 복원 전후 같았다.
- 기존 컨테이너 상태·시작 시각·재시작 횟수와 pr29/r1 번들·snapshot·덤프 해시는 모든 단계 전후로 같았다.
