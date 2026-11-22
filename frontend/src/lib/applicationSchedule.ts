import type { ApplicationSchedule, PolicyCheck, ReceptionStatus } from '../api/types';

export const receptionLabels: Record<ReceptionStatus, string> = {
  before: '접수 예정', open: '접수 중', closed: '접수 마감', unknown: '모집 상태 미확인',
};
export type ScheduleFilter = 'all' | 'open' | 'soon' | 'before' | 'ongoing';
export const kstDay = (now = new Date()) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(now);
export const isoDay = (value: unknown): value is string => typeof value === 'string'
  && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
export function isApplicationSchedule(value: unknown): value is ApplicationSchedule {
  if (!value || typeof value !== 'object') return false;
  const s = value as ApplicationSchedule;
  return isoDay(s.evaluated_on) && (s.start_date === null || isoDay(s.start_date))
    && (s.end_date === null || isoDay(s.end_date)) && typeof s.is_ongoing === 'boolean'
    && (s.days_to_start === null || Number.isInteger(s.days_to_start))
    && (s.days_to_end === null || Number.isInteger(s.days_to_end))
    && !(s.is_ongoing && s.end_date) && !(s.start_date && s.end_date && s.start_date > s.end_date);
}

/** 접수 판정은 Checker 결과를 사용한다. 지난 날짜의 결과는 재판정하지 않고 재확인을 요청한다. */
export function scheduleView(p: PolicyCheck, today: string) {
  const s = p.application_schedule;
  // 기존 API는 일정 선택 필드 없이 접수 상태만 제공한다. 상태는 보존하되 날짜는 만들지 않는다.
  if (!s) return { status: p.reception_status, label: p.reception_status === 'unknown' ? '일정 확인 필요' : receptionLabels[p.reception_status], badge: '', period: '일정 확인 필요', ongoing: false, remaining: null as number | null };
  if (!isApplicationSchedule(s) || s.evaluated_on !== today) {
    return { status: 'unknown' as const, label: '일정 재확인 필요', badge: '', period: '이전 접수 안내입니다. 다시 질문하거나 공식 공고에서 현재 일정을 확인해 주세요.', ongoing: false, remaining: null };
  }
  const matchingDays = (date: string | null, days: number | null) => date === null ? days === null
    : days === (Date.parse(date) - Date.parse(s.evaluated_on)) / (24 * 60 * 60 * 1000);
  const datesMatch = matchingDays(s.start_date, s.days_to_start) && matchingDays(s.end_date, s.days_to_end);
  const consistent = !datesMatch ? false : p.reception_status === 'before' ? s.days_to_start !== null && s.days_to_start > 0
    : p.reception_status === 'open' ? (s.days_to_start === null || s.days_to_start <= 0) && (s.is_ongoing || (s.days_to_end !== null && s.days_to_end >= 0))
    : p.reception_status === 'closed' ? s.days_to_end !== null && s.days_to_end < 0 : false;
  if (!consistent) return { status: 'unknown' as const, label: '일정 확인 필요', badge: '', period: '접수 상태와 일정이 일치하지 않아 공식 공고 확인이 필요해요.', ongoing: false, remaining: null };
  const badge = p.reception_status === 'before' ? `시작 D-${s.days_to_start}`
    : p.reception_status === 'closed' ? '' : s.is_ongoing ? '상시 접수'
    : s.days_to_end === 0 ? '오늘 마감' : `마감 D-${s.days_to_end}`;
  const period = s.start_date && s.end_date ? `${s.start_date} ~ ${s.end_date}`
    : s.is_ongoing ? `${s.start_date ? s.start_date + '부터 · ' : ''}상시 접수`
    : s.end_date ? `${s.end_date}까지 (시작일 미확인)` : s.start_date ? `${s.start_date}부터 (종료일 미확인)` : '일정 확인 필요';
  return { status: p.reception_status, label: receptionLabels[p.reception_status], badge, period, ongoing: s.is_ongoing, remaining: s.days_to_end };
}

export function scheduledPolicies(policies: PolicyCheck[], today: string, filter: ScheduleFilter, sort: 'deadline' | 'name') {
  const rows = policies.map(policy => ({ policy, schedule: scheduleView(policy, today) }));
  const selected = rows.filter(({ schedule: s }) => filter === 'all' || (filter === 'soon'
    ? s.status === 'open' && !s.ongoing && s.remaining !== null && s.remaining >= 0 && s.remaining <= 7
    : filter === 'ongoing' ? s.status === 'open' && s.ongoing : s.status === filter));
  return selected.sort((a, b) => {
    if (sort === 'deadline') {
      const rank = (s: typeof a.schedule) => s.status === 'open' && !s.ongoing && s.remaining !== null ? 0 : s.status === 'before' ? 1 : s.status === 'open' ? 2 : s.status === 'unknown' ? 3 : 4;
      const order = rank(a.schedule) - rank(b.schedule);
      if (order) return order;
      if (rank(a.schedule) === 0 && a.schedule.remaining !== b.schedule.remaining) return a.schedule.remaining! - b.schedule.remaining!;
    }
    return a.policy.policy_name.localeCompare(b.policy.policy_name, 'ko') || a.policy.policy_id.localeCompare(b.policy.policy_id);
  });
}
