import { describe, expect, it } from 'vitest';
import { requestErrorMessage, serverErrorMessage } from './client';

describe('오류 문구', () => {
  it('서버 오류 code를 보고 화면이 정한 문구를 쓴다 (서버 message는 보여 주지 않는다)', () => {
    const body = { detail: { code: 'LLM_TIMEOUT', message: '서버 내부 문장' } };
    const text = serverErrorMessage(504, body);
    expect(text).toBe('답변 준비가 늦어지고 있어요. 잠시 후 다시 시도해 주세요.');
    expect(text).not.toContain('서버 내부 문장');
  });

  it('입력 오류는 다시 시도 대신 고쳐서 보내라고 안내한다', () => {
    expect(serverErrorMessage(422, { detail: { code: 'INVALID_REQUEST' } }))
      .toBe('질문이나 내 조건을 다시 확인해 주세요. 질문을 고쳐서 다시 보내 주세요.');
  });

  it('모르는 code이거나 형식이 다르면 오류 번호만 알린다', () => {
    const unknown = '일시적인 문제가 생겼어요. (오류 500) 잠시 후 다시 시도해 주세요.';
    expect(serverErrorMessage(500, { detail: { code: 'INTERNAL_ERROR', message: 'Traceback ...' } })).toBe(unknown);
    expect(serverErrorMessage(500, null)).toBe(unknown);
    expect(serverErrorMessage(502, { detail: '문자열' })).toBe('일시적인 문제가 생겼어요. (오류 502) 잠시 후 다시 시도해 주세요.');
  });

  it('시간 초과는 60초 넘게 오지 않았다고 알려 준다', () => {
    expect(requestErrorMessage(new DOMException('aborted', 'AbortError')))
      .toBe('답변이 60초 넘게 오지 않았어요. 잠시 후 다시 시도해 주세요.');
  });

  it('연결 실패(fetch TypeError)는 서버에 연결하지 못했다고 알려 준다', () => {
    expect(requestErrorMessage(new TypeError('Failed to fetch')))
      .toBe('답변 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
  });
});
