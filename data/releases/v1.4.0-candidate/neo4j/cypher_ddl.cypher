// ========================================================
// LifePolicy AI — Neo4j Constraints & Indexes (DDL)
// 9대 노드 레이블 유니크 제약조건 및 고속 탐색 인덱스 (총 13개 DDL)
// ========================================================

// 1. Unique Constraints
CREATE CONSTRAINT constraint_policy_id IF NOT EXISTS
FOR (p:Policy) REQUIRE p.policy_id IS UNIQUE;

CREATE CONSTRAINT constraint_document_id IF NOT EXISTS
FOR (d:Document) REQUIRE d.document_id IS UNIQUE;

CREATE CONSTRAINT constraint_agency_id IF NOT EXISTS
FOR (o:Organization) REQUIRE o.agency_id IS UNIQUE;

CREATE CONSTRAINT constraint_region_id IF NOT EXISTS
FOR (r:Region) REQUIRE r.region_id IS UNIQUE;

CREATE CONSTRAINT constraint_category_id IF NOT EXISTS
FOR (c:Category) REQUIRE c.category_id IS UNIQUE;

CREATE CONSTRAINT constraint_stage_id IF NOT EXISTS
FOR (s:LifeStage) REQUIRE s.stage_id IS UNIQUE;

CREATE CONSTRAINT constraint_target_group_id IF NOT EXISTS
FOR (tg:TargetGroup) REQUIRE tg.group_id IS UNIQUE;

CREATE CONSTRAINT constraint_condition_group_id IF NOT EXISTS
FOR (g:ConditionGroup) REQUIRE g.group_id IS UNIQUE;

CREATE CONSTRAINT constraint_eligibility_condition_id IF NOT EXISTS
FOR (ec:EligibilityCondition) REQUIRE ec.condition_id IS UNIQUE;

// 2. Indexes for High-Performance Graph Traversal
CREATE INDEX index_policy_category IF NOT EXISTS
FOR (p:Policy) ON (p.category);

CREATE INDEX index_policy_verification IF NOT EXISTS
FOR (p:Policy) ON (p.verification_status);

CREATE INDEX index_condition_group_logic IF NOT EXISTS
FOR (g:ConditionGroup) ON (g.logic);

CREATE INDEX index_eligibility_condition_type IF NOT EXISTS
FOR (ec:EligibilityCondition) ON (ec.type);
