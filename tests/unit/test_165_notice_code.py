"""#165 계약 v1.13: notice에 종류 code가 붙고, 화면이 문장 대신 code로 안내를 알아본다."""

import pytest
from pydantic import ValidationError

from backend.api.schemas import Notice
from backend.rag import pipeline


@pytest.mark.parametrize(("code", "text"), [
    ("unknown_region", pipeline.UNKNOWN_REGION_NOTICE_TEXT),
    ("condition_hold", pipeline.HOLD_NOTICE_TEXT),
    ("answer_aligned", pipeline.ALIGNED_NOTICE_TEXT),
])
def test_warn_notice_carries_code(code, text):
    notice = Notice.model_validate(pipeline._warn_notice(code, text))
    assert (notice.kind, notice.title, notice.text, notice.code) == ("warn", "추가 확인 필요", text, code)


def test_notice_code_is_optional_and_closed():
    assert Notice(kind="info", title="안내", text="본문").code is None
    with pytest.raises(ValidationError):
        Notice(kind="warn", title="안내", text="본문", code="condition_hold_typo")
