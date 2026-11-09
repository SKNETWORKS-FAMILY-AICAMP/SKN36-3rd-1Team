/**
 * 상담 화면과 관계 지도를 잇기 위해 '방금 상담과 관련된 정책'을 찾는다.
 * 1) 답변이 인용한 출처 문서 → 그 문서의 정책 (근거가 있는 연결)
 * 2) 답변에 실제 정책이 없을 때(예시 응답 등) → 질문 단어와 이름·분야·대상이 겹치는 정책 (단순 검색 결과)
 * 2)는 답변 근거나 자격 판정이 아니라 지도로 이동하기 위한 검색이라, 화면에서 그렇게 표시한다.
 */
import { QUESTION_MATCH_WEIGHT as W } from '../config/graph';
import type { GraphIndex, GraphNode } from './graphModel';

/** 출처 문서 ID → 그 문서가 속한 정책 (Document 노드의 policy_id) */
export function policiesForDocuments(index: GraphIndex, documentIds: string[]): GraphNode[] {
  const out: GraphNode[] = [];
  for (const docId of documentIds) {
    const doc = index.nodes.get(docId);
    const policyId = doc?.kind === 'document' && typeof doc.data.policy_id === 'string' ? doc.data.policy_id : null;
    const policy = policyId ? index.nodes.get(policyId) : undefined;
    if (policy?.kind === 'policy' && !out.includes(policy)) out.push(policy);
  }
  return out;
}

/** 질문에 흔히 들어가서 정책을 구분하지 못하는 말 */
const STOP_WORDS = new Set([
  '정책', '지원', '지원금', '있나요', '있어요', '있는', '어디서', '확인', '확인하나요', '확인해요', '알려줘', '알려주세요', '받을', '받을수',
  '무엇', '어떤', '어떻게', '필요하나요', '필요해요', '필요한', '서류', '신청', '방법', '관련', '대상', '조건', '저는', '제가', '그리고', '이랑',
]);
const PARTICLE = /(은|는|이|가|을|를|에|의|도|로|과|와|이랑|에서|으로)$/;

/** 질문을 비교할 단어로 나눈다: 두 글자 이상, 흔한 말 제외, 끝의 조사 떼기 */
export function questionTerms(question: string): string[] {
  const terms = question.split(/[^가-힣A-Za-z0-9]+/)
    .map((w) => (w.length > 2 ? w.replace(PARTICLE, '') : w))
    .filter((w) => w.length >= 2 && !STOP_WORDS.has(w));
  return [...new Set(terms)];
}

/** 질문 단어와 이름·분야·대상·지원 대상 설명이 겹치는 정책을 점수순으로 (점수: config/graph의 QUESTION_MATCH_WEIGHT) */
export function policiesForQuestion(index: GraphIndex, question: string, limit: number): GraphNode[] {
  const terms = questionTerms(question);
  if (terms.length === 0) return [];
  const text = (v: unknown) => (typeof v === 'string' ? v : Array.isArray(v) ? v.filter((x) => typeof x === 'string').join(' ') : '');
  const scored = index.policies.map((p) => {
    const name = p.label, area = `${text(p.data.category)} ${text(p.data.target_groups)}`, detail = text(p.data.eligibility);
    const score = terms.reduce((sum, t) => sum + (name.includes(t) ? W.name : 0) + (area.includes(t) ? W.area : 0) + (detail.includes(t) ? W.detail : 0), 0);
    return { p, score };
  }).filter((x) => x.score > 0);
  scored.sort((a, b) => b.score - a.score || a.p.label.localeCompare(b.p.label, 'ko'));
  return scored.slice(0, limit).map((x) => x.p);
}
