import { expect, it } from 'vitest';
import type { ConditionResult } from '../api/types';
import { andGroupResult, hasOnlyAndGroups, isGroupRow, isUnjudgedOnly, matchesExclusion } from './conditionGroups';

const row = (reason: string, field = 'eligibility'): ConditionResult => ({ field, label: '자격 요건', status: 'needs_check', reason });
const AND = row('모든 조건을 충족해야 합니다(종합 결과). 일부 조건이 검증되지 않아 종합 판정을 보류합니다.');
const OR = row('조건 중 하나 이상 충족해야 합니다(종합 결과).');
const NOT = row('해당 조건이 충족되지 않아야 합니다(종합 결과).');
const age = row('만 9~24세 기준에 입력한 만 30세가 포함되지 않습니다.', 'age');

it('recognizes group rows even when the checker appends a hold reason', () => {
  expect([AND, OR, NOT].every(isGroupRow)).toBe(true);
  expect(isGroupRow(age)).toBe(false);
  expect(isGroupRow(row('UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.'))).toBe(false);
});

it('confirms only AND-only structures and treats anything unclear as not confirmed', () => {
  expect(hasOnlyAndGroups([age, AND])).toBe(true);
  expect(hasOnlyAndGroups([age, OR, AND])).toBe(false);
  expect(hasOnlyAndGroups([age, NOT, AND])).toBe(false);
  expect(hasOnlyAndGroups([age])).toBe(false);
  expect(hasOnlyAndGroups([age, AND, row('여러 루트 조건 그룹의 결합 논리가 확인되지 않았습니다.')])).toBe(false);
  expect(hasOnlyAndGroups([age, row('UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.')])).toBe(false);
});

it('builds an AND summary row with the checker AND rule and recognizes it as AND', () => {
  const at = (status: ConditionResult['status']) => ({ ...age, status });
  expect(andGroupResult([at('eligible'), at('ineligible'), at('needs_check')]).status).toBe('ineligible');
  expect(andGroupResult([at('eligible'), at('needs_check')]).status).toBe('needs_check');
  expect(andGroupResult([at('eligible'), at('eligible')]).status).toBe('eligible');
  expect(andGroupResult([]).status).toBe('needs_check');
  expect(hasOnlyAndGroups([at('eligible'), andGroupResult([at('eligible')])])).toBe(true);
});

it('reports an exclusion match only when a NOT group row is ineligible', () => {
  expect(matchesExclusion([age, { ...NOT, status: 'ineligible' }, AND])).toBe(true);
  expect(matchesExclusion([age, NOT, AND])).toBe(false);
  expect(matchesExclusion([age, { ...NOT, status: 'eligible' }])).toBe(false);
  expect(matchesExclusion([age, { ...AND, status: 'ineligible' }, { ...OR, status: 'ineligible' }])).toBe(false);
});

it('treats a policy as unjudged only when every row says there is nothing to compare', () => {
  const unknown = row('UNKNOWN 조건 그룹은 자동 판정할 수 없습니다. 데이터 품질 경고로 자동 판정을 보류합니다.');
  expect(isUnjudgedOnly([unknown])).toBe(true);
  expect(isUnjudgedOnly([row('확인 가능한 자격 조건 구조가 없어 공식 공고 확인이 필요합니다.')])).toBe(true);
  // 사용자가 확인·입력할 수 있는 구체적인 행이 있으면 미판정으로 묶지 않는다.
  expect(isUnjudgedOnly([unknown, { ...age, status: 'needs_check' }])).toBe(false);
  expect(isUnjudgedOnly([age, AND])).toBe(false);
  expect(isUnjudgedOnly([row('조건 그룹의 내부 구조가 올바르지 않습니다.')])).toBe(false);
  expect(isUnjudgedOnly([])).toBe(false);
});
