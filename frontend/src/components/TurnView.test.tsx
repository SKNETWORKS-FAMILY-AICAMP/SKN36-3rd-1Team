import { kstDay } from '../lib/applicationSchedule';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import type { AskResponse, ConditionResult } from '../api/types';
import { PolicyCard, summarizeConditions, TurnView } from './TurnView';
import { mockAsk } from '../mock/mockAsk';
import fromChecker from './__fixtures__/policyChecksFromChecker.json';

it('shows one prompt per missing field after the answer and keeps suggested questions below', () => {
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
    expect(html.indexOf(prompt)).toBeGreaterThan(html.indexOf(response.answer));
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
        application_schedule: { evaluated_on: kstDay(), start_date: null, end_date: null, is_ongoing: true, days_to_start: null, days_to_end: null },
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
  expect(html).toContain('일정 확인 필요</span>');
  expect(html).toContain('만 나이를 알려 주세요.');
  expect(html).toContain('신청 자격은 공식 공고에서 확인해 주세요.');
  expect(html).toContain('href="https://example.org/notice"');
  expect(html).toMatch(/<strong>.*현재 접수 중.*<a .*?>1<\/a>.*<\/strong>/);
  expect(html).toContain('<strong><span>접수 여부</span></strong>');
  expect(html).toContain('2) 짝 없는 **표시와 일반 *표시');
  expect(html).not.toMatch(/needs_check|reception_status|내부 구조|조건상 적합/);
});

