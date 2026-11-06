// ========================================================
// LifePolicy AI — Neo4j 적재 검증 쿼리 세트
// 주민혁 님 (Neo4j & Qdrant 엔지니어) 검증용 Cypher
// ========================================================

// 1. 노드 라벨별 건수 확인 (목표: Policy 237, Document 246, Org 116, Reg 18, Cat 5, LifeStage 6, TargetGroup 11, Cond 52, Group 343 / 총 1,034개)
MATCH (n)
RETURN labels(n)[0] AS Label, count(n) AS Count
ORDER BY Count DESC;

// 2. 관계 유형별 건수 확인 (목표: 총 3,615건)
MATCH ()-[r]->()
RETURN type(r) AS RelationType, count(r) AS Count
ORDER BY Count DESC;

// 3. 고립 노드(Orphan Node) 탐색 (목표: 반드시 0건)
MATCH (n)
WHERE NOT (n)--()
RETURN labels(n)[0] AS OrphanLabel, count(n) AS OrphanCount;

// 4. ConditionGroup DAG 계층 구조 탐색 예시 (루트 -> 서브그룹 -> 조건)
MATCH (p:Policy)-[:HAS_ROOT_CONDITION_GROUP]->(rg:ConditionGroup)
OPTIONAL MATCH (rg)-[:CONTAINS_GROUP]->(sg:ConditionGroup)
OPTIONAL MATCH (sg)-[:CONTAINS_CONDITION]->(ec:EligibilityCondition)
RETURN p.policy_id, p.policy_name, rg.logic AS root_logic, sg.logic AS sub_logic, ec.name AS condition
LIMIT 20;

// 5. 특정 사용자 프로필 기반 Cypher 질의 예시 (만 29세 청년, 신혼부부, 서울 거주)
MATCH (p:Policy)-[:AVAILABLE_IN]->(r:Region {region_id: 'REGION_SEOUL'})
MATCH (p)-[:APPLIES_TO_LIFESTAGE]->(ls:LifeStage {stage_id: 'LIFESTAGE_청년'})
MATCH (p)-[:TARGETS]->(tg:TargetGroup {group_id: 'GROUP_NEWLYWED'})
RETURN p.policy_id, p.policy_name, p.benefit, p.official_apply_url;
