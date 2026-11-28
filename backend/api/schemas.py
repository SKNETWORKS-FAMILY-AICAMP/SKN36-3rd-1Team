"""질문 API와 내부 조건·답변 함수의 데이터 계약."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from backend.core.contract import MAX_VALID_AGE, PROFILE_INPUT_FIELDS

ConditionStatus = Literal["eligible", "ineligible", "needs_check"]
ReceptionStatus = Literal["before", "open", "closed", "unknown"]
AnswerStatus = Literal["answered", "no_evidence", "info"]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UserProfile(BaseModel):
    age: Annotated[int, Field(strict=True, ge=0, le=MAX_VALID_AGE)] | None
    region: str
    income: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None
    # #39: 자녀 월령(개월). 신청자 본인 나이(age)와 별개다. 생략·null은 미입력, 0은 유효값.
    # strict라 "24"·24.5·24.0·true를 정수로 바꾸지 않고 422로 거부한다. 상한은 정책 근거가 없어 두지 않는다.
    # 신청 여부를 확인하려는 자녀 한 명의 값만 받는다. 자녀가 여러 명이면 답변 본문에서 자녀별 확인을 안내한다.
    child_age_months: Annotated[int, Field(strict=True, ge=0)] | None = None


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
    """Checker가 확정한 NOT 제외 사유 하나(#77 계약, #78). ConditionResult와 같은 field/label 체계를 쓴다."""

    field: NonBlank
    label: NonBlank
    reason: NonBlank


class OfficialRequirement(BaseModel):
    """정책이 요구하는 공식 기준 하나(#77 계약, #76이 채운다). label·value는 근거 원문 그대로이며 임의 환산 금지.

    document_id·chunk_id는 그 기준이 나온 같은 정책의 채택 근거다. 사용자의 충족 여부(conditions)가 아니다.
    """

    label: NonBlank
    value: NonBlank
    document_id: str | None = None
    chunk_id: str | None = None


class SourceExcerpt(BaseModel):
    """같은 정책의 명시된 원문 블록 발췌. 완전한 제출 서류 목록이라고 보장하지 않는다."""
    text: NonBlank
    document_id: NonBlank
    chunk_id: NonBlank
    source_url: str


class RequirementExcerpt(SourceExcerpt):
    """[지원대상]·[선정기준] 원문 블록 발췌(#107). 검수된 공식 기준(official_requirements, #76)이 아니다."""
    label: NonBlank


class ApplicationSchedule(BaseModel):
    evaluated_on: str
    start_date: str | None
    end_date: str | None
    is_ongoing: bool
    days_to_start: int | None
    days_to_end: int | None
    # v1.13: 접수 중·상시 아님·마감까지 7일 이하(Checker SOON_WINDOW_DAYS). 화면은 직접 계산하지 않는다.
    closing_soon: bool = False


class PolicyCheck(EligibilityOutput):
    application_schedule: ApplicationSchedule | None = None
    # v1.13: 마감순 정렬 순위(Checker deadline_rank). 0 마감일 있는 접수 중 → 4 마감.
    deadline_rank: int | None = None
    required_documents: list[SourceExcerpt] | None = None
    policy_name: str
    agency: str
    source_url: str
    official_apply_url: str | None
    contacts: list[Contact] | None = None
    requirement_excerpts: list[RequirementExcerpt] | None = None
    # #77 계약에서 세 기능(제외 정보·공식 요건·문의처)을 함께 설계했다. exclusion_reasons는 Checker가 확정한
    # 같은 정책의 제외 사유를 그대로 담고, 최종 판정과 맞지 않으면 None이다(#78, pipeline._confirmed_exclusion_reasons).
    # official_requirements는 채택 근거의 자격 블록에서 원문이 항목명을 붙인 기준(또는 원문과 맞는 검수 기록)만
    # 원문 그대로 채운다(#76, backend/rag/official_requirements.py). 확인된 기준이 없으면 None이다.
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
    deadline_rank: int | None = None
    required_documents: list[SourceExcerpt] | None = None
    requirement_excerpts: list[RequirementExcerpt] | None = None


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
        if self.kind == "ask_info" and self.field not in PROFILE_INPUT_FIELDS:
            raise ValueError("지원하는 프로필 항목을 지정해야 합니다.")
        return self


class GeneratorOutput(BaseModel):
    status: AnswerStatus
    answer: NonBlank
    used_source_ids: list[Annotated[int, Field(strict=True, gt=0)]]
    follow_up_questions: list[FollowUp]


# notice 종류(v1.13). 화면은 문장(text) 대신 이 값으로 안내 종류를 알아본다. 모르는 안내는 null.
NoticeCode = Literal["unknown_region", "condition_hold", "answer_aligned"]


class Notice(BaseModel):
    kind: Literal["info", "warn"]
    title: str
    text: str
    code: NoticeCode | None = None


class AskResponse(BaseModel):
    status: AnswerStatus
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    follow_up_questions: list[FollowUp] = Field(default_factory=list)
    checked_at: str | None = None
    notice: Notice | None = None
    is_sample: bool = False
