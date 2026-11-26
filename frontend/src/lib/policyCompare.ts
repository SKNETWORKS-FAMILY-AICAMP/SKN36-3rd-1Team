import type { PolicyCheck } from '../api/types';
import { scheduledPolicies, scheduleView } from './applicationSchedule';
import { safeHttpUrl } from './validate';

/** 정책 비교에 담을 수 있는 개수 */
export const COMPARE_MIN = 2;
export const COMPARE_MAX = 3;

/** 비교 대상: 정책 둘러보기 목록의 한 줄(목록 API 또는 배포 스냅샷에서 온 값) */
export interface CompareInput { policy: PolicyCheck; regions: string[]; categories: string[] }

/** 이 정책을 상담에서 확인한 가장 최근 결과(Checker 조건)와 그때 입력한 조건 태그. 비교 화면은 새로 판정하지 않는다. */
export interface ConsultedCheck { check: PolicyCheck; tags: string[] }

/**
 * 배포 데이터(정책 노드)에 있는 공고 원문 요약 필드. 목록 API에는 없어서 관계 지도와 같은 배포 스냅샷에서 읽는다.
 * 값이 없으면 만들지 않고 '데이터 미확인'으로 둔다.
 */
export interface PolicyDetail { benefit?: string; eligibility?: string; application_method?: string; target_groups?: string[] }

export type CompareTone = 'ok' | 'warn' | 'bad' | 'muted' | 'plain';
export interface CompareCell { text: string; tone: CompareTone }
export interface CompareRow { key: string; label: string; cells: CompareCell[]; /** 정책끼리 값이 다른지 */ differs: boolean }
export interface ChecklistItem { key: string; label: string }

/** 상담에서 아직 확인하지 않은 정책의 개인 조건 상태. 판정을 만들거나 '충족'으로 표시하지 않는다. */
export const PERSONAL_UNCHECKED_TEXT = '아직 내 조건 확인 전';
/** 목록·공고 요약 데이터에 값이 없을 때. '조건이 없다'는 뜻이 아니다. */
export const MISSING_TEXT = '데이터 미확인';

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
const list = (values: string[] | undefined | null) => (values ?? []).map(v => v.trim()).filter(Boolean);
const missing: CompareCell = { text: MISSING_TEXT, tone: 'muted' };
const plain = (value: string): CompareCell => (value ? { text: value, tone: 'plain' } : missing);

/** 그래프 노드 data에서 비교에 쓰는 문자열 필드만 꺼낸다(외부 데이터라 형식을 다시 확인한다) */
export function detailFromNode(data: Record<string, unknown> | undefined): PolicyDetail {
  if (!data) return {};
  const groups = Array.isArray(data.target_groups) ? data.target_groups.filter((v): v is string => typeof v === 'string') : [];
  return { benefit: text(data.benefit), eligibility: text(data.eligibility), application_method: text(data.application_method), target_groups: groups };
}

/** 카드·표 순서: 추천 순위가 아니라 사실 기준(마감이 가까운 순, 같으면 이름순)으로만 정렬한다. */
export function orderByDeadline(inputs: CompareInput[], today: string): CompareInput[] {
  const byId = new Map(inputs.map(input => [input.policy.policy_id, input]));
  return scheduledPolicies(inputs.map(input => input.policy), today, 'all', 'deadline').map(({ policy }) => byId.get(policy.policy_id)!);
}

function receptionTone(status: string): CompareTone {
  return status === 'open' ? 'ok' : status === 'closed' ? 'bad' : status === 'before' ? 'muted' : 'warn';
}

/**
 * 선택한 정책 2~3개를 같은 항목 기준으로 나란히 놓는다. 원본 배열은 바꾸지 않는다.
 * personal: 상담에서 확인한 정책의 Checker 결과 요약(화면 쪽에서 기존 카드와 같은 규칙으로 만든다). 없으면 '확인 전'.
 */
export function buildComparison(inputs: CompareInput[], details: Map<string, PolicyDetail>, today: string, personal: Map<string, CompareCell> = new Map()) {
  const views = inputs.map(({ policy }) => scheduleView(policy, today));
  const rows: CompareRow[] = [];
  const add = (key: string, label: string, cells: CompareCell[]) => {
    rows.push({ key, label, cells, differs: cells.some(cell => cell.text !== cells[0].text) });
  };
  const fromDetail = (pick: (d: PolicyDetail) => string) => inputs.map(({ policy }) => plain(pick(details.get(policy.policy_id) ?? {})));

  add('benefit', '지원 내용', fromDetail(d => d.benefit ?? ''));
  add('reception', '접수 상태', views.map(v => ({ text: v.badge ? `${v.label} · ${v.badge}` : v.label, tone: receptionTone(v.status) })));
  add('period', '신청 기간', views.map(v => plain(v.period && v.period !== v.label ? v.period : '')));
  add('personal', '내 조건', inputs.map(({ policy }) => personal.get(policy.policy_id) ?? { text: PERSONAL_UNCHECKED_TEXT, tone: 'muted' }));
  add('eligibility', '지원 대상 (공고 기준)', fromDetail(d => d.eligibility ?? ''));
  add('target', '대상 그룹', fromDetail(d => list(d.target_groups).join(' · ')));
  add('region', '지역', inputs.map(({ regions }) => plain(list(regions).join(' · '))));
  add('category', '분야', inputs.map(({ categories }) => plain(list(categories).join(' · '))));
  add('agency', '담당 기관', inputs.map(({ policy }) => plain(text(policy.agency))));
  add('method', '신청 방법', fromDetail(d => d.application_method ?? ''));
  add('documents', '필요 서류', inputs.map(({ policy }) => plain(policy.required_documents?.length ? `원문 발췌 ${policy.required_documents.length}건` : '')));
  add('contact', '문의처', inputs.map(({ policy }) => plain(policy.contacts?.length
    ? policy.contacts.map(c => [c.agency, c.phone].filter(Boolean).join(' ')).join(' / ') : '')));

  const counts = { open: 0, soon: 0, unknown: 0, consulted: 0 };
  views.forEach((v, i) => {
    if (v.status === 'open') counts.open++;
    if (v.status === 'open' && !v.ongoing && v.remaining !== null && v.remaining >= 0 && v.remaining <= 7) counts.soon++;
    if (v.status === 'unknown') counts.unknown++;
    if (personal.has(inputs[i].policy.policy_id)) counts.consulted++;
  });
  const checklists = inputs.map(({ policy }, i) => checklistFor(policy, views[i].status, personal.has(policy.policy_id)));
  return { rows, counts, checklists, views };
}

/** 사용자가 공고에서 직접 확인해야 하는 항목. 데이터가 확인된 항목은 빼고, 개인 조건 확인은 상담 전이면 남긴다. */
export function checklistFor(policy: PolicyCheck, status: string, consulted = false): ChecklistItem[] {
  const items: ChecklistItem[] = [];
  items.push(consulted
    ? { key: 'personal', label: '상담 결과의 추가 확인 조건 확인' }
    : { key: 'personal', label: '내 조건(나이·지역·소득 등) 확인' });
  if (status === 'unknown') items.push({ key: 'schedule', label: '현재 신청 일정 확인' });
  if (!policy.required_documents?.length) items.push({ key: 'documents', label: '필요 서류 확인' });
  if (!safeHttpUrl(policy.official_apply_url)) items.push({ key: 'apply', label: '신청 주소·방법 확인' });
  return items;
}
