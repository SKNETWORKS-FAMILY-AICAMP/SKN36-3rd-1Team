"""정책 ID URL 컨버터.

FastAPI 계약(backend/api/schemas.py AskRequest.selected_policy_id)과 같은 규칙: ^POL_[A-Z0-9_]+$, 최대 128자.
형식이 틀린 주소는 View를 실행하지 않고 URL 단계에서 404가 된다.
"""
import re

POLICY_ID_PATTERN = re.compile(r"^POL_[A-Z0-9_]+$")
POLICY_ID_MAX_LENGTH = 128


def is_policy_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= POLICY_ID_MAX_LENGTH and bool(POLICY_ID_PATTERN.fullmatch(value))


class PolicyIdConverter:
    regex = r"POL_[A-Z0-9_]+"

    def to_python(self, value: str) -> str:
        if not is_policy_id(value):
            raise ValueError("정책 ID 형식이 아닙니다.")
        return value

    def to_url(self, value: str) -> str:
        if not is_policy_id(value):
            raise ValueError("정책 ID 형식이 아닙니다.")
        return value