it('shows each policy its own contacts and hides the section when there are none', () => {
  const response: AskResponse = {
    status: 'answered', answer: '본문', citations: [], follow_up_questions: [], checked_at: null, is_sample: false,
    policy_checks: [
      { policy_id: 'A', policy_name: '정책 A', agency: '기관 A', reception_status: 'open',
        source_url: 'https://example.org/a', official_apply_url: null,
        conditions: [{ field: 'age', label: '나이', status: 'eligible', reason: '충족' }],
        contacts: [{ agency: '가상 상담센터', phone: '02-000-0000' }, { agency: '가상 콜센터', phone: null }] },
      { policy_id: 'B', policy_name: '정책 B', agency: '기관 B', reception_status: 'open',
        source_url: 'https://example.org/b', official_apply_url: null,
        conditions: [{ field: 'age', label: '나이', status: 'eligible', reason: '충족' }],
        contacts: null },
    ],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
  const [cardA, cardB] = html.split('정책 B');
  expect(cardA).toContain('가상 상담센터 · 02-000-0000');
  expect(cardA).toContain('가상 콜센터');
  // 전화번호가 없는 기관은 번호 없이 기관명만 보여준다(가짜 구분자 없이).
  expect(cardA).not.toContain('가상 콜센터 ·');
  expect(cardB).not.toContain('가상 상담센터');
  expect(cardB).not.toContain('가상 콜센터');
  expect(cardB).not.toContain('문의처');
});

it('links each contact to its own source_url when present and safe', () => {
  const response: AskResponse = {
    status: 'answered', answer: '본문', citations: [], follow_up_questions: [], checked_at: null, is_sample: false,
    policy_checks: [
      { policy_id: 'A', policy_name: '정책 A', agency: '기관 A', reception_status: 'open',
        source_url: 'https://example.org/a', official_apply_url: null,
        conditions: [{ field: 'age', label: '나이', status: 'eligible', reason: '충족' }],
        contacts: [
          { agency: '가상 상담센터', phone: '02-000-0000', document_id: 'D1', chunk_id: 'C1', source_url: 'https://example.org/contact' },
          { agency: '가상 콜센터', phone: null, document_id: null, chunk_id: null, source_url: null },
        ] },
    ],
  };
  const noop = () => {};
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
  expect(html).toContain('href="https://example.org/contact"');
  // source_url이 없는 문의처는 링크 없이 기관명·번호만 보여준다.
  expect(html).not.toContain('가상 콜센터 </span><a');
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
      application_schedule: status === 'open' ? { evaluated_on: kstDay(), start_date: null, end_date: null, is_ongoing: true, days_to_start: null, days_to_end: null } : null,
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

function renderPolicy(key: keyof typeof fromChecker, followUps: AskResponse['follow_up_questions'] = [], overrides: Partial<AskResponse['policy_checks'][number]> = {}) {
  const response: AskResponse = {
    status: 'answered', answer: '가상 본문', citations: [], follow_up_questions: followUps,
    checked_at: null, is_sample: false, policy_checks: [{ ...fromChecker[key], ...overrides } as AskResponse['policy_checks'][number]],
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

it('warns only when the input actually falls into an exclusion (NOT) group', () => {
  const notice = '지원 제외 대상 조건에 해당해요';
  // 80세: 만 75세 이상 제외 그룹에 해당(Checker NOT 행 ineligible) → 상단 요약과 카드 모두 경고
  const over80 = renderPolicy('POL_005_80');
  expect(over80.overview).toContain(notice);
  expect(over80.card).toContain(notice);
  // 30세: 제외 그룹 결과가 확인 필요 → 경고 없음. 단순 AND·OR 정책에도 경고 없음
  for (const key of ['POL_005_30', 'POL_GOV_030_30', 'POL_GOV_002_70'] as const) {
    const { overview, card } = renderPolicy(key);
    expect(overview + card).not.toContain(notice);
  }
});

it('shows confirmed exclusion details once and keeps the warning for a null response', () => {
  const notice = '지원 제외 대상 조건에 해당해요';
  const reason = '가상 연령 제외 사유';
  const confirmed = renderPolicy('POL_005_80', [], {
    exclusion_reasons: [{ field: 'age', label: '연령 제외', reason }],
  });
  expect(confirmed.overview + confirmed.card).not.toContain(notice);
  expect(confirmed.card.match(new RegExp(reason, 'g'))).toHaveLength(1);
  const unconfirmed = renderPolicy('POL_005_80', [], { exclusion_reasons: null });
  expect(unconfirmed.card).toContain(notice);
  expect(unconfirmed.card).not.toContain(reason);
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

it.each(['ineligible', 'needs_check'] as const)('distinguishes an AND failure from UNKNOWN review (%s)', (status) => {
  const conditions: ConditionResult[] = [
    { field: 'age', label: '나이', status: status === 'ineligible' ? 'ineligible' : 'eligible', reason: '가상 나이 비교' },
    { field: 'eligibility', label: '자격 요건', status: 'needs_check', reason: 'UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.' },
    { field: 'eligibility', label: '자격 요건', status, reason: '모든 조건을 충족해야 합니다(종합 결과).' },
    { field: 'income', label: '소득', status: 'needs_check', reason: '소득 확인 필요' },
  ];
  const { card } = renderPolicy('POL_GOV_002_70', [], { conditions });
  expect(card).toContain('추가 확인 필요');
  expect(card).not.toContain('조건상 적합');
  expect(card.includes('확인된 필수 조건을 충족하지 않아요.')).toBe(status === 'ineligible');
  expect(card.includes('조건이 어떻게 결합되는지 확인되지 않았어요.')).toBe(status === 'needs_check');
  // 미충족이 확정되면 원인 항목(나이)을 요약 맨 앞과 상세 배지에 '조건 불일치'로 보여 준다.
  expect(card.includes('<span class="badge b-bad">조건 불일치</span><span>나이 — 확인된 필수 조건을 충족하지 않아요.</span>')).toBe(status === 'ineligible');
  if (status === 'ineligible') expect(card.indexOf('조건 불일치')).toBeLessThan(card.indexOf('추가 확인 필요'));
  expect(card.includes('개별 비교')).toBe(status === 'needs_check');
  const { confirmedFailure, hidesVerdict, badgeFor } = summarizeConditions(conditions);
  expect(confirmedFailure).toBe(status === 'ineligible');
  expect(hidesVerdict).toBe(status === 'needs_check');
  expect(badgeFor('eligible')[1]).toBe('개별 비교');
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

// 실제 /api/ask 응답(11/11)의 국민취업지원제도 policy_check. 데이터에 비교할 조건이 없는 UNKNOWN 정책이다.
const unjudgedPolicy: AskResponse['policy_checks'][number] = {
  policy_id: 'POL_010', policy_name: '고용노동부 국민취업지원제도 (Ⅰ·Ⅱ유형 및 청년특례)', agency: '고용노동부 국민취업지원기획팀',
  reception_status: 'open', source_url: 'https://www.gov.kr/portal/rcvfvrSvc/dtlEx/149200005007', official_apply_url: 'https://www.work24.go.kr',
  conditions: [{ field: 'eligibility', label: '자격 요건', status: 'needs_check', reason: 'UNKNOWN 조건 그룹은 자동 판정할 수 없습니다. 데이터 품질 경고로 자동 판정을 보류합니다.' }],
};
const holdNotice = { kind: 'warn', title: '추가 확인 필요', text: '자동 판정을 보류한 조건이 있습니다. 공식 공고를 확인해 주세요.' } as const;

function renderAnswer(response: AskResponse) {
  const noop = () => {};
  return renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={false} />);
}

it('labels policies with nothing to compare as not personally judged instead of repeating checks', () => {
  const cited = { title: '정부24', url: unjudgedPolicy.source_url, document_id: 'D', page_number: null };
  const html = renderAnswer({ status: 'answered', answer: '기준 설명 [1]', citations: [cited], follow_up_questions: [],
    checked_at: null, is_sample: false, notice: holdNotice, policy_checks: [unjudgedPolicy] });
  const card = html.split('관련 정책 1개')[1];
  expect(card).toContain('자동 판정 안 함');
  expect(card).toContain('이 상담에서는 신청 자격을 자동으로 확인하지 않았어요. 자세한 자격 요건은 공식 원문에서 확인해 주세요.');
  expect(card).not.toMatch(/위 답변|추가 확인 필요|데이터에 정리/);
  // 카드와 같은 내용인 공통 보류 안내는 생략한다. 출처 번호와 원문 링크는 그대로다.
  expect(html).not.toContain('자동 판정을 보류한 조건이 있습니다');
  expect(html).not.toContain('신청 자격은 추가 확인이 필요해요');
  expect(html).not.toMatch(/notice (warn|info)/);
  expect(html).toMatch(/<sup>.*<a href="https:\/\/www\.gov\.kr\/portal\/rcvfvrSvc\/dtlEx\/149200005007"/);
  expect(card).toContain('원문 보기');
});

it('keeps the info-status guide, which is about the answer rather than the card', () => {
  const html = renderAnswer({ status: 'info', answer: '안내 본문', citations: [], follow_up_questions: [], checked_at: null,
    is_sample: false, notice: holdNotice, policy_checks: [unjudgedPolicy] });
  expect(html).toContain('추가 확인이 필요해요');
  expect(html).not.toContain('자동 판정을 보류한 조건이 있습니다');
  expect(html).not.toContain('신청 자격은 추가 확인이 필요해요');
  expect(html).toContain('자동 판정 안 함');
});

it('keeps the hold warning and specific checks when another policy has items the user can resolve', () => {
  const html = renderAnswer({ status: 'answered', answer: '본문', citations: [], checked_at: null, is_sample: false, notice: holdNotice,
    follow_up_questions: [{ kind: 'ask_info', field: 'income', text: '연 소득을 알려 주세요.' }],
    policy_checks: [unjudgedPolicy, fromChecker.POL_005_30 as AskResponse['policy_checks'][number]] });
  // #78 합의: 종합 판정이 확인 필요인 정책이 있으면 화면은 합의 문구로 보여 준다.
  expect(html).toContain('신청 자격은 추가 확인이 필요해요');
  expect(html).not.toContain('자동 판정을 보류한 조건이 있습니다');
  expect(html).toContain('notice warn');
  expect(html).toContain('연 소득을 알려 주세요.');
  expect(html).toContain('외 10개 보기');
  expect(html).toContain('자동 판정 안 함');
});

it('keeps other warnings such as an unrecognized region even for unjudged policies', () => {
  const regionNotice = { kind: 'warn', title: '추가 확인 필요', text: '입력 지역을 확인하지 못했습니다. 시·도 이름을 확인해 주세요.' } as const;
  const html = renderAnswer({ status: 'answered', answer: '본문', citations: [], follow_up_questions: [], checked_at: null,
    is_sample: false, notice: regionNotice, policy_checks: [unjudgedPolicy] });
  expect(html).toContain('입력 지역을 확인하지 못했습니다');
  expect(html).toContain('notice warn');
});

it('shows "이 정책 물어보기" first in the card footer only in live mode, and hides the condition footnote on unjudged cards', () => {
  const noop = () => {};
  const live = (sampleMode: boolean, policies: AskResponse['policy_checks']) => renderToStaticMarkup(
    <TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response: {
      status: 'answered', answer: '본문', citations: [], follow_up_questions: [], checked_at: null, is_sample: sampleMode, policy_checks: policies } }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={sampleMode} onAskPolicy={noop} />,
  );
  const footer = (html: string) => html.slice(html.indexOf('class="pfoot"'));
  const unjudgedCard = footer(live(false, [unjudgedPolicy]));
  expect(unjudgedCard).toContain('이 정책 물어보기');
  expect(unjudgedCard.indexOf('이 정책 물어보기')).toBeLessThan(unjudgedCard.indexOf('관계 지도'));
  expect(unjudgedCard).not.toContain('조건 하나하나의 충족');
  // 비교한 조건이 있는 카드는 기존 안내를 유지한다.
  expect(footer(live(false, [fromChecker.POL_GOV_002_70 as AskResponse['policy_checks'][number]]))).toContain('조건 하나하나의 충족');
  // 예시 모드에서는 가상 정책이라 질문 버튼을 숨긴다.
  expect(live(true, [unjudgedPolicy])).not.toContain('이 정책 물어보기');
});

it('둘러보기 카드는 상태 미확인 문구를 한 번만 쓰고, 주 버튼과 보조 버튼 순서를 지킨다', () => {
  const p: AskResponse['policy_checks'][number] = {
    policy_id: 'sample_only_unknown', policy_name: '가상 정책 (sample_only)', agency: '가상 기관', reception_status: 'unknown',
    application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null, conditions: [],
  };
  const html = renderToStaticMarkup(<PolicyCard p={p} group="g" period="일정 확인 필요" stages={[]} sample={false}
    onDemoApply={() => {}} onAskPolicy={() => {}} askAsPrimary />);
  // 제목 옆 배지에만 '일정 확인 필요'가 있고, 신청 기간 행은 확인할 곳만 안내한다
  expect(html.match(/일정 확인 필요/g)).toHaveLength(1);
  expect(html).toContain('badge-quiet');
  expect(html).toContain('공식 원문에서 확인해 주세요.');
  // 둘러보기에서는 '내 조건으로 확인하기'가 주 버튼이고 '이 정책 물어보기'는 중복으로 두지 않는다
  expect(html).toContain('내 조건으로 확인하기');
  expect(html).not.toContain('이 정책 물어보기');
  expect(html.indexOf('관계 지도')).toBeLessThan(html.indexOf('원문 보기'));
});

it('답변 본문이 없는 info 응답에서도 빠진 정보 요청을 모두 보여 준다', () => {
  const response: AskResponse = {
    status: 'info', answer: '', citations: [], policy_checks: [], checked_at: null, is_sample: false,
    follow_up_questions: [
      { kind: 'ask_info', field: 'age', text: '만 나이를 알려 주세요.' },
      { kind: 'ask_info', field: 'region', text: '거주 지역을 알려 주세요.' },
      { kind: 'ask_info', field: 'income', text: '연 소득을 알려 주세요.' },
    ],
  };
  const html = renderToStaticMarkup(<TurnView turn={{ id: 1, kind: 'qa', question: '가상 질문', tags: [], state: 'done', response }}
    onFollowUp={() => {}} onRetry={() => {}} onDemoApply={() => {}} sampleMode={false} />);
  for (const prompt of ['만 나이를 알려 주세요.', '거주 지역을 알려 주세요.', '연 소득을 알려 주세요.']) expect(html).toContain(prompt);
});
