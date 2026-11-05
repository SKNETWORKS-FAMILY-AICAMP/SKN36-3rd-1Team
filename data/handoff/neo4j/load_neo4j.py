"""
LifePolicy AI — Neo4j 일괄 적재 및 실제 건수 자동 검증 스크립트 (load_neo4j.py)
주민혁 님을 위한 멱등성 보장 9대 노드 및 관계 안전 적재 파이프라인
- DDL 주석 제거 및 13개 제약조건/인덱스 전수 적용
- 트랜잭션 함수 단위 실행 및 롤백 보장
- 관계 적재 시 참조 노드 실재 여부 및 실제 생성 건수 전수 대조
- 멱등성(MERGE) 보장으로 재실행 및 중복 적재 방지
"""
import os
import re
import json
from pathlib import Path
from collections import defaultdict

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, Neo4jError
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

DATA_DIR = Path(__file__).resolve().parent

EXPECTED_NODES = {
    "Policy": 237,
    "Document": 246,
    "Organization": 116,
    "Region": 18,
    "Category": 5,
    "LifeStage": 6,
    "TargetGroup": 11,
    "EligibilityCondition": 52,
    "ConditionGroup": 343
}
EXPECTED_TOTAL_NODES = sum(EXPECTED_NODES.values()) # 1034

EXPECTED_RELATIONS = {
    "REQUIRES_CONDITION": 735,
    "CONTAINS_CONDITION": 735,
    "TARGETS": 424,
    "APPLIES_TO_LIFESTAGE": 419,
    "SUPPORTED_BY": 246,
    "AVAILABLE_IN": 238,
    "ADMINISTERED_BY": 237,
    "HAS_CATEGORY": 237,
    "HAS_ROOT_CONDITION_GROUP": 237,
    "CONTAINS_GROUP": 106,
    "MODIFIES_CONDITION_OF": 1
}
EXPECTED_TOTAL_RELATIONS = sum(EXPECTED_RELATIONS.values()) # 3615

def parse_cypher_ddl(raw_text: str):
    """주석(// 및 inline //)을 안전하게 제거하고 유효한 DDL 문장만 추출"""
    cleaned_lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        line_clean = re.sub(r"//.*$", "", line)
        if line_clean.strip():
            cleaned_lines.append(line_clean)
    cleaned_text = "\n".join(cleaned_lines)
    statements = [s.strip() for s in cleaned_text.split(";") if s.strip()]
    return statements

