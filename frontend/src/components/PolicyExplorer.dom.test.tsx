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
