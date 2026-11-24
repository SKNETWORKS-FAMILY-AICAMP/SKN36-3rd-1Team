"""sample_only 일정: 실제 DB·모델 호출 없이 Checker의 일정 전달과 시간 경계를 검사한다."""
from datetime import datetime
from unittest.mock import patch
import pytest
from backend.eligibility import checker
from backend.api.schemas import ApplicationSchedule

class Clock:
    @classmethod
    def now(cls, tz):
        return datetime(2026, 11, 22, 12, tzinfo=tz)

def policy(**dates):
    return {"graph": {"policy": dates}}

@pytest.mark.parametrize("dates,status,remaining", [
    ({"application_start":"2026-11-01","application_end":"2026-11-25"},"open",3),
    ({"application_start":"2026-11-01","application_end":"2026-11-22"},"open",0),
    ({"application_start":"2026-11-01","application_end":"2026-11-21"},"closed",-1),
    ({"application_start":"2026-11-25","application_end":"2026-12-01"},"before",9),
    ({"application_is_ongoing":True},"open",None),
    ({"application_start":" ","application_is_ongoing":True},"open",None),
])
def test_dates_reuse_checker_status(dates,status,remaining):
    with patch.object(checker,"datetime",Clock):
        p=policy(**dates)
        assert checker._reception_status(p)==status
        s=ApplicationSchedule.model_validate(checker.application_schedule(p,status))
        assert s.days_to_end==remaining
        assert s.evaluated_on=="2026-11-22"

@pytest.mark.parametrize("dates", [{},
    {"application_period":"예산 소진 시까지"},
    {"application_start":"2026-02-30","application_end":"2026-12-01"},
    {"application_start":"2026-12-01","application_end":"2026-11-01"},
    {"application_start":"2026-11-01","application_end":"2026-12-01","application_is_ongoing":True},
    {"application_start":"2026-11-01","application_end":"2026-11-22T18:00:00+09:00"},
])
def test_unknown_or_conflicting_has_no_countdown(dates):
    with patch.object(checker,"datetime",Clock):
        p=policy(**dates)
        assert checker._reception_status(p)=="unknown"
        assert checker.application_schedule(p,"unknown") is None
        assert checker.application_schedule(p,"open") is None

def test_does_not_override_checker_result():
    with patch.object(checker,"datetime",Clock):
        assert checker.application_schedule(policy(application_is_ongoing=True),"closed") is None


def test_real_pipeline_preserves_schedule_without_extra_calls():
    from unittest.mock import Mock
    from backend.api.schemas import AskRequest
    from backend.rag.pipeline import run_ask_pipeline
    item={"policy_id":"POL_SAMPLE_SCHEDULE","policy_name":"가상 일정 정책", "best_rank":1,
          "graph":{"policy":{"application_start":"2026-11-01","application_end":"2026-11-25"},"eligibility":{}},
          "graph_status":"FOUND","data_quality":{},
          "evidence":[{"rank":1,"chunk_id":"CH_SAMPLE","document_id":"DOC_SAMPLE","text":"sample_only 가상 근거", "source_url":"https://example.org/notice"}]}
    search=Mock(return_value={"policies":[item]})
    generate=Mock(return_value={"status":"answered","answer":"가상 안내 [1]","used_source_ids":[1],"follow_up_questions":[]})
    request=AskRequest.model_validate({"question":"가상 정책 일정", "profile":{"age":None,"region":"","income":None},"history":[]})
    with patch.object(checker,"datetime",Clock):
        result=run_ask_pipeline(request,search_fn=search,check_fn=checker.check_eligibility,generate_fn=generate)
    assert result.policy_checks[0].application_schedule.days_to_end==3
    assert result.policy_checks[0].reception_status=="open"
    assert search.call_count==generate.call_count==1
