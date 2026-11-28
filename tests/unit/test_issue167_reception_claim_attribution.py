"""#167: 여러 정책 답변에서 정책명 없는 단독 단정문의 대상을 같은 문단의 출처로 정한다. LLM·DB 호출 없음.

같은 문단의 다른 문장이 모두 한 정책만 인용하고 그 정책이 이 문장의 인용과 같으면 그 정책의 판정과 비교한다.
- 판정과 맞으면 그대로 둔다(접수 중인데 '접수 여부를 확인해 주세요'로 바뀌던 문제).
- 판정과 다르면 그 정책의 판정 문구로 바꾼다.
대상을 정할 수 없으면(문단에 다른 정책 출처가 있거나 문장이 혼자이면) #23처럼 중립 문구로 바꾸고 인용을 뺀다.
정책 ID·이름은 형식 확인용 가상 값(sample_only)이고, 첫 테스트의 문장은 2026-11-29 실제 답변을 줄인 것이다.
"""

from backend.rag.pipeline import RECEPTION_NOTICE, _align_answer_with_checks


def check(pid: str, name: str, status: str) -> dict:
    return {"policy_id": pid, "policy_name": name, "reception_status": status,
            "conditions": [{"field": "eligibility", "label": "자격 요건", "reason": "가상 판정", "status": "needs_check"}]}


CHECKS = [check("P_OPEN", "가상 취업지원제도", "open"), check("P_U1", "가상 해외취업 지원", "unknown"),
          check("P_U2", "가상 내일채움공제", "unknown")]
# [1][4][5] → 접수 중 정책, [2]·[3] → 접수 미확인 정책 (실제 사례와 같은 배치)
CONTEXTS = [{"source_id": n, "policy_id": pid} for n, pid in
            ((1, "P_OPEN"), (2, "P_U1"), (3, "P_U2"), (4, "P_OPEN"), (5, "P_OPEN"))]

OTHER_PARAGRAPHS = (
    "\n\n**가상 해외취업 지원**도 있습니다[2]. 현재 접수 여부는 공식 공고에서 확인해 주세요[2].\n\n"
    "**가상 내일채움공제**도 있습니다[3]. 현재 접수 여부는 공식 공고에서 확인해 주세요[3]."
)


def align(answer: str, checks=CHECKS) -> tuple[str, bool]:
    return _align_answer_with_checks(answer, CONTEXTS, checks)


def test_real_case_open_claim_in_single_policy_paragraph_is_kept():
    text = ("구직 중인 청년에게는 **가상 취업지원제도**가 있습니다[5][4]. 현재 입력만으로는 자격을 확정할 수 없습니다[1]. "
            "현재 접수 중이에요[1]. 직접 신청 링크는 확인되지 않았어요[1]." + OTHER_PARAGRAPHS)
    assert align(text) == (text, False)


def test_conflicting_claim_in_single_policy_paragraph_uses_that_policy():
    checks = [check("P_OPEN", "가상 취업지원제도", "closed"), *CHECKS[1:]]
    text = "**가상 취업지원제도**가 있습니다[4]. 현재 접수 중이에요[1]." + OTHER_PARAGRAPHS
    fixed, changed = align(text, checks)
    assert changed
    first = fixed.split("\n\n")[0]
    assert "접수가 마감됐어요" in first and "[1]" in first
    assert "접수 중이에요" not in first
    assert RECEPTION_NOTICE["unknown"] not in first


def test_paragraph_citing_another_policy_still_neutralizes():
    # 문단의 다른 문장은 [2](접수 미확인 정책)인데 이 문장만 [1](접수 중 정책)을 붙였다 → 대상을 정할 수 없다.
    text = "**가상 해외취업 지원**도 있습니다[2]. 현재 접수 중이에요[1]."
    fixed, changed = align(text)
    assert changed and "접수 중이에요" not in fixed and "[1]" not in fixed
    assert RECEPTION_NOTICE["unknown"] in fixed


def test_paragraph_citing_several_policies_still_neutralizes():
    text = "두 정책이 있습니다[2][4]. 현재 접수 중이에요[1]."
    fixed, changed = align(text)
    assert changed and "접수 중이에요" not in fixed and "[1]" not in fixed


def test_lone_sentence_still_neutralizes():
    # #23 기존 동작: 문단에 다른 근거 문장이 없으면 인용 하나로 대상을 정하지 않는다.
    fixed, changed = align("현재 접수 중이에요[1].")
    assert changed and "접수 중이에요" not in fixed and "[1]" not in fixed


def test_other_paragraph_does_not_count():
    # 같은 정책 출처라도 다른 문단에 있으면 이 문장의 대상 근거로 쓰지 않는다.
    text = "**가상 취업지원제도**가 있습니다[4].\n\n현재 접수 중이에요[1]."
    fixed, changed = align(text)
    assert changed and "현재 접수 중이에요[1]" not in fixed
