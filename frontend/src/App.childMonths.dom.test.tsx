// @vitest-environment jsdom
/**
 * #39 자녀 월령 입력(ask_contract v1.8)을 실제 App 화면에서 확인한다.
 * - 입력한 월령이 profile.child_age_months로 그대로 간다(0개월 포함).
 * - 빈칸·잘못된 값은 null로 보내고 입력칸에 안내한다.
 * - 신청자 나이(age)와 자녀 월령은 서로 영향을 주지 않는다.
 * - 분야 카드로 질문해도 접어 둔 조건을 그대로 보낸다.
 * 서버 호출은 가짜 함수로 바꾸고, 응답은 sample_only 가상 값이다.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AskRequest, AskResponse } from './api/types';

const askPolicy = vi.hoisted(() => vi.fn());
vi.mock('./api/client', () => ({ askPolicy, usingSample: false }));
vi.mock('./api/policyCatalog', () => ({ POLICY_API_URL: 'http://127.0.0.1:1/api/policies', loadPolicyCatalog: vi.fn(() => new Promise(() => {})) }));

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
});

/** React가 받는 방식으로 입력칸 값을 바꾼다 */
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => { setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); });
}

const childInput = () => host.querySelector<HTMLInputElement>('#child-age-months')!;
const ageInput = () => host.querySelector<HTMLInputElement>('#age')!;
const conditionsToggle = () => host.querySelector<HTMLButtonElement>('.conditions-toggle')!;
const toggleConditions = () => act(() => conditionsToggle().click());

/** 큰 검색창으로 질문을 보내고, 서버로 간 요청을 돌려준다 */
async function askAndCapture(question: string): Promise<AskRequest> {
  const before = askPolicy.mock.calls.length;
  type(host.querySelector<HTMLInputElement>('input[aria-label="정책 질문"]')!, question);
  await act(async () => { host.querySelector<HTMLFormElement>('.ask form')!.requestSubmit(); });
  expect(askPolicy.mock.calls.length).toBe(before + 1);
  return askPolicy.mock.lastCall![0] as AskRequest;
}

describe('자녀 월령 입력 (#39)', () => {
  it('입력칸이 있고, 자녀 한 명 기준 안내를 함께 보여 준다', () => {
    const panel = host.querySelector<HTMLElement>('#profile-conditions')!;
    expect(panel.hidden).toBe(true);
    expect(conditionsToggle().getAttribute('aria-expanded')).toBe('false');
    expect(conditionsToggle().getAttribute('aria-controls')).toBe(panel.id);
    toggleConditions();
    expect(panel.hidden).toBe(false);
    expect(conditionsToggle().getAttribute('aria-expanded')).toBe('true');
    expect(askPolicy).not.toHaveBeenCalled();
    expect(childInput()).not.toBeNull();
    expect(host.querySelector('#child-age-months-hint')?.textContent).toContain('자녀 한 명 기준');
  });

  it('빈칸이면 null로 보낸다', async () => {
    const request = await askAndCapture('자녀 관련 지원이 있나요?');
    expect(request.profile.child_age_months).toBeNull();
  });

  it.each([0, 10, 24, 25])('%i개월은 그대로 보낸다(0개월도 빠지지 않는다)', async (months) => {
    type(childInput(), String(months));
    const request = await askAndCapture('자녀 관련 지원이 있나요?');
    expect(request.profile.child_age_months).toBe(months);
    expect(host.querySelector('.qtags')?.textContent).toContain(`자녀 ${months}개월`);
  });

  it.each(['24.5', '-1', 'abc', '24개월', '1e2'])('잘못된 값 %s은 보내지 않고 안내한다', async (bad) => {
    type(childInput(), bad);
    expect(host.querySelector('#child-age-months-error')?.textContent).toContain('0 이상의 정수');
    expect(childInput().getAttribute('aria-invalid')).toBe('true');
    const request = await askAndCapture('자녀 관련 지원이 있나요?');
    expect(request.profile.child_age_months).toBeNull();
  });

  it('신청자 나이와 자녀 월령은 서로 바뀌거나 섞이지 않는다', async () => {
    type(ageInput(), '32');
    type(childInput(), '2');
    const request = await askAndCapture('자녀 관련 지원이 있나요?');
    expect(request.profile.age).toBe(32);
    expect(request.profile.child_age_months).toBe(2);

    type(childInput(), '');
    const onlyAge = await askAndCapture('육아 지원 정책이 있나요?');
    expect(onlyAge.profile).toMatchObject({ age: 32, child_age_months: null });
  });

  it('조건을 접었다 펼쳐도 자녀 월령이 유지되고 분야 카드의 상담에 전달된다', async () => {
    toggleConditions();
    type(childInput(), '10');
    toggleConditions();
    toggleConditions();
    expect(childInput().value).toBe('10');
    toggleConditions();
    const board = host.querySelector<HTMLElement>('#board')!;
    const explorer = host.querySelector<HTMLElement>('#policy-explorer')!;
    expect(board.hidden).toBe(true);
    expect(explorer.hidden).toBe(false);
    const cards = host.querySelectorAll<HTMLButtonElement>('.cats .cat');
    expect(cards).toHaveLength(5);
    await act(async () => cards[0].click());
    const request = askPolicy.mock.lastCall![0] as AskRequest;
    expect(request.profile.child_age_months).toBe(10);
    expect(board.hidden).toBe(false);
    expect(board.compareDocumentPosition(explorer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(host.querySelector('.cats')).toBeNull();
  });

  it('"10개월이에요" 같은 말은 신청자 나이로 읽지 않는다', async () => {
    const request = await askAndCapture('아기가 10개월이에요');
    expect(request.profile.age).toBeNull();
    expect(request.profile.child_age_months).toBeNull();
  });
});
