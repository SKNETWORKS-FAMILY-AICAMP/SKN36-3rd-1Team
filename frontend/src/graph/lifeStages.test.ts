import { describe, expect, it } from 'vitest';
import { NODE_FILES, RELATIONSHIP_FILE } from '../config/graph';
import { buildGraphIndex } from './graphModel';
import {
  formatAgeRange, groupByCategory, lifeStageSummary, parseAgeRange, policyLifeStages, stageForAge,
} from './lifeStages';
import { loadGraphDataset } from './loadGraph';

const jsonl = (rows: object[]) => rows.map((r) => JSON.stringify(r)).join('\n');

/** 가상 그래프 (sample_only): 청년 정책 2개(하나만 나이 조건), 생애단계 없는 정책 1개 */
function sampleIndex() {
  const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
  files['nodes_policy.jsonl'] = jsonl([
    { policy_id: 'P1', policy_name: '가상 청년 월세', category: '주거' },
    { policy_id: 'P2', policy_name: '가상 청년 교육', category: '교육' },
    { policy_id: 'P3', policy_name: '가상 미분류 정책', category: '복지' },
  ]);
  files['nodes_lifestage.jsonl'] = jsonl([
    { stage_id: 'LIFESTAGE_청년', name: '청년', age_range: '19~39세' },
    { stage_id: 'LIFESTAGE_노년', name: '노년', age_range: '65세 이상' },
  ]);
  files['nodes_eligibility_condition.jsonl'] = jsonl([
    { condition_id: 'COND_AGE_19_34', type: 'AGE', value: '19~34' },
    { condition_id: 'COND_INC_1', type: 'INCOME', value: '0~50' },
  ]);
  files[RELATIONSHIP_FILE] = jsonl([
    { source_id: 'P1', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_청년' },
    { source_id: 'P1', relation: 'REQUIRES_CONDITION', target_id: 'COND_AGE_19_34' },
    { source_id: 'P1', relation: 'REQUIRES_CONDITION', target_id: 'COND_INC_1' },
    { source_id: 'P2', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_청년' },
  ]);
  return buildGraphIndex(files);
}

describe('나이 표기', () => {
  it('데이터의 여러 표기를 읽고, 상한 120은 상한 없음으로 본다', () => {
    expect(parseAgeRange('0~5세')).toEqual({ min: 0, max: 5 });
    expect(parseAgeRange('65세 이상')).toEqual({ min: 65, max: null });
    expect(parseAgeRange('19~120')).toEqual({ min: 19, max: null });
    expect(parseAgeRange('알 수 없음')).toBeNull();
    expect(formatAgeRange({ min: 19, max: 34 })).toBe('만 19~34세');
    expect(formatAgeRange({ min: 19, max: null })).toBe('만 19세 이상');
    expect(formatAgeRange({ min: 0, max: null })).toBe('나이 제한 없음');
  });
});

describe('생애단계 묶기 (가상 그래프)', () => {
  const { stages, unclassified } = lifeStageSummary(sampleIndex());
  const youth = stages.find((s) => s.id === 'LIFESTAGE_청년')!;

  it('생애단계 관계로 정책을 묶고, 나이 조건은 AGE 조건만 붙인다', () => {
    // 정책 이름 가나다순: '가상 청년 교육'(P2) → '가상 청년 월세'(P1)
    expect(youth.policies.map((p) => p.policy.id)).toEqual(['P2', 'P1']);
    expect(youth.policies.find((p) => p.policy.id === 'P1')!.ages).toEqual([{ min: 19, max: 34 }]);
    expect(youth.policies.find((p) => p.policy.id === 'P2')!.ages).toEqual([]);
    expect(unclassified).toBe(1);
  });

  it('입력한 나이로 생애단계를 찾는다 (데이터의 나이 범위 기준)', () => {
    expect(stageForAge(stages, 29)).toBe('LIFESTAGE_청년');
    expect(stageForAge(stages, 70)).toBe('LIFESTAGE_노년');
    expect(stageForAge(stages, 50)).toBeNull(); // 가상 그래프에 중장년이 없다
    expect(stageForAge(stages, null)).toBeNull();
  });

  it('정책 하나의 생애단계 이름을 타임라인 순서로 준다 (데이터에 없는 정책은 빈 배열)', () => {
    const index = sampleIndex();
    expect(policyLifeStages(index, 'P1')).toEqual(['청년']);
    expect(policyLifeStages(index, 'P3')).toEqual([]);
    expect(policyLifeStages(index, 'POL_SAMPLE_A')).toEqual([]);
  });

  it('분야별로 묶고 정책이 많은 분야부터 보여 준다', () => {
    expect(groupByCategory(youth.policies).map((g) => g.category)).toEqual(['교육', '주거']);
  });
});

describe('팀 기준 데이터', () => {
  it('생애단계 정책 수가 그래프의 APPLIES_TO_LIFESTAGE 관계 수와 같고, 미분류와 합치면 전체 정책이 빠짐없이 들어간다', async () => {
    const { index } = await loadGraphDataset();
    const { stages, unclassified } = lifeStageSummary(index);
    const relations = [...index.outgoing.values()].flat().filter((e) => e.relation === 'APPLIES_TO_LIFESTAGE');
    expect(stages.reduce((sum, s) => sum + s.policies.length, 0)).toBe(relations.length);
    const classified = new Set(stages.flatMap((s) => s.policies.map((p) => p.policy.id)));
    expect(classified.size + unclassified).toBe(index.policies.length);
    expect(stages.every((s) => s.range)).toBe(true);
  });
});
