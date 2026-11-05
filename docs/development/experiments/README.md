# Issue #5 실험 자료 (2026-11-05, 노트북)

Qdrant 실험 컬렉션 `exp_b2_qwen3emb06b_v1` 구축·검색 실험과 환경 점검에 쓴 스크립트와 실측 결과다.
모든 스크립트는 프로젝트 함수(`database/`)만 호출하며, **프로젝트 루트에서 `.venv`로** 실행한다.
결과 JSON의 질문은 원문 근거로 만든 스모크용이며 **검색 성능 평가 결과가 아니다.**

| 파일 | DB 쓰기 | 내용 |
|---|---|---|
| `exp1_load_search.py` | **있음** (컬렉션 생성 + 34 point upsert) | 1차: 문서 4건(DOC_NHUF_003·BOK_001·YC_003·GOV_006) B2 청크 34개 임베딩·적재·재적재 중복 검사·질문 4개 × 지시문 없음/있음 검색. 컬렉션이 이미 있으면 쓰지 않고 멈춘다 |
| `exp1_report.json` | — | 1차 실측 결과 (index_run_id `RUN_20261105_B2_V2_EXP1`) |
| `exp2_lh001.py` | **있음** (73 point upsert) | 2차: DOC_LH_001 73개 추가(총 107), 기존 34개 해시 보존 확인, PDF 페이지 검증, PDF 질문 6개 검색. 전제(34개·LH 0개)가 다르면 멈춘다 |
| `exp2_report.json` | — | 2차 실측 결과 (index_run_id `RUN_20261105_B2_V2_EXP2_LH001`) |
| `exp2_diagnose.py` | 없음 | P3·P6 근거 청크의 전체 순위 진단 (읽기 전용 검색) |
| `latency_check.py` | 없음 | localhost vs 127.0.0.1 지연 측정 |
| `neo4j_readonly_check.py` | 없음 | Neo4j 읽기 전용 연결·스키마·메모리 점검 |

## 재생성 순서 (데스크톱, 사용자 승인 후)

```powershell
# Qdrant·Ollama 준비, .env 설정 후
.venv\Scripts\python.exe docs/development/experiments/exp1_load_search.py <출력경로>\exp1_report_desktop.json
.venv\Scripts\python.exe docs/development/experiments/exp2_lh001.py <출력경로>\exp2_report_desktop.json
$env:RUN_QDRANT_SMOKE="1"; .venv\Scripts\python.exe -m unittest tests/integration/test_qdrant_smoke.py -v
```

- 결과 JSON은 저장소 밖(또는 Git에 올리지 않을 경로)에 저장하고, 노트북 결과(이 폴더의 JSON)와 비교한다.
- 비교 항목: point 107·문서별 14/7/3/10/73, 질문별 Top-5 순위·근거 순위, 임베딩 시간·토큰/초.
- 벡터 값은 하드웨어(CPU/GPU)에 따라 미세하게 다를 수 있으므로 순위 차이는 그대로 기록한다.

## 노트북 실측 요약

- 환경: i5-1135G7, RAM 15.7GB, Ollama 100% CPU(num_ctx 4096), `qwen3-embedding:0.6b`, 입력 규칙 `policy_section_body_v2`
- 임베딩: 1차 34개 69.98초(11,953토큰, batch 16) / 2차 73개 245.5초(31,834토큰, batch 4), L2 norm 1.0
- 검색: 1차 기대 문서 8/8 1위·근거 7/8 1위, 2차 P3 근거 Top-5 밖(15·20위), P6 LH 문서 Top-5 밖(지시문 없음)
- 지연: 127.0.0.1 기준 질의 임베딩 0.16~0.41초, 검색 0.009~0.019초
