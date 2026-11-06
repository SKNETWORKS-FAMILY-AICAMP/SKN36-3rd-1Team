// ========================================================
// LifePolicy AI — Neo4j Constraints & Indexes (DDL)
// ========================================================

CREATE CONSTRAINT constraint_policy_id IF NOT EXISTS
FOR (p:Policy) REQUIRE p.policy_id IS UNIQUE;

CREATE CONSTRAINT constraint_document_id IF NOT EXISTS
FOR (d:Document) REQUIRE d.document_id IS UNIQUE;

CREATE CONSTRAINT constraint_lifestage_name IF NOT EXISTS
FOR (s:LifeStage) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT constraint_organization_id IF NOT EXISTS
FOR (o:Organization) REQUIRE o.agency_id IS UNIQUE;

CREATE CONSTRAINT constraint_region_name IF NOT EXISTS
FOR (r:Region) REQUIRE r.name IS UNIQUE;

CREATE CONSTRAINT constraint_category_name IF NOT EXISTS
FOR (c:Category) REQUIRE c.name IS UNIQUE;

CREATE CONSTRAINT constraint_group_id IF NOT EXISTS
FOR (g:ConditionGroup) REQUIRE g.group_id IS UNIQUE;

CREATE CONSTRAINT constraint_condition_id IF NOT EXISTS
FOR (e:EligibilityCondition) REQUIRE e.condition_id IS UNIQUE;

CREATE INDEX index_policy_verification IF NOT EXISTS
FOR (p:Policy) ON (p.verification_status);

CREATE INDEX index_condition_type IF NOT EXISTS
FOR (e:EligibilityCondition) ON (e.type);
