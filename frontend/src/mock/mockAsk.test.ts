import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AskRequest, UserProfile } from '../api/types';
import { EXAMPLES, mockAsk } from './mockAsk';

const empty: UserProfile = { age: null, region: '', income: null };
const work = EXAMPLES.find((e) => e.key === 'work')!;

/** 예시 응답은 1초 기다린 뒤 돌아오므로 가짜 시계로 바로 진행한다. */
async function ask(req: Partial<AskRequest> & { question: string }) {
  const p = mockAsk({ profile: empty, history: [], ...req });
  await vi.advanceTimersByTimeAsync(1500);
  return p;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('mockAsk 예시 응답', () => {
  it('조건이 없으면 나이·지역·소득 모두 추가 확인 필요', async () => {
    const r = await ask({ question: work.question });
    expect(r.is_sample).toBe(true);
    expect(r.policy_checks[0].conditions.map((c) => c.status)).toEqual(['needs_check', 'needs_check', 'needs_check']);
    expect(r.checked_at).not.toBeNull();
  });

  it('나이 경계값: 19·34세는 적합, 18·35세는 불일치', async () => {
    for (const [age, status] of [[18, 'ineligible'], [19, 'eligible'], [34, 'eligible'], [35, 'ineligible']] as const) {
      const r = await ask({ question: work.question, profile: { ...empty, age } });
      expect(r.policy_checks[0].conditions[0].status).toBe(status);
    }
  });

  it('소득은 알려 줘도 자동 판정하지 않는다 (PRD: 복잡한 소득 조건)', async () => {
    const r = await ask({ question: work.question, profile: { age: 26, region: '서울', income: 4000 } });
    const income = r.policy_checks[0].conditions.find((c) => c.field === 'income')!;
    expect(income.status).toBe('needs_check');
    expect(income.reason).toContain('4,000만 원');
  });

  it('앞 대화를 이어서 조건을 다시 확인한다', async () => {
    const r = await ask({
      question: '26살, 서울, 소득은 4000만원이야',
      profile: { age: 26, region: '서울', income: 4000 },
      history: [{ role: 'user', text: work.question }],
    });
    expect(r.status).toBe('answered');
    expect(r.answer).toContain('다시 확인');
    expect(r.follow_up_questions.some((f) => f.field === 'income')).toBe(false);
  });

  it('정보 요청(ask_info) 버튼은 계약대로 나이·지역·소득만 쓴다', async () => {
    for (const ex of EXAMPLES) {
      const r = await ask({ question: ex.question });
      for (const f of r.follow_up_questions) {
        if (f.kind === 'ask_info') expect(['age', 'region', 'income']).toContain(f.field);
      }
    }
  });

  it('되물은 소득에 단위만 적어 답해도 소득으로 읽는다', async () => {
    const r = await ask({
      question: '4000만 원이요', awaiting_field: 'income',
      history: [{ role: 'user', text: work.question }],
    });
    expect(r.answer).toContain('연 4,000만 원');
  });

  it('예시 답변의 추가 질문 버튼은 확정 답 없이 안내(info)만 한다', async () => {
    const housing = EXAMPLES.find((e) => e.key === 'housing')!;
    const r = await ask({ question: '혼인 기간 조건도 알려줘', history: [{ role: 'user', text: housing.question }] });
    expect(r.status).toBe('info');
    expect(r.citations).toHaveLength(0);
  });

  it('예시에 없는 질문은 근거 없음 (실제 백엔드처럼 notice 없이)', async () => {
    const r = await ask({ question: '아무거나' });
    expect(r.status).toBe('no_evidence');
    expect(r.citations).toHaveLength(0);
    expect(r.notice).toBeNull();
  });

  it("'오류'가 든 질문은 처음 한 번 실패한다", async () => {
    const first = mockAsk({ question: '오류 테스트 1', profile: empty, history: [] });
    const caught = first.catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(1500);
    expect(await caught).toBeInstanceOf(Error);
    const second = await ask({ question: '오류 테스트 1' });
    expect(second.status).toBe('no_evidence');
  });

  it('신청 주소가 없는 예시는 안내만 하고 정책 카드를 만들지 않는다', async () => {
    const r = await ask({ question: EXAMPLES.find((e) => e.key === 'housing')!.question });
    expect(r.policy_checks).toHaveLength(0);
    expect(r.notice?.title).toContain('신청 주소');
  });

  it('추가 질문은 구조화된 값으로 온다: 정보 요청은 field가 있다', async () => {
    const r = await ask({ question: work.question });
    const info = r.follow_up_questions.filter((f) => f.kind === 'ask_info');
    expect(info.map((f) => f.field)).toEqual(['region', 'income']);
    expect(r.follow_up_questions.find((f) => f.kind === 'question')?.text).toContain('서류');
  });
});
