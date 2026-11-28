import { describe, expect, it } from 'vitest';
import { DATASET_DIR } from '../config/dataset';
import { GRAPH_SOURCE_PATHS } from './loadGraph';

describe('관계 지도 데이터 경로', () => {
  it('import.meta.glob 경로가 config/dataset.ts의 데이터 폴더와 같다', () => {
    expect(GRAPH_SOURCE_PATHS.length).toBeGreaterThan(1);
    for (const path of GRAPH_SOURCE_PATHS) expect(path).toContain(`/${DATASET_DIR}/`);
    expect(GRAPH_SOURCE_PATHS.some((path) => path.endsWith('/manifest.json'))).toBe(true);
  });
});
