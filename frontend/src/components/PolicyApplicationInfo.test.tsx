import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { Citation, PolicyCheck } from '../api/types';
import { isAskResponse } from '../api/client';
import { PolicyCard } from './TurnView';

const policy = (): PolicyCheck => ({ policy_id: 'sample_only', policy_name: '가상 정책', agency: '가상 기관',
  source_url: 'https://example.org/representative', official_apply_url: null, reception_status: 'unknown',
  conditions: [{ field: 'income', label: '소득', status: 'needs_check', reason: '공식 공고 확인이 필요해요.' }] });
const sources: Citation[] = [{ document_id: 'D1', title: '가상 기준 문서', url: 'https://example.org/requirements' }];
function render(p = policy(), citations = sources) {
  return renderToStaticMarkup(<PolicyCard p={p} sources={citations} period="일정 확인 필요" group="test" stages={[]} sample={false} onDemoApply={() => {}} />);
}
const response = (p: unknown) => ({ status: 'answered', answer: 'sample_only', citations: [], follow_up_questions: [], policy_checks: [p], checked_at: null, is_sample: true });
it('공식 기준과 내 조건을 분리하고 연도·단위·값을 원문 그대로 표시한다', () => {
  const p = policy(); p.official_requirements = [{ label: '소득', value: '2025년 기준 중위소득 100% 이하', document_id: 'D1', chunk_id: 'C1' }];
  const html = render(p);
  expect(html).toContain('공식 자격기준'); expect(html).toContain('내 조건 확인');
  expect(html).toContain('2025년 기준 중위소득 100% 이하');
  expect(html).toContain('href="https://example.org/requirements"');
  expect(html).not.toContain('조건상 적합'); expect(html).not.toContain('2026년');
});
it('기준 문서가 대표 원문과 달라도 해당 문서만 연결한다', () => {
  const p = policy(); p.official_requirements = [{ label: '나이', value: '만 19세 이상', document_id: 'D1' }];
  const section = render(p).split('aria-label="공식 자격기준"')[1].split('</section>')[0];
  expect(section).toContain('https://example.org/requirements'); expect(section).not.toContain('https://example.org/representative');
});
it.each([null, 'D2'])('출처 ID %s가 없거나 다른 정책 문서이면 대표 원문으로 대신 연결하지 않는다', document_id => {
  const p = policy(); p.official_requirements = [{ label: '기준', value: '공식 값', document_id }];
  const section = render(p).split('aria-label="공식 자격기준"')[1].split('</section>')[0];
  expect(section).not.toContain('<a '); expect(section).toContain('출처 링크는 제공되지 않았어요');
});
it('안전하지 않은 기준 링크를 숨기고 기준 값은 유지한다', () => {
  const p = policy(); p.official_requirements = [{ label: '기준', value: '원문 값', document_id: 'D1' }];
  expect(render(p, [{ ...sources[0], url: 'javascript:bad' }])).not.toContain('javascript:bad');
  expect(render(p, [{ ...sources[0], url: 'javascript:bad' }])).toContain('원문 값');
});
it('기준 필드가 없는 기존 응답·빈 배열은 기존 조건 표시를 유지한다', () => {
  for (const requirements of [undefined, null, []]) {
    const p = policy(); p.official_requirements = requirements;
    expect(render(p)).not.toContain('aria-label="공식 자격기준"');
    expect(render(p)).toContain('공식 공고 확인이 필요해요.'); expect(isAskResponse(response(p))).toBe(true);
  }
});
it('서류 목록을 만들어내지 않고 확인 경로만 제공한다', () => {
  expect(render()).toContain('확인된 서류 목록이 제공되지 않았어요');
  expect(render()).toContain('공식 원문에서 서류 확인');
  expect(render()).not.toContain('주민등록등본');
  const p = policy(); p.source_url = ''; p.agency = '';
  expect(render(p)).not.toContain('공식 원문에서 서류 확인'); expect(render(p)).not.toContain('>신청하기');
});
it('제외 사유는 API가 제공한 문구만 표시한다', () => {
  const p = policy(); p.exclusion_reasons = [{ field: 'age', label: '연령 제외', reason: '공고의 제외 조건에 해당해요.' }];
  expect(render(p)).toContain('공고의 제외 조건에 해당해요.');
});
it.each([123, {}, [{ label: '기준', value: 123 }], [{ label: '기준', value: '값', document_id: {} }]])('잘못된 공식 기준 응답 %j를 화면 진입 전에 거부한다', bad => {
  expect(isAskResponse(response({ ...policy(), official_requirements: bad }))).toBe(false);
});
it('검수된 공식 기준이 없으면 지원대상 원문 발췌를 같은 자리에 원문 링크와 함께 보여 준다', () => {
  const p = policy(); p.requirement_excerpts = [{ label: '지원대상', text: '만 19~34세 (sample_only)', document_id: 'D9', chunk_id: 'C9', source_url: 'https://example.org/excerpt' }];
  const section = render(p).split('aria-label="공식 자격기준"')[1].split('</section>')[0];
  expect(section).toContain('공식 자격기준 · 원문 발췌'); expect(section).toContain('만 19~34세 (sample_only)');
  expect(section).toContain('href="https://example.org/excerpt"'); expect(isAskResponse(response(p))).toBe(true);
});
it('검수된 공식 기준(#76)이 있으면 원문 발췌보다 그것을 보여 준다', () => {
  const p = policy(); p.official_requirements = [{ label: '소득', value: '검수된 값', document_id: 'D1' }];
  p.requirement_excerpts = [{ label: '지원대상', text: '발췌 값', document_id: 'D9', chunk_id: 'C9', source_url: 'https://example.org/excerpt' }];
  const section = render(p).split('aria-label="공식 자격기준"')[1].split('</section>')[0];
  expect(section).toContain('검수된 값'); expect(section).not.toContain('발췌 값'); expect(section).not.toContain('원문 발췌');
});
it.each([123, [{ label: '지원대상', text: '' }], [{ label: '지원대상', text: '값', document_id: 'D', chunk_id: 'C' }], [{ text: '값', document_id: 'D', chunk_id: 'C', source_url: '' }]])('잘못된 원문 발췌 응답 %j를 화면 진입 전에 거부한다', bad => {
  expect(isAskResponse(response({ ...policy(), requirement_excerpts: bad }))).toBe(false);
});
it('문의처 출처의 잘못된 타입도 거부한다', () => {
  expect(isAskResponse(response({ ...policy(), contacts: [{ agency: '기관', phone: null, source_url: {} }] }))).toBe(false);
});
