// @vitest-environment jsdom
/**
 * 목록 API 없이(로컬 그래프 목록) 상담 응답의 일정으로 목록 행을 갱신할 때,
 * 같은 응답의 마감순 순위(deadline_rank, 계약 v1.13)도 함께 반영되는지 확인한다(#165).
 * 모든 정책 값은 sample_only 가상 데이터다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AskResponse, PolicyCheck } from '../api/types';
import { kstDay } from '../lib/applicationSchedule';

vi.mock('../api/policyCatalog', () => ({ POLICY_API_URL: '', loadPolicyCatalog: vi.fn() }));
vi.mock('../graph/loadGraph', () => ({ loadGraphDataset: async () => ({ index: {}, version: 'sample_only' }) }));
const row = (id: string) => ({
  policy: { policy_id: id, policy_name: `가상 정책 ${id}`, agency: 'sample_only', source_url: 'https://example.org/notice',
    official_apply_url: null, reception_status: 'unknown' as const, application_schedule: null, conditions: [] },
  regions: ['전국'], categories: ['가상 분야'],
});
vi.mock('../lib/policyCatalog', () => ({ catalogPolicies: () => [row('A'), row('B'), row('C')] }));

import { PolicyExplorer } from './PolicyExplorer';
import { SavedPoliciesProvider } from './SavedPolicies';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement, root: Root;
beforeEach(() => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

it('상담으로 마감일이 확인된 정책은 마감 임박순 맨 앞에 온다(서버 deadline_rank 반영)', async () => {
  const today = kstDay();
  // 이름순이면 맨 뒤인 C가 상담으로 '마감일 있는 접수 중'(서버 순위 0)이 확인됐다.
  const consulted: PolicyCheck = {
    ...row('C').policy, reception_status: 'open', deadline_rank: 0,
    application_schedule: { evaluated_on: today, start_date: null, end_date: dayAfter(today, 3), is_ongoing: false,
      days_to_start: null, days_to_end: 3, closing_soon: true },
  };
  const response: AskResponse = { status: 'answered', answer: 'sample_only [1]', citations: [], policy_checks: [consulted],
    follow_up_questions: [], checked_at: null, is_sample: false, notice: null };
  await act(async () => {
    root.render(<SavedPoliciesProvider><PolicyExplorer response={response} onAsk={() => {}} onDemoApply={() => {}} /></SavedPoliciesProvider>);
  });
  const names = [...host.querySelectorAll('details.pcard')].map(card => card.textContent ?? '');
  expect(names).toHaveLength(3);
  expect(names[0]).toContain('가상 정책 C');
  expect(names[0]).toContain('마감 D-3');
});

function dayAfter(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
