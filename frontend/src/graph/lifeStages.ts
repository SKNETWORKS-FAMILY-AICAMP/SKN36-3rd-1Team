/**
 * 생애주기 타임라인용 계산.
 * - 생애단계 소속: 그래프의 APPLIES_TO_LIFESTAGE 관계 (데이터의 분류. 넓게 붙은 정책도 있다)
 * - 나이 조건: 정책 → REQUIRES_CONDITION → type이 AGE인 조건 (실제 나이 기준이 확인된 정책만 있다)
 * 화면은 둘을 구분해서 보여 주고, 어느 쪽도 자격 확정으로 표현하지 않는다.
 */
import { AGE_NO_UPPER, LIFE_STAGES, SHARED_GROUP_DEFAULT, ungroupedLabel } from '../config/graph';
import type { GraphIndex, GraphNode } from './graphModel';
import { groupByField } from './relatedPolicies';

export interface AgeRange { min: number; max: number | null }

/** '0~5세', '19~39세', '65세 이상', '19~120' 같은 나이 표기를 읽는다. 못 읽으면 null */
export function parseAgeRange(text: unknown): AgeRange | null {
  if (typeof text !== 'string') return null;
  const range = text.match(/(\d{1,3})\s*~\s*(\d{1,3})/);
  if (range) {
    const max = Number(range[2]);
    return { min: Number(range[1]), max: max >= AGE_NO_UPPER ? null : max };
  }
  const over = text.match(/(\d{1,3})\s*세?\s*이상/);
  return over ? { min: Number(over[1]), max: null } : null;
}

export function formatAgeRange(r: AgeRange): string {
  if (r.max == null) return r.min === 0 ? '나이 제한 없음' : `만 ${r.min}세 이상`;
  return r.min === 0 ? `만 ${r.max}세 이하` : `만 ${r.min}~${r.max}세`;
}

export interface StagePolicy {
  policy: GraphNode;
  /** 확인된 나이 조건들 (없으면 빈 배열 = 생애단계 분류만 있음) */
  ages: AgeRange[];
}

export interface LifeStage {
  id: string;
  name: string;
  range: AgeRange | null;
  description: string | null;
  policies: StagePolicy[];
}

/** 정책의 나이 조건 (AGE 타입 조건만) */
export function policyAgeRanges(index: GraphIndex, policyId: string): AgeRange[] {
  return (index.outgoing.get(policyId) ?? [])
    .filter((e) => e.relation === 'REQUIRES_CONDITION')
    .map((e) => index.nodes.get(e.target))
    .filter((n): n is GraphNode => !!n && n.data.type === 'AGE')
    .map((n) => parseAgeRange(n.data.value))
    .filter((r): r is AgeRange => !!r);
}

/** 생애단계별 정책 목록과, 어느 단계에도 연결되지 않은 정책 수 */
export function lifeStageSummary(index: GraphIndex): { stages: LifeStage[]; unclassified: number } {
  const byStage = new Map<string, StagePolicy[]>(LIFE_STAGES.map((s) => [s.id, []]));
  let unclassified = 0;
  for (const policy of index.policies) {
    const stageIds = (index.outgoing.get(policy.id) ?? [])
      .filter((e) => e.relation === 'APPLIES_TO_LIFESTAGE' && byStage.has(e.target))
      .map((e) => e.target);
    if (stageIds.length === 0) { unclassified += 1; continue; }
    const entry = { policy, ages: policyAgeRanges(index, policy.id) };
    for (const id of new Set(stageIds)) byStage.get(id)!.push(entry);
  }
  const stages = LIFE_STAGES.map((s) => {
    const node = index.nodes.get(s.id);
    return {
      id: s.id, name: s.name,
      range: parseAgeRange(node?.data.age_range),
      description: typeof node?.data.description === 'string' ? node.data.description : null,
      policies: byStage.get(s.id)!.sort((a, b) => a.policy.label.localeCompare(b.policy.label, 'ko')),
    };
  });
  return { stages, unclassified };
}

/** 정책 하나가 연결된 생애단계 이름 (타임라인 순서). 데이터에 없는 정책이면 빈 배열 */
export function policyLifeStages(index: GraphIndex, policyId: string): string[] {
  const linked = new Set((index.outgoing.get(policyId) ?? [])
    .filter((e) => e.relation === 'APPLIES_TO_LIFESTAGE')
    .map((e) => e.target));
  return LIFE_STAGES.filter((s) => linked.has(s.id)).map((s) => s.name);
}

/** 입력한 나이가 들어가는 생애단계 (데이터의 나이 범위 기준). 없으면 null */
export function stageForAge(stages: LifeStage[], age: number | null): string | null {
  if (age == null) return null;
  const hit = stages.find((s) => s.range && age >= s.range.min && (s.range.max == null || age <= s.range.max));
  return hit ? hit.id : null;
}

/** 분야별로 묶는다. 정책이 많은 분야부터 (묶는 방법은 relatedPolicies의 groupByField와 같다) */
export function groupByCategory(list: StagePolicy[]): { category: string; items: StagePolicy[] }[] {
  return groupByField(list, SHARED_GROUP_DEFAULT.key, ungroupedLabel(SHARED_GROUP_DEFAULT.label)).map(({ value, items }) => ({ category: value, items }));
}
