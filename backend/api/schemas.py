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
    selected_policy_id: Annotated[str, StringConstraints(pattern=r"^POL_[A-Z0-9_]+$", max_length=128)] | None = None


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
    """정책의 공식 기준 원문 발췌. value는 원문 그대로이며 임의 환산·검수 완료 승격 금지."""

    label: NonBlank
    value: NonBlank
    document_id: str | None = None
    chunk_id: str | None = None
    source_url: str | None = None


class SourceExcerpt(BaseModel):
    """같은 정책의 명시된 원문 블록 발췌. 완전한 제출 서류 목록이라고 보장하지 않는다."""
    text: NonBlank
    document_id: NonBlank
    chunk_id: NonBlank
    source_url: str


class ApplicationSchedule(BaseModel):
    evaluated_on: str
    start_date: str | None
    end_date: str | None
    is_ongoing: bool
    days_to_start: int | None
    days_to_end: int | None


class PolicyCheck(EligibilityOutput):
    application_schedule: ApplicationSchedule | None = None
    required_documents: list[SourceExcerpt] | None = None
    policy_name: str
    agency: str
    source_url: str
    official_apply_url: str | None
    contacts: list[Contact] | None = None
    # 제외 사유의 조건 트리 구현은 #78이 담당한다. 공식 기준은 명시된 원문 블록만 발췌한다.
    exclusion_reasons: list[ExclusionReason] | None = None
    official_requirements: list[OfficialRequirement] | None = None


class PolicySummary(BaseModel):
    """개인 프로필·자격 판정이 없는 탐색 목록 한 건."""
    policy_id: NonBlank
    policy_name: NonBlank
    agency: str
    regions: list[str]
    categories: list[str]
    source_url: str
    official_apply_url: str | None
    reception_status: ReceptionStatus
    application_schedule: ApplicationSchedule | None
    required_documents: list[SourceExcerpt] | None = None
    official_requirements: list[OfficialRequirement] | None = None


class PolicyListResponse(BaseModel):
    items: list[PolicySummary]
    total: int
    offset: int
    limit: int
    dataset_version: str
    manifest_sha256: str
    evaluated_on: str
    source_kind: Literal["release_files"] = "release_files"
    regions: list[str]
    categories: list[str]


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
