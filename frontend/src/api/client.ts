import type { AskRequest, AskResponse } from './types';
import { mockAsk } from '../mock/mockAsk';

/** 백엔드 주소. 비어 있으면 예시 응답으로 동작한다(.env.example 참고). */
const ASK_API_URL = (import.meta.env.VITE_ASK_API_URL as string | undefined)?.trim() || '';
const TIMEOUT_MS = 30_000;

export const usingSample = !ASK_API_URL;

/**
 * 질문을 보내고 답변을 받는다.
 * 실패하면 Error를 던지고, 화면은 오류 상태와 '다시 시도'를 보여 준다.
 */
export async function askPolicy(request: AskRequest): Promise<AskResponse> {
  if (usingSample) return mockAsk(request);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(ASK_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`서버 응답 오류 (${res.status})`);
    return (await res.json()) as AskResponse;
  } finally {
    clearTimeout(timer);
  }
}
