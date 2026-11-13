# scripts

데이터 수집·정제·검증과 DB 복원에 사용하는 실행 스크립트입니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`fetch_youth_api.py`](fetch_youth_api.py) | 온통청년 API 수집 실행 |
| [`build_processed_dataset.py`](build_processed_dataset.py) | 초기 데이터 정제에 사용. 원본(`data/raw`)은 `.gitignore` 대상이라 저장소에 포함되지 않습니다. 먼저 [`download_raw.py`](download_raw.py)로 받거나(`data/metadata/download_manifest.json`이 다시 쓰입니다) 원본 위치를 `--raw-dir`로 지정하세요. 경로는 `--raw-dir`, `--processed-dir`, `--metadata-dir`로 지정할 수 있습니다. |
| [`verify_handoff_package.py`](verify_handoff_package.py) | 인계 데이터 검증 |
| [`restore_final_v140.py`](restore_final_v140.py) | 최종 DB 복원 및 검증 |

팀원 로컬 실행은 아래 최종 DB 복원 가이드를 따릅니다.

`build_handoff_package.py`가 생성한 `load_neo4j.py`를 실행하기 전에 현재 셸에 `NEO4J_PASSWORD` 환경 변수를 설정하세요. 이 적재 스크립트는 `.env`를 자동으로 읽지 않습니다.

## 관련 문서

- [상세 가이드](../docs/development/ISSUE43_RESTORE_GUIDE.md)
