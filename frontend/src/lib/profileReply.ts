import type { UserProfile } from '../api/types';

/**
 * 조건만 알려 주는 답(예: "난 26살이야")을 받았을 때 화면이 할 일 (#64).
 * - store: 앞 질문이 없다. 내 조건에만 넣고 안내한다.
 * - busy: 다시 보낼 앞 질문의 답변을 아직 기다리는 중이다. 내 조건도 바꾸지 않고 중복 안내만 한다.
 * - resend: 내 조건에 넣고, 앞 질문을 바뀐 조건으로 다시 보낸다.
 *   field는 조건 입력칸과 같은 규칙으로, 읽은 항목이 하나면 그 키, 여러 개면 null이다.
 */
export type ProfileReplyPlan =
  | { kind: 'store' }
  | { kind: 'busy' }
  | { kind: 'resend'; question: string; profile: UserProfile; field: string | null };

export function planProfileReply(
  reply: Partial<UserProfile>,
  previousQuestion: string | null,
  current: UserProfile,
  isPending: (question: string) => boolean,
): ProfileReplyPlan {
  if (previousQuestion == null) return { kind: 'store' };
  if (isPending(previousQuestion)) return { kind: 'busy' };
  const keys = Object.keys(reply);
  return { kind: 'resend', question: previousQuestion, profile: { ...current, ...reply }, field: keys.length === 1 ? keys[0] : null };
}
