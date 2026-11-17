# docs

서비스 요구사항, 데이터·API 계약, 개발 가이드, 실험 및 검증 기록을 관리합니다.
각 하위 폴더에 한글 제목 목차(`README.md`)를 두었습니다. 아래 순서로 보면 됩니다.

## ⭐ 가장 먼저 볼 것 — 최종 산출물

[`reports/README.md`](reports/README.md) → **산출물 1~5** (가장 중요한 문서입니다. 다른 문서는 대부분 이 5개의 세부 근거 자료입니다.)

## 폴더별 목차

| 폴더 | 내용 | 목차 |
| --- | --- | --- |
| [`product/`](product/) | 서비스 범위·요구사항 | [목차](product/README.md) |
| [`api/`](api/) | 질문 API·데이터 계약 | [목차](api/README.md) |
| [`data/`](data/) | 데이터 수집·전처리·스키마 세부 근거 | [목차](data/README.md) |
| [`development/`](development/) | 팀 공통 코딩 규칙, 이슈·PR별 작업 기록 | [목차](development/README.md) |
| [`reports/`](reports/) | 최종 산출물 1~5 + 중간 보고서 | [목차](reports/README.md) |

## 처음 볼 파일 (자주 찾는 것만 바로가기)

| 경로 | 역할 |
| --- | --- |
| [`product/PRD.md`](product/PRD.md) | 서비스 범위와 요구사항 |
| [`api/ask_contract.md`](api/ask_contract.md) | 질문 API와 응답 계약 |
| [`data/data_contract.md`](data/data_contract.md) | 데이터 필드와 해석 규칙 |
| [`development/ISSUE43_RESTORE_GUIDE.md`](development/ISSUE43_RESTORE_GUIDE.md) | 최종 DB 복원 및 검증 |
| [`development/ISSUE41_AB_RUN03_ANALYSIS.md`](development/ISSUE41_AB_RUN03_ANALYSIS.md) | 임베딩 모델 비교 기록 |

## 그 외 루트에 남아 있는 문서

| 파일 | 내용 | 참고 |
| --- | --- | --- |
| [`DATA_COLLECTION_REPORT.md`](DATA_COLLECTION_REPORT.md) | 데이터 수집·전처리 검증(초안) | 더 자세한 최종판은 [산출물 01](reports/README.md) |
| [`CROSSCHECK_REPORT.md`](CROSSCHECK_REPORT.md) | GPT 수집 자료 대조·독립 검증 | 산출물 01의 세부 근거 |
| [`reports/ssok-deliverables/SSOK 발표.pdf`](<reports/ssok-deliverables/SSOK 발표.pdf>) | 발표 PPT |

## 관련 문서

- [상세 가이드](../README.md#로컬에서-전체-실행하기)
