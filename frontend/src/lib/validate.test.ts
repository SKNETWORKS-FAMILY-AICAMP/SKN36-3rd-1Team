import { describe, expect, it } from 'vitest';
import { checkAge, safeHttpUrl } from './validate';

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
