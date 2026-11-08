import type { CSSProperties } from 'react';

/**
 * 이 화면을 위해 직접 그린 손그림 SVG 아이콘(외부 캐릭터·이미지 미사용).
 * 가는 갈색 선 + 파스텔 면 + 작은 점 눈·빗금 볼터치.
 */
const O = '#4a3a33';
const S = `stroke="${O}" stroke-linejoin="round" stroke-linecap="round"`;

type Mood = 'smile' | 'open' | 'worry';
function face(x: number, y: number, mood: Mood = 'smile'): string {
  const eye = (cx: number) => `<ellipse cx="${cx}" cy="${y}" rx="1.45" ry="1.85" fill="${O}"/><circle cx="${cx + 0.45}" cy="${y - 0.7}" r=".45" fill="#fff"/>`;
  const brows = mood === 'worry' ? `<path d="M${x - 9} ${y - 4.5} l3 1.2M${x + 9} ${y - 4.5} l-3 1.2" fill="none" ${S} stroke-width="1.3"/>` : '';
  const mouth = mood === 'worry' ? `<path d="M${x - 2} ${y + 4.4} q2 -1.6 4 0" fill="none" ${S} stroke-width="1.3"/>`
    : mood === 'open' ? `<path d="M${x - 2} ${y + 2.6} q2 3.4 4 0z" fill="#f08a96" ${S} stroke-width="1.2"/>`
    : `<path d="M${x - 2.2} ${y + 2.8} q1.1 1.3 2.2 0 q1.1 1.3 2.2 0" fill="none" ${S} stroke-width="1.3"/>`;
  const blush = (cx: number) => `<path d="M${cx - 2.6} ${y + 4.2} l1.6 -2.6M${cx - 0.4} ${y + 4.2} l1.6 -2.6M${cx + 1.8} ${y + 4.2} l1.6 -2.6" stroke="#ff8fa6" stroke-width="1.1" stroke-linecap="round"/>`;
  return eye(x - 7) + eye(x + 7) + brows + mouth + blush(x - 11) + blush(x + 11);
}

/** 작은 얼굴 (두 사람이 함께 있거나 아기처럼 머리가 작은 캐릭터용). gap: 두 눈 사이 절반 */
function smallFace(x: number, y: number, gap = 4.5): string {
  const eye = (cx: number) => `<ellipse cx="${cx}" cy="${y}" rx="1.15" ry="1.45" fill="${O}"/><circle cx="${cx + 0.35}" cy="${y - 0.55}" r=".35" fill="#fff"/>`;
  const mouth = `<path d="M${x - 1.4} ${y + 2.2} q.7 .9 1.4 0 q.7 .9 1.4 0" fill="none" ${S} stroke-width="1.1"/>`;
  const blush = (cx: number) => `<ellipse cx="${cx}" cy="${y + 2.4}" rx="1.8" ry="1" fill="#ffb3c1"/>`;
  return eye(x - gap) + eye(x + gap) + mouth + blush(x - gap - 2.8) + blush(x + gap + 2.8);
}

/** 사람 캐릭터 공통: 둥근 찹쌀떡 머리 */
const head = (cx: number, cy: number, r: number) =>
  `<path d="M${cx - r} ${cy}c0-${r * 0.75} ${r * 0.45}-${r} ${r}-${r}s${r} ${r * 0.25} ${r} ${r}c0 ${r * 0.6}-${r * 0.45} ${r * 0.85}-${r} ${r * 0.85}s-${r}-${r * 0.25}-${r}-${r * 0.85}z" fill="#fff4e8" ${S}/>`;

