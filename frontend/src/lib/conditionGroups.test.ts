import { expect, it } from 'vitest';
import type { ConditionResult } from '../api/types';
import { andGroupResult, hasOnlyAndGroups, isGroupRow } from './conditionGroups';

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
