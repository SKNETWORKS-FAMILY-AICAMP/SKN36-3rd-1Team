import type { PolicyCheck } from '../api/types';
import { scheduleView } from './applicationSchedule';
import { safeHttpUrl } from './validate';

/** 정책 비교에 담을 수 있는 개수 */
export const COMPARE_MIN = 2;
export const COMPARE_MAX = 3;

/** 비교 대상: 정책 둘러보기 목록의 한 줄(목록 API 또는 배포 스냅샷에서 온 값) */
export interface CompareInput { policy: PolicyCheck; regions: string[]; categories: string[] }

/**
 * 배포 데이터(정책 노드)에 있는 공고 원문 요약 필드. 목록 API에는 없어서 관계 지도와 같은 배포 스냅샷에서 읽는다.
 * 값이 없으면 만들지 않고 '공식 원문에서 확인'으로 둔다.
 */
export interface PolicyDetail { benefit?: string; eligibility?: string; application_method?: string; target_groups?: string[] }

export type CompareTone = 'ok' | 'warn' | 'bad' | 'muted' | 'plain';
export interface CompareCell { text: string; tone: CompareTone }
export interface CompareRow { key: string; label: string; cells: CompareCell[]; /** 정책끼리 값이 다른지 */ differs: boolean }
export interface ChecklistItem { key: string; label: string }

/** 개인 자격은 목록 데이터에 없다. 판정을 만들거나 '충족'으로 표시하지 않는다. */
export const PERSONAL_CHECK_TEXT = '추가 확인 필요';
const CHECK_SOURCE = '공식 원문에서 확인 필요';

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
const list = (values: string[] | undefined | null) => (values ?? []).map(v => v.trim()).filter(Boolean);

/** 그래프 노드 data에서 비교에 쓰는 문자열 필드만 꺼낸다(외부 데이터라 형식을 다시 확인한다) */
export function detailFromNode(data: Record<string, unknown> | undefined): PolicyDetail {
  if (!data) return {};
  const groups = Array.isArray(data.target_groups) ? data.target_groups.filter((v): v is string => typeof v === 'string') : [];
  return { benefit: text(data.benefit), eligibility: text(data.eligibility), application_method: text(data.application_method), target_groups: groups };
}

function receptionTone(status: string): CompareTone {
  return status === 'open' ? 'ok' : status === 'closed' ? 'bad' : status === 'before' ? 'muted' : 'warn';
}

/** 선택한 정책 2~3개를 같은 항목 기준으로 나란히 놓는다. 원본 배열은 바꾸지 않는다. */
export function buildComparison(inputs: CompareInput[], details: Map<string, PolicyDetail>, today: string) {
  const views = inputs.map(({ policy }) => scheduleView(policy, today));
  const rows: CompareRow[] = [];
  const add = (key: string, label: string, cells: CompareCell[]) => {
    rows.push({ key, label, cells, differs: cells.some(cell => cell.text !== cells[0].text) });
  };
  const fromDetail = (pick: (d: PolicyDetail) => string) => inputs.map(({ policy }) => {
    const value = pick(details.get(policy.policy_id) ?? {});
    return value ? { text: value, tone: 'plain' as const } : { text: CHECK_SOURCE, tone: 'muted' as const };
  });

  add('reception', '접수 상태', views.map(v => ({ text: v.badge ? `${v.label} · ${v.badge}` : v.label, tone: receptionTone(v.status) })));
  add('period', '신청 기간', views.map(v => (v.period && v.period !== v.label
    ? { text: v.period, tone: 'plain' as const } : { text: CHECK_SOURCE, tone: 'muted' as const })));
  add('personal', '내 조건 판정', inputs.map(() => ({ text: PERSONAL_CHECK_TEXT, tone: 'warn' as const })));
  add('benefit', '지원 내용', fromDetail(d => d.benefit ?? ''));
  add('eligibility', '지원 대상 (공고 기준)', fromDetail(d => d.eligibility ?? ''));
  add('target', '대상 그룹', fromDetail(d => list(d.target_groups).join(' · ')));
  add('region', '지역', inputs.map(({ regions }) => (list(regions).length
    ? { text: list(regions).join(' · '), tone: 'plain' as const } : { text: CHECK_SOURCE, tone: 'muted' as const })));
  add('category', '분야', inputs.map(({ categories }) => (list(categories).length
    ? { text: list(categories).join(' · '), tone: 'plain' as const } : { text: CHECK_SOURCE, tone: 'muted' as const })));
  add('agency', '담당 기관', inputs.map(({ policy }) => (text(policy.agency)
    ? { text: text(policy.agency), tone: 'plain' as const } : { text: CHECK_SOURCE, tone: 'muted' as const })));
  add('method', '신청 방법', fromDetail(d => d.application_method ?? ''));
  add('documents', '필요 서류', inputs.map(({ policy }) => (policy.required_documents?.length
    ? { text: `원문 발췌 ${policy.required_documents.length}건`, tone: 'plain' as const } : { text: '확인된 서류 목록 없음 · ' + CHECK_SOURCE, tone: 'muted' as const })));
  add('contact', '문의처', inputs.map(({ policy }) => (policy.contacts?.length
    ? { text: policy.contacts.map(c => [c.agency, c.phone].filter(Boolean).join(' ')).join(' / '), tone: 'plain' as const }
    : { text: CHECK_SOURCE, tone: 'muted' as const })));

  const counts = { open: 0, soon: 0, unknown: 0 };
  for (const v of views) {
    if (v.status === 'open') counts.open++;
    if (v.status === 'open' && !v.ongoing && v.remaining !== null && v.remaining >= 0 && v.remaining <= 7) counts.soon++;
    if (v.status === 'unknown') counts.unknown++;
  }
  const checklists = inputs.map(({ policy }, i) => checklistFor(policy, views[i].status));
  return { rows, counts, checklists };
}

/** 사용자가 공고에서 직접 확인해야 하는 항목. 데이터가 확인된 항목은 빼고, 개인 조건 확인은 항상 남긴다. */
export function checklistFor(policy: PolicyCheck, status: string): ChecklistItem[] {
  const items: ChecklistItem[] = [{ key: 'personal', label: '내 조건(나이·지역·소득 등) 확인' }];
  if (status === 'unknown') items.push({ key: 'schedule', label: '현재 신청 일정 확인' });
  if (!policy.required_documents?.length) items.push({ key: 'documents', label: '필요 서류 확인' });
  if (!safeHttpUrl(policy.official_apply_url)) items.push({ key: 'apply', label: '신청 주소·방법 확인' });
  return items;
}
