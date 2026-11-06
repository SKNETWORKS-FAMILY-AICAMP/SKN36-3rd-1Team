/**
 * 화면 ↔ 백엔드 질문 API 계약 (초안).
 *
 * PRD §8.1·D-07의 전달 항목(answer, citations, status, follow_up_question, error)과
 * 조건 확인 결과(충족·불일치·미확인, 이유, 부족 항목, 모집 상태)를 담는다.
 * 필드 이름과 값은 아직 확정되지 않았다. 나지인·박소희와 합의한 뒤 이 파일만 고치면
 * 화면 코드는 그대로 쓸 수 있도록 화면 전용 이름을 쓰지 않는다. JSON 필드는 snake_case.
 */

/** 사용자 조건. 실사용자 개인정보가 아니라 화면에서 입력한 선택 값이다. */
export interface UserProfile {
  age: number | null;
  region: string; // '' = 미입력. 시·도 약칭(서울, 경기 …)
  income: number | null; // 연 소득, 만 원 단위. 화면에는 표시만 하고 판정은 백엔드가 한다
}

export interface ChatTurnMessage {
  role: 'user' | 'assistant';
  text: string;
}

export interface AskRequest {
  question: string;
  profile: UserProfile;
  /** 이어지는 대화를 위해 앞선 질문·답변을 함께 보낸다. */
  history: ChatTurnMessage[];
  /** AI가 되물은 항목(예: '혼인 기간')에 대한 대답일 때 그 항목 이름. */
  awaiting_field?: string | null;
}

/** PRD §2.1: 조건상 적합 / 추가 확인 필요 / 조건 불일치 */
export type ConditionStatus = 'eligible' | 'needs_check' | 'ineligible';

/** PRD §2.1: 접수 전 / 접수 중 / 마감 / 미확인 (조건 결과와 따로 반환) */
export type ReceptionStatus = 'before' | 'open' | 'closed' | 'unknown';

export interface ConditionResult {
  field: string; // 'age' | 'region' | 'income' …
  label: string; // 화면 표시 이름: 나이, 지역, 소득
  reason: string;
  status: ConditionStatus;
}

export interface PolicyCheck {
  policy_id: string;
  policy_name: string;
  agency: string;
  reception_status: ReceptionStatus;
  conditions: ConditionResult[];
  source_url: string;
  /** 신청 주소가 확인된 경우에만 값이 있다(null이면 '신청하기'를 숨긴다). */
  official_apply_url: string | null;
}

export interface Citation {
  title: string;
  url: string;
  document_id?: string;
  page_number?: number | null;
}

export interface Notice {
  kind: 'info' | 'warn';
  title: string;
  text: string;
}

/** answered: 근거 있는 답변 / no_evidence: 근거 없음 / info: 안내만 */
export type AnswerStatus = 'answered' | 'no_evidence' | 'info';

export interface AskResponse {
  status: AnswerStatus;
  answer: string; // 본문 속 [1], [2]는 citations 순번
  citations: Citation[];
  policy_checks: PolicyCheck[];
  follow_up_questions: string[];
  /** 공식 원문 수집·확인 시점 (PRD §7.1 최신성) */
  checked_at: string | null;
  notice?: Notice | null;
  /** 예시 응답(시연용 가상 데이터)인지. 화면에 '예시 응답'으로 표시한다. */
  is_sample: boolean;
}
