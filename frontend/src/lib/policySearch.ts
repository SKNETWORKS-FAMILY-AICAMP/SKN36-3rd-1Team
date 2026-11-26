/**
 * 정책 목록 검색어 비교용 정규화(#103).
 * 공식 표기 'K-패스'와 사용자가 흔히 치는 'K패스'·'k패스'를 같게 보고,
 * 'K-'·'K-패'처럼 입력하는 도중에도 결과가 사라지지 않도록 붙임표를 통째로 지운다.
 * 정책별 별칭을 따로 두지 않으므로 다른 정책명에도 같은 규칙이 적용된다.
 */

/** 지우는 붙임표류: - (U+002D) ‐ (U+2010) ‑ (U+2011) – (U+2013) — (U+2014) － (U+FF0D) */
export const SEARCH_HYPHENS = ['-', '‐', '‑', '–', '—', '－'] as const;

const IGNORED = new RegExp(`[\\s${SEARCH_HYPHENS.map(ch => (ch === '-' ? '\\-' : ch)).join('')}]`, 'g');

/** 소문자로 바꾸고 공백·붙임표류를 지운 비교용 문자열. 검색어와 '정책명 기관명' 양쪽에 같이 쓴다 */
export function policySearchKey(text: string): string {
  return text.toLocaleLowerCase().replace(IGNORED, '');
}
