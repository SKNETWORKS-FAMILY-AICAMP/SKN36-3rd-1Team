// ========================================================
// LifePolicy AI — Neo4j 적재 후 무결성 검증 쿼리
// ========================================================

// 1. 정책 수 및 6대 생애단계별 분포 확인
MATCH (p:Policy)
OPTIONAL MATCH (p)-[:APPLIES_TO_LIFESTAGE]->(s:LifeStage)
RETURN s.name AS LifeStage, count(DISTINCT p) AS PolicyCount
ORDER BY PolicyCount DESC;

// 2. 복합 조건 그룹(ConditionGroup) 및 루트 진입점 확인
MATCH (p:Policy)-[:HAS_ROOT_CONDITION_GROUP]->(rg:ConditionGroup)
RETURN rg.logic AS Logic, count(rg) AS RootGroupCount;

// 3. 고아 정책 노드 검증 (근거 문서 미연결 정책 확인 - 0건이어야 정상)
MATCH (p:Policy)
WHERE NOT (p)-[:SUPPORTED_BY]->(:Document)
RETURN count(p) AS PoliciesWithoutDocument;
