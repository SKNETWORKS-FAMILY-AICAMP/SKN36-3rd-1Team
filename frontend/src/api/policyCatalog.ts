import type { PolicyListResponse } from './types';
import { ASK_API_URL, isPolicyCheck } from './client';
import { isoDay } from '../lib/applicationSchedule';
import { decodeEntitiesDeep } from '../lib/htmlEntities';

const PAGE_SIZE = 500;
const CATALOG_TIMEOUT_MS = 10_000;
const configured = (import.meta.env.VITE_POLICY_API_URL as string | undefined)?.trim();
export const POLICY_API_URL = configured || (ASK_API_URL ? new URL('policies', ASK_API_URL).href : '');
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string');
export function isPolicyListResponse(value: unknown): value is PolicyListResponse {
  if (!value || typeof value !== 'object') return false;
  const r = value as PolicyListResponse;
  return Array.isArray(r.items) && r.items.every(item => item && strings(item.regions) && strings(item.categories) && isPolicyCheck({ ...item, conditions: [] }))
    && Number.isInteger(r.total) && r.total >= 0 && Number.isInteger(r.offset) && r.offset >= 0
    && Number.isInteger(r.limit) && r.limit > 0 && r.items.length <= r.limit && r.offset + r.items.length <= r.total
    && typeof r.dataset_version === 'string' && !!r.dataset_version && /^[a-f0-9]{64}$/.test(r.manifest_sha256)
    && isoDay(r.evaluated_on) && r.source_kind === 'release_files' && strings(r.regions) && strings(r.categories);
}

/** 목록은 초기/날짜 변경/탭 복귀 때만 갱신한다. 필터·정렬·저장에는 호출하지 않는다. */
export async function loadPolicyCatalog(signal: AbortSignal, endpoint = POLICY_API_URL): Promise<PolicyListResponse> {
  if (!endpoint) throw new Error('정책 목록 주소가 설정되지 않았어요.');
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) controller.abort(); else signal.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, CATALOG_TIMEOUT_MS);
  let result: PolicyListResponse | null = null;
  try {
    do {
      const url = new URL(endpoint); url.searchParams.set('limit', String(PAGE_SIZE)); url.searchParams.set('offset', String(result?.items.length ?? 0));
      const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw new Error('정책 목록을 불러오지 못했어요. 다시 시도해 주세요.');
      const page: unknown = await response.json();
      if (!isPolicyListResponse(page) || (page.total > page.offset && !page.items.length)) throw new Error('정책 목록 형식을 확인하지 못했어요.');
      if (page.offset !== (result?.items.length ?? 0)) throw new Error('정책 목록 순서가 일치하지 않아요.');
      if (result && (page.manifest_sha256 !== result.manifest_sha256 || page.dataset_version !== result.dataset_version
        || page.evaluated_on !== result.evaluated_on || page.total !== result.total)) throw new Error('조회 중 정책 데이터가 바뀌었어요. 다시 시도해 주세요.');
      if (result !== null) result.items.push(...page.items);
      else result = page;
    } while (result.items.length < result.total);
    if (new Set(result.items.map(p => p.policy_id)).size !== result.total) throw new Error('정책 목록에 중복이 있어요.');
    // 정책 원문의 HTML 문자 코드(&#9312; 등)를 글자로 바꿔 보여 준다(데이터는 그대로)
    return decodeEntitiesDeep(result);
  } finally { clearTimeout(timer); signal.removeEventListener('abort', abort); }
}
