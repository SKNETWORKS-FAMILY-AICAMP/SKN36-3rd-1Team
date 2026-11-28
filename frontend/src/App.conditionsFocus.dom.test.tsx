// @vitest-environment jsdom
/**
 * 하단 바 '내 조건' 버튼과 상담 화면 오류 격리를 실제 App 화면에서 확인한다.
 * - 조건 패널이 닫혀 있어도 버튼을 누르면 패널을 열고 나이 입력칸에 포커스를 준다.
 * - 답변 한 건을 그리다 오류가 나도 앱 전체가 비지 않고, 그 답변 자리에만 안내가 나온다.
 * 서버 호출은 가짜 함수로 바꾸고, 응답은 sample_only 가상 값이다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AskResponse } from './api/types';

const askPolicy = vi.hoisted(() => vi.fn());
vi.mock('./api/client', () => ({ askPolicy, usingSample: false }));
vi.mock('./api/policyCatalog', () => ({ POLICY_API_URL: 'http://127.0.0.1:1/api/policies', loadPolicyCatalog: vi.fn(() => new Promise(() => {})) }));
const turnViewFails = vi.hoisted(() => ({ on: false }));
vi.mock('./components/TurnView', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components/TurnView')>();
  return {
    ...actual,
    TurnView: (props: Parameters<typeof actual.TurnView>[0]) => {
      if (turnViewFails.on) throw new Error('sample_only 렌더 오류');
      return actual.TurnView(props);
    },
  };
});

import App from './App';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SAMPLE: AskResponse = {
  answer: 'sample_only 가상 답변입니다.', citations: [], status: 'answered', policy_checks: [], follow_up_questions: [],
} as unknown as AskResponse;

let host: HTMLDivElement, root: Root;

beforeEach(() => {
  window.matchMedia ??= ((query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.IntersectionObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof IntersectionObserver;
  vi.spyOn(console, 'error').mockImplementation(() => {});
  turnViewFails.on = false;
  askPolicy.mockReset();
  askPolicy.mockResolvedValue(SAMPLE);
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<App />));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('하단 바 내 조건 버튼', () => {
  it('닫힌 조건 패널을 열고 나이 입력칸에 포커스를 준다', () => {
    vi.useFakeTimers();
    const panel = host.querySelector<HTMLElement>('#profile-conditions')!;
    expect(panel.hidden).toBe(true);
    act(() => host.querySelector<HTMLButtonElement>('.mini-cond')!.click());
    expect(panel.hidden).toBe(false);
    act(() => { vi.runAllTimers(); });
    expect(document.activeElement).toBe(host.querySelector('#age'));
  });
});

describe('상담 화면 오류 격리', () => {
  it('답변 한 건을 그리지 못해도 질문 입력과 화면 머리는 남는다', async () => {
    turnViewFails.on = true;
    const input = host.querySelector<HTMLInputElement>('.ask input')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    act(() => { setter.call(input, 'sample_only 질문'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => { input.form!.requestSubmit(); });
    await act(async () => {});
    expect(host.textContent).toContain('이 답변을 보여 드리지 못했어요');
    expect(host.querySelector('.gnb')).not.toBeNull();
    expect(host.querySelector('.ask input')).not.toBeNull();
  });
});
