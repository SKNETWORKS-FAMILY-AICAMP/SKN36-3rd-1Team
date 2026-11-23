# Issue #105 r3 통합 복원 번들 Runbook (B2 + B3 + Neo4j)

> **2026-11-23 갱신 — Issue #105 r3b 재구축 준비 (#110 데이터 정정 반영):** build 프로필을 새 격리 프로젝트 `issue105-r3b-build`(56666/56697)로 바꿨다(§3).
> 아래 2026-11-22 기록(#104 기준 build·snapshot·ZIP `rag-final-v140-r3-65022ff8adb9.zip`)은 보존 자산이며 #110 반영본이 아니다.
> **C0 진입 (2026-11-23, #110 병합 `7aa5266`):** `R3_PINS`를 #110 릴리스 기준 pending으로 바꿨다(§5.5.2). manifest `29b57236…`·release_counts·points만 확정, 계획·임베딩 결과 값은 미확정.
> #104 기준 1·2차 확정값은 §5.5(아래 기록)에 그대로 보존한다.
> **C1 진입 (2026-11-23, 1931ca8 기준):** r3b 서버 계획 값을 확정해 `R3_PIN_STAGE = "stage1"`로 바꿨다(§5.5.2). 최종 `vector_digest` 두 칸만 미확정.
> **C2 완료 (2026-11-23, HEAD `43729a7` 위 미커밋 변경):** `R3_PIN_STAGE = "stage2"`. B2 1,233·B3 1,320 Point의 최종 digest를 기존 서버 dry-run 보고서와 대조해 확정했다(§5.5.3). C1 값은 유지했다.
> (C2 인계 당시 기록) 다음 작업은 r3b Neo4j 적재·검증이다(§5.5.4). 이번 C2 인계에서는 실제 DB·Docker·임베딩·snapshot·ZIP 작업을 실행하지 않았다.
> → **현재 상태:** 그 뒤 Neo4j 적재·교차 검증(§5.5.5), snapshot·최종 번들 생성, 독립 복원까지 모두 PASS했다(§5.7, 보고서 19·20·21).
> **C2 커밋·최종 번들·독립 복원 완료 (2026-11-23):** C2는 `1c007e7`로 커밋됐다. 그 커밋을 source로 한 최종 번들 `rag-final-v140-r3-1c007e75d1c2.zip`(SHA-256 `a233264e…4eb5`)을 만들었고, 분리된 verify 환경(`issue105-r3-verify`)에서 preflight → `--apply` → `--verify-only`가 모두 PASS했다(§5.7, 보고서 19·20·21).
> r3b build 쪽 Neo4j 적재·교차 검증(보고서 13~16)과 snapshot 생성(17·18) 기록은 §5.5.5에 있다. 남은 작업과 검증 한계는 §7에 있다.
> **PR #111 리뷰 반영 (2026-11-23, P1-3 `756ed30`·P2-4 `4331650`):** 복원 도구가 임시 컬렉션 복원 + 검증 후 alias 공개, 최종 payload 검증으로 바뀌었다(§5.8). 위 최종 ZIP·독립 복원 PASS는 그 이전 도구의 보존 기록이며, 새 도구로 만든 ZIP과 실제 복원은 아직 없다 *(P1-3·P2-4 반영 직후 시점의 과거 기록 — 현재 상태는 바로 아래 갱신 줄)*. §5.7.3 PowerShell 경로 계산을 절대 경로 기준으로 고쳤다(P2-5).
> → **갱신 (2026-11-23, 강의실 노트북):** 새 도구를 담은 번들 `rag-final-v140-r3-6d0450fd6c1d.zip`(SHA-256 `eb178f95…9554`, source `6d0450f`)을 만들고, 새 verify 환경에서 preflight → `--apply` → `--verify-only`(재시도 1회 포함)와 B2/B3 6개 표기 검색 검증까지 실행했다(§5.9). 기존 ZIP `…1c007e75d1c2.zip`의 기록은 그대로 보존한다.

> 상태 (2026-11-22): **`R3_PINS` 2차 확정** (미확정 0, 최신 데이터 병합본 `a60f5bc` 기준).
>
> **완료한 작업 (build 격리 서버 `issue105-r3-build`)**
> - B2(r2)·B3(#31) 원본 snapshot 복원 → digest가 기록과 일치 (B3 `0a794e34…6371` 포함)
> - 서버 계획(`basis=server_snapshot`) 생성·검증
> - 갱신(`--apply`): B2 → B3 순서, 컬렉션마다 1회
> - 임베딩(`--index`): B2 96개, B3 111개, 컬렉션마다 1회
> - 최종 상태(complete): B2 1,233 Point `d1fa3194…c427`, B3 1,320 Point `a471e21f…c0ed` → `vector_digest` 두 칸 기록(§5.5 2차)
>
> **아직 하지 않은 작업:** Neo4j 적재(build Neo4j는 비어 있음)·`--cross-db` 교차 검증·snapshot 생성·ZIP 생성·독립 복원
>
> **현재 허용 범위**
> - 모든 쓰기 진입점이 기준값 게이트를 통과한다. 이후 단계는 각자의 fingerprint·Docker 소유·digest 검사를 따로 통과해야 한다.
> - 게이트 통과는 테스트 더블로만 확인했다. 실제 교차 검증·번들 생성·복원 성공을 뜻하지 않는다.
> - 근거 보고서(로컬): `.cache\issue105-r3\05-b3-plan-verify.json`, `10-b2-plan-verify.json`, `20-b2-index-verify.json`, `24-b3-index-verify.json`
>
> **실행 환경:** 모든 명령은 저장소의 `.venv\Scripts\python.exe`로 실행한다(아래 예시의 `python`도 이것을 뜻한다). 다른 Python은 의존성 버전이 달라 쓰지 않는다.
>
> §1.1 표의 "미확정" 행은 최신 데이터 병합 전 상태의 경계를 설명한다. 테스트로 세 상태를 계속 확인한다: 전부 미확정(`PendingPinTests`), 1차(`Stage1PinTests`), 2차 실제값(`FinalPinTests`).

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

| 환경 | Compose 프로젝트 | Qdrant | Bolt | 인증 변수 | 출력 폴더 |
|---|---|---|---|---|---|
| build (r3b, Issue #105 재구축용 — #110 데이터 정정 반영, 현재) | `issue105-r3b-build` | 127.0.0.1:56666 | 127.0.0.1:56697 | `ISSUE105_R3B_BUILD_QDRANT_API_KEY`, `ISSUE105_R3B_BUILD_NEO4J_PASSWORD` | `.cache\issue105-r3b\` |
| verify | `issue105-r3-verify` | 127.0.0.1:56656 | 127.0.0.1:56687 | `ISSUE105_R3_VERIFY_QDRANT_API_KEY`, `ISSUE105_R3_VERIFY_NEO4J_PASSWORD` | (보고서만) |
| **보존** build-104 (#104 기준, 대상 아님) | `issue105-r3-build` | 127.0.0.1:56646 | 127.0.0.1:56677 | `ISSUE105_R3_BUILD_QDRANT_API_KEY`, `ISSUE105_R3_BUILD_NEO4J_PASSWORD` | `.cache\issue105-r3\` |

- build 프로필은 2026-11-23에 r3b로 바꿨다(`R3_ENVIRONMENTS["build"]`, compose `issue105_r3b_build.compose.yaml`).
- #104 build는 `PRESERVED_R3_ENVIRONMENTS["build-104"]`에 기록만 남긴다. 도구가 선택할 수 없고, 그 값에서 거부 목록이 만들어진다. 기존 compose(`issue105_r3_build.compose.yaml`)는 바꾸지 않았다.
- #104 산출물(B2 1233 `d1fa3194…`·B3 1320 `a471e21f…` 컬렉션, Neo4j 1133/2674, snapshot·ZIP `rag-final-v140-r3-65022ff8adb9.zip`, 보고서 25~40, DPAPI 파일)은 그대로 보존한다.
  - 현재 도구로는 #104 build 서버에 접속할 수 없다. #104 상태는 ZIP과 verify 복원으로만 다시 확인한다.

다음 접속·출력은 거부된다.

- **포트:** 공유·r1·r2 포트(6333·7687·36333·56446·56487·56556·56587 등)와 보존 #104 build 포트(56646·56677) — `FOREIGN_PORTS`
  - verify 래퍼는 여기에 현재 build(r3b) 포트까지 더해 거부한다.
- **인증값:** 원본·공유·r2·보존 #104 build 인증값과 같은 값(`QDRANT_API_KEY`, `NEO4J_PASSWORD`, `ISSUE91_*`, `ISSUE105_R3_BUILD_*`) — `FOREIGN_SECRET_ENVS`
- **Docker 소유:** build 프로필 프로젝트(`issue105-r3b-build`) 라벨과 그 프로젝트 볼륨만 인정한다. #104 프로젝트·볼륨(`issue105-r3-build_*`)의 컨테이너는 거부한다.
- **출력 경로:** 보존 폴더 `.cache\issue105-r3\` 안에는 새 파일도 만들지 않는다. `is_preserved_output`이 CLI 인자 검사 단계에서 쓰기 전에 거부한다.
  - 대상: `update_r3_collections`의 `--report`·`--write-plan`, `load_neo4j_r3_isolated --report`, `plan_r3_vector_reuse --report`, `restore_r3_isolated --report`, `build_restore_bundle_r3`의 `--out-dir`(함수 안에서도 다시 확인)
  - `--plan`·`--bundle` 읽기는 허용한다.
- `environment()`는 build·verify 프로필이 보존·외부 자산과 프로젝트·포트·인증 변수가 겹치면 거부한다.

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

### 5.3 build 환경 갱신·임베딩 (R3_PINS 1차 확정 후, 컬렉션마다 — 2026-11-22 B2·B3 완료)

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
- 같은 이름의 ZIP·`.zip.partial`·`.zip.sha256`·`.zip.report.json`이 하나라도 있으면 시작 전에 거부한다. 기존 파일은 지우거나 덮어쓰지 않는다(2026-11-22 보완).
  - `.zip.partial`은 배타적으로 새로 만든다. 다른 작업이 먼저 만들었으면 거부한다.
  - 실패 시 정리는 이번 실행이 만든 `.zip.partial`(같은 파일 식별자)과 이번 실행의 UUID staging 폴더(`.staging-<sha12>-<uuid>`)만 한다. 강제 종료로 남은 `.partial`·staging 폴더는 보존한다(사람이 확인 후 옮긴다).
  - 최종 ZIP은 하드 링크로 만든다. 그 사이 같은 이름이 생겼으면 거부한다.
  - ZIP 완성 뒤 `.sha256` 또는 보고서 쓰기가 실패하면 이미 만든 ZIP(·`.sha256`)은 보존하고 오류로 멈춘다. 같은 이름 재실행은 거부되며 자동 재시도는 없다.
- ZIP을 풀기 전에 엔트리 전체를 검사한다(`check_zip_entries`): 하위·절대 경로, 드라이브(`:`), 역슬래시, 제어 문자, `.`·`..`, 폴더 엔트리, 심볼릭 링크 등 일반 파일이 아닌 유형, 암호화 엔트리, 중복 이름(대소문자만 다른 이름 포함)을 거부한다.
- `--create-snapshot`은 서버 계획만 받고, build Docker 소유를 확인한다. fingerprint에는 서버 상태와 컨테이너 식별 정보가 함께 들어간다.

**snapshot 파일 보호·무결성** (2026-11-22 보완, `build_restore_bundle_r3.create_snapshot`)

- 출력 폴더: `.cache\issue105-r3\bundle-r3\` (Git 제외). 파일: `<b2|b3>-r3-v1.12.4.snapshot`, `snapshot-record-<b2|b3>.json`
- 최종 snapshot·기록 JSON·`.partial` 중 하나라도 있으면 Qdrant 호출 전에 거부한다. 기존 파일은 지우거나 덮어쓰지 않는다.
  - 강제 종료로 남은 `.partial`도 보존한다. 사람이 확인하고 새 출력 폴더(`--out-dir`)를 쓰거나 파일을 옮긴 뒤 다시 실행한다.
- `create_snapshot` 호출 전에 `.partial`을 배타적으로 새로 만든다. 다른 작업이 먼저 만들었으면 거부한다(서버 snapshot 0개).
- 실패 시 정리는 이번 실행이 만든 `.partial`(같은 파일 식별자)만 지운다.
- 최종 이름은 하드 링크로 만든다. 그 사이 같은 이름이 생겼으면 거부한다(덮어쓰기 없음).
- **SHA-256 검증은 생략할 수 없다.** 크기 일치만으로는 통과하지 않는다.
  1. 서버가 돌려준 checksum이 64자리 소문자 hex면 로컬 SHA-256과 전체 값을 비교한다.
  2. checksum이 없거나 형식이 다르면 서버 snapshot 파일 SHA-256을 읽기 전용으로 계산해 비교한다:
     `docker exec <소유 확인한 qdrant 컨테이너 ID> sha256sum -- /qdrant/snapshots/<컬렉션>/<서버 snapshot 이름>`
     - 컨테이너 ID(64자리)·컬렉션·snapshot 이름 형식이 틀리면 docker를 부르지 않는다. 출력이 정확히 한 줄 `<hex>  <같은 경로>`가 아니면 실패.
     - 2026-11-22 확인: 업로드한 원본 snapshot 2개는 서버 목록에서 checksum이 null이었고, 이 방법의 SHA가 로컬 원본 SHA(`2e07c17a…`, `502b7b7e…`)와 같았다.
  3. 둘 다 안 되면 실패한다(검증 불가).
- 기록 JSON의 `snapshot_integrity`에 검증 근거를 남긴다: `method`(`server_reported_checksum` 또는 `server_file_sha256_docker_exec`), `local_sha256`, `server_sha256`, 서버가 돌려준 checksum·크기, `verified`.
- 중간 실패 시 남는 것
  - 서버: 생성된 snapshot은 컨테이너 안 `/qdrant/snapshots/<컬렉션>/`에 남는다(볼륨 아님, 자동 삭제 없음). 재실행하면 서버 snapshot이 하나 더 생긴다.
  - 로컬: 이번 실행의 `.partial`은 정리된다. 기록 JSON만 실패하면 검증된 snapshot 파일은 남고, 같은 경로 재실행은 거부된다.
  - 컬렉션 데이터: snapshot 생성 후 fingerprint(Point 수·vector·payload digest·컨테이너)를 다시 확인한다.

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
2. **임베딩 완료 후(2차, 2026-11-22 반영 완료):** `update_r3_collections` dry-run(complete)의 `proposed_final_pins.vector_digest`를 검토해 기록하고 커밋한다.
   - 반영값: B2 `vector_digest` `d1fa319424c1b682aa75faa4b92680b29eeffd3ae980d49b2c97c67f373dc427` (1,233 Point)
   - 반영값: B3 `vector_digest` `a471e21fec0b3a7ac486a1d30a63c29f7d4f061ca91503684ae91479afbec0ed` (1,320 Point)
   - 반영 전 읽기 전용 재확인: 두 컬렉션 모두 complete, exact count = scan 수 = 고정 points, Point ID = 릴리스 대상, 재사용 부분 digest = 고정값, 1024·Cosine, Docker 소유 불변
   - 이후에야 `--cross-db`, `--create-snapshot`, `--build`, 번들 복원이 허용된다.
   - 번들 커밋에는 2차까지 확정한 `restore_profiles_v140.py`가 들어 있어야 한다.

#### 5.5.1 2차 확정 시점의 벡터 출처

| 컬렉션 | 구분 | Point 수 | `index_run_id` |
|---|---|---|---|
| B2 | 신규 임베딩 (2026-11-22) | 96 | `RUN_FULL_A_B2_V140_20261122_151453` |
| B2 | 재사용 (r2 snapshot) | 1,133 | `RUN_FULL_A_B2_V140_20261107_173008` |
| B2 | 재사용 (r2 snapshot) | 4 | `RUN_FULL_A_B2_V140_20261119_015749` |
| B3 | 신규 임베딩 (2026-11-22) | 111 | `RUN_FULL_B_B3_V140_20261122_151957` |
| B3 | 재사용 (#31 snapshot) | 1,209 | `RUN_FULL_B_B3_V140_20261107_173026` |

- 신규 Point는 모두 `qwen3-embedding:0.6b`·1024차원·`policy_section_body_v2`이다.
- 재사용 Point는 원래 `index_run_id`를 그대로 보존한다. 그래서 B2 한 컬렉션 안에 실행 ID 3개(재사용 2 + 신규 1)가, B3에는 2개가 섞여 있다. 버그가 아니며, 실행 ID 하나로 컬렉션 전체를 대표할 수 없다.
- **한계:** 과거 실행(2026-11-07·2026-11-19)의 Ollama 모델 digest는 Point payload나 보고서에 기록되지 않았다. 재사용 벡터가 신규와 같은 모델 빌드인지는 모델 이름·차원·입력 규칙으로만 확인된다. 신규 임베딩 직전에는 모델 digest가 r2 기록(`ac6da0df…`)과 같은지 수동으로 확인했다.

#### 5.5.2 Issue #105 r3b 기준값 재확정 (#110 데이터 정정 반영) — 단계별 기준값과 허용 쓰기 (2026-11-23)

`R3_PIN_STAGE`(선언)와 `pin_stage()`(값에서 계산)가 같아야 한다. 테스트가 확인하므로 값만 바꾸고 단계를 잊을 수 없다.

| 단계 | 기준값 상태 | 기준값 게이트를 통과하는 쓰기 (`STAGE_ALLOWED_WRITES`) | 거부되는 쓰기 |
|---|---|---|---|
| pending (C0, 2026-11-23 완료) | manifest `29b57236c36587233a90c8f159d2e98388bd96e427cccdac102462626f79a6b7`, release_counts 246·268·663·1133·2674·211, points B2 1233·B3 1320, remove_count 0. 나머지(reuse·embed·delete·plan_sha256·reuse_vector_digest·vector_digest) None | 원본 snapshot 복원(`--restore-snapshot`), `--write-plan`, `--report` (새 로컬 파일) | `--apply`, `--index`, Neo4j `--load`·`--resume`, `--cross-db`, `--create-snapshot`, `--build`, 번들 복원 `--apply` |
| stage1 (C1, 완료) | 위 + r3b 서버 계획의 reuse·embed·delete·plan_sha256·reuse_vector_digest. 최종 vector_digest 두 칸만 None | pending + `--apply`, `--index`, Neo4j `--load`·`--resume` | `--cross-db`, `--create-snapshot`, `--build`, 번들 복원 |
| **stage2 (C2, 현재)** | 위 + complete 상태의 컬렉션별 vector_digest. None 0 | 모든 쓰기 (각 단계의 Docker 소유·fingerprint·서버 상태 검사는 그대로) | (기준값 게이트로는 없음. 실행 승인을 뜻하지 않음) |

- C0 값은 릴리스 파일에서 다시 계산해 확인했다(manifest 파일 34개 체크섬 일치, 12개 정책 `application_start/end` = null, 12개 `source_text` SHA = `content_hash`).
- `load_release()`는 C0 값(manifest·건수·points)과 다르면 거부한다. 쓰기용 계획 검증(`require_pins`)은 C0에서 거부되고, 검토용 검증은 확정된 칸만 대조한다.
- `remove_count` 0은 예상값이다. r3b 서버 계획의 값이 다르면 `validate_plan`이 거부하므로, 그때 근거를 검토해 C1에서 고친다.
- C1 확정값 (r3b 서버 계획, `basis=server_snapshot`, 계획 파일 원문에서 `plan_sha256`을 다시 계산해 확인):
  - B2: points 1233, reuse 1125, embed 108(신규 94 + 삭제 후 재임베딩 14), delete 14, remove 0, `reuse_vector_digest` `5e914b9e…fa7b`, `plan_sha256` `80aed399…7738`
    - 재사용 1125 = identical 1099 + core_refresh 26(#110 `content_hash`만 변경). 실행 ID `…20261107_173008` 1121·`…20261119_015749` 4
  - B3: points 1320, reuse 1197, embed 123(신규 100 + 재임베딩 23), delete 23, remove 0, `reuse_vector_digest` `d926c898…4cce`, `plan_sha256` `787d6cd3…f02d`
    - 재사용 1197 = identical 990 + metadata_refresh 179 + core_refresh 28(#110 26 + 기존 위치 보정 2). 실행 ID `…20261107_173026` 1197
  - 원본: B2 r2 snapshot 1139 `8577c3c8…`, B3 #31 snapshot 1220 `0a794e34…` (R3_SOURCES와 같음). 계획 파일 SHA-256: `plan-b2.json` `65e530d7…`, `plan-b3.json` `8e7d6258…` (로컬, Git 제외)
- 참고: 원본 snapshot 복원 보고서(`01-b3-restore.json`, `02-b2-restore.json`)의 최상위 `mode="dry-run"`·`db_writes=0`은 복원 직후 실행한 확인용 dry-run 값이다. 실제 쓰기 결과는 `result.status="RESTORED"`에 있다(§7).

#### 5.5.3 C2 완료 근거 — Issue #105 r3b (#110 데이터 정정 반영) (2026-11-23)

기존 보고서를 읽어 대조했다. 이번 인계 작업에서 서버를 다시 호출한 결과는 아니다.

| 항목 | B2 | B3 |
|---|---|---|
| 보고서 (`.cache/issue105-r3b/`) | `11-b2-final-dryrun.json` | `12-b3-final-dryrun.json` |
| status / state | PASS / complete | PASS / complete |
| points | 1233 | 1320 |
| full_vector_digest = proposed_final_pins.vector_digest | `90d0f59318c6a7fb0a084bd1d1242a793cfd6ae6a4d04fb3145933ff5e17689d` | `718046c382cffb43afbc5dd1866ac9c84fc8b25dc293f4bd2d4ac7e32bbd7120` |
| payload_pending / missing_for_index / delete_present / problems | 0 / [] / [] / [] | 0 / [] / [] / [] |
| reuse_vector_digest_matches_plan | true | true |
| db_writes / embedding_calls (최종 dry-run) | 0 / 0 | 0 / 0 |

- 보고서의 `r3_pins_finalized=false`는 **C2 코드를 반영하기 전 C1 상태**를 뜻한다. 서버의 `complete`와 모순되지 않는다. 기존 보고서를 고쳐 쓰지 않는다.
- manifest `29b57236c36587233a90c8f159d2e98388bd96e427cccdac102462626f79a6b7`, release_counts 246·268·663·1133·2674·211 및 C1의 points·reuse_count·embed_count·delete_count·remove_count·reuse_vector_digest·plan_sha256을 그대로 유지한다.
- `R3_PIN_STAGE`, `pin_stage()`, `PIN_STAGES`, `STAGE_ALLOWED_WRITES`는 최종 단계를 `stage2`로 통일한다. `require_finalized()`의 미확정 차단과 갱신 단계의 두 digest만 유예하는 규칙은 유지한다.
- 확정 핀을 통과하더라도 다른 계획·manifest·digest, 잘못된 fingerprint·소유·주소·인증, 기존 출력 파일은 계속 거부한다. pending/stage1 테스트를 유지하고, 양쪽 컬렉션의 digest만 변조하는 테스트를 추가했다(아래). skip은 추가하지 않는다.
- 번들은 확정 핀이 담긴 커밋과 작업 트리의 소스 일치가 필요하다. 현재 미커밋 C2 상태는 번들 생성 완료가 아니다.

**C2 회귀 검증 결과 (2026-11-23, Claude 재검증)**

- 관련 r2/r3 unit 14개 파일: **269 passed, 13 skipped, 1 failed**. 실패 1건은 `DefaultDatasetRegressionTests`이며 HEAD 기준선에 있던 실패다. skip은 모두 r2 `NOT_APPLICABLE`이다.
- 전체 `tests/unit`: **55 failed, 1673 passed, 17 skipped, 20 errors**. 실패 목록이 HEAD(`43729a7`, C1) 기준선과 같아 **신규 실패 0, 사라진 실패 0**이다. 기존 실패는 1.3.2 전용 기대값, 고정 평가셋, 정적 플래그 manifest, 로컬 컬렉션 없음 등 이전부터 있던 것이다.
- 실제 stage2 핀 게이트 테스트: 모든 쓰기 진입점이 기준값 게이트를 통과한 뒤 다음 검사(계획·manifest·digest·Docker·접속)에서 멈추고 실제 쓰기는 0이다. pending/stage1 fixture 테스트도 유지했다.
- **Point 수는 같고 vector_digest만 다른 경우**: 실제 핀으로 만든 최소 번들에서 B2·B3 각각 `check_bundle`이 그 컬렉션에서 거부하는 테스트(`RealPinBundleDigestTests`)를 추가해 통과했다. 계획 검증(`validate_plan`)은 vector_digest를 대조하지 않으므로, 그 경로의 sample 계획 테스트는 digest 변조 검증으로 보지 않는다.
- 실행 방식: 기존 `.venv` pytest. 테스트용 Git 저장소·가짜 snapshot·ZIP은 `tempfile.TemporaryDirectory()`(시스템 TEMP, 프로젝트 밖)에만 만든다. Docker·Qdrant·Neo4j·네트워크는 테스트 더블이거나 실패하도록 patch되어 있다. OS 수준 격리는 아니며, 실행 전후 보호 자산 SHA 비교로 불변을 확인했다.
- 이전 Codex 인계 실행(62 failed, 130 passed)은 감사 훅이 `.zip`·`.snapshot` 쓰기와 테스트용 Git 픽스처를 차단해 생긴 환경 차단 실패였다. 해당 자료(`.cache/issue105-c2-audit-20261123/`, `C:\dev\issue105-c2-verification-20261123-01`)는 실행하거나 고치지 않고 보존했다.

#### 5.5.4 다음 Neo4j 절차 — 별도 실행 승인 후

`load_neo4j_r3_isolated.py --help`의 인자와 구현을 확인했다. 아래는 **실행하지 않은 후속 절차**다. §5.2~5.5.1의 56646/56677 명령은 #104 보존 기록이며 현재 대상이 아니다.

1. 이미 준비된 `issue105-r3b-build`와 전용 인증 환경 변수 `ISSUE105_R3B_BUILD_NEO4J_PASSWORD`, `ISSUE105_R3B_BUILD_QDRANT_API_KEY`를 사용한다. 값은 출력하지 않고 공유·r2·#104 인증을 재사용하지 않는다.
2. 새 보고서 폴더를 선택한다(아래 `.cache/issue105-r3b-neo4j-next/`는 예시이며 이번 작업에서 만들지 않음). 기본 dry-run으로 대상 상태·Docker 소유·fingerprint를 확인한다. `state=empty`여야 `--load`를 진행한다.
3. 직전 **Neo4j dry-run** fingerprint를 사람이 확인해 넣는다. Qdrant의 11/12 보고서 fingerprint를 재사용하지 않는다. partial이면 중단·검토하고 별도 승인 없이 `--resume`하지 않는다.
4. 적재 후 `--verify`, B2/B3 `--cross-db`를 순서대로 확인한다. 기대 그래프는 노드 1133·관계 2674·REQUIRES_CONDITION 211이다. 교차 검증은 points·digest·문서 연결·원문 슬라이스·검증 전후 Qdrant 불변까지 통과해야 한다.

```powershell
.venv/Scripts/python.exe -B scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56697 --report .cache/issue105-r3b-neo4j-next/01-preflight.json
.venv/Scripts/python.exe -B scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56697 --load --confirm-write --expect-fingerprint <직전_Neo4j_dry-run_값> --report .cache/issue105-r3b-neo4j-next/02-load.json
.venv/Scripts/python.exe -B scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56697 --verify --report .cache/issue105-r3b-neo4j-next/03-verify.json
.venv/Scripts/python.exe -B scripts/load_neo4j_r3_isolated.py --neo4j-url bolt://127.0.0.1:56697 --cross-db --qdrant-url http://127.0.0.1:56666 --report .cache/issue105-r3b-neo4j-next/04-cross-db.json
```

#### 5.5.5 r3b build 환경 Neo4j 적재·교차 검증·snapshot 결과 (2026-11-23, 완료 기록)

§5.5.4 절차를 실제로 실행한 결과다. 보고서는 `.cache\issue105-r3b\`에 있다(Git 제외). 이 문서 작업에서는 서버를 다시 호출하지 않고 보고서 원문만 대조했다.

| 보고서 | 결과 |
|---|---|
| `13-neo4j-pre-load-dryrun.json` | PASS, `mode=dry-run`, `state=empty`, 노드·관계 0, `db_writes=0` |
| `14-neo4j-load.json` | PASS, `mode=load`, `state=complete`, 노드 1133·관계 2674 |
| `15-neo4j-verify.json` | PASS, `mode=verify`, `ok=true`, 노드 1133·관계 2674, `db_writes=0` |
| `16-cross-db-verify.json` | PASS, `mode=cross-db`, `db_writes=0`. B2 1233·B3 1320 Point, digest = R3_PINS, `cross_db=PASS`, `full_index_verification.passed=true`, `qdrant_unchanged=true` |
| `17-b2-pre-snapshot-dryrun.json`, `18-b3-pre-snapshot-dryrun.json` | snapshot 직전 fresh dry-run (fingerprint 발급용) |

- snapshot 기록(`bundle-r3b-20261123-01\snapshot-record-b2.json`·`-b3.json`): `snapshot_integrity.method=server_reported_checksum`, `verified=true`, `verify_full_index_passed=true`. 로컬 snapshot 파일 SHA-256이 기록과 같다(이번 문서 작업에서 다시 계산해 확인).

### 5.6 독립 복원 검증 (verify 환경)

```powershell
python scripts/restore_r3_isolated.py --repo <압축 푼 소스> --bundle <번들 폴더>                       # preflight + fingerprint
python scripts/restore_r3_isolated.py --repo <소스> --bundle <번들> --apply --confirm-write --expect-fingerprint <값>
python scripts/restore_r3_isolated.py --repo <소스> --bundle <번들> --verify-only
```

`--apply` 하나로 다음이 끝까지 진행되고, 이어서 verify-only 재검증이 실행된다. 아래는 **현재 코드**(P1-3 `756ed30`·P2-4 `4331650`) 기준이다. §5.7.9의 완료 기록은 그 이전 도구(정식 이름에 직접 업로드)로 실행한 결과다(§5.8).

1. 두 snapshot을 **임시 컬렉션**(`<정식 이름>__tmp_<32자리 무작위 hex>`)에만 업로드 (정식 B2·B3 이름으로는 업로드하지 않음)
2. 임시 컬렉션마다 차원·Point 수·전체 vector digest·payload 정렬과 protected digest·최종 payload 검증
3. Neo4j `load_graph(verify=True)`
4. 임시 컬렉션마다 Neo4j 교차 검증(`verify_cross_db`)과 원문 슬라이스 검증(`verify_full_index`)
5. 1~4가 모두 통과한 뒤에만 정식 이름을 alias로 공개하고, alias 경유로 Point 수·vector digest·payload digest를 다시 확인

자세한 구조·잔여 위험·검증 상태는 §5.8에 있다.

Issue #105 최종 번들(#110 데이터 정정 반영)의 실제 실행 절차와 결과는 §5.7에 정리했다.

### 5.7 Issue #105 최종 번들 독립 복원 (#110 데이터 정정 반영, 2026-11-23)

이 절은 **완료 기록**(§5.7.1, §5.7.9)과 **다시 수행할 때의 절차**(§5.7.3~5.7.8, §5.7.10)를 구분한다. 절차의 명령은 Windows PowerShell 5.1 기준이고, 이번 문서 작업에서는 실행하지 않았다.

> **2026-11-23 P1-3·P2-4 반영 후 주의:** §5.7.1의 최종 ZIP과 §5.7.9의 PASS 기록은 `1c007e7` 시점 복원 도구(정식 이름 직접 업로드, 최종 payload digest 비교 없음)로 만든·실행한 **보존 기록**이다. 새 구조(임시 컬렉션 + alias 공개, 최종 payload 검증)를 담은 **새 ZIP은 아직 만들지 않았고, 새 도구로 실제 독립 복원도 실행하지 않았다**(§5.8) *(이 문단 작성 시점의 과거 기록 — 현재 상태는 아래 갱신 줄과 §5.9)*. 래퍼는 번들 안의 복원 도구와 압축을 푼 소스 트리의 도구가 같은지 확인하므로, 기존 ZIP을 §5.7.3 절차로 풀어 실행하면 그 ZIP에 든 옛 도구가 실행된다. 새 번들을 만들면 §5.7.3~5.7.6의 ZIP 이름·SHA-256·source ZIP 이름·snapshot SHA-256을 새 값으로 바꿔야 한다.
>
> → **갱신:** 새 ZIP 생성과 새 도구 독립 복원은 이후 실행했다. 사용한 값·결과는 §5.9에 따로 기록했다(아래 §5.7.1·§5.7.9의 옛 기록과 섞지 않음).

#### 5.7.1 최종 번들 구성과 체크섬 (완료 기록)

- 위치(로컬, Git 제외): `.cache\issue105-r3b\bundle-r3b-20261123-01\`
- 번들: `rag-final-v140-r3-1c007e75d1c2.zip`, 37,912,062 bytes
  - SHA-256 `a233264e2634256db45b82a7bcfe238dd1619c3a99492fc2b757a64887ca4eb5`
  - 실제 파일 해시 = `.zip.sha256` = `.zip.report.json`의 `bundle_sha256` (이번 문서 작업에서 다시 계산해 확인)
- source commit: `1c007e75d1c2c9d94857a76e066539fb11a65ce6` (C2 커밋), 커밋 파일 603개, manifest `29b57236c36587233a90c8f159d2e98388bd96e427cccdac102462626f79a6b7`
- 구성 파일 7개 (평평한 구조):

| 파일 | 내용 |
|---|---|
| `b2-r3-v1.12.4.snapshot` | B2 1233 Point, 16,025,600 bytes, SHA-256 `834f17b6a0c18c265573ebc3da59a29473d5fa0242001cca3d0c2749fa98ba89` |
| `b3-r3-v1.12.4.snapshot` | B3 1320 Point, 17,081,344 bytes, SHA-256 `91311f2d2f8a23369d302cee865b282c38b651b579aaca0d8f3bc95faf333ff8` |
| `source-1c007e75d1c2.zip` | `git archive <source_commit>` (미커밋·미추적 파일 없음) |
| `compose.yaml` | `docs/development/issue105_r3_verify.compose.yaml` (커밋 blob) |
| `restore_final_v140_r3.py` | 복원 도구 (커밋 blob) |
| `package.json` | schema 2, 컬렉션별 points·vector_digest·payload_digest·snapshot SHA·크기·벡터 출처, Neo4j `release_files` 15개 |
| `SHA256SUMS` | 위 6개 파일의 SHA-256 |

- `package.json`의 컬렉션 값: B2 vector `90d0f593…689d`·payload `0584dbbf…6bd0`, B3 vector `718046c3…7120`·payload `b4d6a9bc…d539`
  - vector digest는 `R3_PINS` 최종값과 같다.
  - payload digest는 `R3_PINS`에 **없다**(정정: 예전 문구 "R3_PINS 최종값과 같음"은 vector digest에만 해당한다). 번들 생성 도구가 build 서버 최종 상태에서 `digest([(point_id, payload)])`로 계산해 `package.json`에 기록한 값이며, P2-4 이후 복원 도구가 최종 payload 기준값으로 쓴다(§5.8).
- **보존용 아티팩트:** 이 ZIP의 `restore_final_v140_r3.py`는 `1c007e7` 시점 도구다. P1-3(임시 컬렉션 + alias 공개)·P2-4(최종 payload 검증)가 들어 있지 않다. 이름·SHA-256·snapshot 값은 확정 기록이므로 바꾸지 않는다. 현재 코드를 반영한 새 ZIP은 아직 생성·검증되지 않았다 *(작성 시점의 과거 기록)*.
  → **갱신:** 새 ZIP `rag-final-v140-r3-6d0450fd6c1d.zip`은 이후 생성·독립 복원 검증했다(§5.9.1). 이 ZIP(`a233264e…4eb5`)은 새 번들의 snapshot 출처로만 읽었고 바꾸지 않았다.

#### 5.7.2 build 환경과 verify 환경 분리

| 구분 | build (r3b) | verify |
|---|---|---|
| Compose 프로젝트 | `issue105-r3b-build` | `issue105-r3-verify` |
| Qdrant / Bolt | 127.0.0.1:56666 / 56697 | 127.0.0.1:56656 / 56687 |
| 인증 변수 | `ISSUE105_R3B_BUILD_QDRANT_API_KEY`, `ISSUE105_R3B_BUILD_NEO4J_PASSWORD` | `ISSUE105_R3_VERIFY_QDRANT_API_KEY`, `ISSUE105_R3_VERIFY_NEO4J_PASSWORD` |
| 볼륨 | `issue105-r3b-build_*` | `issue105-r3-verify_qdrant_storage`, `_neo4j_data`, `_neo4j_logs` |
| 역할 | 갱신·임베딩·Neo4j 적재·snapshot·번들 | 번들만으로 빈 DB에 복원·검증 |

- verify 래퍼(`restore_r3_isolated.py`)는 verify 고정 주소만 허용하고, build·#104·r2·공유 포트(`DENIED_PORTS`)와 그 인증값 재사용을 거부한다.
- 래퍼는 Docker 소유를 확인한다: 두 포트의 컨테이너가 정확히 `issue105-r3-verify`의 qdrant·neo4j이고, 고정 이미지 digest, 127.0.0.1 바인딩, 그 프로젝트 볼륨만 써야 한다.
- 서버 조건: Qdrant 컬렉션 0·별칭 0, Neo4j 노드·관계 0인 빈 서버만 받는다. 그래서 verify 프로젝트 하나는 **복원 1회용**이다.
- 이번 실행의 컨테이너 ID(보고서 19~21): qdrant `29ed29788919…`, neo4j `6de7d57c5af1…`. 둘 다 2026-11-22T19:16:39Z에 시작했고 위 볼륨 3개를 사용했다.

#### 5.7.3 안전한 압축 해제와 독립 소스 준비 (절차)

이번 독립 복원에 적용한 조건을 재현하는 절차다. 위치는 `.cache\issue105-r3b\independent-restore-20261123-01\bundle`(번들 7개, `SHA256SUMS` 대조 OK)과 `...\repo`(source ZIP, `.git`·`.venv` 없음)였다. 아래 명령은 그 조건을 PowerShell 5.1로 옮긴 것이며, 이번 문서 작업에서는 실행하지 않았다.

- 조건:
  - 번들 ZIP SHA-256이 §5.7.1과 같아야 한다.
  - 풀기 전에 ZIP 엔트리를 모두 검사한다: 절대 경로·드라이브·역슬래시·`..`·`.`·심볼릭 링크·중복 이름(대소문자 무시)이면 중단한다. 번들 ZIP은 하위 경로도 거부한다.
  - **새 폴더에만** 풀고, 파일을 하나씩 만들 때 기존 파일이 있으면 실패하게 한다(덮어쓰기 금지).
  - 풀고 나서 `SHA256SUMS`의 목록·해시가 실제 파일과 정확히 같아야 한다.
  - 어느 단계든 실패하면 만든 폴더를 지우지 말고 그대로 둔 채 멈춘다. 자동으로 다시 실행하지 않는다(새 폴더 이름으로 사람이 다시 시작).
- 필요 조건: Windows PowerShell 5.1, .NET Framework 4.7.2 이상(`ZipArchiveEntry.ExternalAttributes`로 링크를 판별한다)
- **경로는 모두 절대 경로로 계산한다 (P2-5 수정).** PowerShell cmdlet(`Test-Path`·`Join-Path`·`Get-FileHash`)은 상대 경로를
  PowerShell 현재 위치(`Set-Location`) 기준으로 풀지만, .NET 메서드(`[IO.Path]::GetFullPath`·`Directory.CreateDirectory`·
  `ExtractToFile`)는 프로세스 작업 폴더(`[Environment]::CurrentDirectory`) 기준으로 푼다. `Set-Location`은 후자를 바꾸지 않는다.
  예전 블록은 상대 경로 `$Destination`을 `GetFullPath`에 넘겨, "없음" 확인(`Test-Path`)과 실제 압축 해제 위치가 서로 다른
  폴더가 될 수 있었다. 이제 `$repoRoot`(이 저장소의 절대 경로)에서 `$root`·`$bundle`·`$repo`를 계산하고, 함수도 절대 경로만 받는다.

```powershell
$ErrorActionPreference = 'Stop'
$repoRoot = 'C:\dev\project\SKN36-3rd-1Team'                              # 이 저장소 (모든 경로의 절대 기준점)
Set-Location -LiteralPath $repoRoot                                         # 편의용. 아래 경로는 현재 위치와 무관하다
$zip     = Join-Path $repoRoot '.cache\issue105-r3b\bundle-r3b-20261123-01\rag-final-v140-r3-1c007e75d1c2.zip'
$zipSha  = 'a233264e2634256db45b82a7bcfe238dd1619c3a99492fc2b757a64887ca4eb5'
$root    = Join-Path $repoRoot '.cache\issue105-r3b\independent-restore-20261123-02'   # 새 이름 (기존 -01 폴더는 증거로 보존)
$bundle  = Join-Path $root 'bundle'                                         # 절대 경로 ($root 기준)
$repo    = Join-Path $root 'repo'                                           # 절대 경로 ($root 기준)
foreach ($p in $zip, $root, $bundle, $repo) {
  if ($p -notmatch '^[A-Za-z]:\\') { throw "절대 경로가 아닙니다: $p → 중단" }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
function Expand-R3ZipSafely {
  param([Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$FlatOnly)
  # .NET 메서드에 넘기기 전에 절대 경로만 받는다 (현재 위치·프로세스 작업 폴더가 달라도 같은 폴더를 가리키게)
  foreach ($p in $ZipPath, $Destination) { if ($p -notmatch '^[A-Za-z]:\\') { throw "절대 경로만 받습니다: $p → 중단" } }
  if (Test-Path -LiteralPath $Destination) { throw "이미 있음: $Destination → 중단 (덮어쓰기 금지)" }
  $archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).Path)
  try {
    $seen = @{}
    foreach ($entry in $archive.Entries) {
      $name = $entry.FullName
      if ($null -eq $entry.PSObject.Properties['ExternalAttributes']) { throw '.NET Framework 4.7.2 이상이 필요합니다 → 중단' }
      if ([string]::IsNullOrEmpty($name) -or $name.StartsWith('/') -or $name.Contains('\') -or $name.Contains(':')) { throw "허용하지 않는 경로: $name" }
      $parts = $name.TrimEnd('/') -split '/'
      if (($parts -contains '..') -or ($parts -contains '.') -or ($parts -contains '')) { throw "경로 탈출·빈 경로: $name" }
      if ($FlatOnly -and (($parts.Count -ne 1) -or $name.EndsWith('/'))) { throw "하위 경로 엔트리: $name" }
      if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw "심볼릭 링크: $name" }
      $key = $name.ToLowerInvariant()
      if ($seen.ContainsKey($key)) { throw "중복 엔트리: $name" }
      $seen[$key] = $true
    }
    $rootFull = [System.IO.Path]::GetFullPath($Destination)                 # 절대 경로 정규화만 (작업 폴더에 의존하지 않음)
    if ($rootFull.TrimEnd('\') -ne $Destination.TrimEnd('\')) { throw "경로 정규화 결과가 다릅니다: $Destination → 중단" }
    [void][System.IO.Directory]::CreateDirectory($rootFull)                 # 위에서 없음을 확인한 새 폴더
    $prefix = $rootFull.TrimEnd('\') + '\'
    foreach ($entry in $archive.Entries) {
      $target = [System.IO.Path]::GetFullPath((Join-Path $rootFull ($entry.FullName -replace '/', '\')))
      if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "경로 탈출: $($entry.FullName)" }
      if ($entry.FullName.EndsWith('/')) { [void][System.IO.Directory]::CreateDirectory($target); continue }
      [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target))
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $false)   # $false = 기존 파일 덮어쓰기 금지
    }
  } finally {
    $archive.Dispose()
  }
}

if (Test-Path -LiteralPath $root) { throw "이미 있음: $root → 중단 (새 이름 사용)" }
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower() -ne $zipSha) { throw '번들 ZIP SHA-256 불일치 → 중단' }
Expand-R3ZipSafely -ZipPath $zip -Destination $bundle -FlatOnly

$sums = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS') -Encoding UTF8) {
  if (-not $line.Trim()) { continue }
  $hash, $name = $line -split '\s+', 2
  $sums[$name.Trim().TrimStart('*')] = $hash.ToLower()
}
$files = @(Get-ChildItem -LiteralPath $bundle -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | ForEach-Object { $_.Name })
if ((@($sums.Keys | Sort-Object) -join '|') -ne (@($files | Sort-Object) -join '|')) { throw 'SHA256SUMS 목록과 실제 파일이 다릅니다 → 중단' }
foreach ($name in $sums.Keys) {
  if ((Get-FileHash -LiteralPath (Join-Path $bundle $name) -Algorithm SHA256).Hash.ToLower() -ne $sums[$name]) { throw "체크섬 불일치: $name → 중단" }
}
Expand-R3ZipSafely -ZipPath (Join-Path $bundle 'source-1c007e75d1c2.zip') -Destination $repo
if (-not (Test-Path -LiteralPath (Join-Path $repo 'scripts\restore_r3_isolated.py'))) { throw '소스 트리에 복원 래퍼가 없습니다 → 중단' }
```

- 래퍼는 `--repo`가 **자신이 들어 있는 소스 트리**인지 확인한다. 그래서 압축을 푼 `repo\scripts\restore_r3_isolated.py`를 실행하고 `--repo`에 그 `repo`를 넘긴다.
- Python은 이 저장소의 `.venv\Scripts\python.exe`를 쓴다(소스 트리에는 `.venv`가 없음). 래퍼는 import와 데이터셋 경로를 자신의 소스 트리로 고정한다.
- 아래 §5.7.4~5.7.8은 **같은 PowerShell 세션**에서 위 변수(`$repoRoot`, `$root`, `$bundle`, `$repo`)를 이어서 쓴다. 모두 절대 경로이므로 중간에 `Set-Location`으로 위치를 바꿔도 같은 파일을 가리킨다. Python·docker에 넘기는 `--repo`·`--bundle`·`--report`·`-f`도 절대 경로가 된다.
- §5.2~5.5의 한 줄 명령(`python scripts/...`, `docker compose -f docs/...`)은 **저장소 루트를 현재 위치로 둔 상태**를 전제로 한 기록이다. 외부 프로그램은 PowerShell 현재 위치를 작업 폴더로 받아 상대 경로를 푼다. 다른 위치에서 실행하려면 경로를 절대 경로로 바꾼다.

#### 5.7.4 인증 환경 변수 (절차, 실제 값은 기록하지 않음)

- verify 전용 값 두 개를 새로 만들어 DPAPI(`Export-Clixml`, 현재 Windows 사용자만 복호화 가능)로 저장한다. build·#104·r2·공유 값과 같으면 래퍼가 거부한다.
- 이번 실행에서 verify 인증값을 어디에 보관했는지는 보고서에 남지 않았다. 아래 경로는 권장 예시다.
- 값을 화면·로그·보고서에 출력하지 않는다.
- **환경 변수는 단계마다 `try { Enter-R3VerifySecrets; … } finally { Exit-R3VerifySecrets }` 안에서만 설정한다.** 어느 단계에서 오류가 나도 `finally`가 현재 PowerShell 프로세스에서 두 값을 지운다(§5.7.5~§5.7.8의 각 블록). 인증 파일은 지우지 않는다.

```powershell
function New-R3Secret { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) }
function Read-R3Secret([string]$path) {
  $s = Import-Clixml -Path $path; $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p); $s.Dispose() }
}
$verifyKeyFile = Join-Path $repoRoot '.cache\issue105-r3b\verify_qdrant_api_key.clixml'   # 절대 경로 (§5.7.3의 $repoRoot)
$verifyPwFile  = Join-Path $repoRoot '.cache\issue105-r3b\verify_neo4j_password.clixml'
function Enter-R3VerifySecrets {
  # 현재 프로세스에만 설정한다. 값은 출력하지 않는다.
  foreach ($f in $verifyKeyFile, $verifyPwFile) { if (-not (Test-Path -LiteralPath $f)) { throw "인증 파일이 없습니다: $f → 중단" } }
  $env:ISSUE105_R3_VERIFY_QDRANT_API_KEY = Read-R3Secret $verifyKeyFile
  $env:ISSUE105_R3_VERIFY_NEO4J_PASSWORD = Read-R3Secret $verifyPwFile
  if ($env:ISSUE105_R3_VERIFY_QDRANT_API_KEY -eq $env:ISSUE105_R3_VERIFY_NEO4J_PASSWORD) { throw 'verify 인증값 두 개가 같습니다 → 중단' }
}
function Exit-R3VerifySecrets {
  # 성공·실패와 관계없이 finally에서 호출한다. 인증 파일은 지우지 않는다.
  [Environment]::SetEnvironmentVariable('ISSUE105_R3_VERIFY_QDRANT_API_KEY', $null, 'Process')
  [Environment]::SetEnvironmentVariable('ISSUE105_R3_VERIFY_NEO4J_PASSWORD', $null, 'Process')
}
# 최초 1회만: 새 파일로 저장 (이미 있으면 건너뛰고, -NoClobber 때문에 덮어쓰지도 않음). 환경 변수는 여기서 설정하지 않는다.
if (-not (Test-Path -LiteralPath $verifyKeyFile)) { ConvertTo-SecureString (New-R3Secret) -AsPlainText -Force | Export-Clixml -Path $verifyKeyFile -NoClobber }
if (-not (Test-Path -LiteralPath $verifyPwFile))  { ConvertTo-SecureString (New-R3Secret) -AsPlainText -Force | Export-Clixml -Path $verifyPwFile -NoClobber }
```

#### 5.7.5 새 Docker 환경 생성 전 충돌 검사 (절차)

- 프로젝트 이름(`issue105-r3-verify`)과 고정 포트(Qdrant 56656, Bolt 56687)만 쓴다. 다른 프로젝트·볼륨·네트워크는 건드리지 않는다.
- PowerShell 5.1에서는 외부 명령(docker)이 실패해도 예외가 나지 않는다. 그래서 `$LASTEXITCODE`를 직접 확인하는 함수로 감싸고, **모든 호출은 이름 있는 매개변수 `-DockerArgs @(...)`로 넘긴다.**
- 아래 검사 중 하나라도 걸리면 `throw`로 멈추고, `finally`에서 인증 환경 변수를 지운다. 기존 프로젝트·볼륨이 있으면 **정리하지 말고 멈춘다.**

```powershell
function Invoke-R3Docker {
  param([Parameter(Mandatory = $true)][string[]]$DockerArgs)
  $ErrorActionPreference = 'Continue'                                      # 외부 명령 stderr로 멈추지 않게 하고 종료 코드로 판정
  $out = & docker @DockerArgs
  if ($LASTEXITCODE -ne 0) { throw "docker $($DockerArgs -join ' ') 실패 (exit $LASTEXITCODE) → 중단" }
  return @($out | Where-Object { $_ -and $_.Trim() })
}
$project = 'issue105-r3-verify'; $ports = 56656, 56687
$compose = Join-Path $bundle 'compose.yaml'

try {
  # 1) 인증 환경 변수 (값은 출력하지 않음, compose 구성에 필요)
  Enter-R3VerifySecrets

  # 2) Docker 데몬·명령 자체
  [void](Invoke-R3Docker -DockerArgs @('version', '--format', '{{.Server.Version}}'))

  # 3) 같은 프로젝트의 컨테이너·볼륨·네트워크가 없어야 한다
  if (Invoke-R3Docker -DockerArgs @('ps', '-a', '--filter', "label=com.docker.compose.project=$project", '--format', '{{.Names}}')) { throw "$project 컨테이너가 이미 있습니다 → 중단" }
  if (Invoke-R3Docker -DockerArgs @('volume', 'ls', '--filter', "label=com.docker.compose.project=$project", '--format', '{{.Name}}')) { throw "$project 볼륨(라벨)이 이미 있습니다 → 중단" }
  if (@(Invoke-R3Docker -DockerArgs @('volume', 'ls', '--format', '{{.Name}}')) -like "${project}_*") { throw "$project 이름의 볼륨이 이미 있습니다 → 중단" }
  if (@(Invoke-R3Docker -DockerArgs @('network', 'ls', '--format', '{{.Name}}')) -like "${project}*") { throw "$project 네트워크가 이미 있습니다 → 중단" }

  # 4) 호스트 포트: Docker가 게시한 포트와 Windows에서 듣고 있는 포트 모두 비어 있어야 한다
  foreach ($port in $ports) {
    if (Invoke-R3Docker -DockerArgs @('ps', '-a', '--filter', "publish=$port", '--format', '{{.Names}}')) { throw "포트 $port 를 쓰는 컨테이너가 있습니다 → 중단" }
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "포트 $port 가 이미 열려 있습니다 → 중단" }
  }

  # 5) Compose 구성 (필수 인증 변수·문법·프로젝트 이름)
  [void](Invoke-R3Docker -DockerArgs @('compose', '-f', $compose, 'config', '--quiet'))
  if (-not (@(Invoke-R3Docker -DockerArgs @('compose', '-f', $compose, 'config')) -match "^name:\s*$project\s*$")) { throw "compose 프로젝트 이름이 $project 가 아닙니다 → 중단" }

  # 6) 위 검사를 모두 통과하고 실행 승인을 받은 뒤에만 기동 (실패해도 자동 재시도·정리하지 않음)
  [void](Invoke-R3Docker -DockerArgs @('compose', '-f', $compose, 'up', '-d'))
} finally {
  Exit-R3VerifySecrets
}
```

- `down -v`, `docker volume rm`, `docker system prune` 같은 삭제 명령은 쓰지 않는다. 다시 하려면 새 프로젝트 이름과 포트가 필요하고, 프로필·compose 변경 승인이 필요하다.
- 기동 뒤의 Docker 소유·이미지 digest·바인딩·볼륨 확인은 다음 단계의 래퍼 preflight가 한다. 컨테이너는 인증값을 기동할 때 받았으므로, 이후 단계에서 환경 변수를 다시 설정하는 것은 래퍼가 접속할 때 쓰기 위해서다.

#### 5.7.6 Preflight (절차, 읽기 전용)

- 보고서는 **새 폴더의 새 파일**에만 쓴다. 이미 있으면 멈춘다. 기존 보고서 19·20·21은 증거로 보존한다.
- 래퍼도 기존 보고서 파일과 보존 폴더(#104 `.cache\issue105-r3`)를 거부한다.

```powershell
$py        = Join-Path $repoRoot '.venv\Scripts\python.exe'                # 절대 경로 (§5.7.3의 $repoRoot)
$wrapper   = Join-Path $repo 'scripts\restore_r3_isolated.py'
$runDir    = Join-Path $repoRoot '.cache\issue105-r3b\verify-run-20261123-02'   # 새 이름 (기존 보고서 폴더와 겹치지 않게)
foreach ($p in $py, $wrapper, $runDir, $repo, $bundle) {
  if ($p -notmatch '^[A-Za-z]:\\') { throw "절대 경로가 아닙니다: $p → 중단 (§5.7.3을 같은 세션에서 먼저 실행)" }
}
$preReport = Join-Path $runDir '01-verify-preflight.json'
$appReport = Join-Path $runDir '02-verify-apply.json'
$verReport = Join-Path $runDir '03-verify-only.json'

function Read-R3Report([string]$path) { Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
function Assert-R3Counts($counts) {
  $want = @{ policies = 246; documents = 268; release_chunks = 663; neo4j_nodes = 1133; neo4j_relationships = 2674; requires_condition = 211 }
  foreach ($k in $want.Keys) { if ($counts.$k -ne $want[$k]) { throw "건수 불일치: $k = $($counts.$k) (기대 $($want[$k])) → 중단" } }
}

if (Test-Path -LiteralPath $runDir) { throw "이미 있음: $runDir → 중단 (보고서 덮어쓰기 금지)" }
[void](New-Item -ItemType Directory -Path $runDir)
try {
  Enter-R3VerifySecrets
  & $py -B $wrapper --repo $repo --bundle $bundle --report $preReport
  if ($LASTEXITCODE -ne 0) { throw "preflight 실패 (exit $LASTEXITCODE) → 중단, 재실행 금지" }
} finally {
  Exit-R3VerifySecrets
}
$pre = Read-R3Report $preReport
if ($pre.status -ne 'PASS' -or $pre.mode -ne 'preflight' -or $pre.db_writes -ne 0 -or $pre.servers.state -ne 'empty' `
    -or $pre.restore_preflight.status -ne 'PREFLIGHT_PASS_NO_WRITES' -or $pre.fingerprint -cnotmatch '^[0-9a-f]{64}$') {
  throw 'preflight 결과가 조건과 다릅니다 → 중단'
}
Assert-R3Counts $pre.restore_preflight.counts
if ($pre.restore_preflight.snapshots.b2 -ne '834f17b6a0c18c265573ebc3da59a29473d5fa0242001cca3d0c2749fa98ba89' -or
    $pre.restore_preflight.snapshots.b3 -ne '91311f2d2f8a23369d302cee865b282c38b651b579aaca0d8f3bc95faf333ff8') { throw 'snapshot SHA-256 불일치 → 중단' }
"preflight fingerprint: $($pre.fingerprint)"                              # 사람이 확인 (인증값 아님)
```

#### 5.7.7 복원 `--apply` (절차, 1회만)

- 직전 preflight 보고서(`$preReport`)의 fingerprint만 쓴다. 래퍼는 그 값과 현재 상태(Docker 식별 정보 + 서버 상태)가 같을 때만 쓴다.
- 쓰기 순서 (현재 도구): snapshot 2개를 임시 컬렉션에 업로드 → 임시 컬렉션 검증(차원·Point 수·전체 vector digest·payload 정렬·protected digest·최종 payload) → Neo4j `load_graph(verify=True)` → 임시 컬렉션마다 `verify_cross_db`와 `verify_full_index` → 정식 alias 공개 → alias 경유 재확인 → 이어서 읽기 전용 재검증(`post_verify`)
  - §5.7.9의 기록(보고서 20)은 옛 도구의 순서다: 정식 이름에 snapshot 2개 업로드 → 컬렉션 검증 → Neo4j → 교차 검증 → 재검증 (alias 없음)
  - 실패 보고서에 `partial_publish`·`aliases`가 있으면 alias 공개 단계에서 멈춘 것이다. alias를 지우거나 바꾸지 않고 그대로 보존한다(§5.8).
- 실패하면 **다시 실행하지 않는다**(§5.7.10). 새 fingerprint를 발급받아 자동으로 재시도하지 않는다. 컨테이너·볼륨·보고서는 그대로 둔다.

```powershell
if (Test-Path -LiteralPath $appReport) { throw "이미 있음: $appReport → 중단 (재실행 금지)" }
$expected = (Read-R3Report $preReport).fingerprint
if ($expected -cnotmatch '^[0-9a-f]{64}$') { throw 'preflight fingerprint 형식 오류 → 중단' }
try {
  Enter-R3VerifySecrets
  & $py -B $wrapper --repo $repo --bundle $bundle --apply --confirm-write --expect-fingerprint $expected --report $appReport
  $applyExit = $LASTEXITCODE
} finally {
  Exit-R3VerifySecrets
}
if (-not (Test-Path -LiteralPath $appReport)) { throw "apply 보고서가 없습니다 (exit $applyExit) → 중단, 재실행 금지" }
$app = Read-R3Report $appReport
if ($applyExit -ne 0 -or $app.status -ne 'PASS' -or $app.mode -ne 'apply' -or $app.restore_apply.status -ne 'PASS' `
    -or $app.post_verify.status -ne 'PASS' -or $app.restore_apply.embedding_calls -ne 0) {
  throw "apply 실패 또는 조건 불일치 (exit $applyExit) → 중단. 상태를 보존하고 재실행하지 않는다"
}
Assert-R3Counts $app.restore_apply.counts
```

#### 5.7.8 별도 `--verify-only` (절차, 읽기 전용)

- **apply 보고서가 PASS일 때만** 실행한다. 복원과 분리된 실행으로 다시 읽어 확인한다.

```powershell
$app = Read-R3Report $appReport
if ($app.status -ne 'PASS' -or $app.restore_apply.status -ne 'PASS' -or $app.post_verify.status -ne 'PASS') { throw 'apply가 PASS가 아닙니다 → verify-only 실행 안 함' }
if (Test-Path -LiteralPath $verReport) { throw "이미 있음: $verReport → 중단 (덮어쓰기 금지)" }
try {
  Enter-R3VerifySecrets
  & $py -B $wrapper --repo $repo --bundle $bundle --verify-only --report $verReport
  if ($LASTEXITCODE -ne 0) { throw "verify-only 실패 (exit $LASTEXITCODE) → 중단, 재실행 금지" }
} finally {
  Exit-R3VerifySecrets
}
$ver = Read-R3Report $verReport
if ($ver.status -ne 'PASS' -or $ver.mode -ne 'verify-only' -or $ver.db_writes -ne 0 -or $ver.servers.state -ne 'complete' `
    -or $ver.post_verify.status -ne 'PASS' -or $ver.post_verify.embedding_calls -ne 0 `
    -or -not $ver.post_verify.all_vectors_and_protected_payload_unchanged -or -not $ver.post_verify.full_graph_properties_verified) {
  throw 'verify-only 결과가 조건과 다릅니다 → 중단'
}
Assert-R3Counts $ver.post_verify.counts
foreach ($key in 'b2', 'b3') {
  $c = $ver.post_verify.collections.$key
  if ($c.cross_db.status -ne 'PASS' -or -not $c.cross_db.all_points_read -or @($c.cross_db.failures).Count -ne 0 -or -not $c.verify_full_index_passed) {
    throw "$key 교차 검증·전체 인덱스 검증 실패 → 중단"
  }
}
"verify-only fingerprint (복원 후): $($ver.fingerprint)"
```

- **새 번들(P1-3·P2-4 도구)로 실행할 때만** 위 블록 뒤에 다음을 추가로 확인한다. 옛 ZIP(§5.7.1)의 도구는 이 필드를 만들지 않으므로 옛 ZIP에는 쓰지 않는다. 아직 실제 보고서로 실행한 적이 없는 절차다(fake JSON으로만 확인).
  → **갱신:** §5.9의 실제 verify-only 보고서(`03-verify-only.json`)는 이 블록이 아니라 같은 조건(published, `partial_publish=false`, 임시 이름 형식, alias 2개 매핑, payload digest = `package.json`)을 포함한 Python 엄격 검증기(79개 검사)로 확인했다. 이 PowerShell 블록 자체를 실제 보고서에 실행한 기록은 아직 없다.
- 필드 위치 (`restore_r3_isolated.py` `run("verify-only")` → `main()`): 보고서 최상위에 `status`·`mode`·`db_writes`·`servers`·`fingerprint`·`post_verify`가 있다. `servers.qdrant.state`·`servers.qdrant.aliases`는 래퍼의 서버 확인 결과, `post_verify`는 `restore_final_v140_r3.restore(verify_only)` 결과로 `collections`·`physical_collections`·`aliases`·`partial_publish`를 담는다. (실패 보고서에서는 `partial_publish`·`aliases`가 최상위에 붙는다.)
- 없는 필드가 `$null`로 비교되어 통과하지 않도록 `Get-R3Required`로 존재·비어 있지 않음을 먼저 확인한다.

```powershell
function Get-R3Required($object, [string]$path) {
  # 점(.)으로 구분한 경로의 필드가 실제로 있고 null·빈 문자열이 아닐 때만 값을 돌려준다
  $value = $object
  foreach ($name in $path -split '\.') {
    if ($null -eq $value -or $null -eq $value.PSObject.Properties[$name]) { throw "보고서에 필드가 없습니다: $path → 중단" }
    $value = $value.PSObject.Properties[$name].Value
  }
  if ($null -eq $value -or ($value -is [string] -and $value -eq '')) { throw "보고서 필드가 비어 있습니다: $path → 중단" }
  return $value
}
$package = Get-Content -LiteralPath (Join-Path $bundle 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$canonical = @{ b2 = 'exp_b2_qwen3emb06b_v140_v1'; b3 = 'exp_b3_qwen3emb06b_v140_v1' }
if ((Get-R3Required $ver 'servers.qdrant.state') -cne 'published') { throw "Qdrant 상태가 published가 아닙니다 → 중단" }
$partial = Get-R3Required $ver 'post_verify.partial_publish'
if ($partial -isnot [bool] -or $partial) { throw '부분 공개 상태이거나 partial_publish가 false가 아닙니다 → 중단 (alias를 지우거나 바꾸지 않는다)' }
$want = @{}
foreach ($key in 'b2', 'b3') {
  $name = $canonical[$key]
  if ((Get-R3Required $ver "post_verify.collections.$key.collection") -cne $name) { throw "$key 정식 컬렉션 이름이 다릅니다 → 중단" }
  $temp = Get-R3Required $ver "post_verify.physical_collections.$key"
  if ($temp -cnotmatch ('^' + [regex]::Escape($name) + '__tmp_[0-9a-f]{32}$')) { throw "$key 임시 컬렉션 이름 형식 오류: $temp → 중단" }
  $want[$name] = $temp
  $actual = Get-R3Required $ver "post_verify.collections.$key.payload_digest"
  $expect = Get-R3Required $package "collections.$key.payload_digest"
  if ($actual -cnotmatch '^[0-9a-f]{64}$' -or $actual -cne $expect) { throw "$key 최종 payload digest가 package.json과 다릅니다 → 중단" }
}
foreach ($source in 'post_verify.aliases', 'servers.qdrant.aliases') {      # 복원 도구·래퍼 양쪽의 alias 매핑이 정확히 2개
  $map = Get-R3Required $ver $source
  $names = @($map.PSObject.Properties | ForEach-Object { $_.Name })
  if ($names.Count -ne $want.Count) { throw "$source alias 개수가 $($want.Count)개가 아닙니다 → 중단" }
  foreach ($name in $want.Keys) {
    if ((Get-R3Required $ver "$source.$name") -cne $want[$name]) { throw "$source 의 $name alias가 임시 컬렉션을 가리키지 않습니다 → 중단" }
  }
}
'새 번들 전용 확인: PASS'
```

- 기대 결과는 이번 실행(§5.7.9)과 같다: B2 1233·B3 1320 Point, Neo4j 1133/2674, Cross-DB 검사 17,871개(B2 8,631 + B3 9,240) PASS, MISSING·MISMATCH·NOT_TESTED 0.
- fingerprint는 복원 전(preflight, 빈 서버)과 복원 후(verify-only, complete)가 다르다. 서버 상태가 fingerprint에 들어가기 때문이다.

#### 5.7.9 실제 검증 결과와 증거 (완료 기록, 2026-11-23)

모든 보고서는 `.cache\issue105-r3b\`에 있다(Git 제외). 이 문서 작업에서는 보고서 원문만 다시 읽어 대조했고, 서버에는 접속하지 않았다.

> 아래 결과는 §5.7.1 ZIP에 든 **옛 복원 도구**(`1c007e7`)로 얻은 것이다. 정식 이름 컬렉션 2개에 직접 복원했고(Qdrant 상태 `restored_collection`, alias 없음), 최종 payload digest 비교(P2-4)는 없었다. 현재 도구의 상태 이름(`published`·`not_published`)과 새 검증은 이 기록에 해당하지 않으며, 새 도구로는 아직 실행하지 않았다(§5.8).

| 단계 | 보고서 | 확인된 값 |
|---|---|---|
| preflight | `19-verify-preflight.json` | `status=PASS`, `mode=preflight`, `db_writes=0`, `servers.state=empty`(Qdrant 컬렉션 0, Neo4j 노드·관계 0, Qdrant 1.12.4, Neo4j 5.26.32 community), `restore_preflight.status=PREFLIGHT_PASS_NO_WRITES`, snapshot SHA = §5.7.1, fingerprint `2333fef226167277162e2cb010981951b9f61dbc9612d30ca8e9d9ed998daa2c` |
| 복원 | `20-verify-apply.json` | `status=PASS`, `mode=apply`, `restore_apply.status=PASS`, `post_verify.status=PASS`, `embedding_calls=0`, `all_vectors_and_protected_payload_unchanged=true`, `full_graph_properties_verified=true` (적재 검증 `load_graph.verification`, 재검증 `verify_loaded_graph`), 소스 동일성 파일 4개·Neo4j `release_files` 15개 일치 |
| 별도 재검증 | `21-verify-only.json` | `status=PASS`, `mode=verify-only`, `db_writes=0`, `servers.state=complete`(Qdrant `restored_collection`: 컬렉션 2개, Neo4j `complete`: 1133/2674), `post_verify.status=PASS`, `embedding_calls=0`, 최종 fingerprint `f85a25136144357beb9e09bfb2df9665a2bc4c3945541c302f65198df4a55051` |

- **건수** (20·21의 `counts`): 정책 246, 문서 268, 릴리스 청크 663, Neo4j 노드 1133, 관계 2674, `REQUIRES_CONDITION` 211
- **B2** (`exp_b2_qwen3emb06b_v140_v1`): 1233 Point, vector digest `90d0f593…689d`, `verify_full_index_passed=true`
  - `cross_db.status=PASS`, `all_points_read=true`, 실패 0
  - 7개 검사 × 1233 = PASS 8,631, MISSING·MISMATCH·NOT_TESTED 0
  - `metadata_points_updated=0`
- **B3** (`exp_b3_qwen3emb06b_v140_v1`): 1320 Point, vector digest `718046c3…7120`, `verify_full_index_passed=true`
  - `cross_db.status=PASS`, `all_points_read=true`, 실패 0
  - 7개 검사 × 1320 = PASS 9,240, MISSING·MISMATCH·NOT_TESTED 0
  - `metadata_points_updated=0`
- **Neo4j:** 노드 1133·관계 2674, 전체 속성 검증 true
- **Cross-DB 합계:** 17,871개 검사(B2 8,631 + B3 9,240) PASS. MISSING·MISMATCH·NOT_TESTED 0
- **fingerprint 구분:** 복원 전(preflight, 빈 서버) `2333fef2…a2c` / 복원 후(verify-only, complete) `f85a2513…051`
- **보고서 표기 참고**
  - `20-verify-apply.json`에는 최상위 `db_writes` 필드가 없다. 이 보고서의 최상위 `servers` 값(empty)과 fingerprint(`2333fef2…`)는 쓰기 **직전** preflight 상태다.
  - 복원 결과는 `restore_apply`·`post_verify` 안에 있다. 21번의 Qdrant 서버 상태 표기는 `restored_collection`이고, 전체 상태 `servers.state`가 `complete`다.

#### 5.7.10 실패 시 중단 원칙과 자산 보존

- 어느 단계든 실패하면 **같은 명령을 다시 실행하지 않는다.** 새 fingerprint로 자동 재시도하지 않는다.
- verify 컨테이너·볼륨·부분 복원 상태를 그대로 둔다. 래퍼는 빈 서버만 받으므로, 다시 하려면 **새 프로젝트 이름**으로 새 환경을 만든다(승인 필요).
- 쓰지 않는 명령: `down -v`, `docker volume rm`, `docker system prune`, Neo4j `DELETE`·`DETACH DELETE`·`DROP`, Qdrant 컬렉션 삭제
- 실패 보고서와 로그는 보존한다. 상태는 읽기 전용 명령(`--verify-only`, preflight)으로만 확인한다.
- 보존 대상(어떤 경우에도 변경·삭제하지 않음):
  - #104 산출물 `.cache\issue105-r3\`, #91 r2 snapshot·ZIP, #31 B3 원본 snapshot
  - r3b build 볼륨과 `.cache\issue105-r3b\`의 계획·보고서·snapshot·번들
  - DPAPI 인증 파일, 기존 미추적 경로 11개

### 5.8 P1-3·P2-4 복원 구조 (PR #111 리뷰 반영, 코드만 반영·실환경 미검증)

커밋: P1-3 `756ed30`(임시 컬렉션 복원 + 검증 후 alias 공개), P2-4 `4331650`(최종 payload 검증). 대상: `scripts/restore_final_v140_r3.py`, `scripts/restore_r3_isolated.py`, `database/full_index.py`(검증 대상 컬렉션 이름 주입만).

**복원 구조 (`--apply`)**
- 정식 B2·B3 이름(`exp_b2_qwen3emb06b_v140_v1`, `exp_b3_qwen3emb06b_v140_v1`)으로는 snapshot을 업로드하지 않는다. `upload_snapshot`이 임시 이름 형식이 아니면 HTTP 호출 전에 거부한다.
- 임시 컬렉션 이름: `<정식 이름>__tmp_<secrets.token_hex(16)>` (32자리 hex, 128비트, 실행마다 새로). 길이 64자 = `store` 실험 컬렉션 이름 상한이며, 쓰기 전에 그 이름 검사를 통과해야 한다.
- 업로드마다 직전·직후에 컬렉션 집합과 alias 매핑을 다시 확인한다. 예상 밖 컬렉션·alias·임시 이름 충돌이 보이면 업로드하지 않고 멈춘다.
- 임시 컬렉션 전체 검증·Neo4j·Cross-DB·원문 슬라이스가 모두 통과한 뒤에만, 공개 직전 검사(정식 이름 컬렉션·alias 없음)를 거쳐 한 요청으로 정식 alias 2개를 만든다. 공개 직후 전체 매핑과 alias 경유 Point 수·vector digest·payload digest를 다시 확인한다.
- 복원 결과: 실제 컬렉션은 임시 이름 2개, 정식 이름은 그 임시 컬렉션을 가리키는 alias 2개. 백엔드는 정식 이름으로 조회한다(Qdrant가 alias를 실제 컬렉션으로 풀어 줌 — v1.12.4 소스 기준, 실서버 미확인).
- `update_r3_collections.py`는 build 전용이며 alias가 있는 DB를 계속 거부한다(복원 DB는 갱신 대상이 아님).

**`--verify-only`**
- 쓰기 0회. Qdrant가 **published 상태**(임시 컬렉션 2개 + 정식 alias 2개가 정확히 짝)일 때만 진행한다. 래퍼도 Neo4j `complete` + Qdrant `published`일 때만 `servers.state=complete`로 보고 허용한다.
- 정식 이름 컬렉션(옛 방식)·alias 없음·부분 alias·잘못된 대상·다른 컬렉션/alias가 있으면 거부한다.

**최종 payload 검증 (P2-4)**
- 기준 1: Point마다 payload dict 전체 = 소스 릴리스(`R3_PINS` manifest로 고정)에서 다시 만든 기대 payload. 예상 밖 필드 추가·필드 삭제·값 변경을 잡는다(`index_run_id`는 기대값을 레코드에서 가져오므로 기준 2로 잡는다).
- 기준 2: 전체 payload digest = 번들 `package.json`의 컬렉션별 `payload_digest`. `R3_PINS`에는 payload digest가 없다. 번들에 값이 없거나 64자리 hex가 아니면 복원하지 않는다.
- `--apply`: 의도된 정렬(`category`·`life_stages`)을 적용한 **뒤의 값**으로 두 기준을 검사한다. 예상 밖 필드는 지우지 않고 FAILED로 멈춘다.
- `--verify-only`: 쓰기 없이 현재 값으로 검사한다(정렬이 필요하면 기존처럼 거부).
- 기존 차원·거리·Point 수·vector digest(R3_PINS)·protected digest·Neo4j·Cross-DB·원문 슬라이스 검증은 그대로다.

**남은 위험 (완전한 동시성 안전을 주장하지 않는다)**
- **정식 alias TOCTOU 덮어쓰기:** Qdrant 1.12.4의 alias 생성은 같은 이름 "컬렉션"이 있으면 거부하지만, 같은 이름 "alias"가 있으면 오류 없이 대상을 바꾼다. 공개 직전 검사와 alias 생성 요청 사이(요청 1회 왕복)에 외부 alias가 생기면 그 매핑은 덮어써진다. 외부 alias가 우리 임시 컬렉션을 가리키면 감지도 못 하고 PASS가 난다. Point 데이터는 지워지지 않는다.
- **B2/B3 부분 공개:** 서버가 alias 작업을 하나씩 저장하므로(요청 전체 원자성 없음) B2만 공개되고 B3가 실패할 수 있다. 이때 FAILED + `partial_publish: true` + 현재 alias 매핑을 보고하고, alias·임시 컬렉션을 지우거나 되돌리거나 재시도하지 않는다.
- **임시 이름 TOCTOU:** 직전 검사 뒤 서버가 존재를 확인하기 전(전송·해시·압축 해제 동안)에 같은 임시 이름이 생기면 덮어쓴다. 128비트 난수라 확률로만 막는다.
- **격리 환경은 잠금이 아니다:** 전용 프로젝트·포트·인증·Docker 소유 확인·fingerprint는 다른 쓰기 주체의 접근 가능성을 낮출 뿐이다. 같은 전용 키를 가진 동시 작업은 막지 못한다. 복원 중에는 그 검증 환경에서 다른 작업을 하지 않는다.
- **payload 기준 2는 번들 안의 값이다:** 번들 전체(`package.json`·`SHA256SUMS`·snapshot)를 일관되게 바꾸면 기준 2는 속을 수 있다. 기준 1은 `index_run_id`를 뺀 나머지를 저장소 고정 릴리스로 막는다.

**검증 상태 (실제로 한 것 / 아직 안 한 것)**
- 실행함: fake/mock 단위 테스트만. HEAD `4331650`에서 r2·r3 관련 14개 파일 330 passed, 4 skipped(덤프 없음), 0 failed. 알려진 위험(alias TOCTOU, 임시 이름 TOCTOU)은 "결함 재현 테스트"로 따로 고정했으며 안전성 근거로 세지 않는다.
- 실행 안 함: 새 도구로 실제 Qdrant·Neo4j 복원, alias 공개·거부의 실서버 동작, 64자 임시 이름 snapshot 복원, 백엔드 alias 조회, payload index 유지, 실제 snapshot payload와 기대 payload의 dict 전체 일치(다르면 안전 측 FAILED), 새 ZIP 생성·독립 복원.
- → **갱신 (2026-11-23, §5.9에서 실서버로 확인):**
  - 확인됨: 새 ZIP 생성·독립 복원, 새 도구로 실제 Qdrant·Neo4j 복원, 64자 임시 이름 snapshot 복원, alias **공개**(정식 이름 2개 → 임시 컬렉션 2개), 실제 payload와 기대 payload의 dict 전체 일치·전체 payload digest 일치, alias 경유 벡터 검색(`HybridSearchSession.search_policy_evidence`).
  - 여전히 미확인: alias **거부** 동작(정식 이름 컬렉션이 이미 있을 때), payload index 유지, alias TOCTOU·부분 공개(실서버에서 재현하지 않음), 그래프 보강 검색·API·LLM·화면.

### 5.9 신규 번들 실환경 독립 복원·검색 검증 (완료 기록, 2026-11-23, 강의실 노트북)

이 절은 §5.7(옛 번들 `…1c007e75d1c2.zip`, 옛 도구)과 **다른 실행**의 기록이다. 모든 값은 아래 `.cache\issue105-r3c\`(Git 제외) 보고서를 다시 읽어 대조한 것이다. 인증값은 기록하지 않는다(DPAPI 파일 `.cache\issue105-r3c\secrets\` 2개, 값 미출력).

#### 5.9.1 신규 번들

| 항목 | 값 |
|---|---|
| ZIP | `.cache\issue105-r3c\bundle-6d0450fd6c1d-01\rag-final-v140-r3-6d0450fd6c1d.zip` |
| 크기 / SHA-256 | 38,000,184 bytes / `eb178f9514a0533b0920463a3b20bb7bd884f2e2e6d1f156cf11bff1e09a9554` (= `.zip.sha256`, `.zip.report.json`의 `bundle_sha256`) |
| source commit | `6d0450fd6c1d20d2499ac0e4482074e30d819c71` (커밋 파일 629개, source ZIP 엔트리 741개) |
| 빌드 | 공식 `build_restore_bundle_r3.py --build`, 보고서 `status=BUILT`, 작업 트리 추적 변경 0 |
| 복원 도구 | 번들·source 트리·커밋 blob 모두 SHA-256 `f5fc6903…57ca` (P1-3·P2-4 포함) |
| `package.json` (압축 해제본) | SHA-256 `66cea9df…fcc8`, `collections`·`neo4j`·`release_counts`는 옛 번들과 동일, 다른 값은 `source_commit`·`source_archive`·`created_at`뿐 |

- 이 Runbook 수정 커밋은 신규 ZIP의 source commit(`6d0450f`) **이후** 커밋이 된다. 복원 계약 비교 대상 파일(`SOURCE_IDENTITY_FILES`)은 이 문서 수정으로 바뀌지 않는다.

#### 5.9.2 기존 snapshot 재사용과 파생 record (원본 서버 증거 아님)

- snapshot은 새로 만들지 않았다. 옛 번들 ZIP(`a233264e…4eb5`, SHA256SUMS 6개·CRC 검증)에서 바이트 그대로 추출했다.
  - B2 `b2-r3-v1.12.4.snapshot` 16,025,600 bytes, `834f17b6a0c18c265573ebc3da59a29473d5fa0242001cca3d0c2749fa98ba89`
  - B3 `b3-r3-v1.12.4.snapshot` 17,081,344 bytes, `91311f2d2f8a23369d302cee865b282c38b651b579aaca0d8f3bc95faf333ff8`
- `create_snapshot()`이 build 서버에서 만든 **원본** `snapshot-record-<b2|b3>.json`이 이 노트북에 없어, 옛 번들 `package.json`(`fdd9937a…a512`)에서 공식 record 스키마의 13개 필드만 옮긴 **파생 record**를 썼다(공식 빌드 테스트가 쓰는 부분집합과 같은 형태).
  - `snapshot-record-b2.json` `a1d50cba6fa240fc066dbb91a8e1e63ef8c794e1284fdf5d274c4441c6f96ad4`
  - `snapshot-record-b3.json` `ffaa822c5e1454497400ad58170f3b90c80f4ca6791e46b3e42df050ab618fa3`
  - 근거 기록: `.cache\issue105-r3c\bundle-6d0450fd6c1d-01\derived-record-provenance.json` (`c495c5405295ab6e33a53d42e86f538667f9713e6f0ad5e5d205e13e54d216b5`, `derived=true`, `original_server_evidence=false`, 번들 구성 파일 아님)
- **신뢰성 차이**
  - 원본 record에만 있던 서버 측 근거 10개는 만들지 않았다: `fingerprint`, `snapshot_integrity`(서버 checksum 대조), `server_snapshot_name`, `docker_project`, `plan_basis`, `verify_full_index_passed`, record별 `manifest_lf_sha256`, `snapshot_file`, 원본 `created_at`, `server_state_note`.
  - 파생 값의 교차 확인 수준: `points`·`vector_digest`·`plan_sha256`은 `R3_PINS`와, snapshot SHA·크기는 실제 바이트와 대조됐다. `payload_digest`·`plan_counts`·`source`는 생성 시점에는 `package.json`에만 근거했다.
  - `payload_digest`는 이후 실제 복원 데이터에서 다시 계산한 값과 일치했다(§5.9.4). `plan_counts`·`source`는 독립 재확인되지 않았다.
  - 원본 record는 build PC의 `.cache\issue105-r3b\bundle-r3b-20261123-01\`에 있을 것으로 보이나 이 노트북에서 확인하지 못했다.

#### 5.9.3 독립 복원 실행 이력 (verify 프로젝트 `issue105-r3-verify`)

- 환경: Docker Engine 29.6.2, Compose 5.3.1, `compose up -d --pull never`(로컬 이미지 digest = 고정값, pull 없음). 컨테이너 qdrant `fa0e0b73409f`, neo4j `0207625f6fbe`(2026-11-23T07:42:43Z 기동). 기존 `issue91-r2-verify-20261119`·`playmate-issue153-*` 자산은 모든 단계 전후로 바뀌지 않았다.
- 경로: 압축 해제 `.cache\issue105-r3c\independent-restore-6d0450fd6c1d-01\{bundle,repo}`, 보고서 `.cache\issue105-r3c\verify-run-6d0450fd6c1d-01\`

| 단계 | 결과 | 보고서 SHA-256 |
|---|---|---|
| preflight (1회) | `status=PASS`, `db_writes=0`, `servers.state=empty`, `PREFLIGHT_PASS_NO_WRITES`, fingerprint `c12a1207e923f5ac8f08ac21b874e3759edc74057a4dc3d35908633962457e60` | `01-verify-preflight.json` `e84c0ca65a2c55ace0ece4458f44a4fc0d940c640701531f00a70cec61d64a49` |
| `--apply` (1회, 종료 코드 0) | `status=PASS`, `restore_apply.status=PASS`, apply 내부 `post_verify.status=PASS`, `partial_publish=false`, `embedding_calls=0` | `02-verify-apply.json` `198602c9857bee064fa603863d0fd36f41836d9b1a5f5839a1dd2e0bfd543a25` |
| 별도 `--verify-only` 1차 시도 | **중단(미완료).** 실행 스크립트를 `-Command "… *>&1 | Out-File"`로 감싸 Python stderr 경고(`Api key is used with an insecure connection`)가 PowerShell 종료 오류로 바뀌어, 래퍼가 초기 서버 확인 중 끊겼다. 보고서 없음. 중단 전후 서버 상태(컬렉션·alias·Point·전체 digest·Neo4j 건수)와 컨테이너는 같음을 읽기 전용으로 확인했다 | — |
| 별도 `--verify-only` 2차 시도 (`-File` 방식, 종료 코드 0) | `status=PASS`, `mode=verify-only`, `db_writes=0`, `servers.state=complete`, `servers.qdrant.state=published`, 실행 전후 서버 상태 동일 | `03-verify-only.json` `b51d34377166862816486144d6a7b8121178f2df57b6727cc3cf0a571ff52cce` |

- 보고서 검증은 Python 엄격 검증기(필드 누락·null·타입 오류를 실패로 처리, 정상/비정상 fake JSON 24종으로 자체 검증)로 했다: apply 128개·verify-only 79개 검사, 실패 0.
  - 같은 apply 보고서를 처음 PowerShell 검사로 볼 때 `cross_db.failures=0` 4건이 FAIL로 나왔으나, 빈 배열을 감싸 반환한 검사 스크립트 오류였다(실제 `failures`는 길이 0 list).

#### 5.9.4 실제 데이터 검증 값 (apply·verify-only 보고서와 읽기 전용 전체 scroll 재계산이 일치)

| 항목 | B2 | B3 |
|---|---|---|
| 정식 alias → 임시 컬렉션 | `exp_b2_qwen3emb06b_v140_v1` → `…__tmp_fddc5cfe6518d0fc518c58258562d384` | `exp_b3_qwen3emb06b_v140_v1` → `…__tmp_3d650cc25caa7511158992e8b326fd3e` |
| Points | 1233 | 1320 |
| vector digest (= `R3_PINS`) | `90d0f59318c6…689d` | `718046c382cf…7120` |
| payload digest (= `package.json`) | `0584dbbf81ee…6bd0` | `b4d6a9bc369c…d539` |
| payload 정렬 갱신 | 0 | 0 |
| 원문 슬라이스 (`verify_full_index_passed`) | true | true |
| Cross-DB (7개 검사 × Point) | PASS 8,631 | PASS 9,240 |

- Cross-DB 합계 17,871 PASS, MISSING·MISMATCH·NOT_TESTED 0, `failures=[]`, `all_points_read=true`
- protected digest(`all_vectors_and_protected_payload_unchanged`) true
- Neo4j 노드 1133, 관계 2674, `full_graph_properties_verified=true`
- alias 2개 published, `partial_publish=false`, 복원 단계 `embedding_calls=0`

#### 5.9.5 실제 검색 검증 — 모두의카드·K-패스 6개 표기 (2026-11-23T17:20:57+09:00)

- 방법: 실제 `database/hybrid_search.py`의 `HybridSearchSession`(프로필 `b2_06b_v140`·`b3_06b_v140`, 정식 alias 이름)에 검증 서버 `127.0.0.1:56656`용 QdrantClient를 `client=`로 주입했다(공유 `.env` Qdrant 미사용). 필터 없음.
- 임베딩: 로컬 Ollama(loopback) `qwen3-embedding:0.6b`, digest `ac6da0dfba84…`(기록값과 동일), Q8_0, 1024차원, 지시문 없음, `truncate=false`. **임베딩 호출 6회**(검색어당 1회, B2·B3가 같은 벡터를 캐시로 공유), **Qdrant 검색 24회**(6개 × B2/B3 × k=10·200). 질의 텍스트는 정규화하지 않았다.
- 결과: **12개 조합 모두에서 `POL_STG_MOLIT_001`(모두의 카드(K-패스) (대중교통비 환급지원))이 1위.** 관심 정책 청크는 모든 조합에서 Top200 안에 전부 있었다(B2 46개, B3 52개). k=10과 k=200의 상위 10개는 순서·점수가 같았다. 결과 검사 51개, 실패 0.

| 검색어 | B2 1위 점수 / Top10 관심 정책 청크 | B3 1위 점수 / Top10 관심 정책 청크 |
|---|---|---|
| 모두의카드 | 0.7039129 / 10 | 0.7039129 / 10 |
| 모두의 카드 | 0.62388283 / 10 | 0.62388283 / 10 |
| K-패스 | 0.6098848 / 10 | 0.63769066 / 9 |
| k-패스 | 0.5696805 / 10 | 0.61113906 / 9 |
| K패스 | 0.56001335 / 10 | 0.5876936 / 9 |
| k패스 | 0.59807146 / 10 | 0.61697626 / 9 |

- 1위 근거·출처 (`search-results.jsonl`의 실제 payload)
  - 모두의카드·모두의 카드 (B2·B3): `DOC_STG_MOLIT_001_FAQ_2026` `…_FAQ_2026_C001` [1. Q: 2026년 4월 ~ 9월 상향되는 모두의카드 혜택 안내] "…안녕하세요. 모두의카드입니다.대중교통을 이용하시는 분들의 교통비 부담을 덜어드리기 위해…" — `https://korea-pass.kr/notice/faqList.do`. B2·B3 청크 본문이 같아(`content_hash 5d002a2d…`) 점수가 같다.
  - K-패스 계열 4개 (B2): `DOC_STG_MOLIT_001_GOV24` `CHK_B2_…_GOV24_C002` [정부24 공공서비스 상세 — K-패스] "…신청방법 : K-패스 모바일 앱(App) 혹은 누리집(korea-pass.kr)을 통해 회원가입…"
  - K-패스 계열 4개 (B3): `DOC_STG_MOLIT_001_GOV24` `CHK_B3_…_GOV24_C001` "[서비스명] K-패스 (서비스ID: 161300000104) [소관기관 / 서비스분야 / 지원유형] 국토교통부 광역교통경제과 / 생활안정 / 현금"
  - K-패스 계열 출처: `https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2143816&bnefType=all&svcId=161300000104`
- **B3 혼입 (Top10 안 다른 정책):** `POL_STG_EX_001` 고속도로 다자녀가구 통행료 할인(`CHK_B3_DOC_STG_EX_001_GOV24_C001`)이 K-패스 10위(0.54675907), k-패스 6위(0.53525734), K패스 **3위**(0.54951525), k패스 7위(0.5585586)에 들어왔다. B2 Top10에는 혼입이 없었다.
- **검증 범위:** 이번 테스트는 관심 정책 1개(`POL_STG_MOLIT_001`)와 그 명칭 6개 표기만 대상으로 한 벡터 검색(`search_policy_evidence`, 필터 없음) 확인이다. 다른 정책·자연어 질문·지역/자격 조건 질의, 그래프 보강 검색, 답변 생성은 포함하지 않았으므로 **B2/B3 전체 검색 품질을 입증하지 않는다.**
- 판정: 검색 품질 PASS/FAIL은 팀 기준이 정해지지 않아 **판정하지 않았다.** "관심 정책 1위"와 "B3 혼입"을 함께 기록한다. k=200은 진단용이다.
- 실행 전후 서버 상태(컬렉션·alias·Point·전체 vector/payload digest·Neo4j 건수), 컨테이너, 기존 자산, 보고서 01~03 해시가 모두 같았다(쓰기 없음).
- 증거 파일 `.cache\issue105-r3c\search-validation-6d0450fd6c1d-01\`

| 파일 | SHA-256 |
|---|---|
| `search-run-metadata.json` | `6faa17bed526f35e0d8633a639a4394edcce41ddacfb5c023f4ffac2ebb55263` |
| `search-results.jsonl` (2,520행) | `b85ab94de725efe9cf872e9065f93ecdb1a4f91f3bd2ef45974182ae15ee07e1` |
| `search-summary.json` | `3fc341ce2c5c00bbb15fefe0dfafb0ab90c2e973ded92152bbe616586280bef0` |
| `search-report.md` | `2eaad2330c71c7f8da919c5dfd84df74c9ae0337fd28a2b6947a1012a2166e81` |
| `SHA256SUMS` | `f61d22751e9d547f47252ef9e23308649cea4a2e93d339975e33bd4eb8e3128b` |

#### 5.9.6 이 실행 이후에도 남은 미검증

- 그래프 보강 검색(`search_policy_context`), FastAPI → RAG → LLM → React E2E
- 다른 PC에서 같은 번들로 독립 재현(이번은 강의실 노트북 1대)
- alias 동시성 위험(공개 직전 검사와 alias 생성 사이 외부 alias 덮어쓰기, B2/B3 부분 공개) — 실서버에서 재현하지 않았고 해결되지도 않았다(§5.8)
- alias **거부** 동작(정식 이름 컬렉션이 이미 있을 때), payload index 유지
- 검색 품질 PASS/FAIL 기준 미확정, B3 혼입(`POL_STG_EX_001`) 검토
- 파생 record의 `plan_counts`·`source` 독립 재확인(원본 record 확보 시)
- verify 컨테이너·볼륨 유지/정리 여부(삭제 명령은 실행하지 않음), 커밋 `6d0450f` push 여부

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

- build 환경의 Docker 실행·snapshot 복원·갱신·임베딩은 완료했다(§5.5).
- ~~**미실행:** Neo4j 적재(`--load`), `--cross-db` 교차 검증, `--create-snapshot`, ZIP 생성(`--build`), verify 환경 독립 복원~~
  → **현재 상태 (2026-11-23):** Issue #105 r3b 기준(#110 데이터 정정 반영)으로 모두 실행·PASS (§5.5.5 보고서 13~18, §5.7 보고서 19~21, 번들 `a233264e…4eb5`).
  - (C2 인계 당시 기록) 위 56677의 빈 DB 설명은 #104 보존 기록이다. 현재 r3b Neo4j(`bolt://127.0.0.1:56697`)의 상태는 이번 C2 인계에서 접속 확인하지 않았다. 다음 실행은 §5.5.4의 dry-run부터 시작한다.
    → 이후 §5.5.4 절차대로 dry-run부터 실행해 적재·검증·교차 검증이 PASS했다(§5.5.5). 이 줄은 당시 시점의 기록으로 남긴다.
- 실제 `docker ps`·`docker inspect` 출력 형식과 FakeDocker의 일치(테스트 더블 기준. r2 verify 래퍼는 실제 환경에서 동작 확인됨)
- 과거 재사용 벡터의 모델 digest(기록 없음, §5.5.1)
- `update_r3_collections`의 `--restore-snapshot`·`--apply`·`--index` 보고서: 최상위 `mode`·`db_writes`가 쓰기 뒤 확인용 dry-run 값으로 덮인다(`main()`의 `output.update(dry_run(...))`). 실제 쓰기 결과는 `result`에 있다. 표시 개선은 별도 작업
- Ollama 모델 digest 확인 자동화(현재 수동)
- 여섯 표현(모두의카드·모두의 카드·K-패스·k-패스·K패스·k패스) 검색 테스트
  - B2·B3 프로필 각각 확인해야 한다.
  - 통과 기준과 별칭 정규화 여부는 팀 결정 사항이다.
  - → **갱신 (2026-11-23):** 새 번들 verify 환경에서 1회 실행·기록했다(§5.9.5). 자동 테스트로 만들지는 않았고, 통과 기준과 별칭 정규화 여부는 여전히 팀 결정 사항이다.
- `tests/integration/test_final_v140_smoke.py`의 r3 버전(두 컬렉션)
- 팀원 설치 가이드의 r3 갱신
- **최종 독립 복원 이후 남은 작업과 검증 한계 (2026-11-23)**
  - 검증 범위: 번들 무결성, 빈 DB 복원, 건수·digest·payload·그래프 속성, Qdrant↔Neo4j 교차 연결, 원문 슬라이스까지다. **검색 품질·답변 품질·FastAPI → RAG → LLM → React E2E는 검증하지 않았다.**
  - 독립 복원은 같은 PC·같은 OS 사용자·같은 Docker Desktop에서 실행했다. 다른 PC·OS와 팀원 환경의 설치는 확인하지 않았다.
  - 복원에 쓴 Python 의존성은 이 저장소의 `.venv`다. 압축을 푼 소스 트리에 `requirements.txt`로 새 가상환경을 만드는 절차는 확인하지 않았다.
  - verify 인증값의 실제 보관 위치는 보고서에 없다(§5.7.4는 권장 예시). 다음 실행 전에 확인이 필요하다.
  - 최종 ZIP과 보고서는 `.cache`(Git 제외)에만 있다. 팀 공유 위치와 보관 기간은 팀 결정 사항이다.
  - 번들 source commit은 `1c007e7`이다. 이 문서 갱신을 커밋하면 PR HEAD가 source commit보다 앞선다. 복원 계약의 비교 대상 파일(복원 도구·기준값 모듈·r2 복원 도구·manifest)은 바뀌지 않는다.
    → **정정 (P1-3·P2-4 이후):** 복원 도구(`scripts/restore_final_v140_r3.py`)가 `756ed30`·`4331650`에서 바뀌었다. 현재 저장소는 기존 ZIP의 source와 비교 대상 파일이 다르므로, 기존 ZIP은 그 ZIP에서 푼 소스 트리로만 복원된다(옛 도구 실행). 위 문장은 당시 기록으로 남긴다.
- **P1-3·P2-4 이후 남은 작업 (2026-11-23):**
  - 현재 커밋을 source로 한 새 snapshot 재사용 번들(ZIP) 생성과 SHA-256 기록 — 미실행
    → **완료:** `rag-final-v140-r3-6d0450fd6c1d.zip` `eb178f95…9554` (파생 record 사용, §5.9.1~5.9.2)
  - 새 번들로 새 verify 프로젝트에서 preflight → `--apply` → `--verify-only`(§5.7.8의 새 번들 전용 확인 포함) — 미실행
    → **완료:** 모두 PASS. verify-only 1차 시도는 실행 방식 오류로 중단, 2차 시도 PASS (§5.9.3). §5.7.8 PowerShell 블록 대신 Python 검증기로 같은 조건을 확인했다.
  - §5.8 "실행 안 함" 항목의 실서버 확인 — Docker 실행 승인 필요
    → **일부 완료:** 복원·alias 공개·payload 일치·alias 경유 검색은 확인, alias 거부·payload index·동시성 위험은 미확인 (§5.8 갱신, §5.9.6)
  - alias TOCTOU·부분 공개를 잔여 위험으로 수용할지 팀장 판단 (미결)
  - 모두의카드·K-패스 6개 표기 검색 회귀: 12개 조합 관심 정책 1위, B3 혼입 4건 기록 — 품질 기준 미확정 (§5.9.5)
  - ~~dev(#106·#109) 통합 시 `test_issue79_pending_review_remains_separate`의 의미 충돌(`needs_review` ↔ #90 `verified`)은 아직 처리하지 않았다.~~
    → **처리 완료 (PR #111 리뷰 P1-1, 커밋 `3b0505b`, 현재 HEAD에 포함):** 테스트를 `test_issue79_incheon_age_evidence_verified`로 바꿔, 인천 연령 관계 2개가 공식 안내문(`DOC_STG_ICN_001_NOTICE_2026`) 근거로 `verified`인지 확인한다(`tests/unit/test_issue79_age_corrections_fullscan.py`). 옛 테스트 이름은 저장소에 남아 있지 않다.
  - verify 환경(`issue105-r3-verify`)은 복원 1회용이다. 컨테이너·볼륨을 유지할지 정리할지는 별도 승인 사항이다(삭제 명령은 이 문서에 넣지 않음).
