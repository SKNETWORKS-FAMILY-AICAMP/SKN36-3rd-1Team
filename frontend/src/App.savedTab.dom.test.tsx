// @vitest-environment jsdom
/**
 * 상단 '관심 정책' 탭(#/saved).
 * - 탭에 이 브라우저에 저장한 관심 정책 수를 보여 준다.
 * - 탭을 누르면 첫 화면은 그대로 두고, 정책 목록을 관심 정책 보기로 바꿔 그 위치로 보낸다(맨 위로 올리지 않는다).
 * - 목록의 '전체 정책' 칩을 누르면 주소도 첫 화면(#/)으로 돌아온다.
 * 저장 값과 정책은 sample_only 가상 값이다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', () => ({ askPolicy: vi.fn(), usingSample: false }));
vi.mock('./api/policyCatalog', () => ({ POLICY_API_URL: 'http://127.0.0.1:1/api/policies', loadPolicyCatalog: vi.fn(() => new Promise(() => {})) }));

import App from './App';
import { SAVED_POLICIES_KEY, SavedPoliciesProvider } from './components/SavedPolicies';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SAVED = [
  { policy_id: 'POL_TEST_001', policy_name: '가상 정책 1 (sample_only)', source_url: 'https://example.org/1' },
  { policy_id: 'POL_TEST_002', policy_name: '가상 정책 2 (sample_only)', source_url: 'https://example.org/2' },
];

let host: HTMLDivElement, root: Root;

/** 주소를 바꾸고 hashchange가 처리될 때까지 기다린다 */
async function go(hash: string) {
  await act(async () => {
    location.hash = hash;
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const savedTab = () => [...host.querySelectorAll<HTMLAnchorElement>('nav.tabs a')].find((a) => a.getAttribute('href') === '#/saved')!;
const chip = (text: string) => [...host.querySelectorAll<HTMLButtonElement>('[aria-label="정책 목록 범위"] button')].find((b) => b.textContent?.startsWith(text))!;

beforeEach(async () => {
  window.matchMedia ??= ((query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.IntersectionObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof IntersectionObserver;
  localStorage.setItem(SAVED_POLICIES_KEY, JSON.stringify(SAVED));
  await go('#/');
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<SavedPoliciesProvider><App /></SavedPoliciesProvider>));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  localStorage.clear();
});

describe('상단 관심 정책 탭', () => {
  it('저장한 관심 정책 수를 탭에 보여 준다', () => {
    expect(savedTab().textContent?.replace(/\s+/g, ' ')).toBe('★ 관심 정책 2');
    expect(savedTab().getAttribute('aria-current')).toBeNull();
  });

  it('탭을 누르면 첫 화면을 유지한 채 목록을 관심 정책 보기로 바꾸고 그 위치로 보낸다', async () => {
    vi.mocked(window.scrollTo).mockClear();
    await go('#/saved');
    expect(savedTab().getAttribute('aria-current')).toBe('page');
    expect(host.querySelector('#policy-explorer')?.closest('[hidden]')).toBeNull();
    expect(chip('★ 관심 정책').getAttribute('aria-pressed')).toBe('true');
    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("목록의 '전체 정책' 칩을 누르면 주소도 첫 화면으로 돌아온다", async () => {
    await go('#/saved');
    await act(async () => {
      chip('전체 정책').click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(location.hash).toBe('#/');
    expect(chip('전체 정책').getAttribute('aria-pressed')).toBe('true');
    expect(savedTab().getAttribute('aria-current')).toBeNull();
  });
});