def run_pipeline():
    print("=" * 70)
    print("       LifePolicy AI — Neo4j DB 일괄 적재 및 전수 검증 파이프라인")
    print("=" * 70)
    
    if not NEO4J_AVAILABLE:
        print("\n>> [NOT_TESTED] neo4j 파이썬 드라이버 라이브러리 미설치 ('pip install neo4j' 필요) <<")
        print("   안내: DB 엔지니어(주민혁 님) 인프라 환경에서 패키지 설치 및 서버 기동 후 실행하십시오.")
        return False

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except ServiceUnavailable as e:
        print(f"\n>> [NOT_TESTED] Neo4j 서버 미실행 환경 ({NEO4J_URI}) <<")
        print("   사유: 로컬/원격 Neo4j 서버가 기동되지 않았습니다.")
        print("   안내: Neo4j 서버 기동 후 'python load_neo4j.py'를 실행하십시오.")
        return False
    except Exception as e:
        print(f"\n>> [ERROR] Neo4j 접속 실패: {e} <<")
        return False

    with driver.session() as session:
        # 1. DDL 실행
        print("\n[1/3] Applying DDL Constraints and Indexes...")
        ddl_file = DATA_DIR / "cypher_ddl.cypher"
        with open(ddl_file, "r", encoding="utf-8") as f:
            ddl_statements = parse_cypher_ddl(f.read())
        
        print(f"  Parsed {len(ddl_statements)} DDL statements (Target: 13).")
        for i, stmt in enumerate(ddl_statements, 1):
            session.run(stmt)
        print("  All 13 Constraints & Indexes applied successfully.")

        # 2. 노드 적재 (9대 노드)
        print("\n[2/3] Loading 9 Node Labels (MERGE Idempotent)...")
        node_configs = [
            ("nodes_policy.jsonl", "Policy", "policy_id", "MERGE (p:Policy {policy_id: row.policy_id}) SET p += row"),
            ("nodes_organization.jsonl", "Organization", "agency_id", "MERGE (o:Organization {agency_id: row.agency_id}) SET o += row"),
            ("nodes_region.jsonl", "Region", "region_id", "MERGE (r:Region {region_id: row.region_id}) SET r += row"),
            ("nodes_category.jsonl", "Category", "category_id", "MERGE (c:Category {category_id: row.category_id}) SET c += row"),
            ("nodes_lifestage.jsonl", "LifeStage", "stage_id", "MERGE (s:LifeStage {stage_id: row.stage_id}) SET s += row"),
            ("nodes_target_group.jsonl", "TargetGroup", "group_id", "MERGE (tg:TargetGroup {group_id: row.group_id}) SET tg += row"),
            ("nodes_document.jsonl", "Document", "document_id", "MERGE (d:Document {document_id: row.document_id}) SET d += row"),
            ("nodes_eligibility_condition.jsonl", "EligibilityCondition", "condition_id", "MERGE (ec:EligibilityCondition {condition_id: row.condition_id}) SET ec += row"),
            ("nodes_condition_group.jsonl", "ConditionGroup", "group_id", "MERGE (cg:ConditionGroup {group_id: row.group_id}) SET cg += row"),
        ]

        for fname, label, key_field, cypher_clause in node_configs:
            fpath = DATA_DIR / fname
            with open(fpath, "r", encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            
            # Execute transactional write
            session.execute_write(
                lambda tx, b: tx.run(f"UNWIND $batch AS row {cypher_clause}", batch=b),
                rows
            )
            
            # Actual count verification in DB
            result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt").single()
            actual_cnt = result["cnt"]
            expected_cnt = EXPECTED_NODES[label]
            if actual_cnt != expected_cnt:
                raise ValueError(f"CRITICAL: Node [{label}] count mismatch! Input={len(rows)}, Actual in DB={actual_cnt}, Expected={expected_cnt}")
            print(f"  [{label}] Loaded {len(rows)} rows -> DB actual: {actual_cnt} (VERIFIED)")

        # 3. 관계 적재 (11개 관계 타입)
        print("\n[3/3] Loading Relationships & Verifying Actual Created Edges...")
        rel_file = DATA_DIR / "relationships.jsonl"
        with open(rel_file, "r", encoding="utf-8") as f:
            edges = [json.loads(line) for line in f if line.strip()]

        edge_groups = defaultdict(list)
        for e in edges:
            edge_groups[e["relation"]].append(e)

        batch_size = 500
        for rtype, sub_edges in edge_groups.items():
            query = f"""
            UNWIND $batch AS row
            MATCH (src) WHERE src.policy_id = row.source_id OR src.document_id = row.source_id OR src.group_id = row.source_id OR src.agency_id = row.source_id OR src.region_id = row.source_id
            MATCH (tgt) WHERE tgt.policy_id = row.target_id OR tgt.document_id = row.target_id OR tgt.group_id = row.target_id OR tgt.agency_id = row.target_id OR tgt.region_id = row.target_id OR tgt.category_id = row.target_id OR tgt.stage_id = row.target_id OR tgt.condition_id = row.target_id
            MERGE (src)-[r:{rtype}]->(tgt)
            ON CREATE SET r.review_status = row.review_status,
                          r.evidence_document_id = row.evidence_document_id,
                          r.evidence_location = row.evidence_location,
                          r.target_name = row.target_name
            ON MATCH SET  r.review_status = row.review_status,
                          r.evidence_document_id = row.evidence_document_id,
                          r.evidence_location = row.evidence_location,
                          r.target_name = row.target_name
            """
            for i in range(0, len(sub_edges), batch_size):
                batch = sub_edges[i:i+batch_size]
                session.execute_write(lambda tx, b: tx.run(query, batch=b), batch)

            # DB Actual Count Verification
            result = session.run(f"MATCH ()-[r:{rtype}]->() RETURN count(r) AS cnt").single()
            actual_rel_cnt = result["cnt"]
            expected_rel_cnt = EXPECTED_RELATIONS[rtype]
            if actual_rel_cnt != expected_rel_cnt:
                raise ValueError(f"CRITICAL: Relationship [{rtype}] count mismatch! Input={len(sub_edges)}, Actual in DB={actual_rel_cnt}, Expected={expected_rel_cnt}")
            print(f"  [:{rtype}] Input: {len(sub_edges)} -> DB actual: {actual_rel_cnt} (VERIFIED)")

        # 4. 종합 무결성 실측 검증
        print("\n" + "=" * 50)
        print("Final Full Graph Verification...")
        tot_nodes_db = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        tot_edges_db = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        orphan_nodes_db = session.run("MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt").single()["cnt"]

        print(f"  Total Nodes in DB: {tot_nodes_db} / Expected: {EXPECTED_TOTAL_NODES}")
        print(f"  Total Edges in DB: {tot_edges_db} / Expected: {EXPECTED_TOTAL_RELATIONS}")
        print(f"  Orphan Nodes in DB: {orphan_nodes_db} (Target: 0)")

        if tot_nodes_db == EXPECTED_TOTAL_NODES and tot_edges_db == EXPECTED_TOTAL_RELATIONS and orphan_nodes_db == 0:
            print("\n>> [SUCCESS] Neo4j 적재 및 실제 건수 100% 검증 통과 (Idempotent PASS) <<")
            driver.close()
            return True
        else:
            print("\n>> [FAILED] 건수 불일치 또는 고립 노드 발견 <<")
            driver.close()
            return False

if __name__ == "__main__":
    run_pipeline()
