import type { UserProfile } from '../api/types';

export const REGIONS = ['서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기', '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주'] as const;

/** 월 단위 금액 표현. 연 소득 칸에 넣으면 단위가 틀리므로 소득으로 읽지 않는다. */
const MONTHLY_INCOME = /월급|월\s*소득|월\s*수입|한\s*달|매달|매월|월\s*\d/;

const REGION_ALIASES: Record<string, string[]> = {
  충북: ['충북', '충청북'], 충남: ['충남', '충청남'], 전북: ['전북', '전라북'], 전남: ['전남', '전라남'], 경북: ['경북', '경상북'], 경남: ['경남', '경상남'],
};

/**
 * 사용자가 말로 적은 나이·지역·소득을 찾아낸다. (예: "26살, 서울, 소득은 4000만원이야")
 * 확실히 읽히는 값만 돌려주고, 나머지는 비워 둔다.
 * awaitingField: AI가 되물은 항목에 대한 대답이면 그 항목 키. 이때는 '26'이나 '4000만 원'처럼
 * 단위·'소득' 같은 말이 빠진 대답도 그 항목 값으로 읽는다.
 */
export function parseProfile(text: string, awaitingField?: string | null): Partial<UserProfile> {
  const out: Partial<UserProfile> = {};

  const age = text.match(/(\d{1,3})\s*(?:살|세)/) ?? (awaitingField === 'age' ? text.match(/^\s*(\d{1,3})\s*$/) : null);
  if (age && Number(age[1]) <= 120) out.age = Number(age[1]);

  for (const region of REGIONS) {
    const names = REGION_ALIASES[region] ?? [region];
    if (names.some((n) => text.includes(n))) { out.region = region; break; }
  }

  // 내 조건의 소득은 연 소득(만 원)이다. 월급·월 소득처럼 월 단위 금액은 연으로 바꿔 추측하지 않고 읽지 않는다 (#64).
  const money = text.replace(/,/g, '').match(/(\d+(?:\.\d+)?)\s*(억|천만|만)\s*원?/);
  if (money && !MONTHLY_INCOME.test(text) && (awaitingField === 'income' || /소득|연봉|수입|벌/.test(text))) {
    const n = Number(money[1]);
    out.income = Math.round(money[2] === '억' ? n * 10000 : money[2] === '천만' ? n * 1000 : n);
  }
  return out;
}

export const hasProfileInfo = (p: Partial<UserProfile>) => p.age != null || !!p.region || p.income != null;

// 조건만 알려 주는 답에 붙는 말. 값과 이 말들을 빼고 아무 글자도 남지 않아야 조건 답으로 본다.
// "서울 월세"·"26살 취업"처럼 낱말 하나만 남아도 새 질문이므로 허용치는 0이다 (#64).
const REPLY_VALUE = /(?:만\s*)?\d+(?:[.,]\d+)?\s*(?:살|세|억|천만|만)\s*원?/g;
const REPLY_REGION = new RegExp(
  `(?:${REGIONS.flatMap((r) => REGION_ALIASES[r] ?? [r]).join('|')})(?:특별자치시|특별자치도|특별시|광역시|도|시)?`, 'g');
const REPLY_FILLER = /살고\s*있어요?|살아요?|사는|거주(?:해요|하고\s*있어요|중)?|이에요|이예요|예요|에요|입니다|이구요|이고|이며|이랑|하고|그리고|이야|이요|소득은?|연봉은?|월급은?|수입은?|정도|쯤|나는|저는|제가|난|전|저|나|에서|에|요|야|임/g;
const REPLY_LEFTOVER_MAX = 0;

/**
 * "난 26살이야", "서울 살아요"처럼 나이·지역·소득만 알려 주는 답이면 읽은 값을, 새 질문이 섞여 있으면 null을 돌려준다.
 * 물음표가 있거나 값·조사를 빼고도 말이 남으면(예: "26살인데 청년월세 받을 수 있어?") 새 질문으로 본다.
 */
export function readProfileReply(text: string): Partial<UserProfile> | null {
  if (/[?？]/.test(text)) return null;
  const found = parseProfile(text);
  if (!hasProfileInfo(found)) return null;
  const leftover = text.replace(REPLY_VALUE, '').replace(REPLY_REGION, '').replace(REPLY_FILLER, '').replace(/[^\p{L}\p{N}]/gu, '');
  return leftover.length <= REPLY_LEFTOVER_MAX ? found : null;
}

export function describeProfile(p: Partial<UserProfile>): string {
  return [
    p.age != null ? `${p.age}세` : null,
    p.region || null,
    p.income != null ? `연 ${p.income.toLocaleString('ko-KR')}만 원` : null,
  ].filter(Boolean).join(' · ');
}
