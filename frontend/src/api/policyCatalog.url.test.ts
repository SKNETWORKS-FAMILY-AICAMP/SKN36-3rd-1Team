/** 질문 API 주소에서 정책 목록 주소를 만드는 규칙. 주소는 가상 값이다(sample_only). */
import { describe, expect, it } from 'vitest';
import { policiesUrlFrom } from './policyCatalog';

describe('policiesUrlFrom', () => {
  it('질문 API와 같은 서버의 /api/policies를 만든다', () => {
    expect(policiesUrlFrom('http://127.0.0.1:8000/api/ask')).toBe('http://127.0.0.1:8000/api/policies');
  });
  it('끝에 / 가 있어도 /api/ask/policies로 가지 않는다', () => {
    expect(policiesUrlFrom('http://127.0.0.1:8000/api/ask/')).toBe('http://127.0.0.1:8000/api/policies');
  });
  it('상대 주소는 현재 페이지 기준으로 푼다', () => {
    expect(policiesUrlFrom('/api/ask', 'http://127.0.0.1:5173/#/')).toBe('http://127.0.0.1:5173/api/policies');
  });
  it('비었거나 풀 수 없는 주소는 빈 값으로 둔다(화면이 멈추지 않음)', () => {
    expect(policiesUrlFrom('')).toBe('');
    expect(policiesUrlFrom(undefined)).toBe('');
    expect(policiesUrlFrom('/api/ask', undefined)).toBe('');
  });
});
