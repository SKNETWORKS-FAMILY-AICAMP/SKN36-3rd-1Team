/**
 * 화면 ↔ 백엔드 질문 API 계약.
 *
 * PRD §8.1·D-07의 전달 항목(answer, citations, status, follow_up_question, error)과
 * 조건 확인 결과(충족·불일치·미확인, 이유, 부족 항목, 모집 상태)를 담는다.
 * 기준: docs/api/ask_contract.md v1.1.
 * 백엔드 backend/api/schemas.py와 필드 이름·값이 같다. 계약이 바뀌면 이 파일부터 맞춘다. JSON 필드는 snake_case.
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
  /** 이어지는 대화를 위해 앞선 질문·답변을 함께 보낸다 (최근 HISTORY_MAX_ITEMS개까지). */
  history: ChatTurnMessage[];
  /**
   * AI가 되물은 항목에 대한 대답일 때 그 항목 키 (FollowUp.field: age, region, income).
   * 서버는 이 값으로 문장을 해석하지 않는다. 화면이 전용 입력칸에서 받은 값을 profile에 넣어 보낸다.
   */
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

/**
 * 답변 아래 추가 질문 버튼.
 * - question: 누르면 text를 그대로 질문으로 보낸다
 * - ask_info: 누르면 화면이 field 항목을 되묻고, 사용자의 대답을 다음 요청에 awaiting_field와 함께 보낸다
 * PRD의 follow_up_question(문장 하나)을 버튼 여러 개로 넓힌 형식이다.
 * 계약: ask_info의 field는 age, region, income 중 하나. 그 밖의 정보 요청은 답변 본문에서 안내한다.
 */
export interface FollowUp {
  kind: 'question' | 'ask_info';
  text: string;
  field?: string | null; // ask_info일 때: age | region | income
}

export interface Notice {
  kind: 'info' | 'warn';
  title: string;
  text: string;
}

/**
 * answered: 출처 검증을 통과한 답변 / no_evidence: 근거 없음(서버가 LLM 없이 정한 문장) / info: 근거가 부족해 안내만
 * 화면은 answered의 answer를 답변 본문으로, info의 answer는 안내 상자 아래 근거 설명으로 보여 준다 (lib/answerView.ts, TurnView).
 */
export type AnswerStatus = 'answered' | 'no_evidence' | 'info';

export interface AskResponse {
  status: AnswerStatus;
  answer: string; // 본문 속 [1], [2]는 citations 순번
  citations: Citation[];
  policy_checks: PolicyCheck[];
  follow_up_questions: FollowUp[];
  /** 공식 원문 수집·확인 시점 (PRD §7.1 최신성) */
  checked_at: string | null;
  notice?: Notice | null;
  /** 예시 응답(시연용 가상 데이터)인지. 화면에 '예시 응답'으로 표시한다. */
  is_sample: boolean;
}
