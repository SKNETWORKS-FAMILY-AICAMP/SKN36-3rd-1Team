import { describe, expect, it } from 'vitest';
import { lifeHref, mapHref, parseRoute } from './route';

describe('화면 주소', () => {
  it('#/map과 정책 ID를 읽는다', () => {
    expect(parseRoute('')).toEqual({ page: 'ask' });
    expect(parseRoute('#/')).toEqual({ page: 'ask' });
    expect(parseRoute('#/map')).toEqual({ page: 'map', policyId: null });
    expect(parseRoute(mapHref('POL_GOV_011'))).toEqual({ page: 'map', policyId: 'POL_GOV_011' });
  });

  it('#/life와 생애단계 이름을 읽고, 목록에 없는 이름은 받지 않는다', () => {
    expect(parseRoute('#/life')).toEqual({ page: 'life', stage: null });
    expect(parseRoute(lifeHref('청년'))).toEqual({ page: 'life', stage: '청년' });
    expect(parseRoute('#/life?stage=외계인')).toEqual({ page: 'life', stage: null });
  });

  it('정책 ID 형식이 아니면 받지 않는다 (주소는 외부 입력)', () => {
    expect(parseRoute('#/map?policy=<script>')).toEqual({ page: 'map', policyId: null });
    expect(parseRoute('#/map?policy=' + 'A'.repeat(65))).toEqual({ page: 'map', policyId: null });
  });
});
