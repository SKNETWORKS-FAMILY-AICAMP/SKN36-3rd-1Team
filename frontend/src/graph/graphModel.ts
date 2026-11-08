/**
 * 정책 관계 지도용 그래프 모델.
 * Neo4j 적재 파일(JSONL)을 그대로 읽어 정책 하나를 중심으로 연결된 노드와 자격 조건 묶음을 만든다.
 * 데이터는 외부 파일이라 형식이 틀리면 조용히 넘어가지 않고 어느 파일·몇 번째 줄인지 알린다.
 * 그래프 연결은 정책 구조를 보여 줄 뿐 자격 확정이 아니다 (CODING_STYLE §6).
 */
import {
  CONDITION_TREE_MAX_DEPTH, NODE_FILES, RELATIONSHIP_FILE, type NodeKind,
} from '../config/graph';

export type Row = Record<string, unknown>;

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  data: Row;
}

export interface GraphEdge {
  source: string;
  relation: string;
  target: string;
  evidenceDocumentId: string | null;
  evidenceLocation: string | null;
  reviewStatus: string | null;
}

export interface GraphIndex {
  nodes: Map<string, GraphNode>;
  outgoing: Map<string, GraphEdge[]>;
  incoming: Map<string, GraphEdge[]>;
  policies: GraphNode[];
  edgeCount: number;
}

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);

/** JSONL 본문을 줄 단위로 읽는다. 빈 줄은 건너뛰고, 깨진 줄은 위치와 함께 오류를 낸다. */
export function parseJsonl(content: string, fileName: string): Row[] {
  const rows: Row[] = [];
  content.split(/\r?\n/).forEach((line, i) => {
    if (!line.trim()) return;
    let value: unknown;
    try { value = JSON.parse(line); } catch { throw new Error(`${fileName} ${i + 1}번째 줄을 읽지 못했어요.`); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${fileName} ${i + 1}번째 줄이 객체가 아니에요.`);
    rows.push(value as Row);
  });
  return rows;
}

/** 파일 이름 → 본문 목록으로 그래프 색인을 만든다. 필요한 파일이 빠지면 오류를 낸다. */
export function buildGraphIndex(files: Record<string, string>): GraphIndex {
  const required = [...Object.keys(NODE_FILES), RELATIONSHIP_FILE];
  const missing = required.filter((name) => typeof files[name] !== 'string');
  if (missing.length) throw new Error(`그래프 파일이 없어요: ${missing.join(', ')}`);

  const nodes = new Map<string, GraphNode>();
  for (const [fileName, spec] of Object.entries(NODE_FILES)) {
    for (const row of parseJsonl(files[fileName], fileName)) {
      const id = text(row[spec.idKey]);
      if (!id) throw new Error(`${fileName}에 ${spec.idKey}가 없는 줄이 있어요.`);
      nodes.set(id, { id, kind: spec.kind, label: text(row[spec.labelKey]) ?? id, data: row });
    }
  }

  const outgoing = new Map<string, GraphEdge[]>();
  const incoming = new Map<string, GraphEdge[]>();
  let edgeCount = 0;
  for (const row of parseJsonl(files[RELATIONSHIP_FILE], RELATIONSHIP_FILE)) {
    const source = text(row.source_id), relation = text(row.relation), target = text(row.target_id);
    if (!source || !relation || !target) throw new Error(`${RELATIONSHIP_FILE}에 source_id·relation·target_id가 빠진 줄이 있어요.`);
    const edge: GraphEdge = {
      source, relation, target,
      evidenceDocumentId: text(row.evidence_document_id),
      evidenceLocation: text(row.evidence_location),
      reviewStatus: text(row.review_status),
    };
    (outgoing.get(source) ?? outgoing.set(source, []).get(source)!).push(edge);
    (incoming.get(target) ?? incoming.set(target, []).get(target)!).push(edge);
    edgeCount += 1;
  }

  const policies = [...nodes.values()].filter((n) => n.kind === 'policy');
  return { nodes, outgoing, incoming, policies, edgeCount };
}

/** 정책 검색: 이름·분야·지역·기관·ID에서 찾는다. 공백·대소문자는 무시한다. */
export function searchPolicies(index: GraphIndex, query: string, limit: number): GraphNode[] {
  const norm = (s: string) => s.replace(/\s+/g, '').toLowerCase();
  const q = norm(query);
  const fields = (n: GraphNode) => [n.label, n.id, n.data.category, n.data.region, n.data.agency].filter((v): v is string => typeof v === 'string');
  const hits = q ? index.policies.filter((n) => fields(n).some((f) => norm(f).includes(q))) : index.policies;
  return hits.slice(0, limit);
}

export interface Neighbor {
  node: GraphNode;
  edge: GraphEdge;
  /** out: 정책 → 상대, in: 상대 → 정책 */
  direction: 'out' | 'in';
}

/**
 * 정책 둘레에 놓을 이웃. 자격 조건은 묶음(루트 그룹)만 이웃으로 두고, 안의 조건은 conditionTrees로 따로 보여 준다.
 * 정책에서 조건으로 바로 가는 REQUIRES_CONDITION은 조건 묶음과 같은 내용이라 지도에서는 생략한다.
 */
export function policyNeighbors(index: GraphIndex, policyId: string): Neighbor[] {
  const result: Neighbor[] = [];
  for (const edge of index.outgoing.get(policyId) ?? []) {
    if (edge.relation === 'REQUIRES_CONDITION') continue;
    const node = index.nodes.get(edge.target);
    if (node) result.push({ node, edge, direction: 'out' });
  }
  for (const edge of index.incoming.get(policyId) ?? []) {
    const node = index.nodes.get(edge.source);
    if (node && node.kind === 'policy') result.push({ node, edge, direction: 'in' });
  }
  return result;
}

export interface ConditionTree {
  id: string;
  logic: string;
  conditions: GraphNode[];
  groups: ConditionTree[];
  /** 순환·깊이 제한으로 더 내려가지 않은 묶음 */
  truncated: boolean;
}

/** 정책의 자격 조건 묶음을 트리로 만든다 (HAS_ROOT_CONDITION_GROUP → CONTAINS_GROUP / CONTAINS_CONDITION). */
export function conditionTrees(index: GraphIndex, policyId: string): ConditionTree[] {
  const build = (groupId: string, depth: number, seen: Set<string>): ConditionTree => {
    const group = index.nodes.get(groupId);
    const logic = typeof group?.data.logic === 'string' ? group.data.logic.toUpperCase() : 'UNKNOWN';
    const tree: ConditionTree = { id: groupId, logic, conditions: [], groups: [], truncated: false };
    if (depth >= CONDITION_TREE_MAX_DEPTH || seen.has(groupId)) { tree.truncated = true; return tree; }
    const next = new Set(seen).add(groupId);
    for (const edge of index.outgoing.get(groupId) ?? []) {
      if (edge.relation === 'CONTAINS_GROUP') tree.groups.push(build(edge.target, depth + 1, next));
      if (edge.relation === 'CONTAINS_CONDITION') {
        const condition = index.nodes.get(edge.target);
        if (condition) tree.conditions.push(condition);
      }
    }
    return tree;
  };
  return (index.outgoing.get(policyId) ?? [])
    .filter((e) => e.relation === 'HAS_ROOT_CONDITION_GROUP')
    .map((e) => build(e.target, 0, new Set()));
}

/** 조건 트리 안의 조건 개수 (지도 요약에 쓴다) */
export function countConditions(trees: ConditionTree[]): number {
  return trees.reduce((sum, t) => sum + t.conditions.length + countConditions(t.groups), 0);
}
