# Issue #41 - completed preliminary A/B experiment

Current result: both B2 collections contain 1,138 Points. RunPod generated/imported the
841 missing 4B vectors; the original 297 records remained unchanged. Float32 REST
comparison was corrected and read-only import validation passed with to_add=0.

Run03: Recall@5 88.4615% / 94.2308%, MRR@5 0.745833 / 0.760897 (0.6B / 4B).
Improved 4 questions, worsened 1, both failed 2. Mean combined latency 954.46 / 997.75ms.
These are preliminary fixed-evidence-span metrics, not policy-level metrics.
Keep the 0.6B baseline; 4B is a further-validation candidate, not an automatic default switch.

Current source basis: 0529da42185eba742c3ad0f99f25eae803521638 plus uncommitted Issue41 changes.
[Full analysis, evidence cases, limitations and PR scope](ISSUE41_AB_RUN03_ANALYSIS.md).
[Durable execution guide](ISSUE41_AB_EXECUTION_GUIDE.md).

The preparation/partial-index/no-evaluation statements below describe historical stages.
They do not describe the current completed state. Preserve old markers/checkpoints/ZIPs.

<details>
<summary>Historical implementation and execution records (preserved)</summary>

# Issue #41 — Qwen3 Embedding B2 A/B 예비 평가

## 현재 상태: 팀 우선순위에 따라 보류

PR #35 병합 후 최종 DB 재적재를 우선하도록 사용자 지시가 변경됐다.
현재 Git HEAD는 `a44bf08dcd30515bbaaf73fc258350f1e9bcdfc1`이며 데이터 manifest와 B2 입력은 아래 실행 기준과 동일하다.
아래의 최초 7개 안전 중단 이후 메모리 확보로 한 차례 재개했고, 25개까지 처리한 뒤
처리량 점검을 위해 체크포인트를 검증하고 배치를 1에서 4로 조정했다.
마지막 화면 진행 안내는 285개였지만 실제 중단 후 조회 결과는 **297/1,138 Point**다.
색인 Python 프로세스 종료를 확인했고 4B Ollama 모델도 해제했다. 이번 최종 재적재 작업에서는 재시작하지 않았다.

297개 전체 ID·2,560차원 벡터·예상 payload가 유효하고, `inspect_collection`은
`partial / missing 841 / reasons []`를 반환했다. 모든 부분 벡터·payload의 digest는
`fcecd0de10079eb6e0c6d0826a75842c2cad5443fe082cc25b7ec443c1495e70`이다.
기록은 `.cache/issue41-ab-execution-20261110-01/paused-checkpoint.json`에 보존했다.
진행 JSONL·백업·원본 A/B 볼륨을 보존했으며, A/B 성능 평가는 실행하지 않았으므로 Recall/MRR 결과는 없다.

향후 재개는 기존 Point를 검증한 뒤 `execute_full_index`의 partial 처리로 **누락 841개만** 적재할 수 있다.
현재 로컬 보조 스크립트의 1회 재개/배치 조정 제한 마커를 삭제해 무작정 재실행하지 않는다.
재개 전 현재 체크포인트를 검증하는 별도 실행 기록을 만들고, A/B 컨테이너를 기동한 뒤
프로세스의 `QDRANT_URL=http://127.0.0.1:46335`를 명시한다.
현재 `.env` 기본 Qdrant 주소 `46336`은 별도 최종 DB이며 #41 재개 대상으로 자동 선택하지 않는다.
최종 DB 재적재 기록은 `FINAL_DB_RELOAD_20261110.md`로 분리했다.

## 목적과 실행 기준

동일한 v1.4.0 B2 청크에서 Qwen3 Embedding 0.6B와 4B의 근거 검색 품질과 지연을 비교한다.
정답은 사람의 최종 검수 전이므로 수치는 예비 평가로만 해석한다.

