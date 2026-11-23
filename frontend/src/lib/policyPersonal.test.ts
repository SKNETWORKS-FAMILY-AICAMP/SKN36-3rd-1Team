import { expect, it } from 'vitest';
import type { PolicyCheck } from '../api/types';
import { policyCalendar } from './policyCalendar';
import { scheduleView } from './applicationSchedule';
import { readSavedPolicies } from '../components/SavedPolicies';
import { catalogPolicies } from './policyCatalog';
import type { GraphIndex, GraphNode } from '../graph/graphModel';

const day = '2026-11-22';
const policy = (): PolicyCheck => ({ policy_id: 'sample_only', policy_name: '가상,정책;테스트\n마감',
  agency: '', source_url: 'https://example.org/notice', official_apply_url: null, conditions: [], reception_status: 'open',
  application_schedule: { evaluated_on: day, start_date: null, end_date: '2026-11-25', is_ongoing: false, days_to_start: null, days_to_end: 3 } });
it('캘린더는 확인된 날짜의 종일 일정과 다음 날 배타적 종료를 기록한다', () => {
  expect(scheduleView(policy(), day).period).toBe('2026-11-25까지 (시작일 미확인)');
  const data = policyCalendar(policy(), day, new Date('2026-11-22T01:00:00Z'))!;
  expect(data).toContain('DTSTART;VALUE=DATE:20261125\r\nDTEND;VALUE=DATE:20261126');
  expect(data).toContain('DTSTAMP:20261122T010000Z');
  expect(data).toContain('SUMMARY:가상\\,정책\\;테스트\\n마감 신청 마감');
  expect(data).toContain('URL:https://example.org/notice');
  expect(data).not.toContain('VALARM');
});
it('긴 한글과 다중바이트 문자를 훼손하지 않고 75바이트 이내로 접는다', () => {
  const p = policy(); p.policy_name = '한글😀'.repeat(60);
  const data = policyCalendar(p, day)!;
  expect(data.replace(/\r\n /g, '')).toContain(p.policy_name);
  for (const line of data.split('\r\n')) expect(new TextEncoder().encode(line).length).toBeLessThanOrEqual(75);
});
it.each(['unknown', 'closed'] as const)('%s 상태는 캘린더를 제공하지 않는다', status => {
  const p = policy(); p.reception_status = status; expect(policyCalendar(p, day)).toBeNull();
});
it('오래된 일정·상시·잘못된 날짜·남은 일수 충돌은 내보내지 않는다', () => {
  expect(policyCalendar(policy(), '2026-11-23')).toBeNull();
  for (const patch of [{ end_date: '2026-02-30' }, { days_to_end: 9 }, { is_ongoing: true }, { end_date: null }]) {
    const p = policy(); Object.assign(p.application_schedule!, patch); expect(policyCalendar(p, day)).toBeNull();
  }
});
it('월말·연말 마감의 다음 날을 올바르게 계산한다', () => {
  const p = policy(); Object.assign(p.application_schedule!, { evaluated_on: '2026-12-31', end_date: '2026-12-31', days_to_end: 0 });
  expect(policyCalendar(p, '2026-12-31')).toContain('DTEND;VALUE=DATE:20270101');
});
it('저장 데이터는 ID 중복·잘못된 행을 제거하고 민감한 부가 필드를 버린다', () => {
  const p = { ...policy(), income: 100, profile: { age: 25 } };
  const rows = readSavedPolicies(JSON.stringify([p, p, null, {}, { policy_id: '', policy_name: '빈 ID' }]));
  expect(rows).toEqual([{ policy_id: p.policy_id, policy_name: p.policy_name, source_url: p.source_url }]);
});
it.each([null, 'broken', '{}', 'null'])('손상된 저장 값 %s는 빈 목록으로 복구한다', raw => expect(readSavedPolicies(raw)).toEqual([]));
it('저장 링크의 스크립트 주소는 제거한다', () => {
  expect(readSavedPolicies(JSON.stringify([{ ...policy(), source_url: 'javascript:alert(1)' }]))[0].source_url).toBe('');
});
it('전체 목록은 그래프의 실제 ID·연결만 사용하고 접수·자격을 추정하지 않는다', () => {
  const p: GraphNode = { id: 'P', kind: 'policy', label: 'sample_only', data: { application_end: '2026-11-25', source_url: 'javascript:bad', agency: '기관' } };
  const region: GraphNode = { id: 'R', kind: 'region', label: '서울', data: {} };
  const index: GraphIndex = { policies: [p], nodes: new Map([['P', p], ['R', region]]), incoming: new Map(), edgeCount: 1,
    outgoing: new Map([['P', [{ source: 'P', target: 'R', relation: 'AVAILABLE_IN', evidenceDocumentId: null, evidenceLocation: null, reviewStatus: null }]]]) };
  const [row] = catalogPolicies(index);
  expect(row.regions).toEqual(['서울']); expect(row.policy.reception_status).toBe('unknown');
  expect(row.policy.conditions).toEqual([]); expect(row.policy.application_schedule).toBeNull(); expect(row.policy.source_url).toBe('');
});
