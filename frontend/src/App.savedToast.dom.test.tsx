// @vitest-environment jsdom
/**
 * 관심 정책 저장 알림.
 * - 정책을 저장하면 '관심 정책에 저장했어요' 알림과 '관심 정책 보기' 버튼을 보여 준다.
 * - 버튼을 누르면 아래 정책 목록을 관심 정책 보기로 바꾸고 그 위치로 보낸다.
 * - 저장 해제에는 알림을 띄우지 않는다.
 * 정책 목록 응답은 sample_only 가상 값이다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PolicyListResponse } from './api/types';

const PAGE = {
  items: [{
    policy_id: 'POL_TEST_001', policy_name: '가상 정책 POL_TEST_001 (sample_only)', agency: '가상 기관', reception_status: 'unknown',
    application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null, regions: ['전국'], categories: ['가상 분야'],
  }],
  total: 1, offset: 0, limit: 500, dataset_version: 'sample_only', manifest_sha256: 'a'.repeat(64), evaluated_on: '2026-11-25',
  source_kind: 'release_files', regions: ['전국'], categories: ['가상 분야'],
} as unknown as PolicyListResponse;

vi.mock('./api/client', () => ({ askPolicy: vi.fn(), usingSample: false }));
vi.mock('./api/policyCatalog', () => ({ POLICY_API_URL: 'http://127.0.0.1:1/api/policies', loadPolicyCatalog: vi.fn(async () => PAGE) }));

import App from './App';
import { SavedPoliciesProvider } from './components/SavedPolicies';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement, root: Root;

const toast = () => host.querySelector<HTMLElement>('.toast')!;
const saveButton = () => [...host.querySelectorAll<HTMLButtonElement>('#policy-explorer button')].find((b) => /관심 정책 저장|저장 해제/.test(b.textContent ?? ''))!;
const scopeChip = (text: string) => [...host.querySelectorAll<HTMLButtonElement>('[aria-label="정책 목록 범위"] button')].find((b) => b.textContent?.startsWith(text))!;

beforeEach(async () => {
  window.matchMedia ??= ((query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.IntersectionObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof IntersectionObserver;
  localStorage.clear();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<SavedPoliciesProvider><App /></SavedPoliciesProvider>));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  localStorage.clear();
});

describe('관심 정책 저장 알림', () => {
  it("저장하면 '관심 정책 보기' 버튼이 있는 알림을 보여 준다", () => {
    expect(toast().textContent).toBe('');
    act(() => saveButton().click());
    expect(toast().classList.contains('on')).toBe(true);
    expect(toast().textContent).toContain('관심 정책에 저장했어요');
    expect(toast().querySelector('button')?.textContent).toBe('관심 정책 보기');
  });

  it("'관심 정책 보기'를 누르면 목록을 관심 정책 보기로 바꾸고 그 위치로 보낸다", () => {
    act(() => saveButton().click());
    expect(scopeChip('전체 정책').getAttribute('aria-pressed')).toBe('true');
    vi.mocked(Element.prototype.scrollIntoView).mockClear();
    act(() => toast().querySelector('button')!.click());
    expect(scopeChip('★ 관심 정책').getAttribute('aria-pressed')).toBe('true');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(toast().classList.contains('on')).toBe(false);
  });

  it('저장 해제에는 알림을 띄우지 않는다', () => {
    act(() => saveButton().click());
    act(() => toast().querySelector('button')!.click());
    expect(toast().classList.contains('on')).toBe(false);
    act(() => saveButton().click());
    expect(toast().classList.contains('on')).toBe(false);
  });
});
