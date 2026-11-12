import { describe, expect, it } from 'vitest';
import { describeProfile, hasProfileInfo, parseProfile, readProfileReply } from './parseProfile';

describe('readProfileReply', () => {
  it('조건만 알려 주는 답은 읽은 값을 돌려준다', () => {
    expect(readProfileReply('난 26살이야')).toEqual({ age: 26 });
    expect(readProfileReply('저 26살이에요')).toEqual({ age: 26 });
    expect(readProfileReply('만 29세입니다')).toEqual({ age: 29 });
    expect(readProfileReply('서울 살아요')).toEqual({ region: '서울' });
    expect(readProfileReply('26살이고 경기도에 살아')).toEqual({ age: 26, region: '경기' });
    expect(readProfileReply('소득은 3000만원이야')).toEqual({ income: 3000 });
  });

  it('새 질문이 섞여 있으면 null', () => {
    expect(readProfileReply('26살인데 청년월세 받을 수 있어?')).toBeNull();
    expect(readProfileReply('26살 청년 지원 알려줘')).toBeNull();
    expect(readProfileReply('서울 청년 정책 알려줘')).toBeNull();
    expect(readProfileReply('전세 대출 알려줘')).toBeNull();
    // 지역·나이에 낱말 하나만 붙어도 새 질문이다
    expect(readProfileReply('서울 월세')).toBeNull();
    expect(readProfileReply('서울 전세')).toBeNull();
    expect(readProfileReply('부산 대출')).toBeNull();
    expect(readProfileReply('26살 취업')).toBeNull();
    expect(readProfileReply('청년 26살')).toBeNull();
  });

  it('월 단위 금액은 연 소득으로 넣지 않는다', () => {
    expect(readProfileReply('월급 200만원이야')).toBeNull();
    expect(readProfileReply('26살이고 월급 200만원이야')).toEqual({ age: 26 }); // 나이만 넣고 월 금액은 버린다
  });

  it('조건이 없으면 null', () => {
    expect(readProfileReply('신청 서류는 뭐가 필요해요')).toBeNull();
    expect(readProfileReply('햄버거 먹고 싶어')).toBeNull();
  });
});

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

  it('월 단위 금액은 연 소득으로 읽지 않는다', () => {
    expect(parseProfile('월급 200만원이야').income).toBeUndefined();
    expect(parseProfile('한 달에 200만원 벌어요').income).toBeUndefined();
    expect(parseProfile('월 소득 250만 원', 'income').income).toBeUndefined();
    expect(parseProfile('26살, 월급 200만원').age).toBe(26);
  });

  it('AI가 되물은 항목의 대답은 단위·소득 같은 말이 빠져도 읽는다', () => {
    expect(parseProfile('4000만 원', 'income').income).toBe(4000);
    expect(parseProfile('26', 'age').age).toBe(26);
    expect(parseProfile('26').age).toBeUndefined(); // 되묻지 않았으면 숫자만으로 나이를 정하지 않는다
    expect(parseProfile('보증금 3000만원', 'age').income).toBeUndefined();
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
