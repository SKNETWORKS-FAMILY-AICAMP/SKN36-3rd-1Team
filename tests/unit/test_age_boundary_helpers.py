"""연령 정합성 검사와 데이터의 '상한 없음' 표현 의미를 별도 검증."""
from backend.eligibility import checker as c
from backend.llm import generator as g


def test_checker_valid_age_limits():
    assert c._is_valid_age(0)
    assert c._is_valid_age(120)
    for value in (True, -1, 121, 20.0, None):
        assert not c._is_valid_age(value)


def test_age_sentinel_distinguishes_extraction_range():
    assert c._has_no_upper_limit(19, 120)
    assert not c._has_no_upper_limit(0, 120)
    assert not c._has_no_upper_limit(19, 34)


def test_generator_valid_age_limits():
    assert g._is_valid_age(0)
    assert g._is_valid_age(120)
    assert not g._is_valid_age(121)
    assert not g._is_valid_age(True)
