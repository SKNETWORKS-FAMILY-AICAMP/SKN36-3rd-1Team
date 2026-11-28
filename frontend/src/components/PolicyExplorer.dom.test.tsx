// @vitest-environment jsdom
/**
 * 정책 둘러보기 목록을 실제 DOM으로 그려, 탭 복귀(포커스) 때 목록을 다시 불러와도
 * 펼친 카드가 닫히지 않는지 확인한다(#122, PR #124에서 재현한 문제).
 * 모든 정책 값은 sample_only 가상 데이터다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PolicyListResponse } from '../api/types';

const load = vi.hoisted(() => vi.fn());
vi.mock('../api/policyCatalog', () => ({ POLICY_API_URL: 'http://127.0.0.1:1/api/policies', loadPolicyCatalog: load }));

import { PolicyExplorer } from './PolicyExplorer';
import { SavedPoliciesProvider } from './SavedPolicies';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const item = (id: string) => ({
  policy_id: id, policy_name: `가상 정책 ${id} (sample_only)`, agency: '가상 기관', reception_status: 'unknown' as const,
  application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null,
  regions: ['전국'], categories: ['가상 분야'],
});
const page = (): PolicyListResponse => ({
  items: [item('POL_TEST_001'), item('POL_TEST_002')], total: 2, offset: 0, limit: 500,
  dataset_version: 'sample_only', manifest_sha256: 'a'.repeat(64), evaluated_on: '2026-11-25',
  source_kind: 'release_files', regions: ['전국'], categories: ['가상 분야'],
} as unknown as PolicyListResponse);

/** 결과를 원하는 때에 돌려줄 수 있는 요청 */
function deferred() {
  let resolve!: (value: PolicyListResponse) => void, reject!: (error: Error) => void;
  const promise = new Promise<PolicyListResponse>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

let host: HTMLDivElement, root: Root;
const cards = () => [...host.querySelectorAll<HTMLDetailsElement>('details.pcard')];
const returnToTab = () => act(() => { window.dispatchEvent(new Event('focus')); });

beforeEach(() => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
  load.mockReset();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function renderLoaded() {
  load.mockResolvedValueOnce(page());
  await act(async () => {
    root.render(<SavedPoliciesProvider><PolicyExplorer response={null} onAsk={() => {}} onDemoApply={() => {}} /></SavedPoliciesProvider>);
  });
  expect(cards()).toHaveLength(2);
}

it('처음 9개를 보여주고, 9개씩 추가한 뒤 검색이 바뀌면 표시 수를 초기화한다', async () => {
  const items = Array.from({ length: 20 }, (_, index) => item(`POL_TEST_${String(index).padStart(3, '0')}`));
  load.mockResolvedValueOnce({ ...page(), items, total: items.length });
  await act(async () => {
    root.render(<SavedPoliciesProvider><PolicyExplorer response={null} onAsk={() => {}} onDemoApply={() => {}} /></SavedPoliciesProvider>);
  });

  const more = () => host.querySelector<HTMLButtonElement>('.explorer-more');
  expect(cards()).toHaveLength(9);
  expect(more()?.textContent).toBe('정책 9개 더 보기');
  act(() => more()?.click());
  expect(cards()).toHaveLength(18);
  expect(more()?.textContent).toBe('정책 2개 더 보기');
  act(() => more()?.click());
  expect(cards()).toHaveLength(20);
  expect(more()).toBeNull();

  const search = host.querySelector<HTMLInputElement>('input[type="search"]')!;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(search, 'POL_TEST_01');
    search.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(cards()).toHaveLength(20);
  act(() => host.querySelector<HTMLButtonElement>('.explorer-submit')!.click());
  expect(cards()).toHaveLength(9);
  expect(more()?.textContent).toBe('정책 1개 더 보기');
});

it('선택한 지역은 검색 버튼을 누르면 적용하고 전국 정책은 전체 지역에서 볼 수 있다', async () => {
  const regional = { ...item('POL_TEST_002'), regions: ['가상 지역'] };
  load.mockResolvedValueOnce({ ...page(), items: [page().items[0], regional] } as PolicyListResponse);
  await act(async () => {
    root.render(<SavedPoliciesProvider><PolicyExplorer response={null} onAsk={() => {}} onDemoApply={() => {}} /></SavedPoliciesProvider>);
  });

  const region = host.querySelector<HTMLSelectElement>('.explorer-selects select')!;
  act(() => {
    region.value = '가상 지역';
    region.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(cards()).toHaveLength(2);
  act(() => host.querySelector<HTMLButtonElement>('.explorer-submit')!.click());
  expect(cards()).toHaveLength(1);
  expect(cards()[0].textContent).toContain('POL_TEST_002');
});

it('정렬과 접수 상태도 검색 버튼을 눌러야 적용하고, 필터 초기화는 바로 적용한다', async () => {
  const before = { ...item('POL_TEST_002'), policy_name: '하 가상 정책 (sample_only)', reception_status: 'before' as const };
  const unknown = { ...item('POL_TEST_001'), policy_name: '가 가상 정책 (sample_only)' };
  load.mockResolvedValueOnce({ ...page(), items: [unknown, before] } as PolicyListResponse);
  await act(async () => {
    root.render(<SavedPoliciesProvider><PolicyExplorer response={null} onAsk={() => {}} onDemoApply={() => {}} /></SavedPoliciesProvider>);
  });
  const names = () => cards().map(card => card.textContent ?? '');
  const submit = () => act(() => host.querySelector<HTMLButtonElement>('.explorer-submit')!.click());
  // 기본은 마감 임박순: 접수 예정 정책이 일정 미확인 정책보다 먼저 온다
  expect(names()[0]).toContain('하 가상 정책');

  const sort = host.querySelectorAll<HTMLSelectElement>('.explorer-selects select')[2];
  act(() => {
    sort.value = 'name';
    sort.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(names()[0]).toContain('하 가상 정책');
  submit();
  expect(names()[0]).toContain('가 가상 정책');

  const chip = [...host.querySelectorAll<HTMLButtonElement>('[aria-label="전체 정책 접수 상태"] button')].find(b => b.textContent === '접수 예정')!;
  act(() => chip.click());
  expect(chip.getAttribute('aria-pressed')).toBe('true');
  expect(cards()).toHaveLength(2);
  submit();
  expect(cards()).toHaveLength(1);
  expect(names()[0]).toContain('하 가상 정책');

  // 결과가 없을 때 나오는 '필터 초기화'는 검색 버튼 없이 바로 적용한다
  const open = [...host.querySelectorAll<HTMLButtonElement>('[aria-label="전체 정책 접수 상태"] button')].find(b => b.textContent === '접수 중')!;
  act(() => open.click());
  submit();
  expect(cards()).toHaveLength(0);
  const reset = [...host.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '필터 초기화')!;
  act(() => reset.click());
  expect(cards()).toHaveLength(2);
  // 초기화해도 고른 정렬(이름순)은 그대로 둔다
  expect(names()[0]).toContain('가 가상 정책');
});

describe('정책 목록 다시 불러오기 중 펼친 카드 (#122)', () => {
  it('탭으로 돌아와 목록을 다시 불러오는 동안과 끝난 뒤에도 같은 카드가 펼쳐져 있다', async () => {
    await renderLoaded();
    const opened = cards()[0];
    opened.open = true;

    const next = deferred();
    load.mockReturnValueOnce(next.promise);
    returnToTab();
    expect(load).toHaveBeenCalledTimes(2);
    // 다시 불러오는 동안: 카드를 지우지 않고, '불러오는 중' 안내로 바꾸지 않는다
    expect(cards()[0]).toBe(opened);
    expect(opened.open).toBe(true);
    expect(host.textContent).not.toContain('정책 목록을 불러오고 있어요');

    await act(async () => { next.resolve(page()); });
    expect(cards()[0]).toBe(opened);
    expect(opened.open).toBe(true);
  });

  it('다시 불러오기에 실패하면 이전 목록을 최신처럼 남기지 않고 오류를 보여 준다', async () => {
    await renderLoaded();
    const next = deferred();
    load.mockReturnValueOnce(next.promise);
    returnToTab();
    await act(async () => { next.reject(new Error('sample_only 실패')); });
    expect(cards()).toHaveLength(0);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('정책 목록을 불러오지 못했어요');
  });
});
