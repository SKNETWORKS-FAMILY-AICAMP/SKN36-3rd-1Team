import { describe, expect, it } from 'vitest';
import { NODE_FILES, RELATIONSHIP_FILE } from '../config/graph';
import { buildGraphIndex } from './graphModel';
import {
  formatAgeRange, groupByCategory, lifeStageSummary, limitedAgeRanges, parseAgeRange, policyAgeRanges, policyLifeStages,
  policyStageIds, stageForAge,
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

describe('나이 기준으로 생애단계 정하기 (가상 그래프)', () => {
  /** 단계 6개와, 데이터 분류가 나이 기준과 어긋난 정책들 (sample_only) */
  function ageIndex() {
    const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
    files['nodes_policy.jsonl'] = jsonl([
      { policy_id: 'A', policy_name: '가상 6~16세 정책' },
      { policy_id: 'B', policy_name: '가상 18세 이상 정책' },
      { policy_id: 'C', policy_name: '가상 나이 제한 없음 정책' },
      { policy_id: 'D', policy_name: '가상 나이 조건 없는 정책' },
    ]);
    files['nodes_lifestage.jsonl'] = jsonl([
      ['영유아', '0~5세'], ['아동', '6~12세'], ['청소년', '13~18세'], ['청년', '19~39세'], ['중장년', '40~64세'], ['노년', '65세 이상'],
    ].map(([name, age_range]) => ({ stage_id: `LIFESTAGE_${name}`, name, age_range })));
    files['nodes_eligibility_condition.jsonl'] = jsonl([
      { condition_id: 'AGE_6_16', type: 'AGE', value: '6~16' },
      { condition_id: 'AGE_18_120', type: 'AGE', value: '18~120' },
      { condition_id: 'AGE_0_120', type: 'AGE', value: '0~120' },
    ]);
    files[RELATIONSHIP_FILE] = jsonl([
      { source_id: 'A', relation: 'REQUIRES_CONDITION', target_id: 'AGE_6_16' },
      { source_id: 'A', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_영유아' },
      { source_id: 'B', relation: 'REQUIRES_CONDITION', target_id: 'AGE_18_120' },
      { source_id: 'B', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_청년' },
      { source_id: 'C', relation: 'REQUIRES_CONDITION', target_id: 'AGE_0_120' },
      { source_id: 'C', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_노년' },
      { source_id: 'D', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'LIFESTAGE_중장년' },
    ]);
    return buildGraphIndex(files);
  }

  it('나이 기준이 있으면 데이터 분류 대신 그 나이와 겹치는 단계에 넣는다', () => {
    const index = ageIndex();
    expect(policyLifeStages(index, 'A')).toEqual(['아동', '청소년']); // 영유아 연결은 나이 기준 밖이라 빠진다
    expect(policyLifeStages(index, 'B')).toEqual(['청소년', '청년', '중장년', '노년']);
  });

  it("나이 제한이 없거나('0~120') 나이 조건이 없으면 데이터 분류를 그대로 따른다", () => {
    const index = ageIndex();
    expect(policyLifeStages(index, 'C')).toEqual(['노년']);
    expect(policyLifeStages(index, 'D')).toEqual(['중장년']);
  });

  it('타임라인 목록도 같은 기준으로 묶는다', () => {
    const { stages, unclassified } = lifeStageSummary(ageIndex());
    const names = (name: string) => stages.find((s) => s.name === name)!.policies.map((p) => p.policy.id);
    expect(names('영유아')).toEqual([]);
    expect(names('아동')).toEqual(['A']);
    expect(names('노년').sort()).toEqual(['B', 'C']);
    expect(unclassified).toBe(0);
  });
});

describe('팀 기준 데이터', () => {
  it('나이 기준이 있는 정책은 그 나이와 겹치는 단계에만, 나머지는 데이터 분류대로 들어가고 빠지는 정책이 없다', async () => {
    const { index } = await loadGraphDataset();
    const { stages, unclassified } = lifeStageSummary(index);
    expect(stages.every((s) => s.range)).toBe(true);
    for (const policy of index.policies) {
      const ages = limitedAgeRanges(policyAgeRanges(index, policy.id));
      const placed = stages.filter((s) => s.policies.some((p) => p.policy.id === policy.id)).map((s) => s.id);
      expect(placed).toEqual(policyStageIds(index, policy.id));
      if (ages.length === 0) continue;
      for (const stage of stages) {
        const fits = ages.some((a) => a.min <= (stage.range!.max ?? Infinity) && stage.range!.min <= (a.max ?? Infinity));
        expect(placed.includes(stage.id)).toBe(fits);
      }
    }
    const classified = new Set(stages.flatMap((s) => s.policies.map((p) => p.policy.id)));
    expect(classified.size + unclassified).toBe(index.policies.length);
  });

  it('공식 나이 기준 밖의 단계에 들어갔던 정책이 그 단계에서 빠진다', async () => {
    const { index } = await loadGraphDataset();
    expect(policyLifeStages(index, 'POL_GOV_049')).not.toContain('영유아'); // 열린교육바우처 6~16세
    expect(policyLifeStages(index, 'POL_BOK_018')).not.toContain('노년'); // 가사·간병 방문 0~64세
    const activity = policyLifeStages(index, 'POL_GOV_023'); // 장애인 활동지원(추가) 6~64세
    expect(activity).not.toContain('영유아');
    expect(activity).not.toContain('노년');
  });
});
