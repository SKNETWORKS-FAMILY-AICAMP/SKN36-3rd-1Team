import type { UserProfile } from '../api/types';

export const REGIONS = ['서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기', '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주'] as const;

const REGION_ALIASES: Record<string, string[]> = {
  충북: ['충북', '충청북'], 충남: ['충남', '충청남'], 전북: ['전북', '전라북'], 전남: ['전남', '전라남'], 경북: ['경북', '경상북'], 경남: ['경남', '경상남'],
};

/**
 * 사용자가 말로 적은 나이·지역·소득을 찾아낸다. (예: "26살, 서울, 소득은 4000만원이야")
 * 확실히 읽히는 값만 돌려주고, 나머지는 비워 둔다.
 */
export function parseProfile(text: string): Partial<UserProfile> {
  const out: Partial<UserProfile> = {};

  const age = text.match(/(\d{1,3})\s*(?:살|세)/);
  if (age && Number(age[1]) <= 120) out.age = Number(age[1]);

  for (const region of REGIONS) {
    const names = REGION_ALIASES[region] ?? [region];
    if (names.some((n) => text.includes(n))) { out.region = region; break; }
  }

  const money = text.replace(/,/g, '').match(/(\d+(?:\.\d+)?)\s*(억|천만|만)\s*원?/);
  if (money && /소득|연봉|월급|수입|벌/.test(text)) {
    const n = Number(money[1]);
    out.income = Math.round(money[2] === '억' ? n * 10000 : money[2] === '천만' ? n * 1000 : n);
  }
  return out;
}

export const hasProfileInfo = (p: Partial<UserProfile>) => p.age != null || !!p.region || p.income != null;

export function describeProfile(p: Partial<UserProfile>): string {
  return [
    p.age != null ? `${p.age}세` : null,
    p.region || null,
    p.income != null ? `연 ${p.income.toLocaleString('ko-KR')}만 원` : null,
  ].filter(Boolean).join(' · ');
}
