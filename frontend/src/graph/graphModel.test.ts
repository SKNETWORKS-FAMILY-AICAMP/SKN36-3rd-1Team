import { describe, expect, it } from 'vitest';
import { NODE_FILES, RELATIONSHIP_FILE } from '../config/graph';
import {
  buildGraphIndex, conditionTrees, countConditions, parseJsonl, policyNeighbors, searchPolicies,
} from './graphModel';
import {
  INITIAL_VIEW, MAP_HEIGHT, MAP_WIDTH, layoutNeighbors, shortLabel, zoomAt, zoomCenter,
} from './graphLayout';
import { loadGraphDataset } from './loadGraph';

const jsonl = (rows: object[]) => rows.map((r) => JSON.stringify(r)).join('\n');

/** 가상 그래프 (sample_only): 정책 2개, 조건 묶음 AND 안에 OR 묶음 */
function sampleFiles(): Record<string, string> {
  const files: Record<string, string> = Object.fromEntries(Object.keys(NODE_FILES).map((f) => [f, '']));
  files['nodes_policy.jsonl'] = jsonl([
    { policy_id: 'POL_A', policy_name: '가상 청년 정책', category: '취업', region: '서울특별시', agency: '가상 기관' },
    { policy_id: 'POL_B', policy_name: '가상 신혼 정책', category: '주거', region: '경기도' },
  ]);
  files['nodes_organization.jsonl'] = jsonl([{ agency_id: 'AGENCY_X', name: '가상 기관' }]);
  files['nodes_region.jsonl'] = jsonl([{ region_id: 'REGION_SEOUL', name: '서울특별시' }]);
  files['nodes_document.jsonl'] = jsonl([{ document_id: 'DOC_A', title: '가상 공고', source_url: 'https://example.org/a' }]);
  files['nodes_condition_group.jsonl'] = jsonl([
    { group_id: 'GRP_A_ROOT', logic: 'AND' }, { group_id: 'GRP_A_OR', logic: 'OR' },
  ]);
  files['nodes_eligibility_condition.jsonl'] = jsonl([
    { condition_id: 'COND_AGE_19_34', name: '연령조건: 만 19세~34세' },
    { condition_id: 'COND_INC_1', name: '소득 1구간' }, { condition_id: 'COND_INC_2', name: '소득 2구간' },
  ]);
  files[RELATIONSHIP_FILE] = jsonl([
    { source_id: 'POL_A', relation: 'ADMINISTERED_BY', target_id: 'AGENCY_X', evidence_document_id: 'DOC_A', evidence_location: '소관기관' },
    { source_id: 'POL_A', relation: 'AVAILABLE_IN', target_id: 'REGION_SEOUL' },
    { source_id: 'POL_A', relation: 'SUPPORTED_BY', target_id: 'DOC_A' },
    { source_id: 'POL_A', relation: 'HAS_ROOT_CONDITION_GROUP', target_id: 'GRP_A_ROOT' },
    { source_id: 'POL_A', relation: 'REQUIRES_CONDITION', target_id: 'COND_AGE_19_34' },
    { source_id: 'GRP_A_ROOT', relation: 'CONTAINS_CONDITION', target_id: 'COND_AGE_19_34' },
    { source_id: 'GRP_A_ROOT', relation: 'CONTAINS_GROUP', target_id: 'GRP_A_OR' },
    { source_id: 'GRP_A_OR', relation: 'CONTAINS_CONDITION', target_id: 'COND_INC_1' },
    { source_id: 'GRP_A_OR', relation: 'CONTAINS_CONDITION', target_id: 'COND_INC_2' },
    { source_id: 'POL_B', relation: 'MODIFIES_CONDITION_OF', target_id: 'POL_A' },
  ]);
  return files;
}

