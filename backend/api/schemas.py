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


class Contact(BaseModel):
    """정책 원문의 [문의처] 항목 하나(#77 계약, PolicyCheck.contacts 필드용)."""

    agency: NonBlank
    phone: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    source_url: str | None = None


class ExclusionReason(BaseModel):
    """NOT 제외 조건 하나(#77 계약 — 자리만 예약, #78이 채운다). ConditionResult와 같은 field/label 체계를 쓴다."""

    field: NonBlank
    label: NonBlank
    reason: NonBlank


class OfficialRequirement(BaseModel):
    """정책이 요구하는 공식 기준 하나(#77 계약 — 자리만 예약, #76-A가 채운다). value는 원문 그대로이며 임의 환산 금지."""

    label: NonBlank
    value: NonBlank
    document_id: str | None = None
    chunk_id: str | None = None


class PolicyCheck(EligibilityOutput):
    policy_name: str
    agency: str
    source_url: str
    official_apply_url: str | None
    contacts: list[Contact] | None = None
    # #77 계약에서 세 기능(제외 정보·공식 요건·문의처)을 함께 설계했다. 아래 둘은 자리만 예약한 것으로,
    # Pipeline이 항상 None을 돌려준다 — 값을 채우는 구현은 각각 #78·#76-A에서 한다.
    exclusion_reasons: list[ExclusionReason] | None = None
    official_requirements: list[OfficialRequirement] | None = None


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
