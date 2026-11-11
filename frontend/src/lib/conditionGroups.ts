import type { ConditionResult } from '../api/types';

/**
 * 조건 그룹 판별(임시). API의 conditions는 그룹 구조 없이 평평한 목록이라, 어떤 개별 조건이
 * '하나 이상 충족(OR)'이나 '제외(NOT)' 그룹 아래에 있는지 화면이 알 수 없다.
 * 그래서 Checker가 그룹마다 붙이는 종합 결과 문구(backend/eligibility/checker.py `_GROUP_REASONS`)로
 * 그룹 행을 알아본다. 4차에서 API가 그룹·제외 여부를 직접 전달하면 이 파일만 교체한다.
 */
const GROUP_REASON_PREFIX = {
  AND: '모든 조건을 충족해야 합니다(종합 결과).',
  OR: '조건 중 하나 이상 충족해야 합니다(종합 결과).',
  NOT: '해당 조건이 충족되지 않아야 합니다(종합 결과).',
} as const;

type GroupLogic = keyof typeof GROUP_REASON_PREFIX;

/** 그룹 종합 결과 행이면 논리(AND/OR/NOT), 아니면 null. Checker가 뒤에 보류 사유를 덧붙일 수 있어 앞부분으로 비교한다. */
function groupLogic(condition: ConditionResult): GroupLogic | null {
  if (condition.field !== 'eligibility') return null;
  const found = (Object.keys(GROUP_REASON_PREFIX) as GroupLogic[])
    .find((logic) => condition.reason.startsWith(GROUP_REASON_PREFIX[logic]));
  return found ?? null;
}

/**
 * 예시 응답(mock)용 AND 종합 행. Checker `_combine`의 AND 규칙과 같다:
 * 불일치가 하나라도 있으면 불일치, 모두 적합이면 적합, 그 밖(확인 필요가 있거나 조건이 없음)은 확인 필요.
 */
export function andGroupResult(conditions: ConditionResult[]): ConditionResult {
  const statuses = conditions.map((condition) => condition.status);
  const status = statuses.includes('ineligible') ? 'ineligible'
    : statuses.length > 0 && statuses.every((value) => value === 'eligible') ? 'eligible'
    : 'needs_check';
  return { field: 'eligibility', label: '자격 요건', status, reason: GROUP_REASON_PREFIX.AND };
}

export function isGroupRow(condition: ConditionResult): boolean {
  return groupLogic(condition) !== null;
}

/**
 * 모든 조건이 AND 그룹으로만 묶였다고 확인될 때만 true.
 * 이때만 개별 조건의 적합·불일치가 정책 자격에 같은 방향으로 반영된다.
 * OR·NOT 그룹, UNKNOWN·구조 오류·여러 루트처럼 그룹 행이 아닌 자격 행이 있거나 그룹 행이 없으면
 * 구조를 확인할 수 없으므로 false다.
 */
export function hasOnlyAndGroups(conditions: ConditionResult[]): boolean {
  const groups = conditions.filter((condition) => condition.field === 'eligibility').map(groupLogic);
  return groups.length > 0 && groups.every((logic) => logic === 'AND');
}

/** Checker가 비교할 조건이 없을 때 쓰는 사유(UNKNOWN 그룹, 조건 구조 없음). 뒤에 데이터 품질 보류 문구가 붙을 수 있다. */
const UNJUDGED_REASON_PREFIXES = ['UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.', '확인 가능한 자격 조건 구조가 없어'];

/**
 * 비교할 조건이 없다는 사유만 있는 정책이면 true(화면 표시: '자동 판정 안 함').
 * 나이·지역·소득 등 구체적인 행이 하나라도 있으면 false라서, 사용자가 확인·입력할 수 있는 항목은 그대로 보인다.
 */
export function isUnjudgedOnly(conditions: ConditionResult[]): boolean {
  const rows = conditions.filter((condition) => !isGroupRow(condition));
  return rows.length > 0 && rows.every((condition) => (
    condition.field === 'eligibility' && condition.status === 'needs_check'
    && UNJUDGED_REASON_PREFIXES.some((prefix) => condition.reason.startsWith(prefix))
  ));
}

/**
 * 제외(NOT) 그룹의 종합 결과가 불일치면, 그 안의 제외 대상 조건에 입력 정보가 실제로 해당한 것이다.
 * (Checker는 안쪽 결과가 '적합'일 때만 NOT을 '불일치'로 계산한다.) 어떤 조건이 해당했는지는 평평한 목록으로 알 수 없다.
 */
export function matchesExclusion(conditions: ConditionResult[]): boolean {
  return conditions.some((condition) => groupLogic(condition) === 'NOT' && condition.status === 'ineligible');
}
