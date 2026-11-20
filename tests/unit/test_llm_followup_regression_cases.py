"""PR #92 후속 질문 분류 고정 반례 세트.

리뷰·재리뷰에서 직접 만든 반례를 한곳에 모아, 분류 정규식을 고칠 때마다
같은 결과가 나오는지 한 번에 확인한다. 실제 LLM은 호출하지 않는다.

기대값
- "question": 사용자→AI 질문 버튼 1개로 남는다.
- "ask_info/<field>": 해당 프로필 입력 요청 1개로 바뀐다.
- "none": 후속 질문 버튼이 남지 않는다(본문 안내로 옮겨지거나 제거).
- "not_ask_info": 입력 요청으로 바뀌지만 않으면 된다(지시문처럼 버튼 처리 방식이 열려 있는 경우).

AI가 사용자에게 제안하는 문장(…싶으신가요, …궁금하신가요, …원하시나요)은 계약상 question 버튼이 될 수 없어 "none"이다.

새 반례를 찾으면 CASES에 한 줄 추가한다. 기대값을 바꿀 때는 이유를 PR에 남긴다.
"""

import pytest

from backend.llm import generator as g


CASES = [
    # (모델 kind, 모델 field, 문장, 기대값)
    ('ask_info', 'income', '지원 대상 소득은 어떻게 산정되나요?', 'question'),  # 재리뷰
    ('question', None, '지원 대상 소득은 어떻게 산정되나요?', 'question'),  # 재리뷰
    ('ask_info', 'region', '거주 기간 요건이 있나요?', 'question'),  # 재리뷰
    ('question', None, '거주 기간 요건이 있나요?', 'question'),  # 재리뷰
    ('ask_info', 'age', '나이 제한 없이 신청할 수 있나요?', 'question'),  # 재리뷰
    ('question', None, '몇 세까지 신청할 수 있나요?', 'question'),  # 재리뷰
    ('ask_info', 'income', '소득이 없어도 신청할 수 있나요?', 'question'),  # 재리뷰
    ('question', None, '소득 인정액은 어떻게 계산하나요?', 'question'),  # 재리뷰
    ('ask_info', 'region', '거주지 이전 시 지원이 유지되나요?', 'question'),  # 재리뷰
    ('question', None, '현재 사시는 곳이 어디신가요?', 'ask_info/region'),  # 재리뷰
    ('question', None, '연 소득이 대략 얼마쯤 되세요?', 'ask_info/income'),  # 재리뷰
    ('question', None, '올해 나이가 어떻게 되세요?', 'ask_info/age'),  # 재리뷰
    ('question', None, '주소지가 서울이신가요?', 'ask_info/region'),  # 재리뷰
    ('question', None, '월 소득이 200만 원 이하이신가요?', 'ask_info/income'),  # 재리뷰
    ('question', None, '만 34세 이하이신가요?', 'ask_info/age'),  # 재리뷰
    ('question', None, '부모님 소득이 얼마인가요?', 'none'),  # 재리뷰
    ('question', None, '배우자 나이가 어떻게 되세요?', 'none'),  # 재리뷰
    ('question', None, '신청하실 때 필요한 서류가 무엇인가요?', 'question'),  # 재리뷰
    ('question', None, '지원금은 언제 받으실 수 있나요?', 'question'),  # 재리뷰
    ('ask_info', 'income', '주거급여 수급자도 신청할 수 있나요?', 'question'),  # 재리뷰
    ('ask_info', 'region', '지역별 지원 금액이 다른가요?', 'question'),  # 재리뷰
    ('ask_info', 'income', '근로소득만 인정되나요?', 'question'),  # 재리뷰
    ('question', None, '소득이 기준을 넘지 않으시나요?', 'ask_info/income'),  # 재리뷰
    ('ask_info', 'income', '소득이 기준을 넘지 않으시나요?', 'ask_info/income'),  # 재리뷰
    ('question', None, '나이가 34세를 넘지 않으셨나요?', 'ask_info/age'),  # 재리뷰
    ('ask_info', 'age', '연령 기준을 확인하시려면 공고를 보세요?', 'not_ask_info'),  # 최종 점검
    ('ask_info', 'region', '지역별 지원 내용이 궁금하신가요?', 'none'),  # 최종 점검: AI 제안
    ('ask_info', 'region', '지역사회서비스 신청 방법은 무엇인가요?', 'question'),  # 최종 점검
    ('ask_info', 'age', '고령자 연령대별 지원 금액은 얼마인가요?', 'question'),  # 최종 점검
    ('ask_info', 'income', '근로소득공제는 어떻게 계산하나요?', 'question'),  # 최종 점검
    ('ask_info', 'region', '거주지 요건이 있나요?', 'question'),  # 최종 점검
    ('question', None, '소득 기준이 중위소득 60% 이하여야 하나요?', 'question'),  # 최종 점검
    ('question', None, '소득 조건 확인을 위해 월 소득을 알려주세요.', 'ask_info/income'),  # 최종 점검
    ('ask_info', 'income', '소득 조건 확인을 위해 월 소득을 알려주세요.', 'ask_info/income'),  # 최종 점검
    ('question', None, '연령 조건에 맞는지 나이를 알려주세요.', 'ask_info/age'),  # 최종 점검
    ('ask_info', 'age', '연령 조건에 맞는지 나이를 알려주세요.', 'ask_info/age'),  # 최종 점검
    ('question', None, '수입이 기준보다 적으신 편인가요?', 'ask_info/income'),  # 최종 점검
    ('question', None, '급여가 기준 중위소득 50% 이하이신가요?', 'ask_info/income'),  # 최종 점검
    ('question', None, '소득이 기준 중위소득 이하가 아니신가요?', 'ask_info/income'),  # 최종 점검
    ('question', None, '현재 거주지가 어디세요?', 'ask_info/region'),  # 최종 점검
    ('ask_info', 'income', '주거급여 신청 시 주의할 점을 알려주세요.', 'none'),  # 최종 점검
    ('question', None, '주거급여 신청 시 주의할 점을 알려주세요.', 'none'),  # 최종 점검
    ('question', None, '어느 지역에 거주하시나요?', 'ask_info/region'),  # 1차 반례
    ('question', None, '지원 대상 확인을 위해 나이가 어떻게 되시나요?', 'ask_info/age'),  # 1차 반례
    ('question', None, '어느 지역 거주자가 대상인가요?', 'question'),  # 1차 반례
    ('question', None, '지원 대상 나이는 몇 세부터인가요?', 'question'),  # 1차 반례
    ('question', None, '몇 살부터 신청할 수 있나요?', 'question'),  # 1차 반례
    ('question', None, '거주 형태는 월세만 대상인가요?', 'question'),  # 1차 반례
    ('ask_info', 'region', '지역 제한이 있는지 알려주세요.', 'question'),  # 1차 반례
    ('ask_info', 'age', '연령 요건을 알려주세요.', 'question'),  # 1차 반례
    ('ask_info', 'income', '소득 산정 방법을 알려주세요.', 'question'),  # 1차 반례
    ('question', None, '신청하시려면 어떤 서류가 필요하신가요?', 'question'),  # 1차 반례
    ('question', None, '지원금은 언제 받으시나요?', 'question'),  # 1차 반례
    ('ask_info', 'income', '신청하시려면 어떤 서류가 필요하신가요?', 'question'),  # 1차 반례
    ('question', None, '배우자 소득이 기준 중위소득 이하인가요?', 'none'),  # 1차 반례
    ('ask_info', 'income', '배우자 소득이 기준 중위소득 이하인가요?', 'none'),  # 1차 반례
    ('ask_info', 'income', '주거급여는 언제 지급되나요?', 'question'),  # 1차 반례
    ('ask_info', 'age', '만 나이를 알려주시면 해당 정책의 연령 요건에 맞는지 확인해 드릴게요.', 'ask_info/age'),  # 1차 반례
    ('question', None, '지원 대상 나이는 어떻게 되나요?', 'question'),  # 1차 반례
    ('question', None, '지원 대상 여부를 확인하려면 현재 나이가 어떻게 되나요?', 'ask_info/age'),  # 1차 반례
    ('ask_info', 'region', '거주 지역은?', 'ask_info/region'),  # 1차 반례
    ('ask_info', 'income', '월 소득이 기준 금액보다 낮으신가요?', 'ask_info/income'),  # 1차 반례
    ('ask_info', 'income', '오늘 기분은 어떠세요?', 'not_ask_info'),  # 1차 반례
    ('question', None, '국민취업지원제도 I유형의 소득 기준도 알고 싶으신가요?', 'none'),  # 재리뷰 수정: AI 제안
    ('question', None, '연령별 지원 내용도 궁금하신가요?', 'none'),  # 재리뷰 수정: AI 제안
    ('question', None, '주소지 변경 방법도 안내받기를 원하시나요?', 'none'),  # 제안형 버튼 제거
    ('question', None, '근로소득이 있으신가요?', 'none'),  # 재리뷰 수정
    ('question', None, '배우자 소득이 200만 원 이하이신가요?', 'none'),  # 재리뷰 수정
    ('question', None, '자녀가 만 7세 이하인가요?', 'none'),  # 재리뷰 수정
    ('ask_info', 'region', '주소를 변경하면 다시 신청해야 하나요?', 'question'),  # 재리뷰 수정
    ('ask_info', 'income', '사업소득도 소득에 포함되나요?', 'question'),  # 재리뷰 수정
    ('question', None, '만 34세 이하만 신청할 수 있나요?', 'question'),  # 재리뷰 수정
    ('question', None, '월 소득 200만 원 이하여야 하나요?', 'question'),  # 재리뷰 수정
    ('question', None, '서울 거주자만 대상인가요?', 'question'),  # 재리뷰 수정
    ('question', None, '소득이 기준을 넘으면 신청할 수 없나요?', 'question'),  # 재리뷰 수정
    ('question', None, '나이가 34세를 넘으면 지원이 끊기나요?', 'question'),  # 재리뷰 수정
]


def _classify(kind, field, text):
    item = {"kind": kind, "text": text}
    if field:
        item["field"] = field
    raw = {"status": "info", "answer": "확인이 필요합니다.", "used_source_ids": [], "follow_up_questions": [item]}
    # 프로필 없음(None): 이미 입력한 값 때문에 요청이 지워지는 경우와 섞이지 않게 한다.
    return g._validate_model_response(raw, {5}, profile=None)["follow_up_questions"]


def test_cases_have_no_duplicates():
    keys = [(kind, field, text) for kind, field, text, _ in CASES]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("kind,field,text,expected", CASES, ids=[c[2] for c in CASES])
def test_follow_up_regression_case(kind, field, text, expected):
    result = _classify(kind, field, text)
    if expected == "none":
        assert result == []
    elif expected == "not_ask_info":
        assert not any(item["kind"] == "ask_info" for item in result)
    elif expected == "question":
        assert result == [{"kind": "question", "text": text}]
    else:
        expected_field = expected.split("/", 1)[1]
        assert result == [{"kind": "ask_info", "field": expected_field, "text": text}]
