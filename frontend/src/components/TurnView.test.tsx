import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import type { AskResponse, ConditionResult } from '../api/types';
import { TurnView } from './TurnView';
import { mockAsk } from '../mock/mockAsk';
import fromChecker from './__fixtures__/policyChecksFromChecker.json';

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
  // 상단 요약은 같은 항목의 서로 다른 사유를 모두 한 줄에 보여 주고, 카드 상세도 그대로 유지한다.
  expect(overview.match(/sample_only_other/g)).toHaveLength(1);
  expect(cards.match(/sample_only_other/g)).toHaveLength(1);
  expect(conditions).toHaveLength(4);
});

// Release v1.4.0-candidate의 실제 조건 트리를 실제 Checker·Pipeline(run_ask_pipeline)에 통과시킨 policy_checks.
// 검색 결과와 LLM 답변만 대역이다. 국민내일배움카드(POL_005)의 만 75세 이상은 '제외(NOT) → OR' 그룹 아래에 있다.

function renderPolicy(key: keyof typeof fromChecker, followUps: AskResponse['follow_up_questions'] = []) {
  const response: AskResponse = {
    status: 'answered', answer: '가상 본문', citations: [], follow_up_questions: followUps,
    checked_at: null, is_sample: false, policy_checks: [fromChecker[key] as AskResponse['policy_checks'][number]],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
  const [overview, card] = html.split('관련 정책 1개');
  return { overview, card };
}

it.each(['POL_005_30', 'POL_005_80'] as const)('does not read exclusion-group age results as eligibility (%s)', (key) => {
  const { overview, card } = renderPolicy(key);
  expect(card).not.toMatch(/조건상 적합|조건 불일치/);
  expect(card).toContain('개별 비교');
  expect(card).toContain('조건이 어떻게 결합되는지 확인되지 않았어요.');
  expect(card).not.toMatch(/하나만 충족|제외 조건이 함께/);
  expect(overview).not.toMatch(/일치하지 않아요|조건상 적합|만 75세/);
  expect(overview + card).not.toContain('(종합 결과)');
  // '기타 자격 조건' 12개 중 2개만 바로 보이고 나머지는 접는다.
  expect(overview).toContain('외 10개 보기');
});

it('hides verdict badges when an OR group may contain the compared condition', () => {
  const { overview, card } = renderPolicy('POL_GOV_030_30');
  expect(card).not.toMatch(/조건상 적합|조건 불일치/);
  expect(card).toContain('만 9~24세 기준에 입력한 만 30세가 포함되지 않습니다.');
  expect(overview.match(/개인\/가구 소득 및 중위소득 비교 기준을 확인해야 합니다\./g)).toHaveLength(1);
});

it('keeps verdict badges for policies whose groups are all AND', () => {
  const { card } = renderPolicy('POL_GOV_002_70');
  expect(card).toContain('조건 불일치');
  expect(card).not.toContain('개별 비교');
  expect(card).not.toContain('(종합 결과)');
});

it('does not repeat a missing-age request that the input button already asks for', () => {
  const age = { kind: 'ask_info', field: 'age', text: '만 나이를 알려 주세요.' } as const;
  const policy = (id: string): AskResponse['policy_checks'][number] => ({
    policy_id: id, policy_name: `가상 정책 ${id}`, agency: '', reception_status: 'unknown', source_url: 'https://example.org',
    official_apply_url: null, conditions: [{ field: 'age', label: '나이', status: 'needs_check', reason: '사용자의 만 나이가 입력되지 않았습니다.' }],
  });
  const response: AskResponse = {
    status: 'answered', answer: '가상 본문', citations: [], follow_up_questions: [age], checked_at: null, is_sample: false,
    policy_checks: [policy('A'), policy('B')],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
  const [overview] = html.split('관련 정책 2개');
  expect(overview.match(/만 나이를 알려 주세요\./g)).toHaveLength(1);
  expect(overview).not.toContain('정책별로 확인할 조건');
});

it('restores the region badge in sample mode without confirming overall eligibility', async () => {
  vi.useFakeTimers();
  try {
    const pending = mockAsk({ question: '구직 중인 청년인데 받을 수 있는 취업 지원이 있나요?', profile: { age: null, region: '서울', income: null }, history: [] });
    await vi.advanceTimersByTimeAsync(1500);
    const response = await pending;
    const noop = () => {};
    const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode />);
    const card = html.split('관련 정책 1개')[1];
    expect(card).toMatch(/조건상 적합<\/span><span>지역/);
    expect(card).toMatch(/추가 확인 필요<\/span><span>나이 · 소득/);
    expect(card).not.toMatch(/개별 비교|결합되는지 확인되지|\(종합 결과\)/);
  } finally {
    vi.useRealTimers();
  }
});
