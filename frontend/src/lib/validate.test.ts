import { describe, expect, it } from 'vitest';
import { checkAge, checkChildMonths, safeHttpUrl } from './validate';

describe('checkChildMonths (#39)', () => {
  it('빈 값은 미입력, 0은 유효한 값이다', () => {
    expect(checkChildMonths('')).toEqual({ ok: true, value: null });
    expect(checkChildMonths(' ')).toEqual({ ok: true, value: null });
    expect(checkChildMonths('0')).toEqual({ ok: true, value: 0 });
  });

  it('0 이상 정수는 상한 없이 받는다(계약 §8.2)', () => {
    for (const [text, value] of [['24', 24], ['25', 25], [' 10 ', 10], ['300', 300]] as const) {
      expect(checkChildMonths(text)).toEqual({ ok: true, value });
    }
  });

  it('음수·소수·문자·지수·정확히 보낼 수 없는 큰 수는 거부한다', () => {
    for (const bad of ['-1', '24.5', '24.0', 'abc', '24개월', '1e2', '99999999999999999999']) {
      expect(checkChildMonths(bad).ok).toBe(false);
    }
  });
});

describe('checkAge', () => {
  it('빈 값은 미입력으로 통과', () => {
    expect(checkAge('')).toEqual({ ok: true, value: null });
    expect(checkAge('  ')).toEqual({ ok: true, value: null });
  });

  it('0~120 정수만 받는다 (경계값 포함)', () => {
    expect(checkAge('0')).toEqual({ ok: true, value: 0 });
    expect(checkAge('26')).toEqual({ ok: true, value: 26 });
    expect(checkAge('120')).toEqual({ ok: true, value: 120 });
  });

  it('범위 밖·음수·소수·문자는 거부한다', () => {
    for (const bad of ['121', '-5', '2.5', '스물', '26세']) {
      expect(checkAge(bad).ok).toBe(false);
    }
  });
});

describe('safeHttpUrl', () => {
  it('http·https 주소는 그대로 쓴다', () => {
    expect(safeHttpUrl('https://www.youthcenter.go.kr/')).toBe('https://www.youthcenter.go.kr/');
    expect(safeHttpUrl('http://example.org/a')).toBe('http://example.org/a');
  });

  it('스크립트·데이터 주소와 잘못된 값은 막는다', () => {
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull();
    expect(safeHttpUrl('JaVaScRiPt:alert(1)')).toBeNull();
    expect(safeHttpUrl('data:text/html,<b>x</b>')).toBeNull();
    expect(safeHttpUrl('/relative/path')).toBeNull();
    expect(safeHttpUrl('')).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});
