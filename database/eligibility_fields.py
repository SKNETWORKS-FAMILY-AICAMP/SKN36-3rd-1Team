"""데이터 품질 경고가 판정 보류 범위로 지정할 수 있는 조건 항목 (Checker·Pipeline·Hybrid 검색 공통).

이 모듈은 다른 모듈을 import하지 않는다. backend/rag/pipeline.py는 .env를 읽기 전에 import되므로,
데이터셋 경로를 import 시점에 고정하는 database.hybrid_search 대신 이 상수만 가져다 쓴다.
"""

# data_quality.affected_attributes·warnings[].affects에 쓰는 개별 항목. "all"(전체 보류)은 따로 다룬다.
SCOPED_ELIGIBILITY_FIELDS = frozenset({
    "age", "region", "income", "housing", "employment", "exclusion", "marriage", "children",
})
ALL_ELIGIBILITY_FIELDS = "all"
