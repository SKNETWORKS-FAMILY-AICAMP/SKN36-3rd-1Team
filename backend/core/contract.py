"""여러 backend 모듈이 같이 쓰는 ask 계약 값(docs/api/ask_contract.md).

값을 바꾸면 계약 문서·프론트 표시와 함께 바꾼다. 이 모듈은 다른 backend 모듈을 import하지 않는다
(rag·llm·eligibility 어디서 가져와도 순환 import가 생기지 않게 하기 위해서다).
"""

import re

# §5.1 history: 최근 10턴까지만 LLM에 전달한다.
MAX_HISTORY_TURNS = 10
# §6-4 select_contexts 기본 근거 수(source_id 1~5). AGE 근거 보강은 그 뒤 최대 1개.
MAX_CONTEXTS = 5
# §3.2 정규화 프로필 age 상한(API 입력 검증과 Checker 판정이 같은 값을 쓴다).
MAX_VALID_AGE = 120
# §7-1 본문 인용 번호 [n]. 번호는 문자열로만 다룬다.
CITATION = re.compile(r"\[(\d+)\]")
# 생성 답변을 문장 단위로 나눈다. 구분자도 결과에 남겨 다시 이어 붙일 수 있다.
SENTENCE_BREAK = re.compile(r"((?<=[.!?。！？])\s+|\n+)")
