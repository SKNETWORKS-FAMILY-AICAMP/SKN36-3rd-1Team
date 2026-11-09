import { describe, expect, it } from 'vitest';
import { NODE_FILES, RELATIONSHIP_FILE } from '../config/graph';
import { buildGraphIndex } from './graphModel';
import { policiesForDocuments, policiesForQuestion, questionTerms } from './relatedPolicies';
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
