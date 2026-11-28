import type { PolicyCheck } from '../api/types';
import { DAY_MS, scheduleView } from './applicationSchedule';
import { safeHttpUrl } from './validate';

const escapeText = (text: string) => text.replace(/\\/g, '\\\\').replace(/\r\n|\r|\n/g, '\\n').replace(/;/g, '\\;').replace(/,/g, '\\,');
/** RFC 5545: UTF-8 문자를 쪼개지 않고 각 줄을 75바이트 이내로 접는다. */
function foldLine(line: string) {
  const lines: string[] = [];
  let part = '', size = 0;
  for (const char of line) {
    const bytes = new TextEncoder().encode(char).length;
    if (size + bytes > 75) { lines.push(part); part = ' '; size = 1; }
    part += char; size += bytes;
  }
  lines.push(part);
  return lines.join('\r\n');
}

/** 날짜만 있는 계약이므로 종일 일정으로 내보낸다. DTEND는 다음 날(배타적)이다. */
export function policyCalendar(policy: PolicyCheck, today: string, now = new Date()): string | null {
  const view = scheduleView(policy, today);
  const end = policy.application_schedule?.end_date;
  if (!end || !['open', 'before'].includes(view.status) || view.ongoing) return null;
  const next = new Date(Date.parse(end) + DAY_MS).toISOString().slice(0, 10);
  const url = safeHttpUrl(policy.source_url);
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SSOK//Policy deadlines//KO', 'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT', `UID:${encodeURIComponent(policy.policy_id)}-${end}@ssok.local`,
    `DTSTAMP:${now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')}`,
    `DTSTART;VALUE=DATE:${end.replace(/-/g, '')}`, `DTEND;VALUE=DATE:${next.replace(/-/g, '')}`,
    `SUMMARY:${escapeText(policy.policy_name + ' 신청 마감')}`,
    `DESCRIPTION:${escapeText('공식 공고에서 마감 시각과 변경 여부를 확인해 주세요. 알림은 캘린더 설정에 따릅니다.' + (url ? '\n' + url : ''))}`,
    ...(url ? [`URL:${url}`] : []), 'END:VEVENT', 'END:VCALENDAR'];
  return lines.map(foldLine).join('\r\n') + '\r\n';
}
