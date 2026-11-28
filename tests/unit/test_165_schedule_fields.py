"""#165 계약 v1.13: '마감 임박'과 마감순 순위는 Checker가 계산해 내려보낸다(화면은 다시 계산하지 않는다). 가상 정책."""
from datetime import date

import pytest

from backend.eligibility.checker import SOON_WINDOW_DAYS, application_schedule, deadline_rank

TODAY = date(2026, 11, 22)


def policy(**node) -> dict:
    return {"policy_id": "POL_SAMPLE", "graph": {"policy": node}}


@pytest.mark.parametrize(("end", "soon"), [("2026-11-22", True), ("2026-11-29", True), ("2026-11-30", False)])
def test_closing_soon_window(end, soon):
    schedule = application_schedule(policy(application_start="2026-11-01", application_end=end), "open", evaluated_on=TODAY)
    assert SOON_WINDOW_DAYS == 7
    assert schedule["closing_soon"] is soon


def test_ongoing_and_not_open_are_never_closing_soon():
    ongoing = application_schedule(policy(application_is_ongoing=True), "open", evaluated_on=TODAY)
    before = application_schedule(policy(application_start="2026-11-25", application_end="2026-11-26"), "before",
                                  evaluated_on=TODAY)
    assert ongoing["closing_soon"] is False and before["closing_soon"] is False


@pytest.mark.parametrize(("reception", "node", "rank"), [
    ("open", {"application_start": "2026-11-01", "application_end": "2026-11-30"}, 0),
    ("before", {"application_start": "2026-11-25", "application_end": "2026-11-30"}, 1),
    ("open", {"application_is_ongoing": True}, 2),
    ("unknown", {}, 3),
    ("closed", {"application_start": "2026-11-01", "application_end": "2026-11-20"}, 4),
])
def test_deadline_rank(reception, node, rank):
    schedule = application_schedule(policy(**node), reception, evaluated_on=TODAY)
    assert deadline_rank(reception, schedule) == rank
