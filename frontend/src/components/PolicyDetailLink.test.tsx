import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PolicyCheck } from '../api/types';
import { policyDetailHref } from '../lib/route';
import { SavedPoliciesProvider } from './SavedPolicies';
import { PolicyCard } from './TurnView';

// sample_only: 실제 정책이 아닌 테스트 값
const policy = (over: Partial<PolicyCheck> = {}): PolicyCheck => ({
  policy_id: 'POL_GOV_011', policy_name: '가상 정책 (sample_only)', agency: '가상 기관', reception_status: 'unknown',
  application_schedule: null, source_url: 'https://example.org/notice', official_apply_url: null, conditions: [], ...over,
});
const card = (p: PolicyCheck, sample = false) => renderToStaticMarkup(
  <SavedPoliciesProvider>
    <PolicyCard p={p} group="g" period="일정 확인 필요" stages={[]} sample={sample} onDemoApply={() => {}} onAskPolicy={() => {}} />
  </SavedPoliciesProvider>,
);
const BASE = 'http://127.0.0.1:8001/policies/';

afterEach(() => { vi.unstubAllEnvs(); });

describe('Django 정책 상세 주소', () => {
  it('정책 ID를 경로에 넣고 끝에 /를 붙인다', () => {
    expect(policyDetailHref('POL_010', BASE)).toBe('http://127.0.0.1:8001/policies/POL_010/');
    expect(policyDetailHref('POL_010', 'http://127.0.0.1:8001/policies')).toBe('http://127.0.0.1:8001/policies/POL_010/');
    expect(policyDetailHref('POL_STG_MOLIT_001', 'https://ssok.example.org/policies/')).toBe('https://ssok.example.org/policies/POL_STG_MOLIT_001/');
  });

  it('주소가 없거나 http(s)가 아니면, 또는 정책 ID 형식이 아니면 만들지 않는다', () => {
    expect(policyDetailHref('POL_010', '')).toBeNull();
    expect(policyDetailHref('POL_010', undefined)).toBeNull();
    expect(policyDetailHref('POL_010', 'javascript:alert(1)')).toBeNull();
    for (const id of ['sample_only_unknown', 'pol_010', '../POL_1', 'POL_<x>', 'POL_' + 'A'.repeat(200)]) {
      expect(policyDetailHref(id, BASE)).toBeNull();
    }
  });
});

describe('PolicyCard 정책 상세보기 링크', () => {
  it('관계 지도 바로 앞에 새 탭 링크로 보인다', () => {
    vi.stubEnv('VITE_POLICY_DETAIL_BASE_URL', BASE);
    const html = card(policy());
    expect(html).toContain('href="http://127.0.0.1:8001/policies/POL_GOV_011/" target="_blank" rel="noopener noreferrer"');
    expect(html).toContain('aria-label="가상 정책 (sample_only) 정책 상세보기 (새 탭)"');
    const detail = html.indexOf('정책 상세보기 ↗'), map = html.indexOf('>관계 지도<');
    expect(detail).toBeGreaterThan(-1);
    expect(detail).toBeLessThan(map);
    // 기존 버튼과 카드 펼침 구조는 그대로다
    for (const text of ['<details class="pcard"', '<summary class="ptop">', '이 정책 물어보기', '>관계 지도<', '원문 보기 ↗', '관심 정책 저장']) {
      expect(html).toContain(text);
    }
  });

  it('예시(가상) 정책 카드에는 링크가 없다', () => {
    vi.stubEnv('VITE_POLICY_DETAIL_BASE_URL', BASE);
    expect(card(policy(), true)).not.toContain('정책 상세보기');
  });

  it('주소 환경 변수가 없으면 링크 없이 기존 카드와 같다', () => {
    vi.stubEnv('VITE_POLICY_DETAIL_BASE_URL', '');
    const html = card(policy());
    expect(html).not.toContain('정책 상세보기');
    expect(html).toContain('>관계 지도<');
  });
});

it('상세 주소에 계정 정보나 query/hash가 있으면 잘못된 설정으로 숨긴다', () => {
  for (const base of ['https://user:password@example.org/policies/', 'https://example.org/policies/?token=sample_only', 'https://example.org/policies/#/']) {
    expect(policyDetailHref('POL_010', base)).toBeNull();
  }
});
