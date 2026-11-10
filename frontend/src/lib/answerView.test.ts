import { describe, expect, it } from 'vitest';
import type { AskResponse } from '../api/types';
import { INFO_CAVEAT, NO_EVIDENCE_GUIDE, answerView, freshnessNote, sourceDetail } from './answerView';

describe('출처 표시', () => {
  it('페이지 번호가 있으면 사이트 주소 뒤에 붙인다', () => {
    expect(sourceDetail('https://www.apply.lh.or.kr/notice.pdf', 3)).toBe('apply.lh.or.kr · p.3');
    expect(sourceDetail('https://www.gov.kr/', null)).toBe('gov.kr');
    expect(sourceDetail(null, 3)).toBe('링크 확인 필요 · p.3');
  });

  it('확인 시점이 없어도 공식 원문 확인 안내는 항상 보여 준다 (PRD §7.1)', () => {
    expect(freshnessNote(null, false)).toBe('최신 내용은 공식 원문에서 확인해 주세요');
    expect(freshnessNote('2026-11-06', true)).toBe('확인 시점 2026.11.06 (시연용 예시 날짜) · 최신 내용은 공식 원문에서 확인해 주세요');
  });
});

/** 실제 백엔드(ask_contract v1.0) 응답 모양의 가상 응답 */
const base: AskResponse = {
  status: 'answered', answer: '', citations: [], policy_checks: [], follow_up_questions: [],
  checked_at: null, notice: null, is_sample: false,
};

describe('answerView 답변 상태별 표시', () => {
  it('answered는 답변 본문을 보여 준다', () => {
    const v = answerView({ ...base, answer: '지원 대상은 만 19~34세예요[1].' });
    expect(v.answerText).toBe('지원 대상은 만 19~34세예요[1].');
    expect(v.guide).toBeNull();
  });

  it('no_evidence는 서버 문장 대신 화면의 고정 문구를 쓴다 (notice가 없어도 빈 카드가 아니다)', () => {
    const v = answerView({ ...base, status: 'no_evidence', answer: '인용할 수 있는 공식 원문을 찾지 못했습니다.' });
    expect(v.answerText).toBeNull();
    expect(v.guide).toEqual({ kind: 'warn', ...NO_EVIDENCE_GUIDE });
  });

  it('info는 LLM 글을 답변 본문이 아니라 안내 상자에 넣고, 확정 답변이 아니라는 문구를 붙인다', () => {
    const v = answerView({ ...base, status: 'info', answer: '거주 지역을 알려 주시면 다시 확인할게요.' });
    expect(v.answerText).toBeNull();
    expect(v.guide?.kind).toBe('info');
    expect(v.guide?.text).toContain('거주 지역을 알려');
    expect(v.guide?.text).toContain(INFO_CAVEAT);
  });

  it('info에 글이 없어도 확정 답변이 아니라는 문구는 남긴다', () => {
    expect(answerView({ ...base, status: 'info', answer: '  ' }).guide?.text).toBe(INFO_CAVEAT);
  });

  it('서버 경고(warn)는 답변 위 경고로, 일반 안내(info)는 아래 안내로 나눈다', () => {
    const warn = { kind: 'warn' as const, title: '추가 확인 필요', text: '자동 판정을 보류한 조건이 있습니다.' };
    const info = { kind: 'info' as const, title: '신청 주소는 공고마다 달라요', text: '출처만 보여 드려요.' };
    expect(answerView({ ...base, answer: '답변[1]', notice: warn })).toMatchObject({ warning: warn, extraInfo: null });
    expect(answerView({ ...base, answer: '답변[1]', notice: info })).toMatchObject({ warning: null, extraInfo: info });
  });
});