describe('graphModel 가상 그래프', () => {
  const index = buildGraphIndex(sampleFiles());

  it('노드·관계를 읽고 정책 목록을 만든다', () => {
    expect(index.policies.map((p) => p.id)).toEqual(['POL_A', 'POL_B']);
    expect(index.edgeCount).toBe(10);
  });

  it('정책 둘레 이웃: 조건 직접 연결은 빼고, 다른 정책이 연결한 관계는 포함한다', () => {
    const relations = policyNeighbors(index, 'POL_A').map((n) => `${n.direction}:${n.edge.relation}`).sort();
    expect(relations).toEqual([
      'in:MODIFIES_CONDITION_OF', 'out:ADMINISTERED_BY', 'out:AVAILABLE_IN', 'out:HAS_ROOT_CONDITION_GROUP', 'out:SUPPORTED_BY',
    ]);
  });

  it('자격 조건 묶음을 AND 안의 OR까지 트리로 만든다', () => {
    const [root] = conditionTrees(index, 'POL_A');
    expect(root.logic).toBe('AND');
    expect(root.conditions.map((c) => c.id)).toEqual(['COND_AGE_19_34']);
    expect(root.groups[0].logic).toBe('OR');
    expect(root.groups[0].conditions).toHaveLength(2);
    expect(countConditions([root])).toBe(3);
  });

  it('조건 묶음이 서로를 가리켜도 무한히 내려가지 않는다', () => {
    const files = sampleFiles();
    files[RELATIONSHIP_FILE] += '\n' + JSON.stringify({ source_id: 'GRP_A_OR', relation: 'CONTAINS_GROUP', target_id: 'GRP_A_ROOT' });
    const [root] = conditionTrees(buildGraphIndex(files), 'POL_A');
    expect(root.groups[0].groups[0].truncated).toBe(true);
  });

  it('정책 검색: 이름·지역·분야, 공백 무시', () => {
    expect(searchPolicies(index, '신혼', 10).map((p) => p.id)).toEqual(['POL_B']);
    expect(searchPolicies(index, '서울 특별시', 10).map((p) => p.id)).toEqual(['POL_A']);
    expect(searchPolicies(index, '', 1)).toHaveLength(1);
  });

  it('깨진 줄과 빠진 파일은 위치를 알려 주며 멈춘다', () => {
    expect(() => parseJsonl('{"a":1}\n{깨짐', 'x.jsonl')).toThrow('x.jsonl 2번째 줄');
    const files = sampleFiles();
    delete files[RELATIONSHIP_FILE];
    expect(() => buildGraphIndex(files)).toThrow('relationships.jsonl');
  });
});

describe('graphLayout', () => {
  it('이웃을 지도 안쪽에 놓고, 같은 종류끼리 모은다', () => {
    const index = buildGraphIndex(sampleFiles());
    const { placed } = layoutNeighbors(policyNeighbors(index, 'POL_A'));
    for (const n of placed) {
      expect(n.x).toBeGreaterThan(0); expect(n.x).toBeLessThan(MAP_WIDTH);
      expect(n.y).toBeGreaterThan(0); expect(n.y).toBeLessThan(MAP_HEIGHT);
    }
    expect(placed[0].node.kind).toBe('group');
  });

  it('확대·축소: 기준점은 제자리에 두고, 배율은 최소·최대 안으로 자른다', () => {
    const point = { x: 200, y: 100 };
    const zoomed = zoomAt(INITIAL_VIEW, 2, point, 0.5, 3);
    expect(zoomed.scale).toBe(2);
    // 확대 전후에 기준점이 같은 화면 위치에 있다: x + scale * q
    const q = { x: (point.x - INITIAL_VIEW.x) / INITIAL_VIEW.scale, y: (point.y - INITIAL_VIEW.y) / INITIAL_VIEW.scale };
    expect(zoomed.x + zoomed.scale * q.x).toBeCloseTo(point.x);
    expect(zoomed.y + zoomed.scale * q.y).toBeCloseTo(point.y);
    expect(zoomAt(zoomed, 10, point, 0.5, 3).scale).toBe(3);
    expect(zoomCenter(INITIAL_VIEW, 0.1, 0.5, 3).scale).toBe(0.5);
  });

  it('긴 이름은 줄여 쓴다', () => {
    expect(shortLabel('가나다라마바사아자차카타', 5)).toBe('가나다라…');
    expect(shortLabel('짧음', 5)).toBe('짧음');
  });
});

describe('팀 기준 데이터 (data/releases/v1.4.0-candidate)', () => {
  it('실제 그래프 파일을 읽으면 manifest 건수와 같고, 끊긴 연결이 없다', async () => {
    const { index, expected } = await loadGraphDataset();
    expect(expected).not.toBeNull();
    expect(index.nodes.size).toBe(expected!.nodes);
    expect(index.edgeCount).toBe(expected!.edges);
    for (const edges of index.outgoing.values()) {
      for (const e of edges) {
        expect(index.nodes.has(e.source)).toBe(true);
        expect(index.nodes.has(e.target)).toBe(true);
      }
    }
    // 모든 정책이 자격 조건 묶음을 하나 이상 가진다
    expect(index.policies.every((p) => conditionTrees(index, p.id).length > 0)).toBe(true);
  });
});
