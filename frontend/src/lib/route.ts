/**
 * 화면 전환(주소의 # 부분만 사용). 라우터 패키지를 새로 설치하지 않는다 (AGENTS.md §5).
 *   #/                     맞춤 정책 찾기(첫 화면)
 *   #/saved                첫 화면의 정책 목록을 관심 정책 보기로 연 상태
 *   #/life                 생애주기 타임라인
 *   #/life?stage=청년       해당 생애단계를 연 타임라인
 *   #/map                  정책 관계 지도
 *   #/map?policy=POL_001   해당 정책을 연 지도
 */
import { useSyncExternalStore } from 'react';
import { LIFE_STAGES } from '../config/graph';
import type { Theme } from './theme';

export type Route =
  | { page: 'ask' }
  | { page: 'saved' }
  | { page: 'life'; stage: string | null }
  | { page: 'map'; policyId: string | null };

/** 주소에서 온 값이라 정책 ID 형식(영문·숫자·_·-)만 받는다 */
const POLICY_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function parseRoute(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const params = new URLSearchParams(query);
  if (path === '/map') {
    const policyId = params.get('policy');
    return { page: 'map', policyId: policyId && POLICY_ID.test(policyId) ? policyId : null };
  }
  if (path === '/life') {
    // 생애단계 이름은 정해진 목록에 있는 것만 받는다
    const stage = params.get('stage');
    return { page: 'life', stage: stage && LIFE_STAGES.some((s) => s.name === stage) ? stage : null };
  }
  if (path === '/saved') return { page: 'saved' };
  return { page: 'ask' };
}

export const askHref = '#/';
export const savedHref = '#/saved';
/** 첫 화면(맞춤 정책 찾기)에 그려지는 화면인지. 관심 정책 보기도 같은 화면의 목록만 바꾼다 */
export const isHomeRoute = (route: Route) => route.page === 'ask' || route.page === 'saved';
export const mapHref = (policyId?: string | null) => (policyId ? `#/map?policy=${encodeURIComponent(policyId)}` : '#/map');
export const lifeHref = (stage?: string | null) => (stage ? `#/life?stage=${encodeURIComponent(stage)}` : '#/life');

/** Django 상세가 테마를 받는 쿼리 이름. web_ssr/policies/views.py THEME_QUERY_PARAM과 같아야 한다 */
export const DETAIL_THEME_PARAM = 'theme';

/** 서버 정책 ID 규칙(backend/api/schemas.py selected_policy_id, web_ssr converters.py)과 같다 */
const DETAIL_POLICY_ID = /^POL_[A-Z0-9_]{1,124}$/;

/**
 * Django 정책 상세(SSR) 페이지 주소. 해시 화면이 아니라 다른 서버의 문서라 새 탭으로 연다.
 * 기본 주소는 VITE_POLICY_DETAIL_BASE_URL(예: http://127.0.0.1:8001/policies/). 비어 있거나
 * http(s)가 아니면 null을 돌려 링크를 숨긴다 — 설정하지 않은 환경의 기존 화면은 그대로다.
 * 환경 변수는 호출할 때 읽는다(테스트에서 바꿀 수 있게).
 * 다크 테마면 ?theme=dark를 붙여 상세 페이지도 같은 테마로 연다. 라이트는 기본값이라 붙이지 않는다.
 */
export function policyDetailHref(policyId: string, base = import.meta.env.VITE_POLICY_DETAIL_BASE_URL as string | undefined,
  theme: Theme = 'light'): string | null {
  if (!base?.trim() || !DETAIL_POLICY_ID.test(policyId)) return null;
  try {
    const root = new URL(base.trim());
    if (!['http:', 'https:'].includes(root.protocol) || root.username || root.password || root.search || root.hash) return null;
    if (!root.pathname.endsWith('/')) root.pathname += '/';
    const href = new URL(`${encodeURIComponent(policyId)}/`, root);
    if (theme !== 'light') href.searchParams.set(DETAIL_THEME_PARAM, theme);
    return href.href;
  } catch {
    return null;
  }
}

const subscribe = (onChange: () => void) => {
  addEventListener('hashchange', onChange);
  return () => removeEventListener('hashchange', onChange);
};

/** 현재 주소의 화면. 같은 주소면 같은 문자열이라 다시 그리지 않는다 */
export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseRoute(hash);
}
