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

## 관련 문서

- [상세 가이드](../data/README.md)
