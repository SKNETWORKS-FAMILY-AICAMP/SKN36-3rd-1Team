import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { PolicyCheck } from '../api/types';
import { PolicyCompare } from '../components/PolicyCompare';
import {
  buildComparison, checklistFor, detailFromNode, MISSING_TEXT, orderByDeadline, PERSONAL_UNCHECKED_TEXT,
  type CompareInput, type ConsultedCheck, type PolicyDetail,
} from './policyCompare';
import { andGroupResult } from './conditionGroups';

// 모든 값은 sample_only 가상 데이터다.
const day = '2026-11-22';
const policy = (id: string, patch: Partial<PolicyCheck> = {}): PolicyCheck => ({
  policy_id: id, policy_name: `가상 정책 ${id} (sample_only)`, agency: '가상 기관', reception_status: 'unknown',
  application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null, conditions: [], ...patch,
});
const input = (p: PolicyCheck, regions = ['서울특별시'], categories = ['주거·금융']): CompareInput => ({ policy: p, regions, categories });
const schedule = (daysToEnd: number) => ({ evaluated_on: day, start_date: '2026-11-01', end_date: `2026-11-${22 + daysToEnd}`, is_ongoing: false, days_to_start: -21, days_to_end: daysToEnd });
const open = policy('A', { reception_status: 'open', official_apply_url: 'https://example.org/apply', application_schedule: schedule(3) });
const details = new Map<string, PolicyDetail>([['A', { benefit: '가상 월 10만 원', eligibility: '가상 대상 A', target_groups: ['청년'] }]]);
const render = (inputs: CompareInput[], consulted?: Map<string, ConsultedCheck>) => renderToStaticMarkup(createElement(PolicyCompare, {
  inputs, consulted, onBack: () => {}, onAsk: () => {}, initialDetails: details,
}));

it('상담하지 않은 정책은 판정을 만들지 않고 확인 전으로 둔다', () => {
  const { rows, counts } = buildComparison([input(open), input(policy('B'))], details, day);
  expect(rows.find(r => r.key === 'personal')!.cells.map(c => c.text)).toEqual([PERSONAL_UNCHECKED_TEXT, PERSONAL_UNCHECKED_TEXT]);
  expect(counts.consulted).toBe(0);
  expect(rows.flatMap(r => r.cells.map(c => c.text)).join('\n')).not.toMatch(/충족|적합|신청 가능|추천/);
});

it('목록·공고 요약에 있는 값만 쓰고, 없는 값은 데이터 미확인으로 둔다', () => {
  const { rows } = buildComparison([input(open), input(policy('B'), [], [])], details, day);
  const cell = (key: string, i: number) => rows.find(r => r.key === key)!.cells[i].text;
  expect(rows[0].key).toBe('benefit');
  expect(cell('reception', 0)).toBe('접수 중 · 마감 D-3');
  expect(cell('period', 0)).toBe('2026-11-01 ~ 2026-11-25');
  expect(cell('benefit', 0)).toBe('가상 월 10만 원');
  for (const key of ['benefit', 'region', 'period', 'documents']) expect(cell(key, 1)).toBe(MISSING_TEXT);
});

it('차이점 표시는 값이 다른 행에만 붙고 원본 배열은 바꾸지 않는다', () => {
  const inputs = [input(policy('B')), input(policy('C'))];
  const before = JSON.stringify(inputs);
  const { rows, counts } = buildComparison(inputs, new Map(), day);
  expect(rows.find(r => r.key === 'agency')!.differs).toBe(false);
  expect(counts).toEqual({ open: 0, soon: 0, unknown: 2, consulted: 0 });
  expect(JSON.stringify(inputs)).toBe(before);
});

it('카드 순서는 추천이 아니라 마감이 가까운 순이다', () => {
  const later = policy('L', { reception_status: 'open', application_schedule: schedule(6) });
  expect(orderByDeadline([input(policy('U')), input(later), input(open)], day).map(i => i.policy.policy_id)).toEqual(['A', 'L', 'U']);
});

it('체크리스트는 확인된 항목을 빼고, 상담 전이면 내 조건 확인을 남긴다', () => {
  expect(checklistFor(open, 'open').map(i => i.key)).toEqual(['personal', 'documents']);
  expect(checklistFor(policy('B'), 'unknown').map(i => i.key)).toEqual(['personal', 'schedule', 'documents', 'apply']);
  expect(checklistFor(open, 'open', true)[0].label).toBe('상담 결과의 추가 확인 조건 확인');
});

it('배포 노드의 외부 값은 문자열만 꺼낸다', () => {
  expect(detailFromNode({ benefit: ' 가상 ', target_groups: ['청년', 3], eligibility: 1 })).toEqual({ benefit: '가상', eligibility: '', application_method: '', target_groups: ['청년'] });
  expect(detailFromNode(undefined)).toEqual({});
});

it('비교 화면은 요약·정책 카드·접힌 상세 비교 순서로 보여 주고 확정 표현을 쓰지 않는다', () => {
  const html = render([input(policy('B')), input(open)]);
  const at = (text: string) => html.indexOf(text);
  expect(at('어떤 정책부터 확인할까요?')).toBeGreaterThan(-1);
  expect(at('가상 정책 A (sample_only)')).toBeLessThan(at('가상 정책 B (sample_only)'));
  expect(at('정책별 요약')).toBeLessThan(at('자세히 비교하기'));
  expect(html).toMatch(/<details class="compare-more"><summary>/);
  expect(html).toContain('내 조건으로 확인하기');
  expect(html).toContain('가상 월 10만 원');
  expect(html).not.toMatch(/신청 가능|추천해요|조건상 적합/);
});

it('상담한 정책은 그 Checker 결과로 요약하고, 추가 확인 조건을 다음 행동으로 준다', () => {
  const checked = policy('A', { conditions: [
    { field: 'age', label: '나이', status: 'eligible', reason: '가상 사유' },
    { field: 'income', label: '소득', status: 'needs_check', reason: '가상 사유' },
  ] });
  const html = render([input(open), input(policy('B'))], new Map([['A', { check: checked, tags: ['29세', '경기'] }]]));
  expect(html).toContain('최근 상담 기준 · 입력: 29세 · 경기');
  expect(html).toContain('추가 확인 필요');
  expect(html).toContain('소득 조건 확인하기');
  expect(html).toContain('<b>1/2개</b>');
  // 상담하지 않은 B는 여전히 확인 전
  expect(html).toContain(PERSONAL_UNCHECKED_TEXT);
});

it('불일치가 확정된 정책은 공식 자격기준 확인으로 안내한다', () => {
  const region = { field: 'region', label: '지역', status: 'ineligible', reason: '가상 사유' } as const;
  // AND 그룹만 있을 때만 불일치를 확정 표시한다(기존 정책 카드와 같은 규칙)
  const failed = policy('A', { conditions: [andGroupResult([region]), region] });
  const html = render([input(open), input(policy('B'))], new Map([['A', { check: failed, tags: [] }]]));
  expect(html).toContain('공식 자격기준 확인 ↗');
  expect(html).toContain('조건 불일치');
  // AND 그룹 구조가 없으면 같은 불일치라도 확정하지 않고 개별 비교로만 보여 준다
  const loose = render([input(open), input(policy('B'))], new Map([['A', { check: policy('A', { conditions: [region] }), tags: [] }]]));
  expect(loose).not.toContain('공식 자격기준 확인 ↗');
  expect(loose).toContain('개별 비교');
});
