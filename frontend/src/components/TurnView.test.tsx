import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { AskResponse, ConditionResult } from '../api/types';
import { TurnView } from './TurnView';

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
  expect(html).toContain('>1, 3</span>');
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
  expect(html.match(/sample_only_income/g)).toHaveLength(2);
  expect(html.match(/sample_only_other/g)).toHaveLength(1);
  expect(conditions).toHaveLength(4);
});
