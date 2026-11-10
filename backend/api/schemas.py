"""질문 API와 내부 조건·답변 함수의 데이터 계약."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

ConditionStatus = Literal["eligible", "ineligible", "needs_check"]
ReceptionStatus = Literal["before", "open", "closed", "unknown"]
AnswerStatus = Literal["answered", "no_evidence", "info"]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UserProfile(BaseModel):
    age: Annotated[int, Field(strict=True, ge=0, le=120)] | None
    region: str
    income: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    profile: UserProfile
    history: list[ChatTurn]
    awaiting_field: str | None = None


class ConditionResult(BaseModel):
    field: NonBlank
    label: NonBlank
    reason: NonBlank
    status: ConditionStatus


class EligibilityOutput(BaseModel):
    policy_id: NonBlank
    conditions: Annotated[list[ConditionResult], Field(min_length=1)]
    reception_status: ReceptionStatus


class PolicyCheck(EligibilityOutput):
    policy_name: str
    agency: str
    source_url: str
    official_apply_url: str | None


class Citation(BaseModel):
    title: str
    url: str
    document_id: str
    page_number: int | None


class FollowUp(BaseModel):
    kind: Literal["question", "ask_info"]
    text: NonBlank
    field: str | None = None

    @model_validator(mode="after")
    def check_profile_field(self) -> "FollowUp":
        if self.kind == "ask_info" and self.field not in {"age", "region", "income"}:
            raise ValueError("지원하는 프로필 항목을 지정해야 합니다.")
        return self


class GeneratorOutput(BaseModel):
    status: AnswerStatus
    answer: NonBlank
    used_source_ids: list[Annotated[int, Field(strict=True, gt=0)]]
    follow_up_questions: list[FollowUp]


class Notice(BaseModel):
    kind: Literal["info", "warn"]
    title: str
    text: str


class AskResponse(BaseModel):
    status: AnswerStatus
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    follow_up_questions: list[FollowUp] = Field(default_factory=list)
    checked_at: str | None = None
    notice: Notice | None = None
    is_sample: bool = False