const SVG = {
  /** 마스코트 '모찌싹': 새싹이 난 찹쌀떡 */
  mascot: `<path d="M32 17c-1-5 2-9 6-8-1 4-3 6-6 8z" fill="#bfeccb" ${S}/><path d="M32 17c0-4-3-7-6-6 0 3 2 5 6 6z" fill="#d8f5df" ${S}/><path d="M9 43c0-14 10-25 23-25s23 11 23 25c0 8-10 12-23 12S9 51 9 43z" fill="#fffdf9" ${S}/><path d="M12 44c-3 0-4 3-2 4M52 44c3 0 4 3 2 4" fill="none" ${S} stroke-width="1.5"/>${face(32, 39)}`,
  briefcase: `<path d="M25 23v-3a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v3" fill="none" ${S}/><path d="M8 34c0-7 4-11 11-11h26c7 0 11 4 11 11v8c0 8-4 12-11 12H19c-7 0-11-4-11-12z" fill="#f8dcc0" ${S}/>${face(32, 39)}`,
  house: `<path d="M9 33 32 13l23 20" fill="#ffc7d2" ${S}/><path d="M15 31v15c0 6 3 8 8 8h18c5 0 8-2 8-8V31L32 16z" fill="#fffaf0" ${S}/>${face(32, 40)}`,
  books: `<path d="M10 44c0-4 2-6 6-6h32c4 0 6 2 6 6v4c0 4-2 6-6 6H16c-4 0-6-2-6-6z" fill="#cfe7ff" ${S}/><path d="M14 31c0-4 2-6 6-6h24c4 0 6 2 6 6v1c0 4-2 6-6 6H20c-4 0-6-2-6-6z" fill="#fff0b8" ${S}/><path d="M19 18c0-3 2-5 5-5h16c3 0 5 2 5 5s-2 7-5 7H24c-3 0-5-4-5-7z" fill="#ffd3dd" ${S}/>${face(32, 45)}`,
  money: `<path d="M25 19c-3-4 0-8 4-6l3 2 3-2c4-2 7 2 4 6z" fill="#fff0b8" ${S}/><path d="M25 21C14 27 9 35 9 42c0 8 10 12 23 12s23-4 23-12c0-7-5-15-16-21z" fill="#fff5d6" ${S}/><path d="M26 21h12" ${S}/>${face(32, 40)}`,
  bottle: `<path d="M27 16c0-5 10-5 10 0v3H27z" fill="#ffd3dd" ${S}/><rect x="23" y="19" width="18" height="6" rx="3" fill="#dcd2ff" ${S}/><path d="M18 36c0-7 4-11 10-11h8c6 0 10 4 10 11v9c0 7-4 10-10 10h-8c-6 0-10-3-10-10z" fill="#f2f9ff" ${S}/>${face(32, 42)}`,
  sparkles: `<path d="M26 8c2 10 6 14 16 16-10 2-14 6-16 16-2-10-6-14-16-16 10-2 14-6 16-16z" fill="#ffe27a" ${S}/><path d="M48 36c1 5 3 7 8 8-5 1-7 3-8 8-1-5-3-7-8-8 5-1 7-3 8-8z" fill="#ffc6d2" ${S} stroke-width="2"/>`,
  cloud: `<path d="M16 46c-6 0-10-4-10-9s4-9 9-9c1-8 7-13 15-13 7 0 13 5 14 12 6 0 11 4 11 10s-5 9-11 9z" fill="#ffffff" ${S}/>${face(32, 36)}`,
  bulb: `<path d="M32 8c-11 0-18 8-18 17 0 7 4 11 7 14v5h22v-5c3-3 7-7 7-14 0-9-7-17-18-17z" fill="#fff3a6" ${S}/><rect x="21" y="44" width="22" height="9" rx="4" fill="#d8d0c2" ${S}/>${face(32, 26)}`,
  star: `<path d="M32 9c2 0 3 1 4 3l5 9 10 2c4 1 5 5 2 8l-7 7 2 10c1 4-3 7-6 5l-10-5-10 5c-3 2-7-1-6-5l2-10-7-7c-3-3-2-7 2-8l10-2 5-9c1-2 2-3 4-3z" fill="#ffd66b" ${S}/>${face(32, 32)}`,
  cake: `<path d="M32 6c2 3 2 5 0 7-2-2-2-4 0-7z" fill="#ffb347" ${S} stroke-width="2"/><rect x="30" y="13" width="4" height="10" rx="2" fill="#a9d8ff" ${S} stroke-width="2"/><rect x="10" y="23" width="44" height="31" rx="9" fill="#fff4f6" ${S}/><path d="M10 33c5 5 9-3 14 1s9-4 14 0 9-4 16 0" fill="none" stroke="#ff9fb2" stroke-width="5" stroke-linecap="round"/>${face(32, 43)}`,
  pin: `<path d="M32 58S14 40 14 26a18 18 0 0 1 36 0c0 14-18 32-18 32z" fill="#ff9fae" ${S}/>${face(32, 26)}`,
  search: `<path d="M41 41l13 13" stroke="${O}" stroke-width="7" stroke-linecap="round"/><path d="M41 41l13 13" stroke="#d9a07a" stroke-width="3.5" stroke-linecap="round"/><circle cx="27" cy="27" r="18" fill="#e3f3ff" ${S}/>${face(27, 26, 'open')}`,
  warning: `<path d="M32 10c3 0 5 2 6 4l17 31c2 5-1 9-6 9H15c-5 0-8-4-6-9l17-31c1-2 3-4 6-4z" fill="#ffd3a3" ${S}/>${face(32, 37, 'worry')}<path d="M49 18c2 3 2 5 0 6-2-1-2-3 0-6z" fill="#a9d8ff" ${S} stroke-width="1.6"/>`,
  info: `<path d="M12 14h40a6 6 0 0 1 6 6v20a6 6 0 0 1-6 6H30l-10 9v-9h-8a6 6 0 0 1-6-6V20a6 6 0 0 1 6-6z" fill="#ece6ff" ${S}/>${face(32, 29)}`,
  check: `<rect x="9" y="9" width="46" height="46" rx="14" fill="#c9f2d8" ${S}/><path d="M20 33l8 8 16-17" fill="none" stroke="#3f9c6a" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>`,
  mail: `<rect x="8" y="16" width="48" height="34" rx="8" fill="#fff6e3" ${S}/><path d="M10 19l22 16 22-16" fill="none" ${S}/><path d="M32 40c-3-3-7-1-5 2l5 5 5-5c2-3-2-5-5-2z" fill="#ff8fa3" stroke="${O}" stroke-width="1.6" stroke-linejoin="round"/>`,
  heart: `<path d="M32 52S10 39 10 25c0-7 5-12 11-12 5 0 9 3 11 7 2-4 6-7 11-7 6 0 11 5 11 12 0 14-22 27-22 27z" fill="#ffaabb" ${S}/>`,

  /* ── 대표 상황(PRD 시나리오) 캐릭터: 마스코트와 같은 둥근 찹쌀떡 얼굴 ── */
  /** 청년 취업준비생: 셔츠에 넥타이, 손에 든 이력서 */
  personaJob: `<path d="M13 61c0-10 8-16 19-16s19 6 19 16z" fill="#cfe1f5" ${S}/>`
    + `<path d="M26.5 45.5l5.5 6 5.5-6" fill="#ffffff" ${S}/><path d="M32 51.5l-2.2 5 2.2 3 2.2-3z" fill="#e8a07a" ${S} stroke-width="1.5"/>`
    + head(32, 27, 18)
    + `<path d="M14.5 25c.8-10 8-16 17.5-16s16.7 6 17.5 16c-3.5-4.5-8.5-5.5-12.5-3.5-3-3.8-9-4.6-13-1.8-4 .2-7.3 2-9.5 5.3z" fill="#7a5a48" ${S}/>`
    + face(32, 30)
    + `<rect x="44" y="47" width="12" height="14" rx="2.5" fill="#fffdf6" ${S} stroke-width="1.6"/><path d="M47 51.5h6M47 55h6M47 58h4" stroke="#c9b8a6" stroke-width="1.4" stroke-linecap="round"/>`,

  /** 신혼 무주택 가구: 나란히 선 두 사람과 작은 하트 */
  personaNewlywed: `<path d="M5 62c0-8 6-12.5 14-12.5S33 54 33 62z" fill="#5b6b8c" ${S}/><path d="M16 50l3 4 3-4" fill="#ffffff" ${S} stroke-width="1.5"/>`
    + `<path d="M31 62c1-8 6.5-12.5 14-12.5S58 54 59 62z" fill="#ffffff" ${S}/>`
    + `<path d="M33 33c0-10 5-15 12-15s12 5 12 15v15c-4 2.5-20 2.5-24 0z" fill="#fbfbff" ${S} stroke-width="1.6"/>`
    + head(19, 38, 12) + `<path d="M7.5 36.5c.6-7 5.2-11 11.5-11s10.9 4 11.5 11c-3.2-2.8-7-3.6-11.5-2.8-4.5-.8-8.3 0-11.5 2.8z" fill="#6b4e3d" ${S}/>`
    + smallFace(19, 41)
    + head(45, 38, 11) + `<path d="M34.5 36c.8-6.5 5-10 10.5-10s9.7 3.5 10.5 10c-3-3.5-6.5-4.4-10.5-3.4-4-1-7.5-.1-10.5 3.4z" fill="#9a6b4f" ${S}/>`
    + `<circle cx="53" cy="27" r="2.8" fill="#ffaabb" ${S} stroke-width="1.3"/><circle cx="49.5" cy="25.5" r="2" fill="#fff0b8" ${S} stroke-width="1.2"/>`
    + smallFace(45, 41, 4)
    + `<path d="M32 15.5s-5-3-5-6.2c0-1.8 1.4-3 3-3 1 0 1.6.5 2 1.2.4-.7 1-1.2 2-1.2 1.6 0 3 1.2 3 3 0 3.2-5 6.2-5 6.2z" fill="#ff9fb2" ${S} stroke-width="1.4"/>`,

  /** 출산·육아 가구: 아기를 안은 보호자 (아기 머리엔 마스코트 같은 새싹) */
  personaParent: `<path d="M10 62c0-11 9-17 22-17s22 6 22 17z" fill="#f6d8c8" ${S}/>`
    + head(32, 25, 17)
    + `<circle cx="32" cy="8.5" r="4.8" fill="#8a6450" ${S}/><path d="M15.5 23c.9-9 8-14 16.5-14s15.6 5 16.5 14c-4.5-3.6-10.2-4.8-16.5-4.8S20 19.4 15.5 23z" fill="#8a6450" ${S}/>`
    + face(32, 27)
    + `<path d="M31.5 40.5c-.6-3 1-5.2 3.6-4.7-.6 2.6-1.6 3.7-3.6 4.7z" fill="#bfeccb" ${S} stroke-width="1.3"/>`
    + `<path d="M23 49c0-5.5 4-9 9-9s9 3.5 9 9c0 4.5-4 6.5-9 6.5s-9-2-9-6.5z" fill="#fffdf9" ${S}/>`
    + smallFace(32, 48, 3)
    + `<path d="M15 53c5 6.5 29 6.5 34 0" fill="none" ${S} stroke-width="2.2"/>`,
} as const;

export type IconName = keyof typeof SVG;

export const iconUrl = (name: IconName) =>
  'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><g stroke-width="1.9">${SVG[name]}</g></svg>`);

export function Icon({ name, className, style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return <img src={iconUrl(name)} alt="" aria-hidden="true" className={className} style={style} decoding="async" />;
}