- Git HEAD: `13f7e8a0cf8f20ddf1322adabdddc807dd6e23a3` (PR #37 반영).
- 브랜치: `feature/41-embedding-ab-eval`. Issue #41 코드는 미커밋 상태로 실행.
- 데이터: `data/releases/v1.4.0-candidate`, 정책 246개 / 문서 261개 / B2 1,138개.
- Manifest LF SHA-256: `49246d1bda0624f6a10d19b252713f6ffb69375e07321fa615f594a44c698389`.
- B2 fingerprint: `ba7e3ef2fd549eeba1f1683b19cd743185cfbf7ddd80427342068ee1cb685527`.
- 모델 공통 입력 SHA-256: `52025671b15d7ff9e51f5ec46ea712611f5856dfb011c31455abc91bda4408a1`.

| 구성 | 모델 | 차원 | 컬렉션 |
|---|---|---:|---|
| A_B2_V140 | qwen3-embedding:0.6b | 1024 | exp_b2_qwen3emb06b_v140_v1 |
| A_B2_4B_V140 | qwen3-embedding:4b | 2560 | exp_b2_qwen3emb4b_v140_v1 |

공통 조건은 Cosine, B2 750자 / overlap 80자 / 관련 짧은 청크 병합,
`policy_section_body_v2`, `uuid5(chunk_id)`, 무지시문 질의, Top-5, 필터 없음이다.
모델별 질의 벡터를 각각 생성한다. Neo4j와 LLM 답변 생성은 사용하지 않는다.

## 원본 보호와 0.6B 정합화

기존 사용자 볼륨 `skn36-3rd-1team_qdrant_storage`와 기존 조사 볼륨
`issue41_qdrant_audit_20261110_01`, `issue41_ab_snapshot_check_20261110_01`을 보존했다.
마지막 검증 볼륨을 읽기 전용으로 연결해 백업하고 새 A/B 볼륨에 복제했다.
원본/복제본 파일 SHA-256 목록 비교와 원본 복제 전후 비교가 통과했다.

- 프로젝트 라벨: `issue41-ab-run01`.
- 실행 컨테이너: `issue41-ab-run01`.
- 새 볼륨: `issue41_ab_run01_storage`.
- 주소: `http://127.0.0.1:46335` (로컬 전용).
- 이미지: Qdrant 1.12.4, digest `sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636`.
- 컨테이너 메모리 제한: 768 MiB, CPU 제한: 2.
- 실행 원본·기록: `.cache/issue41-ab-execution-20261110-01/` (Git 제외 로컬 자료).

수정 전 TAR 백업 `before-alignment-qdrant.tar`의 SHA-256:
`88d6fb56c11b9be803e89bc5c13a9c6a42d8a4991f6648be46f3bc6b05d1908c`.
전체 Point+벡터 백업 `before-alignment-points.json.gz`도 다시 읽어 무결성을 검증했다.
압축 파일 SHA-256은 `a83c2941ed977eb3fa37c6a94fc641550179716a6f7418280fbe4f32ea6bac3b`이다.

실제 쓰기 직전 차이를 재계산한 결과는 133개 Point였다.
`category` 33개와 `life_stages` 121개만 최신 문서별 메타데이터로 수정했다.
중복된 Point가 있으므로 두 필드 건수의 합과 수정 Point 수는 다르다.
변경 없는 Point와 다른 필드는 수정하지 않았다.

수정 후 1,138개 전체 Point의 예상 payload 대조, ID 집합, 1,024차원 / Cosine,
246개 정책·261개 문서 연결이 통과했다. 전체 벡터와 category/life_stages 이외 payload가
수정 전과 동일함을 Point별 비교로 확인했다. 따라서 본문·위치·content_hash도 불변이다.
전체 벡터 digest는 `f5ec8c25f42f68726438879199860897f5e62baaf4549fcc02103747073a67df`이다.

기존 `inspect_collection()`의 complete 판정만으로 이 검증을 대신하지 않았다.
해당 검사는 category/life_stages의 최신성까지 확인하지 않으므로 별도 전체 payload 대조가 필요하다.

## 4B 실행과 메모리 제약

Windows 11 / Python 3.13.15 / 물리 RAM 약 15.7 GiB / Intel Iris Xe 환경이다.
Ollama 0.40.15에서 설치된 4B 모델을 CPU로 실행했으며 관측된 적재량은 약 3.5 GB,
컨텍스트는 4,096이었다. 모델 다운로드나 재설치는 하지 않았다.

대상 컬렉션 부재와 dry-run의 1,138개 / 2,560차원 설정을 확인한 후
배치 1, 요청 timeout 180초, truncate=false로 색인했다.
첫 요청은 15.934681초였다. 7개 Point 적재 후 여유 물리 메모리가
515,551,232바이트(약 491.7 MiB)로 감소하여 다음 배치 전 512 MiB 안전 기준으로 중단했다.
OOM·timeout은 발생하지 않았다. 부분 색인 7개는 ID·차원·전체 예상 payload 검증을 통과했다.
0.6B 전체 정합성도 중단 후 다시 통과했다.

사용자가 앱을 종료해 메모리를 확보하기로 했다. 재개는 여유 RAM 6 GiB 이상을 확인하고,
체크포인트와 동일한 부분 컬렉션인지 검증한 뒤 누락분만 한 차례 처리하도록 제한한다.
사용자 앱을 임의 종료하거나 모델/청킹 설정을 바꾸지 않는다.

## 평가 상태와 지표 정의

현재 4B는 297개 부분 색인 상태로 보류됐으므로 A/B 평가는 아직 실행하지 않았다.
실측 Recall/MRR·모델 간 개선/악화 문항을 작성하지 않는다.

평가기는 고정 freeze 해시와 v1.4.0 본문 근거 위치를 검사한 뒤 55문항을 검색하고 52문항을 채점한다.
일반 질문 3개는 검색하되 품질 지표 분모에서 제외한다.

- 예비 Recall@5: Top-5에서 모든 필수 근거 구간을 청크 안에 완전히 포함시킨 질문 수 / 52.
- 예비 MRR@5: 모든 필수 근거를 확보하는 순위의 역수 평균. 실패는 0.
- 복수 근거이면 마지막 필수 근거가 확보되는 순위를 사용하므로 일반적인 첫 관련 정책 MRR과 다르다.
- outcome은 hit / split_only / partial / doc_only / miss / not_scored를 구분한다.
- 시간은 질의 임베딩, Qdrant 검색, 두 시간의 합을 각각 기록한다.
- 모델별 순차 실행, 배치 1, 질문당 1회, 워밍업 없음으로 cold start를 포함한다.
- 합산 시간에는 채점·사전 검증은 포함하지 않는다. p95는 nearest-rank 방식이다.

## 재현 및 재평가

저장소 루트에서 A/B 컨테이너의 볼륨·포트·라벨을 먼저 확인한다.
아래 환경변수는 해당 프로세스에만 적용하고 `.env` 또는 서비스 프로필은 바꾸지 않는다.

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
$env:QDRANT_URL='http://127.0.0.1:46335'
```

로컬 실행 보조 스크립트 `execute.py`는 컨테이너 자산 검증, 전체 payload 대조,
낮은 배치 색인·체크포인트 검증을 수행한다. 최초 `align`, `index`는 재실행을 거부하고
검증된 부분 색인에는 `index-resume`을 한 번만 허용한다. 기존 또는 정체불명의 컬렉션에 덮어쓰지 않는다.

두 컬렉션 전체 검증이 통과한 뒤, 아직 존재하지 않는 출력 경로로 실행한다.

```powershell
.\.venv\Scripts\python.exe -B .cache/issue41-ab-execution-20261110-01/execute.py verify
.\.venv\Scripts\python.exe -m database.retrieval_v140_eval --mode model-ab --output docs/development/experiments/issue41_ab_run01
```

최종 데이터 재적재 후에는 manifest·질문 freeze·전체 Point와 메타데이터를 다시 대조한다.
본문/정책명/섹션/임베딩 입력이 달라졌으면 기존 벡터를 재사용하지 않고 새 컬렉션에 재색인한다.
메타데이터만 바뀌었더라도 백업과 벡터 불변성 검증을 거친다. 질문을 바꾸면 별도 평가 버전으로 고정하고
기존 정답을 검색 결과에 맞춰 수정하지 않는다. 새 실행은 새 결과 경로를 사용한다.
PR #35 및 팀 선행 조건 완료 전 Neo4j 최종 적재·서비스 연결 전환은 하지 않는다.

## 테스트와 한계

### 2026-11-10 최신 dev 이후 안전 재개 준비

`database.embedding_resume`은 기본 읽기 전용인 별도 재개 도구다. Docker는 PATH에서 찾으며, 필요하면 `--docker`로 실행 파일 경로를 지정한다. 기존 캐시
`execute.py`와 그 일회성 마커는 변경하지 않는다. 해당 도구의 재개 마커는 이미
사용됐으므로 삭제해서 우회하지 않는다.

A/B 전용 `issue41-ab-run01`을 기존 `issue41_ab_run01_storage` 연결 그대로 시작했다.
주소는 `http://127.0.0.1:46335`이며 최종 DB의 `46336`과 분리돼 있다.
전용 DB에서 297개 전체 ID·2,560차원 유한 벡터·정확한 payload·content_hash를
재검증했다. 전체 레코드 digest는 중단 당시와 동일한
`fcecd0de10079eb6e0c6d0826a75842c2cad5443fe082cc25b7ec443c1495e70`이다.
최신 B2 1,138개 기준 실제 누락은 841개이며 0.6B 1,138개도 전체 검증을 통과했다.

저장소 루트에서 실행한다. 아래 첫 명령은 모델 적재나 임베딩 없이 DB와 모델
목록을 읽고, digest·실행 순서·환경 정보를 출력한다. 결과 파일은 매번 새 이름을 쓴다.

```powershell
$env:LIFEPOLICY_DATASET_DIR='data/releases/v1.4.0-candidate'
.\.venv\Scripts\python.exe -B -m database.embedding_resume --url http://127.0.0.1:46335 --context
# 별도 실행 승인과 RAM 확보 후에만 사용; 이번 작업에서는 실행하지 않음
.\.venv\Scripts\python.exe -B -m database.embedding_resume --url http://127.0.0.1:46335 --execute
```

배치 1·Ollama timeout 180초를 고정한다. URL·컨테이너·볼륨·포트·모델·차원을
검증하며 최종 DB URL과 0.6B 컬렉션은 허용하지 않는다. 체크포인트는 새 경로
`.cache/issue41-safe-resume/checkpoint.json`에 원자적으로 저장한다. 최초에는
기존 `paused-checkpoint.json`의 전체 297개 digest와 일치해야 한다. 이후에는
확인된 ID별 전체 레코드 digest와 데이터·입력 fingerprint를 검증한다.
실제 DB에 존재하는 ID만 제외하며, DB에 없는 Point만 임베딩한다.

저장 전 pending ID를 기록하고 저장 후 DB를 재조회해 확인한 뒤 acknowledged로
전환한다. 저장이 성공했으나 응답이 유실된 경우 pending ID가 실제 DB에 있으면
전체 payload를 검증해 수용하며 다시 임베딩하지 않는다. 저장되지 않은 경우에는
다음 실행에서 그 누락 ID만 다시 처리한다. 로그는 확인 완료 후에만 추가하며
로그 줄 수를 적재 건수로 사용하지 않는다. 체크포인트 손상·확인된 Point 변조·
기록되지 않은 추가 Point는 중단 사유다. 파일 잠금은 프로세스 종료 시 풀리고
마커 파일처럼 삭제할 필요가 없다. 다른 도구를 통한 동시 쓰기는 금지한다.

중단은 Ctrl+C 후 프로세스 종료를 확인하고 동일한 읽기 전용 명령부터 재실행한다.
진행 중 요청이 실제로 저장됐는지는 DB 조회로 판단한다. checkpoint·로그·기존
Point를 삭제하거나 강제로 되돌리지 않는다. 디스크 오류·timeout·OOM은 자동
재시도하지 않으며 원인과 실제 DB 상태를 확인한 다음 동일 절차로 재개한다.

실행 전 여유 RAM 6GiB 미만이면 거부하고 다음 요청 전 2GiB 미만이면 중단한다.
두 기준 모두 OOM을 보장해서 막는 수치는 아니다. 모델 적재 중의 피크는 요청 전
RAM 검사만으로 예측할 수 없으며 180초 timeout도 서버 계산 취소를 보장하지 않는다.
이번 관측 여유 RAM은 약 3.4GiB, Ollama 적재 모델은 0개였다. 실제 모델 로딩과
색인은 하지 않았다. 최종 Neo4j를 중지하면 Hybrid 서비스가 끊기며, 최종 Qdrant를
중지하면 서비스 벡터 검색이 중단된다. 어떤 프로세스/서비스도 임의 종료하지 않는다.

평가는 기존 `--mode model-ab`를 유지한다. 55문항 검색·52문항 채점, Top-5,
무지시문·동일 필터로 예비 Recall@5·MRR@5 및 임베딩/검색/합산 평균·p95·총시간을
기록한다. 위 `--context` 출력은 모델 digest, 두 컬렉션의 전체 레코드 digest,
평가 코드·freeze 해시, 모델 순서, Python·OS·RAM·Git 상태를 포함한다.
색인 완료 후 평가 직전/직후 새 파일로 각각 보존한다. 현재 297개 상태의 digest는
완료된 4B 컬렉션의 digest를 대신하지 않는다. 콜드 스타트와 단일 실행의 한계를
명시하고 질문별 결과를 포함한 `summary.json`과 함께 보존한다. 모든 필수 근거
구간 기준인 지표 정의는 일반적인 정책 단위 Recall/MRR과 구분한다.

준비 단계 검증: Python 3.13.15에서 새 안전 재개 테스트 20개를 포함한 관련 단위
테스트 170개가 통과했다. 저장 전 실패·저장 후 응답 유실·Ctrl+C·체크포인트 쓰기
실패 후 재실행, 잘못된 대상/차원/payload/ID, 체크포인트 drift, 잠금 재획득,
기존 마커 보존을 가짜 임베더로 검증했다. 실제 CLI 읽기 전용 실행도 통과했다.
실제 추가 임베딩·Point 쓰기는 없었으며 최종 DB와 A/B DB의 297개 digest가
모두 기존 값 그대로임을 재확인했다. A/B 컨테이너는 실행 상태로 남겨 두었다.
관련 근거는 `.cache/issue41-ab-live-preflight.json`,
`.cache/issue41-safe-resume-preflight.json`, `.cache/issue41-safe-resume-tests.json`이다.

이번 실행 후 Python 3.13.15에서 Issue #41 관련 176개 회귀 테스트가 통과했다(53 + 123).
기존 최신 dev 관련 397개 중 394개 통과 / 2 failures / 1 error 기록과 구분한다.
그 3건은 원본 누락 및 Windows CRLF 바이트 해시 문제이며 승인 데이터·원본 증거 파일을 수정하지 않았다.

미검수 정답, 단일 노트북 CPU, 제한된 RAM, 질문당 한 번의 지연 측정 때문에
최종 서비스 성능이나 모델 우위를 일반화할 수 없다. 4B 전체 색인 및 A/B 결과는 완료 후에만 기록한다.

</details>
