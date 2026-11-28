import { describe, expect, it } from 'vitest';
import { decodeEntitiesDeep, decodeHtmlEntities } from './htmlEntities';

describe('HTML 문자 코드 바꾸기', () => {
  it('데이터에 나온 숫자·이름 코드를 글자로 바꾼다', () => {
    expect(decodeHtmlEntities('(생활지원) 월 65만원 이하 &#9312; 의복 &#9313; 숙식')).toBe('(생활지원) 월 65만원 이하 ① 의복 ② 숙식');
    expect(decodeHtmlEntities('&lsquo;청년&rsquo; 2&times;3 &ge; 5 &rarr; 끝 &#x2460;')).toBe('‘청년’ 2×3 ≥ 5 → 끝 ①');
  });

  it('코드가 아닌 &와 모르는 이름·잘못된 번호는 그대로 둔다', () => {
    for (const text of ['R&D 지원', 'A & B', '&unknownname;', '&#0;', '&#55296;', '&#99999999;', '&#9312']) {
      expect(decodeHtmlEntities(text)).toBe(text);
    }
  });

  it('눈에 안 보이는 제어 문자는 만들지 않는다(탭·줄바꿈만 허용)', () => {
    expect(decodeHtmlEntities('a&#1;b')).toBe('a&#1;b');
    expect(decodeHtmlEntities('a&#10;b')).toBe('a\nb');
  });

  it('객체 안 문자열을 모두 바꾸되 주소(url, *_url)는 그대로 둔다', () => {
    const url = 'https://example.org/a?x=1&amp;y=2';
    const decoded = decodeEntitiesDeep({ benefit: '&#9312; 지원', source_url: url, url, items: [{ text: '&rsquo;', n: 1 }], none: null });
    expect(decoded).toEqual({ benefit: '① 지원', source_url: url, url, items: [{ text: '’', n: 1 }], none: null });
  });
});
