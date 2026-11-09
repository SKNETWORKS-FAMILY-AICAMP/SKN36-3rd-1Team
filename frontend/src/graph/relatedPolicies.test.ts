import { describe, expect, it } from 'vitest';
import { NODE_FILES, RELATIONSHIP_FILE } from '../config/graph';
import { buildGraphIndex } from './graphModel';
import {
  groupByField, policiesForDocuments, policiesForQuestion, policiesSharing, questionTerms, similarPolicies,
} from './relatedPolicies';
import { loadGraphDataset } from './loadGraph';
import { PERSONAS } from '../config/personas';

const jsonl = (rows: object[]) => rows.map((r) => JSON.stringify(r)).join('\n');

/** 가상 그래프 (sample_only) */
function sampleIndex() {
  const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
  files['nodes_policy.jsonl'] = jsonl([
    { policy_id: 'P_RENT', policy_name: '가상 청년월세 지원', category: '주거·금융', target_groups: ['청년'] },
    { policy_id: 'P_BUS', policy_name: '가상 대중교통비 환급', category: '교통·생활비', target_groups: ['청년'] },
    { policy_id: 'P_BABY', policy_name: '가상 출산 축하금', category: '출산·육아', eligibility: '신혼부부 출산 가정' },
  ]);
  files['nodes_document.jsonl'] = jsonl([{ document_id: 'D_RENT', policy_id: 'P_RENT', title: '가상 공고' }]);
  files[RELATIONSHIP_FILE] = '';
  return buildGraphIndex(files);
}

describe('상담과 관련된 정책 찾기', () => {
  const index = sampleIndex();

  it('출처 문서로 정책을 찾는다 (없는 문서는 건너뛴다)', () => {
    expect(policiesForDocuments(index, ['D_RENT', 'D_NONE']).map((p) => p.id)).toEqual(['P_RENT']);
  });

  it('질문 단어는 흔한 말·조사를 빼고 두 글자 이상만 쓴다', () => {
    expect(questionTerms('청년월세 지원 정책이 있나요?')).toEqual(['청년월세']);
    expect(questionTerms('교통비는 어디서 확인해요?')).toEqual(['교통비']);
  });

  it('질문 단어가 이름·분야·대상과 겹치는 정책을 점수순으로 준다', () => {
    expect(policiesForQuestion(index, '청년월세 지원 있나요?', 3).map((p) => p.id)).toEqual(['P_RENT']);
    expect(policiesForQuestion(index, '교통 지원은?', 3).map((p) => p.id)).toEqual(['P_BUS']);
    expect(policiesForQuestion(index, '정책 알려줘', 3)).toEqual([]);
  });

  it('대표 상황 카드 질문은 실제 팀 데이터에서 관련 정책을 하나 이상 찾는다', async () => {
    const { index: real } = await loadGraphDataset();
    for (const p of PERSONAS) expect(policiesForQuestion(real, p.question, 5).length).toBeGreaterThan(0);
  });
});

describe('policiesSharing (지도 팝업: 같은 대상에 연결된 다른 정책)', () => {
  it('들어오는 관계 중 정책만, 지금 정책은 빼고, 중복 없이 이름순으로', () => {
    const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
    files['nodes_policy.jsonl'] = jsonl([
      { policy_id: 'P_A', policy_name: '다 정책' }, { policy_id: 'P_B', policy_name: '나 정책' }, { policy_id: 'P_C', policy_name: '가 정책' },
    ]);
    files['nodes_lifestage.jsonl'] = jsonl([{ stage_id: 'S_Y', name: '청년' }]);
    files['nodes_document.jsonl'] = jsonl([{ document_id: 'D_1', title: '가상 문서' }]);
    files[RELATIONSHIP_FILE] = jsonl([
      { source_id: 'P_A', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'S_Y' },
      { source_id: 'P_B', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'S_Y' },
      { source_id: 'P_C', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'S_Y' },
      { source_id: 'P_C', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'S_Y' },
      { source_id: 'D_1', relation: 'APPLIES_TO_LIFESTAGE', target_id: 'S_Y' },
    ]);
    const index = buildGraphIndex(files);
    expect(policiesSharing(index, 'S_Y', 'P_A').map((p) => p.id)).toEqual(['P_C', 'P_B']);
    expect(policiesSharing(index, 'NOPE', 'P_A')).toEqual([]);
  });
});

describe('similarPolicies (지도 팝업: 연결 구조가 비슷한 정책)', () => {
  const rel = ['HAS_CATEGORY', 'TARGETS', 'APPLIES_TO_LIFESTAGE'];
  function index() {
    const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
    files['nodes_policy.jsonl'] = jsonl(['P_ME', 'P_2', 'P_3', 'P_1'].map((id) => ({ policy_id: id, policy_name: `가상 ${id}` })));
    files['nodes_category.jsonl'] = jsonl([{ category_id: 'C_H', name: '주거' }]);
    files['nodes_target_group.jsonl'] = jsonl([{ group_id: 'T_Y', name: '청년' }]);
    files['nodes_lifestage.jsonl'] = jsonl([{ stage_id: 'S_Y', name: '청년' }]);
    const link = (s: string, r: string, t: string) => ({ source_id: s, relation: r, target_id: t });
    files[RELATIONSHIP_FILE] = jsonl([
      link('P_ME', 'HAS_CATEGORY', 'C_H'), link('P_ME', 'TARGETS', 'T_Y'), link('P_ME', 'APPLIES_TO_LIFESTAGE', 'S_Y'),
      link('P_3', 'HAS_CATEGORY', 'C_H'), link('P_3', 'TARGETS', 'T_Y'), link('P_3', 'APPLIES_TO_LIFESTAGE', 'S_Y'),
      link('P_2', 'HAS_CATEGORY', 'C_H'), link('P_2', 'TARGETS', 'T_Y'), link('P_2', 'TARGETS', 'T_Y'),
      link('P_1', 'HAS_CATEGORY', 'C_H'),
    ]);
    return buildGraphIndex(files);
  }

  it('겹친 개수순, 최소 개수 미만은 빼고, 같은 연결이 두 번 있어도 한 번만 센다', () => {
    const result = similarPolicies(index(), 'P_ME', rel, 2, 5);
    expect(result.map((s) => [s.policy.id, s.shared.length])).toEqual([['P_3', 3], ['P_2', 2]]);
  });

  it('개수 제한과 비교할 관계 종류를 따른다', () => {
    expect(similarPolicies(index(), 'P_ME', rel, 1, 1).map((s) => s.policy.id)).toEqual(['P_3']);
    expect(similarPolicies(index(), 'P_ME', ['HAS_CATEGORY'], 1, 5)).toHaveLength(3);
  });
});

describe('groupByField (지도 팝업 분야 막대·생애주기 분야 칩)', () => {
  const p = (id: string, category?: string) => ({ policy: { id, kind: 'policy' as const, label: id, data: category ? { category } : {} } });
  it('많은 묶음부터, 같으면 이름순, 값이 없으면 fallback 묶음', () => {
    const groups = groupByField([p('A', '주거'), p('B', '교육'), p('C', '주거'), p('D'), p('E', '교육'), p('F', '고용')], 'category', '분야 미분류');
    expect(groups.map((g) => [g.value, g.items.length])).toEqual([['교육', 2], ['주거', 2], ['고용', 1], ['분야 미분류', 1]]);
    expect(groups[1].items.map((i) => i.policy.id)).toEqual(['A', 'C']);
  });
});
