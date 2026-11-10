/**
 * 화면 전환(주소의 # 부분만 사용). 라우터 패키지를 새로 설치하지 않는다 (AGENTS.md §5).
 *   #/                     상담 화면
 *   #/life                 생애주기 타임라인
 *   #/life?stage=청년       해당 생애단계를 연 타임라인
 *   #/map                  정책 관계 지도
 *   #/map?policy=POL_001   해당 정책을 연 지도
 */
import { useSyncExternalStore } from 'react';
import { LIFE_STAGES } from '../config/graph';

export type Route =
  | { page: 'ask' }
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
  return { page: 'ask' };
}

export const askHref = '#/';
export const mapHref = (policyId?: string | null) => (policyId ? `#/map?policy=${encodeURIComponent(policyId)}` : '#/map');
export const lifeHref = (stage?: string | null) => (stage ? `#/life?stage=${encodeURIComponent(stage)}` : '#/life');

const subscribe = (onChange: () => void) => {
  addEventListener('hashchange', onChange);
  return () => removeEventListener('hashchange', onChange);
};

/** 현재 주소의 화면. 같은 주소면 같은 문자열이라 다시 그리지 않는다 */
export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseRoute(hash);
}
