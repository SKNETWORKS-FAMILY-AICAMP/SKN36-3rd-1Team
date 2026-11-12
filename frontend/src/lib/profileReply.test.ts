import { describe, expect, it } from 'vitest';
import type { UserProfile } from '../api/types';
import { planProfileReply } from './profileReply';

const empty: UserProfile = { age: null, region: '', income: null };
const notPending = () => false;
const QUESTION = '구직 중인 청년인데 받을 수 있는 취업 지원이 있나요?';

describe('planProfileReply', () => {
  it('앞 질문이 없으면 조건만 넣는다', () => {
    expect(planProfileReply({ age: 26 }, null, empty, notPending)).toEqual({ kind: 'store' });
  });

  it('앞 질문의 답변을 기다리는 중이면 아무것도 바꾸지 않는다', () => {
    expect(planProfileReply({ age: 26 }, QUESTION, empty, (q) => q === QUESTION)).toEqual({ kind: 'busy' });
  });

  it('앞 질문을 바뀐 조건으로 다시 보낸다', () => {
    expect(planProfileReply({ age: 26 }, QUESTION, { ...empty, region: '서울' }, notPending)).toEqual({
      kind: 'resend', question: QUESTION, profile: { age: 26, region: '서울', income: null }, field: 'age',
    });
  });

  it('읽은 항목이 여러 개면 field는 null이다 (조건 입력칸과 같은 규칙)', () => {
    const plan = planProfileReply({ age: 26, region: '경기' }, QUESTION, empty, notPending);
    expect(plan).toEqual({ kind: 'resend', question: QUESTION, profile: { age: 26, region: '경기', income: null }, field: null });
  });
});
