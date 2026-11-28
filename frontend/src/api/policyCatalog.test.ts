import { afterEach, expect, it, vi } from 'vitest';
import { isPolicyListResponse, loadPolicyCatalog } from './policyCatalog';
import type { PolicyListResponse } from './types';
import { isAskResponse } from './client';

const item = (id: string) => ({ policy_id: id, policy_name: 'sample_only', agency: '', regions: ['서울'], categories: ['주거'],
  source_url: 'https://example.org/', official_apply_url: null, reception_status: 'unknown' as const, application_schedule: null });
const page = (offset = 0): PolicyListResponse => ({ items: [item('POL_' + offset)], total: 2, offset, limit: 500,
  dataset_version: 'sample_only', manifest_sha256: 'a'.repeat(64), evaluated_on: '2026-11-22', source_kind: 'release_files', regions: ['서울'], categories: ['주거'] });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it('목록 페이지를 같은 버전으로 합치며 한 번씩만 요청한다', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => page() }).mockResolvedValueOnce({ ok: true, json: async () => page(1) });
  vi.stubGlobal('fetch', fetch);
  const result = await loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies');
  expect(result.items.map(x => x.policy_id)).toEqual(['POL_0', 'POL_1']); expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][0].searchParams.get('offset')).toBe('1');
});
it.each(['manifest_sha256', 'dataset_version', 'evaluated_on', 'total'] as const)('페이지 사이 %s 변경은 거부한다', async field => {
  const changed = { ...page(1), [field]: field === 'total' ? 3 : field === 'evaluated_on' ? '2026-11-23' : field === 'manifest_sha256' ? 'b'.repeat(64) : 'changed' };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => page() }).mockResolvedValueOnce({ ok: true, json: async () => changed }));
  await expect(loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies')).rejects.toThrow('데이터가 바뀌었어요');
});
it('받은 정책 글의 HTML 문자 코드를 글자로 바꾸고 주소는 그대로 둔다', async () => {
  const coded = { ...item('POL_0'), policy_name: '가상 정책 &lsquo;sample_only&rsquo;', source_url: 'https://example.org/?a=1&amp;b=2',
    requirement_excerpts: [{ label: '지원대상', text: '&#9312; 가상 조건', document_id: 'DOC_0', chunk_id: 'CHK_0', source_url: 'https://example.org/doc?a=1&amp;b=2' }] };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...page(), items: [coded], total: 1 }) }));
  const result = await loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies');
  expect(result.items[0].policy_name).toBe('가상 정책 ‘sample_only’');
  expect(result.items[0].source_url).toBe('https://example.org/?a=1&amp;b=2');
  expect(result.items[0].requirement_excerpts?.[0]).toMatchObject({ text: '① 가상 조건', source_url: 'https://example.org/doc?a=1&amp;b=2' });
});
it('중복 ID·진행하지 않는 페이지를 거부한다', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => page() }).mockResolvedValueOnce({ ok: true, json: async () => ({ ...page(1), items: [item('POL_0')] }) }));
  await expect(loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies')).rejects.toThrow('중복');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...page(), items: [] }) }));
  await expect(loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies')).rejects.toThrow('형식');
});
it('HTTP 오류 시 검색·생성 등 다른 요청으로 우회하지 않는다', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false }); vi.stubGlobal('fetch', fetch);
  await expect(loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies')).rejects.toThrow('불러오지 못했어요');
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('취소 신호는 fetch에 전달하고 외부 신호 리스너를 정리한다', async () => {
  const controller = new AbortController();
  const remove = vi.spyOn(controller.signal, 'removeEventListener');
  vi.stubGlobal('fetch', vi.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('abort', 'AbortError'))))));
  const result = loadPolicyCatalog(controller.signal, 'https://example.org/api/policies'); controller.abort();
  await expect(result).rejects.toThrow('abort'); expect(remove).toHaveBeenCalledTimes(1);
});
it('응답이 지연되면 10초에 취소하고 다음 요청을 만들지 않는다', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')))));
  vi.stubGlobal('fetch', fetch);
  const result = loadPolicyCatalog(new AbortController().signal, 'https://example.org/api/policies');
  const assertion = expect(result).rejects.toThrow('timeout');
  await vi.advanceTimersByTimeAsync(10_000); await assertion;
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('서류·공식 기준·출처 선택 필드와 이전 응답을 검증한다', () => {
  expect(isPolicyListResponse(page())).toBe(true);
  expect(isPolicyListResponse({ ...page(), evaluated_on: '2026-02-30' })).toBe(false);
  expect(isPolicyListResponse({ ...page(), items: [{ ...item('POL_A'), regions: 123 }] })).toBe(false);
  const p = { ...item('POL_A'), conditions: [], required_documents: [{ text: 'sample_only 서류', document_id: 'D', chunk_id: 'C', source_url: 'https://example.org/docs' }] };
  const r = { status: 'answered', answer: '가상 본문', citations: [], policy_checks: [p], follow_up_questions: [], checked_at: null, is_sample: true };
  expect(isAskResponse(r)).toBe(true);
  expect(isAskResponse({ ...r, policy_checks: [{ ...p, required_documents: [{ text: '서류' }] }] })).toBe(false);
});
