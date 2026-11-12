/**
 * 정책 관계 지도 데이터 불러오기.
 *
 * 데이터 위치: 저장소의 data/releases/v1.4.0-candidate (팀 기준 데이터, 백엔드 LIFEPOLICY_DATASET_DIR 기본값과 같다).
 * import.meta.glob은 경로를 문자열로 직접 써야 해서 데이터 버전이 바뀌면 이 파일의 경로 두 곳과
 * vite.config.ts의 server.fs.allow를 함께 고친다.
 * 지도 페이지를 열 때만 내려받도록 나눠 불러온다(첫 화면 용량에 포함되지 않는다).
 */
import { buildGraphIndex, type GraphIndex } from './graphModel';

const GRAPH_FILES = import.meta.glob('../../../data/releases/v1.4.0-candidate/neo4j/*.jsonl', {
  query: '?raw', import: 'default',
}) as Record<string, () => Promise<string>>;

const MANIFEST_FILES = import.meta.glob('../../../data/releases/v1.4.0-candidate/manifest.json', {
  query: '?raw', import: 'default',
}) as Record<string, () => Promise<string>>;

export interface GraphDataset {
  index: GraphIndex;
  /** 화면에 보여 줄 데이터 버전 (manifest_version) */
  version: string;
  /** 릴리스 상태 (예: CANDIDATE / PENDING_DB_REVIEW). 없으면 null */
  status: string | null;
  /** manifest에 적힌 전체 노드·관계 수. 실제 읽은 수와 비교해 데이터가 덜 받아졌는지 확인한다 */
  expected: { nodes: number; edges: number } | null;
}

const fileName = (path: string) => path.slice(path.lastIndexOf('/') + 1);

// 관계 지도와 생애주기 화면이 같은 데이터를 쓰므로 한 번만 받아 둔다 (실패하면 다음에 다시 시도)
let cached: Promise<GraphDataset> | null = null;
export function loadGraphDataset(): Promise<GraphDataset> {
  cached ??= readGraphDataset().catch((err: unknown) => { cached = null; throw err; });
  return cached;
}

async function readGraphDataset(): Promise<GraphDataset> {
  const entries = Object.entries(GRAPH_FILES);
  if (entries.length === 0) {
    throw new Error('그래프 데이터 폴더(data/releases/v1.4.0-candidate/neo4j)를 찾지 못했어요. 최신 main을 받았는지 확인해 주세요.');
  }
  const files: Record<string, string> = {};
  await Promise.all(entries.map(async ([path, load]) => { files[fileName(path)] = await load(); }));
  const index = buildGraphIndex(files);

  let version = 'v1.4.0-candidate', status: string | null = null, expected: GraphDataset['expected'] = null;
  const manifestLoad = Object.values(MANIFEST_FILES)[0];
  if (manifestLoad) {
    const manifest = JSON.parse(await manifestLoad()) as {
      manifest_version?: string; release_status?: string; summary?: { total_graph_nodes?: number; total_graph_edges?: number };
    };
    version = manifest.manifest_version ?? version;
    status = manifest.release_status ?? null;
    const { total_graph_nodes: nodes, total_graph_edges: edges } = manifest.summary ?? {};
    if (typeof nodes === 'number' && typeof edges === 'number') expected = { nodes, edges };
  }
  return { index, version, status, expected };
}
