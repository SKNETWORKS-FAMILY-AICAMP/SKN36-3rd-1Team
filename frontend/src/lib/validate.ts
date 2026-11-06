/** 화면 입력값 검증. 서버도 같은 기준으로 다시 검증해야 한다 (CODING_STYLE §11). */

export const AGE_MIN = 0;
export const AGE_MAX = 120;

export type AgeCheck = { ok: true; value: number | null } | { ok: false; message: string };

/** 나이 입력: 비어 있으면 '미입력', 0~120 사이 정수만 받는다. */
export function checkAge(raw: string): AgeCheck {
  const text = raw.trim();
  if (text === '') return { ok: true, value: null };
  if (!/^\d+$/.test(text)) return { ok: false, message: '나이는 숫자(정수)로만 적어 주세요' };
  const n = Number(text);
  if (n < AGE_MIN || n > AGE_MAX) return { ok: false, message: `나이는 ${AGE_MIN}~${AGE_MAX} 사이로 적어 주세요` };
  return { ok: true, value: n };
}

/**
 * 백엔드·원문에서 온 주소를 링크로 쓰기 전에 검사한다.
 * http/https만 허용하고, javascript: 등 다른 방식이나 잘못된 주소는 null을 돌려준다 (CODING_STYLE §12).
 */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch {
    return null;
  }
}
