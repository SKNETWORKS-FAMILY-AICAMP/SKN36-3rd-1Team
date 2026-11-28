# collectors

공식 API와 웹에서 정책 자료를 수집하고 원문 필드를 정규화합니다. 원본과 정제 결과의 관리 기준은 데이터 README에 있습니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`api/youthcenter_collector.py`](api/youthcenter_collector.py) | 온통청년 API 수집 |
| [`api/gov24_collector.py`](api/gov24_collector.py) | 정부24 API 수집 |
| [`api/bokjiro_collector.py`](api/bokjiro_collector.py) | 복지로 API 수집 |
| [`html/nhuf_fetcher.py`](html/nhuf_fetcher.py) | 주택도시기금 자료 수집 |
| [`preprocessing/normalize.py`](preprocessing/normalize.py) | 수집 필드 정규화 |

## 다시 수집할 때 주의

- 서비스 데이터(`data/releases/v1.4.0-candidate/`)는 동결돼 있다. 수집기를 다시 돌려도 릴리스는 바뀌지 않으며, 새 릴리스는 별도 빌드·검증 절차로만 만든다.
- API 수집기 3종은 수집 중 오류(페이지 실패·쿼터 초과 등)가 하나라도 있으면 `data/processed/*.jsonl`을 덮어쓰지 않고 같은 폴더의 `*.partial.jsonl`에 쓴다. 오류를 확인한 뒤에도 덮어써야 하면 `COLLECTOR_ALLOW_PARTIAL=1`로 다시 실행한다.
- 복지로 `POL_BOK_*`·`DOC_BOK_*` 번호는 정렬된 원본 파일 순서로 매긴다. 서비스가 추가·삭제되면 번호가 밀리므로, 재수집 결과를 릴리스에 넣기 전에 기존 `servId`↔ID 매핑과 대조해야 한다.
- `retrieved_at`은 정제 시점으로 기록된다. 캐시에서 다시 정제하면 실제 수집일과 달라질 수 있다.

## 관련 문서

- [상세 가이드](../data/README.md)
