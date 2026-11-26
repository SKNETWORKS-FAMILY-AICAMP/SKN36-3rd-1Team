import { describe, expect, it } from 'vitest';
import { loadGraphDataset } from '../graph/loadGraph';
import { catalogPolicies } from './policyCatalog';
import { policySearchKey } from './policySearch';

const KPASS_NAME = '모두의 카드(K-패스) (대중교통비 환급지원)';
const KPASS_ID = 'POL_STG_MOLIT_001';
const matches = (query: string, name: string) => policySearchKey(name).includes(policySearchKey(query));

describe('정책 목록 검색 정규화 (#103)', () => {
  it('여섯 표기가 모두 K-패스 정책명과 맞는다', () => {
    for (const q of ['모두의카드', '모두의 카드', 'K-패스', 'k-패스', 'K패스', 'k패스']) expect(matches(q, KPASS_NAME), q).toBe(true);
  });

  it('입력하는 도중에도 결과가 사라지지 않는다', () => {
    for (const q of ['K', 'K-', 'K-패', 'K패', '-패스', '모두의 카']) expect(matches(q, KPASS_NAME), q).toBe(true);
  });

  it('다른 붙임표 문자로 쳐도 맞는다', () => {
    for (const q of ['K‐패스', 'K‑패스', 'K–패스', 'K—패스', 'K－패스']) expect(matches(q, KPASS_NAME), q).toBe(true);
  });

  it('공백·대소문자를 무시하는 기존 동작은 그대로다', () => {
    expect(policySearchKey('  청년 월세  지원 ')).toBe('청년월세지원');
    expect(matches('YOUTH', 'youth 센터 (sample_only)')).toBe(true);
  });

  it('관계없는 정책명은 K패스 계열 검색어와 맞지 않는다', () => {
    for (const name of ['청년 패스트트랙 지원 (sample_only)', '가상 교통 카드 (sample_only)', 'K-문화 체험 (sample_only)']) {
      for (const q of ['K-패스', 'K패스', 'k패스']) expect(matches(q, name), `${q} / ${name}`).toBe(false);
    }
  });
});

describe('팀 기준 데이터 (data/releases/v1.4.0-candidate)', () => {
  it('여섯 표기는 실제 목록에서 K-패스 정책만 찾고, 붙임표 없는 검색어 결과는 이전 규칙과 같다', async () => {
    const rows = catalogPolicies((await loadGraphDataset()).index).map(r => ({ id: r.policy.policy_id, text: `${r.policy.policy_name} ${r.policy.agency}` }));
    expect(rows.length).toBeGreaterThan(200);
    const found = (key: (t: string) => string, q: string) => rows.filter(r => key(r.text).includes(key(q))).map(r => r.id);

    for (const q of ['K-패스', 'k-패스', 'K패스', 'k패스']) expect(found(policySearchKey, q), q).toEqual([KPASS_ID]);
    for (const q of ['모두의카드', '모두의 카드']) expect(found(policySearchKey, q), q).toContain(KPASS_ID);

    // 이전 규칙(소문자 + 공백 제거)과 비교: 정책명·기관명 단어 앞 네 글자 검색어마다 결과가 같아야 한다
    const previous = (t: string) => t.toLocaleLowerCase().replace(/\s/g, '');
    const fragments = new Set(rows.flatMap(r => r.text.split(/\s+/)).map(w => w.slice(0, 4)).filter(w => w.length >= 2 && !/[-‐‑–—－]/.test(w)));
    const changed = [...fragments].filter(q => found(previous, q).join() !== found(policySearchKey, q).join());
    expect(changed).toEqual([]);
  });
});
