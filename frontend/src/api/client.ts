import type { AskRequest, AskResponse } from './types';
import { mockAsk } from '../mock/mockAsk';
import { ASK_TIMEOUT_MS } from '../config/request';

/** 백엔드 주소. 비어 있으면 예시 응답으로 동작한다(.env.example 참고). */
const ASK_API_URL = (import.meta.env.VITE_ASK_API_URL as string | undefined)?.trim() || '';

export const usingSample = !ASK_API_URL;

const RETRY = '잠시 후 다시 시도해 주세요.';
const FIX_INPUT = '질문을 고쳐서 다시 보내 주세요.';

/**
 * 서버 오류 코드별 화면 문구.
 * 계약(ask_contract §9)은 code 목록만 정하고 message 문장은 정하지 않는다.
 * 서버 message는 외부 데이터라 그대로 보여 주지 않고, code만 보고 화면이 정한 문구를 쓴다 (CODING_STYLE §12).
 */
const CODE_MESSAGES: Record<string, string> = {
  SEARCH_TIMEOUT: `답변 준비가 늦어지고 있어요. ${RETRY}`,
  LLM_TIMEOUT: `답변 준비가 늦어지고 있어요. ${RETRY}`,
  SEARCH_BACKEND_UNAVAILABLE: `지금 답변 서버가 응답하지 않아요. ${RETRY}`,
  LLM_UNAVAILABLE: `지금 답변 서버가 응답하지 않아요. ${RETRY}`,
  LLM_OUTPUT_INVALID: `출처를 확인할 수 없는 답변이라 보여 드리지 않았어요. ${RETRY}`,
  ELIGIBILITY_OUTPUT_INVALID: `출처를 확인할 수 없는 답변이라 보여 드리지 않았어요. ${RETRY}`,
  INVALID_REQUEST: `질문이나 내 조건을 다시 확인해 주세요. ${FIX_INPUT}`,
  INVALID_SEARCH_INPUT: `질문이나 내 조건을 다시 확인해 주세요. ${FIX_INPUT}`,
};

/** 서버 오류 응답({"detail": {"code", "message"}})의 code로 화면 문구를 고른다. 모르는 code면 오류 번호만 알린다. */
export function serverErrorMessage(status: number, body: unknown): string {
  const code = (body as { detail?: { code?: unknown } } | null)?.detail?.code;
  return (typeof code === 'string' && CODE_MESSAGES[code]) || `일시적인 문제가 생겼어요. (오류 ${status}) ${RETRY}`;
}

/** 요청 단계에서 난 오류(시간 초과·연결 실패)를 화면 문구로 바꾼다. */
export function requestErrorMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === 'AbortError') {
    return `답변이 ${ASK_TIMEOUT_MS / 1000}초 넘게 오지 않았어요. ${RETRY}`;
  }
  if (err instanceof TypeError) return `답변 서버에 연결하지 못했어요. ${RETRY}`;
  return err instanceof Error ? err.message : `일시적인 문제가 생겼어요. ${RETRY}`;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isString = (value: unknown) => typeof value === 'string';
const isNullableString = (value: unknown) => value === null || isString(value);

export function isAskResponse(value: unknown): value is AskResponse {
  if (!isObject(value)) return false;
  return (value.status === 'answered' || value.status === 'no_evidence' || value.status === 'info')
    && isString(value.answer) && typeof value.is_sample === 'boolean' && isNullableString(value.checked_at)
    && Array.isArray(value.citations) && value.citations.every((item: unknown) =>
      isObject(item) && isString(item.title) && isString(item.url)
      && (item.document_id === undefined || isString(item.document_id))
      && (item.page_number == null || Number.isInteger(item.page_number)))
    && Array.isArray(value.policy_checks) && value.policy_checks.every((item: unknown) =>
      isObject(item) && isString(item.policy_id) && isString(item.policy_name) && isString(item.agency)
      && isString(item.source_url) && isNullableString(item.official_apply_url)
      && (item.reception_status === 'before' || item.reception_status === 'open'
        || item.reception_status === 'closed' || item.reception_status === 'unknown')
      && Array.isArray(item.conditions) && item.conditions.every((condition: unknown) =>
        isObject(condition) && isString(condition.field) && isString(condition.label) && isString(condition.reason)
        && (condition.status === 'eligible' || condition.status === 'ineligible' || condition.status === 'needs_check')))
    && Array.isArray(value.follow_up_questions) && value.follow_up_questions.every((item: unknown) =>
      isObject(item) && isString(item.text) && (item.kind === 'question' ? item.field == null || isString(item.field)
        : item.kind === 'ask_info' && (item.field === 'age' || item.field === 'region' || item.field === 'income')))
    && (value.notice == null || (isObject(value.notice) && (value.notice.kind === 'info' || value.notice.kind === 'warn')
      && isString(value.notice.title) && isString(value.notice.text)));
}

/**
 * 질문을 보내고 답변을 받는다.
 * 실패하면 화면에 그대로 보여 줄 문장(이유 + 다음 행동)을 담은 Error를 던지고, 화면은 오류 상태와 '다시 시도'를 보여 준다.
 */
export async function askPolicy(request: AskRequest): Promise<AskResponse> {
  if (usingSample) return mockAsk(request);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ASK_TIMEOUT_MS);
  try {
    const res = await fetch(ASK_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) {
      const body: unknown = await res.json().catch(() => null);
      throw new Error(serverErrorMessage(res.status, body));
    }
    const body: unknown = await res.json().catch((error: unknown) => {
      if (error instanceof SyntaxError) return null;
      throw error;
    });
    if (!isAskResponse(body)) throw new Error(`답변 형식을 확인하지 못했어요. ${RETRY}`);
    return body;
  } catch (err) {
    throw new Error(requestErrorMessage(err));
  } finally {
    clearTimeout(timer);
  }
}
