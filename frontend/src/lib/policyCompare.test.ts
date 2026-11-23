import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { PolicyCheck } from '../api/types';
import { PolicyCompare } from '../components/PolicyCompare';
import { buildComparison, checklistFor, detailFromNode, PERSONAL_CHECK_TEXT, type CompareInput, type PolicyDetail } from './policyCompare';

// 모든 값은 sample_only 가상 데이터다.
const day = '2026-11-22';
const policy = (id: string, patch: Partial<PolicyCheck> = {}): PolicyCheck => ({
  policy_id: id, policy_name: `가상 정책 ${id} (sample_only)`, agency: '가상 기관', reception_status: 'unknown',
  application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null, conditions: [], ...patch,
});
const input = (p: PolicyCheck, regions = ['서울특별시'], categories = ['주거·금융']): CompareInput => ({ policy: p, regions, categories });
const open = policy('A', {
  reception_status: 'open', official_apply_url: 'https://example.org/apply',
  application_schedule: { evaluated_on: day, start_date: '2026-11-01', end_date: '2026-11-25', is_ongoing: false, days_to_start: -21, days_to_end: 3 },
});
const details = new Map<string, PolicyDetail>([['A', { benefit: '가상 월 10만 원', eligibility: '가상 대상 A', target_groups: ['청년'] }]]);

it('개인 판정은 만들지 않고 모든 정책을 추가 확인 필요로 둔다', () => {
  const { rows } = buildComparison([input(open), input(policy('B'))], details, day);
  const personal = rows.find(r => r.key === 'personal')!;
  expect(personal.cells.map(c => c.text)).toEqual([PERSONAL_CHECK_TEXT, PERSONAL_CHECK_TEXT]);
  const all = rows.flatMap(r => r.cells.map(c => c.text)).join('\n');
  expect(all).not.toMatch(/충족|적합|조건상/);
});

it('목록·공고 요약에 있는 값만 쓰고, 없는 값은 원문 확인으로 둔다', () => {
  const { rows } = buildComparison([input(open), input(policy('B'), [], [])], details, day);
  const cell = (key: string, i: number) => rows.find(r => r.key === key)!.cells[i].text;
  expect(cell('reception', 0)).toBe('접수 중 · 마감 D-3');
  expect(cell('period', 0)).toBe('2026-11-01 ~ 2026-11-25');
  expect(cell('benefit', 0)).toBe('가상 월 10만 원');
  expect(cell('benefit', 1)).toBe('공식 원문에서 확인 필요');
  expect(cell('region', 1)).toBe('공식 원문에서 확인 필요');
  expect(cell('period', 1)).toBe('공식 원문에서 확인 필요');
});

it('차이점 표시는 값이 다른 행에만 붙고 원본 배열은 바꾸지 않는다', () => {
  const inputs = [input(policy('B')), input(policy('C'))];
  const before = JSON.stringify(inputs);
  const { rows, counts } = buildComparison(inputs, new Map(), day);
  expect(rows.find(r => r.key === 'agency')!.differs).toBe(false);
  expect(rows.find(r => r.key === 'personal')!.differs).toBe(false);
  expect(counts).toEqual({ open: 0, soon: 0, unknown: 2 });
  expect(JSON.stringify(inputs)).toBe(before);
});

it('체크리스트는 확인된 항목을 빼고 개인 조건 확인은 항상 남긴다', () => {
  expect(checklistFor(open, 'open').map(i => i.key)).toEqual(['personal', 'documents']);
  expect(checklistFor(policy('B'), 'unknown').map(i => i.key)).toEqual(['personal', 'schedule', 'documents', 'apply']);
});

it('배포 노드의 외부 값은 문자열만 꺼낸다', () => {
  expect(detailFromNode({ benefit: ' 가상 ', target_groups: ['청년', 3], eligibility: 1 })).toEqual({ benefit: '가상', eligibility: '', application_method: '', target_groups: ['청년'] });
  expect(detailFromNode(undefined)).toEqual({});
});

it('비교 화면은 고른 정책 이름·요약·체크리스트를 보여 주고 충족 표시를 하지 않는다', () => {
  const html = renderToStaticMarkup(createElement(PolicyCompare, {
    inputs: [input(open), input(policy('B'))], onBack: () => {}, onAsk: () => {}, initialDetails: details,
  }));
  expect(html).toContain('가상 정책 A (sample_only)');
  expect(html).toContain('가상 정책 B (sample_only)');
  expect(html).toContain('차이점만 보기');
  expect(html).toContain('내 조건 판정 · 모두 추가 확인 필요');
  expect(html).toContain('내 조건(나이·지역·소득 등) 확인');
  expect(html).toContain('내 조건으로 확인하기');
  expect(html).not.toMatch(/충족|조건상 적합/);
});
