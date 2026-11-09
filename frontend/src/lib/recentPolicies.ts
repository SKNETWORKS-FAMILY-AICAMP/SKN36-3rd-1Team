/**
 * 상담 화면의 가장 최근 질문과 그 답변에 나온 정책·출처 문서.
 * 관계 지도·생애주기 화면이 '방금 물어본 것'을 이어서 보여 주는 데 쓴다.
 * 예시 응답(is_sample)의 정책·출처는 실제 데이터에 없는 가상 값이라 넣지 않는다(질문만 쓴다).
 */
import type { Turn } from '../components/TurnView';

export interface RecentPolicies {
  /** 가장 최근에 답을 받은 질문 */
  question: string;
  /** 답변의 정책 카드에 나온 정책 (실제 응답일 때만) */
  policies: { id: string; name: string }[];
  /** 답변이 인용한 출처 문서 ID (실제 응답일 때만). 정책 카드가 없어도 문서로 정책을 찾을 수 있다 */
  documentIds: string[];
}

export function recentPoliciesFromTurns(turns: Turn[]): RecentPolicies | null {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const t = turns[i];
    if (t.kind !== 'qa' || t.state !== 'done' || !t.response) continue;
    if (t.response.is_sample) return { question: t.question, policies: [], documentIds: [] };
    const seen = new Set<string>();
    const policies = t.response.policy_checks
      .filter((p) => p.policy_id && !seen.has(p.policy_id) && seen.add(p.policy_id))
      .map((p) => ({ id: p.policy_id, name: p.policy_name || p.policy_id }));
    const documentIds = [...new Set(t.response.citations.map((c) => c.document_id).filter((d): d is string => !!d))];
    return { question: t.question, policies, documentIds };
  }
  return null;
}
