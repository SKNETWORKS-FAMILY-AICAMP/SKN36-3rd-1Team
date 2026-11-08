/**
 * 정책 관계 지도 표시 설정 (CODING_STYLE §3: 설정값은 한 곳에서 관리).
 * 노드·관계 이름은 data/releases/<버전>/neo4j 의 파일·relation 이름과 같다 (docs/data/data_contract.md).
 */
import type { IconName } from '../components/Icon';

export type NodeKind = 'policy' | 'organization' | 'region' | 'target' | 'lifestage' | 'category' | 'document' | 'group' | 'condition';

/** 그래프 파일 → 노드 종류. idKey는 각 파일의 기본 키, labelKey는 화면에 보여 줄 이름 필드 */
export const NODE_FILES: Record<string, { kind: NodeKind; idKey: string; labelKey: string }> = {
  'nodes_policy.jsonl': { kind: 'policy', idKey: 'policy_id', labelKey: 'policy_name' },
  'nodes_organization.jsonl': { kind: 'organization', idKey: 'agency_id', labelKey: 'name' },
  'nodes_region.jsonl': { kind: 'region', idKey: 'region_id', labelKey: 'name' },
  'nodes_target_group.jsonl': { kind: 'target', idKey: 'group_id', labelKey: 'name' },
  'nodes_lifestage.jsonl': { kind: 'lifestage', idKey: 'stage_id', labelKey: 'name' },
  'nodes_category.jsonl': { kind: 'category', idKey: 'category_id', labelKey: 'name' },
  'nodes_document.jsonl': { kind: 'document', idKey: 'document_id', labelKey: 'title' },
  'nodes_condition_group.jsonl': { kind: 'group', idKey: 'group_id', labelKey: 'logic' },
  'nodes_eligibility_condition.jsonl': { kind: 'condition', idKey: 'condition_id', labelKey: 'name' },
};
export const RELATIONSHIP_FILE = 'relationships.jsonl';

/** 화면 표시 이름. 관계 지도 범례와 상세 정보에 쓴다 */
export const KIND_LABEL: Record<NodeKind, string> = {
  policy: '정책', organization: '소관 기관', region: '지역', target: '대상', lifestage: '생애단계',
  category: '분야', document: '근거 문서', group: '자격 조건', condition: '조건',
};

export const RELATION_LABEL: Record<string, string> = {
  ADMINISTERED_BY: '소관 기관', AVAILABLE_IN: '지원 지역', HAS_CATEGORY: '정책 분야', TARGETS: '지원 대상',
  APPLIES_TO_LIFESTAGE: '생애단계', SUPPORTED_BY: '근거 문서', HAS_ROOT_CONDITION_GROUP: '자격 조건',
  MODIFIES_CONDITION_OF: '조건 변경 관계', REQUIRES_CONDITION: '필요 조건', CONTAINS_GROUP: '하위 조건 묶음',
  CONTAINS_CONDITION: '조건 포함',
};

/** 조건 묶음 논리. UNKNOWN은 원천 데이터에 조건이 없거나 결합 방식을 몰라 자동 판정하지 않는 묶음이다 */
export const LOGIC_LABEL: Record<string, string> = {
  AND: '모두 충족', OR: '하나 이상 충족', NOT: '해당하면 제외', UNKNOWN: '자동 판정 안 함 (원문 확인)',
};

/** 지도에서 정책 둘레에 놓는 순서 (같은 종류끼리 모이게) */
export const KIND_ORDER: NodeKind[] = ['group', 'organization', 'category', 'region', 'target', 'lifestage', 'document', 'policy'];

/** 정책 검색 결과 최대 개수 */
export const POLICY_SEARCH_LIMIT = 40;

/** 지도 노드 이름을 몇 글자에서 자를지 (전체 이름은 상세 정보와 마우스를 올렸을 때 보여 준다) */
export const NODE_LABEL_MAX = 11;

/**
 * 생애주기 타임라인 순서와 캐릭터. id는 nodes_lifestage.jsonl의 stage_id, 나이 범위는 데이터(age_range)를 쓴다.
 * 이 값은 데이터의 '분류'이지 자격 조건이 아니다 (실제 나이 조건은 REQUIRES_CONDITION의 AGE 조건).
 */
export const LIFE_STAGES: { id: string; name: string; icon: IconName }[] = [
  { id: 'LIFESTAGE_영유아', name: '영유아', icon: 'bottle' },
  { id: 'LIFESTAGE_아동', name: '아동', icon: 'books' },
  { id: 'LIFESTAGE_청소년', name: '청소년', icon: 'bulb' },
  { id: 'LIFESTAGE_청년', name: '청년', icon: 'personaJob' },
  { id: 'LIFESTAGE_중장년', name: '중장년', icon: 'money' },
  { id: 'LIFESTAGE_노년', name: '노년', icon: 'heart' },
];

/** 나이 조건 상한이 이 값 이상이면 '상한 없음'으로 본다 (데이터는 상한이 없을 때 120을 쓴다) */
export const AGE_NO_UPPER = 120;

/** 지도 확대·축소: 최소·최대 배율과 버튼 한 번에 바뀌는 배율 */
export const ZOOM_MIN = 0.6;
export const ZOOM_MAX = 3;
export const ZOOM_STEP = 1.25;
/** 이보다 많이 움직이면 '끌기'로 보고 노드 클릭으로 처리하지 않는다 (화면 픽셀) */
export const DRAG_CLICK_TOLERANCE = 4;

/** 조건 묶음을 따라 내려가는 최대 깊이 (순환·잘못된 데이터 방어) */
export const CONDITION_TREE_MAX_DEPTH = 10;
