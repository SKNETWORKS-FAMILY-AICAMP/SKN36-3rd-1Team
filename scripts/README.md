# scripts

데이터 수집·정제·검증과 DB 복원에 사용하는 실행 스크립트입니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`fetch_youth_api.py`](fetch_youth_api.py) | 온통청년 API 수집 실행 |
| [`build_processed_dataset.py`](build_processed_dataset.py) | 초기 데이터 정제에 사용. 당시 원본 파일과 로컬 경로 설정이 필요합니다. |
| [`verify_handoff_package.py`](verify_handoff_package.py) | 인계 데이터 검증 |
| [`restore_final_v140.py`](restore_final_v140.py) | 최종 DB 복원 및 검증 |

팀원 로컬 실행은 아래 최종 DB 복원 가이드를 따릅니다.

## 관련 문서

- [상세 가이드](../docs/development/ISSUE43_RESTORE_GUIDE.md)
