# Issue #105 r3 통합 복원 번들 Runbook (B2 + B3 + Neo4j)

> 상태 (2026-11-22): **`R3_PINS` 1차 확정** (최신 데이터 병합본 `a60f5bc` 기준).
>
> **완료한 작업**
> - build 격리 서버(`issue105-r3-build`)에 B2(r2)·B3(#31) 원본 snapshot을 복원했고, digest가 기록과 일치했다.
> - 서버 계획(`basis=server_snapshot`)을 만들고 검증했다.
>
> **아직 하지 않은 작업:** 갱신(`--apply`)·임베딩(`--index`)·Neo4j 적재·snapshot·ZIP 생성
>
> **현재 허용 범위**
> - 남은 미확정은 컬렉션별 최종 `vector_digest` 두 칸뿐이다. 그래서 갱신 단계(§5.5 1차)만 기준값 게이트를 통과한다.
> - snapshot·번들·번들 복원·교차 검증은 계속 거부된다.
> - 근거 보고서(로컬): `.cache\issue105-r3\05-b3-plan-verify.json`, `10-b2-plan-verify.json`
>
> §1.1 표의 "미확정" 행은 최신 데이터 병합 전 상태의 경계를 설명하며, 테스트(`PendingPinTests`)로 계속 확인한다.

### 1.1 `R3_PINS` 미확정 상태의 쓰기 경계

| 구분 | 진입점 | 조건 |
|---|---|---|
| **허용** | `update_r3_collections --restore-snapshot` (build Qdrant에 원본 snapshot 복원) | build Docker 소유 확인, 대상 컬렉션 없음, 원본 SHA-256·Point 수·vector digest 일치 |
| **허용** | `--write-plan`·`--report` (새 로컬 파일) | 기존 파일 덮어쓰기 거부 |
| 거부 | `update_r3_collections --apply`·`--index` | `ProfileError` (접속 전에 멈춤). 1차 확정 후에는 허용(§5.5) |
| 거부 | `load_neo4j_r3_isolated --load`·`--resume` | `ProfileError`. 1차 확정 후에는 허용(§5.5) |
| 거부 | `load_neo4j_r3_isolated --cross-db` | `ProfileError`. 2차 확정(vector_digest 포함) 후에만 허용 |
| 거부 | `build_restore_bundle_r3 --create-snapshot`·`--build` | `ProfileError` |
| 거부 | `restore_final_v140_r3 --apply`, `restore_r3_isolated --apply` (preflight 포함) | `ProfileError` (`check_bundle`) |

코드 기준: `restore_profiles_v140.WRITES_WITHOUT_PINS`·`WRITES_REQUIRING_PINS`. 테스트: `tests/unit/test_r3_write_gates.py`.

## 1. 목적과 범위

- r2(#91)는 Qdrant B2 컬렉션 1개와 Neo4j를 복원한다. r3는 **B2·B3 두 컬렉션 + Neo4j**를 함께 복원한다.
- 원칙
  - 기존 r2 ZIP·snapshot·복원 도구·테스트는 바꾸지 않는다.
  - r2 번들은 동봉된 r2 소스(`027f40b`)로 계속 복원한다.
  - 벡터 재사용 판정은 r2와 같은 안전 기준을 쓰되, 컬렉션별로 따로 판정한다.
    - 임베딩 입력(정책명 줄·섹션·본문)이 같으면 재사용한다.
    - 모델·차원·입력 규칙이 다르면 재사용하지 않고 멈춘다.
  - 모든 쓰기는 r3 전용 Docker 프로젝트·볼륨에서만 한다.
  - 기본 실행은 dry-run이고, 쓰기에는 `--confirm-write`와 직전 dry-run의 fingerprint가 필요하다.

## 2. 파일과 역할

| 파일 | 역할 | 쓰기 |
|---|---|---|
| `scripts/restore_profiles_v140.py` | r2(동결)·r3 기준값, r3 원본 자산, 격리 환경 정의. `R3_PINS` 미확정이면 `require_finalized()`가 거부 | 없음 |
| `scripts/plan_r3_vector_reuse.py` | 컬렉션별 재사용 계획(재사용·삭제 후 재임베딩·신규·remove). 벡터 값 없이 Point별 벡터 SHA·본문 SHA와 부분 digest만 담음. CLI는 **덤프 사전 검토(basis=dump_preview)** 전용이며 계획 파일을 쓰지 않는다 | 없음(새 보고서만) |
| `scripts/update_r3_collections.py` | build Qdrant에 원본 snapshot 복원 → **서버 기준 계획(basis=server_snapshot)** 저장 → payload 최신화·삭제(chunk_id·본문·벡터 SHA 재확인) → 기존 `execute_full_index`로 빠진 Point만 임베딩 | build Qdrant |
| `scripts/load_neo4j_r3_isolated.py` | build Neo4j 적재·검증(r2 함수 재사용) + B2·B3 각각 `verify_cross_db`·`verify_full_index` | build Neo4j |
| `scripts/build_restore_bundle_r3.py` | 컬렉션별 snapshot 생성·내려받기 → r3 ZIP 생성·재검증 | build Qdrant(snapshot 파일), 출력 폴더 |
| `scripts/restore_final_v140_r3.py` | r3 번들 복원 본체(package schema 2). 번들 구성·메타데이터·`--repo`↔source ZIP 대조 후 빈 DB에만 쓰기 | 대상 DB |
| `scripts/restore_r3_isolated.py` | verify 프로젝트 보호 래퍼(Docker 소유·인증·fingerprint). 고정 주소를 settings로 직접 넘김(환경 변수 덮어쓰기 없음) | verify DB |

build 환경 도구 3개(update·load·build)는 모든 쓰기 전에 **`issue105-r3-build` Docker 소유**를 확인한다.
- 확인 항목: 프로젝트 라벨, 서비스, 이미지 digest, 127.0.0.1 바인딩, 프로젝트 전용 볼륨
- 이 식별 정보는 dry-run fingerprint에 들어간다. 컨테이너가 재시작되기만 해도 fingerprint가 달라져 쓰기가 거부된다.
| `docs/development/issue105_r3_{build,verify}.compose.yaml` | r3 전용 Compose (실행 전 승인 필요) | — |

`scripts/restore_r2_isolated.py`에는 프로젝트·서비스·주소·기대 컬렉션을 넘길 수 있는 키워드 인자만 추가했다.
기본값은 r2 값이므로 r2 동작은 그대로다.

## 3. 격리 환경

| 환경 | Compose 프로젝트 | Qdrant | Bolt | 인증 변수 |
|---|---|---|---|---|
| build | `issue105-r3-build` | 127.0.0.1:56646 | 127.0.0.1:56677 | `ISSUE105_R3_BUILD_QDRANT_API_KEY`, `ISSUE105_R3_BUILD_NEO4J_PASSWORD` |
| verify | `issue105-r3-verify` | 127.0.0.1:56656 | 127.0.0.1:56687 | `ISSUE105_R3_VERIFY_QDRANT_API_KEY`, `ISSUE105_R3_VERIFY_NEO4J_PASSWORD` |

다음 접속은 거부된다.

- **포트:** 공유·r1·r2 포트(6333·7687·36333·56446·56487·56556·56587 등, `FOREIGN_PORTS`)
- **인증값:** 원본·공유·r2 인증값과 같은 값(`QDRANT_API_KEY`, `NEO4J_PASSWORD`, `ISSUE91_*`)

정리할 때는 `down -v`를 쓰지 않는다. 실패한 볼륨은 보존하고, 새 프로젝트 이름으로 다시 시작한다.

## 4. r3 원본 자산 (최신 릴리스 값 아님)

| 컬렉션 | 원본 | snapshot SHA-256 | Point | vector digest |
|---|---|---|---|---|
| B2 | r2 snapshot (`.cache/issue91-r2-20261119/bundle-r2/b2-r2-v1.12.4.snapshot`) | `2e07c17a…0bca0` | 1,139 | `8577c3c8…da3d3` (r2 독립 복원으로 확인) |
| B3 | #31 snapshot (`venv/issue31_20261109/exp_b3_qwen3emb06b_v140_v1.before.snapshot`) | `502b7b7e…4ad8` | 1,220 | `0a794e34…6371` (덤프·공유 DB scan 기준) |

B3 snapshot을 격리 서버에 복원했을 때의 digest는 **아직 미검증**이다.
`update_r3_collections --restore-snapshot`이 복원 직후 이 값과 대조하고, 다르면 멈춘다.

## 5. 최신 데이터 병합 후 절차

### 5.1 계획 기준 — 사전 검토와 최종 고정을 분리한다

| 계획 | 만드는 곳 | `basis` | 용도 |
|---|---|---|---|
| 덤프 계획 | `plan_r3_vector_reuse.py --b3-dump <#31 after 덤프>` | `dump_preview` | **사전 검토용**. 파일로 저장하지 않으며 `proposed_pins.plan_sha256`도 비워 둔다 |
| 서버 계획 | `update_r3_collections.py --write-plan` (build 서버에 원본 snapshot을 실제 복원한 상태) | `server_snapshot` | **최종 고정값(`plan_sha256`) 산출과 모든 쓰기**에 쓰는 유일한 계획 |

두 계획은 혼용하면 안 된다.
- 같은 벡터라도 payload가 다르면 `plan_sha256`이 달라진다.
  - #31 before snapshot과 after 덤프는 region 16건이 다르다.
  - 실측: 재사용·삭제 ID와 재사용 digest는 같고 `plan_sha256`만 다르다.
- 쓰기 도구는 `basis=server_snapshot`이 아닌 계획을 거부한다.
  - 대상: `update --plan`, `create_snapshot`, `validate_plan(require_pins=True)`

### 5.2 기준값 확정까지 (사람이 승인)

1. #95→#96→#98→#104 병합 후 최종 dev 커밋으로 브랜치를 갱신한다. Git 작업은 사용자가 관리한다.
2. (선택, 읽기 전용) 덤프로 사전 검토를 한다. 결과는 참고만 한다.
   ```powershell
   .venv\Scripts\python.exe scripts\plan_r3_vector_reuse.py --b3-dump venv\issue31_20261109\exp_b3_qwen3emb06b_v140_v1.after.json --report <새 보고서.json>
   ```
3. build 프로젝트를 띄운다. 컨테이너 실행은 별도 승인이 필요하다.
   ```powershell
   docker compose -f docs/development/issue105_r3_build.compose.yaml config --quiet
   docker compose -f docs/development/issue105_r3_build.compose.yaml up -d
   ```
4. 원본 snapshot을 복원한다. 컬렉션마다 진행하며, `R3_PINS` 없이 허용된다.
   ```powershell
   python scripts/update_r3_collections.py --qdrant-url http://127.0.0.1:56646 --collection b3                      # state=absent
   python scripts/update_r3_collections.py --qdrant-url http://127.0.0.1:56646 --collection b3 --restore-snapshot --confirm-write
   ```
   - B2도 같은 순서로 진행한다(`--collection b2`).
   - 복원 직후 원본 Point 수와 vector digest를 대조한다. B3는 `0a794e34…`이고, 다르면 멈춘다.
5. 서버 계획을 저장한다. 이것이 최종 고정 기준이다.
   ```powershell
   python scripts/update_r3_collections.py --qdrant-url http://127.0.0.1:56646 --collection b3 --write-plan <plan-b3.json>
   python scripts/update_r3_collections.py --qdrant-url http://127.0.0.1:56646 --collection b2 --write-plan <plan-b2.json>
   ```
6. 두 서버 계획의 `proposed_pins`와 대상 chunk_id 목록을 검토한다.
   - 확인할 것: 신규 문서 청크, 정책명 변경(모두의 카드(K-패스)), 본문 변경, remove 0
   - 승인되면 `R3_PINS`에 기록한다.
     - 릴리스: manifest, release_counts
     - 컬렉션별: points·reuse_count·reuse_vector_digest·embed_count·delete_count·remove_count·plan_sha256(**서버 계획 값**)
   - `vector_digest`는 임베딩 완료 후(5.3의 complete)에만 알 수 있다. 기록 순서는 §5.5를 따른다.
   - 계산값을 자동으로 고정하지 않는다.

> 참고(읽기 전용 모의 결과, 2026-11-22, 데이터 브랜치 tip `bc7763d`, manifest `2e22d2a7…`):
> - B3: 1,320개 = 재사용 1,209 + 임베딩 111(삭제 후 재임베딩 11 + 신규 100), remove 0
> - B2(조사 단계 계산): 1,233개 = 재사용 1,137 + 임베딩 96
>
> 최종 병합본에서 다시 계산해야 하며, 이 값을 고정값으로 쓰지 않는다.

### 5.3 build 환경 갱신·임베딩 (R3_PINS 1차 확정 후, 컬렉션마다)

```powershell
python scripts/update_r3_collections.py --qdrant-url http://127.0.0.1:56646 --collection b3 --plan <plan-b3.json>   # payload_pending + fingerprint
python scripts/update_r3_collections.py ... --plan <plan-b3.json> --apply --confirm-write --expect-fingerprint <값>
python scripts/update_r3_collections.py ... --plan <plan-b3.json>                                                  # ready_for_index + 새 fingerprint
python scripts/update_r3_collections.py ... --plan <plan-b3.json> --index --confirm-write --expect-fingerprint <값> --ollama-url http://127.0.0.1:11434
python scripts/update_r3_collections.py ... --plan <plan-b3.json>                                                  # complete → proposed_final_pins
```

- 삭제는 계획에 기록된 chunk_id·본문 SHA-256·벡터 SHA-256이 서버와 모두 같은 Point만 한다. 하나라도 다르면 아무것도 지우지 않는다.
- 임베딩 상한은 남은 Point 수이며, 고정 `embed_count`를 넘지 않는다.
- 임베딩 전에 Ollama 모델 digest가 r2 기록(`ac6da0df…`)과 같은지 확인한다(미구현, 수동 확인).

Neo4j 적재와 교차 검증은 다음 순서로 한다.

```powershell
python scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56677                       # empty + fingerprint
python scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56677 --load --confirm-write --expect-fingerprint <값>
python scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56677 --cross-db --qdrant-url http://127.0.0.1:56646
```

### 5.4 snapshot·번들

```powershell
python scripts/build_restore_bundle_r3.py --create-snapshot --confirm-write --collection b2 --plan <plan-b2.json> --qdrant-url http://127.0.0.1:56646 --expect-fingerprint <값>
python scripts/build_restore_bundle_r3.py --create-snapshot --confirm-write --collection b3 --plan <plan-b3.json> --qdrant-url http://127.0.0.1:56646 --expect-fingerprint <값>
python scripts/build_restore_bundle_r3.py --build --confirm-write --source-commit <확정 R3_PINS가 들어간 40자리 커밋>
```

**번들 구성** (평평한 파일 7개)

- `b2-r3-v1.12.4.snapshot`
- `b3-r3-v1.12.4.snapshot`
- `source-<sha12>.zip`
- `compose.yaml` (verify Compose)
- `restore_final_v140_r3.py`
- `package.json`
- `SHA256SUMS`

**`package.json`** (schema_version 2)

- **컬렉션별:** collection, config, dimensions, points, vector_digest, payload_digest, snapshot, snapshot SHA·크기, vector_provenance(원본·계획 SHA·건수)
- **Neo4j:** 이미지, 그리고 `release_files`. source ZIP 안의 `neo4j/*` 파일별 SHA-256이다.
- **공통:** manifest LF SHA-256, release_counts, source_commit

**번들 생성 보호 장치**

- 커밋의 기준값 모듈 내용이 작업 트리와 다르면 거부한다.
  - 줄바꿈 차이만 있으면 허용한다. Windows `core.autocrlf=true`에서는 blob이 LF, 작업 트리가 CRLF이기 때문이다.
  - 커밋하지 않은 `R3_PINS` 변경은 거부한다.
- 같은 이름의 ZIP·체크섬·보고서가 있으면 거부한다(덮어쓰지 않음).
- `--create-snapshot`은 서버 계획만 받고, build Docker 소유를 확인한다. fingerprint에는 서버 상태와 컨테이너 식별 정보가 함께 들어간다.

**복원 쪽 fail-closed 검증** (`restore_final_v140_r3`)

- `SHA256SUMS`가 번들 구성 파일 6개(snapshot 2, source ZIP, compose, 복원 도구, package.json)를 정확히 나열해야 한다. 빠지거나 추가된 파일이 있으면 거부한다.
- package 메타데이터를 확인한다.
  - `source_commit`은 40자리여야 한다.
  - `source_archive`는 `source-<sha12>.zip`이어야 하고 `source_commit`과 짝이 맞아야 한다.
  - `restore_tool` 이름이 맞아야 한다.
- `--repo`가 source ZIP과 같은 소스여야 한다. 대상 파일은 복원 도구·기준값 모듈·r2 복원 도구·manifest이며, 줄바꿈 차이만 허용한다.
- package의 Neo4j `release_files`가 `--repo` manifest의 `neo4j/*` SHA-256과 같아야 한다.
- 두 번째 snapshot 업로드 등 도중에 실패하면 예외로 멈추고 FAILED를 반환한다.
  - 부분 자산은 보존한다.
  - 재실행은 빈 DB가 아니므로 거부된다. 새 verify 프로젝트에서 다시 시작한다.

### 5.5 `R3_PINS` 기록 순서

`vector_digest`는 임베딩 완료 후에만 정해지므로, 두 번에 나눠 기록·커밋한다(사용자가 Git 관리).

1. **서버 계획 검토 후(1차, 2026-11-22 반영 완료):** manifest·release_counts·points·reuse·embed·delete·remove·plan_sha256을 기록한다.
   - 반영값: manifest `2e22d2a7…b509`, release_counts 246·268·663·1133·2674·211
   - B2: points 1233, reuse 1137, embed 96, delete 2, plan_sha256 `fb91460e…1fa1`
   - B3: points 1320, reuse 1209, embed 111, delete 11, plan_sha256 `94fbaf91…7ec8`
   - remove는 두 컬렉션 모두 0이다.
   - 컬렉션별 `vector_digest` 두 칸은 None으로 둔다. 임시값을 넣지 않는다.
   - 이 상태에서 허용되는 쓰기는 **갱신 단계**뿐이다: `--apply`, `--index`, Neo4j `--load`·`--resume`
   - 근거: `restore_profiles_v140.UPDATE_STAGE_DEFERRED_PINS`. 이 두 칸 외의 None은 미룰 수 없다.
2. **임베딩 완료 후(2차):** `update_r3_collections` dry-run(complete)의 `proposed_final_pins.vector_digest`를 검토해 기록하고 커밋한다.
   - 이후에야 `--cross-db`, `--create-snapshot`, `--build`, 번들 복원이 허용된다.
   - 번들 커밋에는 2차까지 확정한 `restore_profiles_v140.py`가 들어 있어야 한다.

### 5.6 독립 복원 검증 (verify 환경)

```powershell
python scripts/restore_r3_isolated.py --repo <압축 푼 소스> --bundle <번들 폴더>                       # preflight + fingerprint
python scripts/restore_r3_isolated.py --repo <소스> --bundle <번들> --apply --confirm-write --expect-fingerprint <값>
python scripts/restore_r3_isolated.py --repo <소스> --bundle <번들> --verify-only
```

`--apply` 하나로 다음이 끝까지 진행되고, 이어서 verify-only 재검증이 실행된다.

1. 두 snapshot 업로드
2. 컬렉션마다 차원·Point 수·전체 digest·payload 정합성 확인
3. Neo4j `load_graph(verify=True)`
4. 컬렉션마다 Neo4j 교차 검증(`verify_cross_db`)과 원문 슬라이스 검증(`verify_full_index`)

## 6. r2 호환성과 데이터 PR 병합 시 주의

- **r2 도구 상수는 r2 값으로 유지해야 한다.** `restore_final_v140.py`의 `SNAPSHOT_POINTS`·`EXPECTED`와 seed·update·load·build의 고정값이 여기에 해당한다.
- 데이터 브랜치 tip(`bc7763d`)의 변경을 그대로 병합하면 안 된다.
  - 이 tip은 r2 도구의 `EXPECTED`를 최신 그래프 수치로 바꾸면서 `SNAPSHOT_POINTS=1139`는 남겨 두었다.
  - r2 테스트도 같은 식으로 일부만 고쳤다.
  - 병합 충돌 시에는 r2 파일을 r2 값으로 두고, 최신 릴리스는 r3 도구·테스트가 맡게 한다.
  - 대상 파일: `scripts/restore_final_v140.py`, `tests/unit/test_restore_final_v140.py`, `test_restore_r2_isolated.py`, `test_load_neo4j_r2_isolated.py`, `tests/integration/test_final_v140_smoke.py`
- **실제 릴리스를 읽는 r2 테스트**는 `tests/unit/r2_release_gate.py`로 r2 릴리스(manifest `d48ef11b…`)에서만 실행된다.
  - 최신 릴리스에서는 실패 대신 `NOT_APPLICABLE` 사유로 건너뛴다.
  - mock·fixture 기반 r2 테스트는 계속 실행된다.
- `test_build_restore_bundle_v140`의 임시 저장소는 r2 기준 파일을 r2 소스 커밋(`027f40b`)에서 읽는다.
- **모의 병합 확인 결과**(scratchpad 복제본, tip + 이 변경 + r2 파일 r2 값 유지): r2/r3 관련 13개 파일 202 passed, 18 skipped(r2 릴리스 아님 14, 덤프 없음 4)
- **주의:** tip을 그대로 병합하면 충돌 없이 데이터 PR의 r2 변경이 들어온다(2차 리뷰 모의). 이때 r2 기준 감지 테스트가 실패해 드러나므로 위 대상 파일을 r2 값으로 되돌린다.
  - 감지 테스트: `test_restore_profiles_v140.R2ProfileTests`, `test_restore_final_v140_r3.test_r2_restore_tool_is_unchanged_by_r3`

## 7. 미검증·남은 작업

- 실제 Docker 실행, snapshot 복원, 임베딩, Neo4j 적재, ZIP 생성, 독립 복원(모두 미실행)
- 실제 `docker ps`·`docker inspect` 출력 형식과 FakeDocker의 일치(테스트 더블 기준. r2 verify 래퍼는 실제 환경에서 동작 확인됨)
- B3 #31 snapshot 복원 digest가 `0a794e34…`로 나오는지
- Ollama 모델 digest 확인 자동화(현재 수동)
- 여섯 표현(모두의카드·모두의 카드·K-패스·k-패스·K패스·k패스) 검색 테스트
  - B2·B3 프로필 각각 확인해야 한다.
  - 통과 기준과 별칭 정규화 여부는 팀 결정 사항이다.
- `tests/integration/test_final_v140_smoke.py`의 r3 버전(두 컬렉션)
- 팀원 설치 가이드의 r3 갱신
