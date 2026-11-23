import type { PolicyCheck } from '../api/types';
import type { GraphIndex } from '../graph/graphModel';
import { safeHttpUrl } from './validate';
export interface CatalogPolicy { policy: PolicyCheck; regions: string[]; categories: string[] }
/** 배포 스냅샷은 탐색에만 사용한다. 접수·자격을 프론트에서 판정하지 않는다. */
export function catalogPolicies(index: GraphIndex): CatalogPolicy[] {
  return index.policies.map(node => {
    const linked = (kind: string) => [...new Set((index.outgoing.get(node.id) ?? [])
      .map(edge => index.nodes.get(edge.target)).filter(n => n?.kind === kind).map(n => n!.label))];
    const text = (key: string) => typeof node.data[key] === 'string' ? node.data[key] as string : '';
    return { policy: { policy_id: node.id, policy_name: node.label, agency: text('agency'),
      source_url: safeHttpUrl(text('source_url')) ?? '', official_apply_url: safeHttpUrl(text('official_apply_url')),
      reception_status: 'unknown', application_schedule: null, conditions: [] },
      regions: linked('region'), categories: linked('category') };
  });
}
