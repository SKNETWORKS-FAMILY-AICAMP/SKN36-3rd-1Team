import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { AskResponse, ConditionResult } from '../api/types';
import { TurnView } from './TurnView';

it('shows one prompt per missing field before the answer and keeps suggested questions below', () => {
  const age = { kind: 'ask_info', field: 'age', text: '만 나이를 알려 주세요.' } as const;
  const response: AskResponse = {
    status: 'answered', answer: '가상 본문', citations: [], policy_checks: [], checked_at: null, is_sample: false,
    follow_up_questions: [
      age, { ...age },
      { kind: 'ask_info', field: 'region', text: '거주 지역을 알려 주세요.' },
      { kind: 'ask_info', field: 'income', text: '연 소득을 알려 주세요.' },
      { kind: 'question', text: '신청 방법은 무엇인가요?' },
    ],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(
    <TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />,
  );
  expect(html.match(/만 나이를 알려 주세요\./g)).toHaveLength(1);
  for (const prompt of [age.text, '거주 지역을 알려 주세요.', '연 소득을 알려 주세요.']) {
    expect(html).toContain(prompt);
    expect(html.indexOf(prompt)).toBeLessThan(html.indexOf(response.answer));
  }
  expect(html.indexOf('신청 방법은 무엇인가요?')).toBeGreaterThan(html.indexOf(response.answer));
});

it('keeps reception and eligibility separate for each policy', () => {
  const response: AskResponse = {
    status: 'answered', answer: '1) **현재 접수 중 [1]**인 정책과 **접수 여부**를 확인할 정책이 있어요.\n2) 짝 없는 **표시와 일반 *표시 [1]',
    citations: [{ title: '공식 공고', url: 'https://example.org/notice', document_id: 'D1', page_number: null }],
    follow_up_questions: [], checked_at: null, is_sample: false,
    policy_checks: [
      { policy_id: 'A', policy_name: '정책 A', agency: '기관', reception_status: 'open',
        source_url: 'https://example.org/notice', official_apply_url: null,
        conditions: [{ field: 'age', label: '나이', status: 'needs_check', reason: '사용자의 만 나이가 입력되지 않았습니다.' }] },
      { policy_id: 'B', policy_name: '정책 B', agency: '기관', reception_status: 'unknown',
        source_url: 'https://example.org/other', official_apply_url: null,
        conditions: [{ field: 'eligibility', label: '자격', status: 'needs_check', reason: '조건 그룹의 내부 구조가 올바르지 않습니다.' }] },
    ],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
  expect(html).toContain('접수 중</span>');
  expect(html).toContain('모집 상태 미확인</span>');
  expect(html).toContain('만 나이를 알려 주세요.');
  expect(html).toContain('신청 자격은 공식 공고에서 확인해 주세요.');
  expect(html).toContain('href="https://example.org/notice"');
  expect(html).toMatch(/<strong>.*현재 접수 중.*<a .*?>1<\/a>.*<\/strong>/);
  expect(html).toContain('<strong><span>접수 여부</span></strong>');
  expect(html).toContain('2) 짝 없는 **표시와 일반 *표시');
  expect(html).not.toMatch(/needs_check|reception_status|내부 구조|조건상 적합/);
});

it('shows retry in demo mode without instructions for triggering an error', () => {
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'error' }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode />);
  expect(html).toContain('다시 시도');
  expect(html).not.toContain('질문에 “오류”를 넣으면');
});

it.each([
  ['closed', false], ['before', false], ['open', true],
] as const)('shows the application link according to reception status %s', (status, canApply) => {
  const response: AskResponse = {
    status: 'answered', answer: '가상 답변', citations: [], follow_up_questions: [],
    checked_at: null, notice: null, is_sample: false,
    policy_checks: [{
      policy_id: 'POL_SAMPLE', policy_name: '가상 정책', agency: '가상 기관',
      reception_status: status, source_url: 'https://example.org/notice',
      official_apply_url: 'https://example.org/apply', conditions: [],
    }],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(
    <TurnView
      turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false}
    />,
  );
  expect(html).toContain('href="https://example.org/notice"');
  expect(html.includes('href="https://example.org/apply"')).toBe(canApply);
});

it('groups identical source links without renumbering inline citations', () => {
  const first = { title: '가상 원문', url: 'https://example.org/notice', document_id: 'D1', page_number: 2 };
  const response: AskResponse = {
    status: 'answered', answer: '가상 답변 [1] [2] [3]',
    citations: [
      first,
      { ...first, title: '다른 원문', url: 'https://example.org/other', document_id: 'D2' },
      { ...first, page_number: 4 },
    ],
    follow_up_questions: [], policy_checks: [], checked_at: null, notice: null, is_sample: true,
  };
  const noop = () => {};
  const html = renderToStaticMarkup(
    <TurnView
      turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode
    />,
  );
  expect(html.match(/class="src"/g)).toHaveLength(2);
  expect(html).not.toContain('class="n"');
  expect(html).toContain('출처 3 원문 보기');
  expect(html).toContain('example.org · p.2 · example.org · p.4');
  expect(html.match(/href="https:\/\/example\.org\/notice"/g)).toHaveLength(3);
});

it('shows identical income notices once per policy without dropping distinct conditions', () => {
  const income: ConditionResult = {
    field: 'income', label: '소득', status: 'needs_check', reason: 'sample_only_income',
  };
  const conditions: ConditionResult[] = [
    income, { ...income },
    { ...income, reason: 'sample_only_other' },
    { ...income, status: 'eligible' },
  ];
  const response: AskResponse = {
    status: 'answered', answer: '가상 답변', citations: [], follow_up_questions: [],
    checked_at: null, notice: null, is_sample: true,
    policy_checks: [{
      policy_id: 'POL_SAMPLE', policy_name: '가상 정책', agency: '가상 기관',
      reception_status: 'unknown', source_url: 'https://example.org', official_apply_url: null, conditions,
    }],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(
    <TurnView
      turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode
    />,
  );
  const [overview, cards] = html.split('관련 정책 1개');
  expect(overview).toContain('정책별로 확인할 조건');
  expect(overview.match(/sample_only_income/g)).toHaveLength(1);
  expect(cards.match(/sample_only_income/g)).toHaveLength(2);
  expect(html.match(/sample_only_other/g)).toHaveLength(1);
  expect(conditions).toHaveLength(4);
});
