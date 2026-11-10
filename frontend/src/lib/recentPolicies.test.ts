import { describe, expect, it } from 'vitest';
import type { AskResponse, PolicyCheck } from '../api/types';
import type { Turn } from '../components/TurnView';
import { recentPoliciesFromTurns } from './recentPolicies';

/** 가상 정책 결과 (sample_only) */
const check = (id: string, name: string): PolicyCheck => ({
  policy_id: id, policy_name: name, agency: '가상 기관', reception_status: 'unknown', conditions: [], source_url: 'https://example.org', official_apply_url: null,
});
const answer = (checks: PolicyCheck[], isSample = false, docs: string[] = []): AskResponse => ({
  status: 'answered', answer: '답변[1]', citations: docs.map((d) => ({ title: d, url: 'https://example.org', document_id: d })),
  policy_checks: checks, follow_up_questions: [], checked_at: null, notice: null, is_sample: isSample,
});
const qa = (id: number, question: string, response?: AskResponse, state: 'loading' | 'done' | 'error' = 'done'): Turn =>
  ({ id, kind: 'qa', question, tags: [], state, response });

describe('최근 상담 맥락', () => {
  it('가장 최근에 답을 받은 질문의 정책·출처 문서를 고르고, 같은 정책은 한 번만 넣는다', () => {
    const turns: Turn[] = [
      qa(1, '첫 질문', answer([check('POL_1', '정책 1')])),
      qa(2, '두 번째 질문', answer([check('POL_2', '정책 2'), check('POL_2', '정책 2'), check('POL_3', '정책 3')], false, ['DOC_2', 'DOC_2'])),
      qa(3, '기다리는 질문', undefined, 'loading'),
    ];
    expect(recentPoliciesFromTurns(turns)).toEqual({
      question: '두 번째 질문', policies: [{ id: 'POL_2', name: '정책 2' }, { id: 'POL_3', name: '정책 3' }], documentIds: ['DOC_2'],
    });
  });

  it('예시 응답이면 가상 정책·출처는 빼고 질문만 남긴다', () => {
    expect(recentPoliciesFromTurns([qa(1, '예시 질문', answer([check('POL_SAMPLE_A', '예시 정책 A')], true, ['DOC_X']))]))
      .toEqual({ question: '예시 질문', policies: [], documentIds: [] });
    expect(recentPoliciesFromTurns([])).toBeNull();
  });
});
