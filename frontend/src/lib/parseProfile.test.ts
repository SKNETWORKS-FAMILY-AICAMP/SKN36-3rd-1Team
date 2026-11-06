import { describe, expect, it } from 'vitest';
import { describeProfile, hasProfileInfo, parseProfile } from './parseProfile';

describe('parseProfile', () => {
  it('나이·지역·소득을 한 문장에서 읽는다', () => {
    expect(parseProfile('26살, 서울, 소득은 4000만원이야')).toEqual({ age: 26, region: '서울', income: 4000 });
  });

  it('여러 표현을 읽는다', () => {
    expect(parseProfile('만 29세 충청남도 살아요')).toEqual({ age: 29, region: '충남' });
    expect(parseProfile('연봉 3,500만 원 받아요').income).toBe(3500);
    expect(parseProfile('소득은 1.2억이에요').income).toBe(12000);
  });

  it('소득 관련 말이 없으면 금액을 소득으로 보지 않는다', () => {
    expect(parseProfile('보증금 3000만원 전세').income).toBeUndefined();
  });

  it('범위를 넘는 나이는 버린다', () => {
    expect(parseProfile('200살').age).toBeUndefined();
  });

  it('아무 정보가 없으면 빈 결과', () => {
    const found = parseProfile('신청 서류는 뭐가 필요해요?');
    expect(found).toEqual({});
    expect(hasProfileInfo(found)).toBe(false);
  });

  it('조건 요약 문구', () => {
    expect(describeProfile({ age: 26, region: '서울', income: 4000 })).toBe('26세 · 서울 · 연 4,000만 원');
    expect(describeProfile({})).toBe('');
  });
});
