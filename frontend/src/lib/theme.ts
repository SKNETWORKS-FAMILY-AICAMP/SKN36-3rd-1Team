/**
 * 지금 화면 테마. App이 <html data-theme>에 넣은 값을 그대로 읽는다(테마 버튼으로만 바뀐다).
 * Django 정책 상세는 다른 서버라 React의 저장값을 읽지 못하므로, 상세 링크에 이 값을 실어 보낸다.
 */
import { useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark';

export function currentTheme(): Theme {
  return typeof document !== 'undefined' && document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

// 정책 카드마다 감시자를 따로 만들지 않도록 <html> 감시자 하나를 함께 쓴다
const listeners = new Set<() => void>();
let observer: MutationObserver | null = null;

const subscribe = (onChange: () => void) => {
  if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') return () => {};
  listeners.add(onChange);
  if (!observer) {
    observer = new MutationObserver(() => listeners.forEach(listener => listener()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }
  return () => {
    listeners.delete(onChange);
    if (!listeners.size) { observer?.disconnect(); observer = null; }
  };
};

/** 테마 버튼을 누르면 다시 그려서 링크 주소도 바로 바뀐다. 서버 렌더링(테스트)에서는 라이트 */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme, () => 'light');
}
