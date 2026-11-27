import type { AskResponse, ConditionResult, Notice } from '../api/types';
import {
  hasConfirmedAndFailureWithUnknown, hasOnlyAndGroups, isUnjudgedOnly, matchesExclusion,
} from './conditionGroups';

/**
 * 답변 상태별로 화면에 무엇을 보여 줄지 정한다 (CODING_STYLE §11: 정상 답변·추가 확인·근거 없음을 다른 상태로 표현).
 *
 * - answered: 출처 번호 검증을 통과한 답변이라 본문으로 보여 준다.
 * - no_evidence: 서버 문장 대신 화면의 고정 문구를 쓴다. 근거 없이 답하지 않는다는 뜻을 화면이 직접 정한다.
 * - info: LLM이 쓴 안내라 답변 본문으로 보이지 않게 안내 상자에 넣고, 확정 답변이 아니라는 문구를 항상 붙인다.
 * 서버 notice는 경고(warn)면 답변 위, 일반 안내(info)면 아래에 둔다.
 */

export const NO_EVIDENCE_GUIDE = {
  title: '공식 문서에서 근거를 찾지 못했어요',
  text: '근거 없이 답하지 않을게요. 분야 카드를 눌러 보거나, 질문을 조금 더 구체적으로 적어 주세요.',
};

/** backend/rag/pipeline.py가 needs_check 조건이 있을 때 붙이는 일반 보류 안내 */
const HOLD_NOTICE_TEXT = '자동 판정을 보류한 조건이 있습니다. 공식 공고를 확인해 주세요.';

/**
 * 팀 합의 #78: 종합 판정이 확인 필요(needs_check)일 때 화면 안내는 "신청 자격은 추가 확인이 필요해요"로 보여 준다.
 * API의 notice 원문(계약 §8 예시)은 그대로 두고 화면 표시만 바꾼다.
 */
export const HOLD_NOTICE_DISPLAY: Notice = {
  kind: 'warn',
  title: '신청 자격은 추가 확인이 필요해요',
  text: '자동 판정을 보류한 조건이 있어요. 공식 공고에서 확인해 주세요.',
};

/**
 * 화면이 불일치를 확정할 수 있는 정책이면 true. 카드와 같은 기준이다.
 * - NOT 제외 그룹에 해당(matchesExclusion)
 * - UNKNOWN 하위 그룹이 있어도 단일 AND 종합 행이 불일치로 확정(#40, #80 소득 예외)
 * - AND 그룹만 있고 불일치 행이 있음
 */
function isConfirmedIneligible(conditions: ConditionResult[]): boolean {
  return matchesExclusion(conditions)
    || hasConfirmedAndFailureWithUnknown(conditions)
    || (hasOnlyAndGroups(conditions) && conditions.some(({ status }) => status === 'ineligible'));
}

export const INFO_GUIDE_TITLE = '추가 확인이 필요해요';
export const INFO_CAVEAT = '공식 원문 근거가 부족해 확정 답변은 하지 않았어요.';

export interface AnswerView {
  /** 답변 본문 (answered일 때만) */
  answerText: string | null;
  /** 답변 대신 보여 줄 안내 (no_evidence·info) */
  guide: { kind: 'warn' | 'info'; title: string; text: string } | null;
  /** 답변 위에 두는 서버 경고 */
  warning: Notice | null;
  /** 답변 아래에 두는 서버 안내 */
  extraInfo: Notice | null;
}

/** 출처 한 줄의 부가 정보: 사이트 주소와, 있으면 원문 페이지 (PRD FR-05 '가능하면 페이지 표시') */
export function sourceDetail(href: string | null, pageNumber?: number | null): string {
  let host = '링크 확인 필요';
  if (href) {
    try { host = new URL(href).hostname.replace(/^www\./, ''); } catch { host = href; }
  }
  return pageNumber != null && pageNumber > 0 ? `${host} · p.${pageNumber}` : host;
}

/** 출처 아래 최신성 안내 (PRD §7.1). 확인 시점이 없어도 공식 원문 확인 안내는 항상 보여 준다. */
export function freshnessNote(checkedAt: string | null, isSample: boolean): string {
  const when = checkedAt ? `확인 시점 ${checkedAt.replace(/-/g, '.')}${isSample ? ' (시연용 예시 날짜)' : ''} · ` : '';
  return `${when}최신 내용은 공식 원문에서 확인해 주세요`;
}

export function answerView(r: AskResponse): AnswerView {
  const notice = r.notice ?? null;
  const answer = r.answer.trim();
  const view: AnswerView = {
    answerText: null,
    guide: null,
    warning: notice?.kind === 'warn' ? notice : null,
    extraInfo: notice?.kind === 'info' ? notice : null,
  };
  // 모든 정책의 보류 사유가 '비교할 조건 없음'뿐이면, 일반 보류 경고는 카드의 '자동 판정 안 함' 안내와 같은 내용이라 생략한다.
  // 다른 경고(지역 확인 등)나 구체적인 확인 사항이 있는 정책이 하나라도 있으면 경고를 그대로 둔다.
  const isHoldNotice = notice?.kind === 'warn' && notice.text === HOLD_NOTICE_TEXT;
  if (
    isHoldNotice
    && r.policy_checks.length > 0 && r.policy_checks.every((policy) => isUnjudgedOnly(policy.conditions))
  ) {
    view.warning = null;
  } else if (isHoldNotice && r.policy_checks.some((policy) => !isConfirmedIneligible(policy.conditions))) {
    // 불일치가 확정된 정책만 있으면 "신청 자격은 추가 확인이 필요해요"가 카드의 '조건 불일치'와 어긋나므로 서버 문장을 그대로 둔다.
    view.warning = HOLD_NOTICE_DISPLAY;
  }

  if (r.status === 'answered') {
    view.answerText = answer || null;
  } else if (r.status === 'no_evidence') {
    view.guide = { kind: 'warn', ...NO_EVIDENCE_GUIDE };
  } else {
    view.guide = { kind: 'info', title: INFO_GUIDE_TITLE, text: answer ? `${answer} ${INFO_CAVEAT}` : INFO_CAVEAT };
  }
  return view;
}
