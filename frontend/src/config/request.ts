/**
 * 질문 요청 설정값. 시간 제한·이력 길이처럼 백엔드 계약과 맞춰야 하는 값을 한 곳에 둔다 (CODING_STYLE §3).
 * 기준: docs/api/ask_contract.md v1.0 (#23 브랜치, dev 머지 전)
 */
import type { NodeKind } from './graph';

/** 질문 요청을 포기하는 시간. 계약의 통합 목표값 60초. 실제 응답 시간을 잰 뒤 조정한다. */
export const ASK_TIMEOUT_MS = 60_000;

/** 함께 보내는 대화 이력 개수. 계약: 최근 10개까지 (백엔드도 최근 10개만 쓴다). */
export const HISTORY_MAX_ITEMS = 10;

/** 생애주기 화면의 '이 정책 물어보기'가 상담 화면에 보내는 질문 */
export const policyQuestion = (policyName: string) => `${policyName} 지원 대상이랑 신청 방법, 필요한 서류 알려줘`;

/**
 * 관계 지도 팝업의 '물어보기' 질문 (원 종류별). 정책·근거 문서는 policyQuestion을 쓴다.
 * 질문만 만들 뿐 자격을 판단하지 않는다. 답변은 백엔드가 근거 문서로 만든다.
 */
export const TOPIC_QUESTION: Partial<Record<NodeKind, (name: string) => string>> = {
  organization: (name) => `${name}에서 하는 지원 정책 알려줘`,
  region: (name) => `${name} 지원 정책 알려줘`,
  category: (name) => `${name} 분야 지원 정책 알려줘`,
  target: (name) => `${name} 대상 지원 정책 알려줘`,
  lifestage: (name) => `${name} 시기에 받을 수 있는 지원 정책 알려줘`,
};
/** 조건 묶음 원의 질문 (가운데 정책 이름을 넣는다) */
export const conditionQuestion = (policyName: string) => `${policyName} 자격 조건 자세히 알려줘`;

/** 기다리는 동안 바꿔 보여 줄 안내 문구. after: 요청 후 지난 시간(ms) */
export const LOADING_STAGES = [
  { after: 0, text: '공식 원문을 찾고 있어요' },
  { after: 10_000, text: '조건을 확인하고 답변을 정리하고 있어요' },
  { after: 25_000, text: '조금 오래 걸리고 있어요. 잠시만 기다려 주세요' },
] as const;
