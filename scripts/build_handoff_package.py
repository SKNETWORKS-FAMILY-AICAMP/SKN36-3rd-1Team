"""
SSOK — STEP 3 최종 인계 패키지 빌드 스크립트 (build_handoff_package.py)
Neo4j 노드/관계 JSONL, Qdrant 페이로드, DDL, 검증 쿼리, 인계 문서 및 매니페스트 전수 생성
- P0-1: 100% 참조 무결성 (Broken References = 0)
- P0-2: ConditionGroup 계층형 DAG (AND/OR/NOT/UNKNOWN)
- P0-3: chunk_id 정규식 표준화 & Qdrant Point ID (UUIDv5 결정론적 생성)
- DDL 주석 파싱 결함 수정 (13개 DDL 전수 실행)
- 관계 실측 적재 검증 로직 탑재
"""
import os
import json
import uuid
import hashlib
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
HANDOFF_DIR = BASE_DIR / "data" / "handoff"
NEO4J_DIR = HANDOFF_DIR / "neo4j"
QDRANT_DIR = HANDOFF_DIR / "qdrant"

NEO4J_DIR.mkdir(parents=True, exist_ok=True)
QDRANT_DIR.mkdir(parents=True, exist_ok=True)

# 1. 원천 정제 데이터 로드
with open(PROCESSED_DIR / "policies.jsonl", "r", encoding="utf-8") as f:
    policies = [json.loads(line) for line in f]

with open(PROCESSED_DIR / "documents.jsonl", "r", encoding="utf-8") as f:
    documents = [json.loads(line) for line in f]

with open(PROCESSED_DIR / "chunks.jsonl", "r", encoding="utf-8") as f:
    chunks = [json.loads(line) for line in f]

with open(PROCESSED_DIR / "relationships.jsonl", "r", encoding="utf-8") as f:
    existing_edges = [json.loads(line) for line in f]

print(f"Loaded: {len(policies)} policies, {len(documents)} docs, {len(chunks)} chunks, {len(existing_edges)} existing edges.")

# 2. 노드 ID 수집 및 노드 엔티티 정의
all_node_ids = set()

# 2.1 Policies 노드 (237건)
policy_nodes = []
for p in policies:
    pid = p["policy_id"]
    all_node_ids.add(pid)
    node = {
        "policy_id": pid,
        "official_id": p.get("official_id"),
        "policy_name": p.get("policy_name"),
        "source_type": p.get("source_type"),
        "category": p.get("category"),
        "agency": p.get("agency"),
        "organization": p.get("organization"),
        "region": p.get("region"),
        "life_stages": p.get("life_stages", p.get("life_cycles", [])),
        "target_groups": p.get("target_groups", []),
        "eligibility": p.get("eligibility"),
        "benefit": p.get("benefit"),
        "application_period": p.get("application_period"),
        "application_method": p.get("application_method"),
        "official_apply_url": p.get("official_apply_url"),
        "source_url": p.get("source_url"),
        "verification_status": p.get("verification_status"),
        "collected_at": p.get("collected_at")
    }
    policy_nodes.append(node)

with open(NEO4J_DIR / "nodes_policy.jsonl", "w", encoding="utf-8") as f:
    for node in policy_nodes:
        f.write(json.dumps(node, ensure_ascii=False) + "\n")

# 2.2 Organizations 노드 (116건) — relationships.jsonl의 target_id(AGENCY_*)와 100% 일치
org_edges = [e for e in existing_edges if e.get("relation") == "ADMINISTERED_BY"]
org_dict = {}
for e in org_edges:
    tgt_id = e["target_id"]
    if tgt_id not in org_dict:
        org_dict[tgt_id] = {
            "agency_id": tgt_id,
            "name": e.get("target_name") or tgt_id.replace("AGENCY_", "").replace("_", " "),
            "type": "소관부처/지자체"
        }
        all_node_ids.add(tgt_id)

with open(NEO4J_DIR / "nodes_organization.jsonl", "w", encoding="utf-8") as f:
    for o in org_dict.values():
        f.write(json.dumps(o, ensure_ascii=False) + "\n")

# 2.3 Regions 노드 (18건) — relationships.jsonl의 target_id(REGION_*)와 100% 일치
region_edges = [e for e in existing_edges if e.get("relation") == "AVAILABLE_IN"]
region_dict = {}
for e in region_edges:
    tgt_id = e["target_id"]
    if tgt_id not in region_dict:
        region_dict[tgt_id] = {
            "region_id": tgt_id,
            "name": e.get("target_name") or tgt_id.replace("REGION_", "")
        }
        all_node_ids.add(tgt_id)

with open(NEO4J_DIR / "nodes_region.jsonl", "w", encoding="utf-8") as f:
    for r in region_dict.values():
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# 2.4 Categories 노드 (5건) — relationships.jsonl의 target_id(CAT_*)와 100% 일치
cat_edges = [e for e in existing_edges if e.get("relation") == "HAS_CATEGORY"]
cat_dict = {}
for e in cat_edges:
    tgt_id = e["target_id"]
    if tgt_id not in cat_dict:
        cat_dict[tgt_id] = {
            "category_id": tgt_id,
            "name": e.get("target_name") or tgt_id.replace("CAT_", "")
        }
        all_node_ids.add(tgt_id)

with open(NEO4J_DIR / "nodes_category.jsonl", "w", encoding="utf-8") as f:
    for c in cat_dict.values():
        f.write(json.dumps(c, ensure_ascii=False) + "\n")

# 2.5 LifeStages 노드 (6대 표준 생애단계)
lifestages = [
    {"stage_id": "LIFESTAGE_영유아", "name": "영유아", "age_range": "0~5세", "description": "취학 전 영유아 및 보육 지원"},
    {"stage_id": "LIFESTAGE_아동", "name": "아동", "age_range": "6~12세", "description": "초등 학령기 아동 및 방과후 돌봄 지원"},
    {"stage_id": "LIFESTAGE_청소년", "name": "청소년", "age_range": "13~18세", "description": "중고등 청소년, 진로 및 자립 지원"},
    {"stage_id": "LIFESTAGE_청년", "name": "청년", "age_range": "19~39세", "description": "청년 구직·주거·자산형성·일자리 지원"},
    {"stage_id": "LIFESTAGE_중장년", "name": "중장년", "age_range": "40~64세", "description": "중장년 재취업, 노후 준비 및 전직 지원"},
    {"stage_id": "LIFESTAGE_노년", "name": "노년", "age_range": "65세 이상", "description": "어르신 기초연금, 노후 건강 및 돌봄 지원"}
]
for ls in lifestages:
    all_node_ids.add(ls["stage_id"])

with open(NEO4J_DIR / "nodes_lifestage.jsonl", "w", encoding="utf-8") as f:
    for ls in lifestages:
        f.write(json.dumps(ls, ensure_ascii=False) + "\n")

# 2.6 TargetGroup 노드 (11건) — relationships.jsonl의 target_id(GROUP_*)와 100% 일치
target_edges = [e for e in existing_edges if e.get("relation") == "TARGETS"]
target_group_dict = {}
for e in target_edges:
    tgt_id = e["target_id"]
    if tgt_id not in target_group_dict:
        target_group_dict[tgt_id] = {
            "group_id": tgt_id,
            "name": e.get("target_name") or tgt_id.replace("GROUP_", ""),
            "description": f"생애주기 외 특정 자격/가구/사회적 집단 ({e.get('target_name')})"
        }
        all_node_ids.add(tgt_id)

with open(NEO4J_DIR / "nodes_target_group.jsonl", "w", encoding="utf-8") as f:
    for tg in target_group_dict.values():
        f.write(json.dumps(tg, ensure_ascii=False) + "\n")

# 2.7 Documents 노드 (246건)
for d in documents:
    doc_id = d["document_id"]
    all_node_ids.add(doc_id)

with open(NEO4J_DIR / "nodes_document.jsonl", "w", encoding="utf-8") as f:
    for d in documents:
        doc_node = {
            "document_id": d["document_id"],
            "policy_id": d["policy_id"],
            "title": d["title"],
            "source_type": d["source_type"],
            "source_url": d["source_url"],
            "local_raw_path": d.get("local_raw_path"),
            "content_hash": d.get("content_hash"),
            "verification_status": d.get("verification_status")
        }
        f.write(json.dumps(doc_node, ensure_ascii=False) + "\n")

# 2.8 EligibilityConditions 노드 (52건) — relationships.jsonl의 target_id(COND_*)와 100% 일치
cond_edges = [e for e in existing_edges if e.get("relation") == "REQUIRES_CONDITION"]
conditions_dict = {}

for e in cond_edges:
    cid = e["target_id"]
    cname = e.get("target_name") or cid
    ev_loc = e.get("evidence_location") or ""
    
    # 타입 및 연산자 분류
    if cid.startswith("COND_AGE_"):
        ctype = "AGE"
        op = "BETWEEN"
        parts = cid.replace("COND_AGE_", "").split("_")
        val = f"{parts[0]}~{parts[1]}" if len(parts) >= 2 else parts[0]
        unit = "세"
    elif cid.startswith("COND_HH_"):
        ctype = "HOUSING_HOUSEHOLD"
        op = "EQUAL"
        val = cid.replace("COND_HH_", "")
        unit = "유형"
    elif cid.startswith("COND_INC_"):
        ctype = "INCOME"
        op = "IN_RANGE"
        val = cid.replace("COND_INC_", "")
        unit = "%"
    elif cid.startswith("COND_JOB_"):
        ctype = "EMPLOYMENT"
        op = "IN"
        val = cid.replace("COND_JOB_", "")
        unit = "상태"
    elif cid.startswith("COND_EDU_"):
        ctype = "EDUCATION"
        op = "IN"
        val = cid.replace("COND_EDU_", "")
        unit = "학력"
    elif cid.startswith("COND_SP_"):
        ctype = "SPECIAL_TARGET"
        op = "IN"
        val = cid.replace("COND_SP_", "")
        unit = "특성"
    else:
        ctype = "OTHER"
        op = "EQUAL"
        val = cid
        unit = ""

    if cid not in conditions_dict:
        conditions_dict[cid] = {
            "condition_id": cid,
            "type": ctype,
            "name": cname,
            "operator": op,
            "value": val,
            "unit": unit,
            "evidence_location": ev_loc,
            "description": f"정책 지원 자격 조건 ({cname})"
        }
        all_node_ids.add(cid)

with open(NEO4J_DIR / "nodes_eligibility_condition.jsonl", "w", encoding="utf-8") as f:
    for ec in conditions_dict.values():
        f.write(json.dumps(ec, ensure_ascii=False) + "\n")

# 2.9 ConditionGroups 노드 & 계층적 DAG 관계 구축 (P0-2)
pol_conditions = defaultdict(list)
for e in cond_edges:
    pol_conditions[e["source_id"]].append(e["target_id"])

condition_groups = []
handoff_edges = list(existing_edges) # 기존 2,537건 보존

for p in policies:
    pid = p["policy_id"]
    pname = p.get("policy_name", pid)
    req = p.get("requirements") or {}
    v_stat = p.get("verification_status", "extracted")
    ev_doc = p.get("source_document_ids", [None])[0] if p.get("source_document_ids") else None
    
    cond_list = pol_conditions.get(pid, [])
    has_exclusion = bool(req.get("exclusion_criteria"))
    
    # 루트 그룹 식별자
    root_grp_id = f"GRP_{pid}_ROOT"
    all_node_ids.add(root_grp_id)
    
    # 엣지: Policy -> Root Condition Group
    handoff_edges.append({
        "source_id": pid,
        "relation": "HAS_ROOT_CONDITION_GROUP",
        "target_id": root_grp_id,
        "target_name": f"Root Condition Group ({pid})",
        "evidence_document_id": ev_doc,
        "evidence_location": "기본자격요건",
        "review_status": v_stat
    })
    
    # 조건이 전혀 없는 정책 (155건): UNKNOWN 3값 논리 적용
    if not cond_list and not has_exclusion:
        condition_groups.append({
            "group_id": root_grp_id,
            "policy_id": pid,
            "logic": "UNKNOWN",
            "is_root": True,
            "description": f"{pname} 기본 자격 조건 그룹 (원천 조건 미제공 또는 전 국민 개방형 / 3값 논리 UNKNOWN 평가)"
        })
        continue
    
    # 조건이 있는 정책: 복합 DAG 구성
    type_groups = defaultdict(list)
    for cid in cond_list:
        if cid.startswith("COND_INC_"):
            type_groups["INC"].append(cid)
        elif cid.startswith("COND_HH_") and cid != "COND_HH_무주택세대":
            type_groups["HH"].append(cid)
        elif cid.startswith("COND_JOB_"):
            type_groups["JOB"].append(cid)
        elif cid.startswith("COND_EDU_"):
            type_groups["EDU"].append(cid)
        elif cid.startswith("COND_SP_"):
            type_groups["SP"].append(cid)
        else:
            type_groups["DIRECT"].append(cid)
    
    # 루트 그룹 생성 (logic: AND)
    condition_groups.append({
        "group_id": root_grp_id,
        "policy_id": pid,
        "logic": "AND",
        "is_root": True,
        "description": f"{pname} 복합 자격 조건 루트 그룹 (AND 결합)"
    })
    
    # 직결 필수 조건 (연령, 무주택 등)
    for cid in type_groups.get("DIRECT", []):
        handoff_edges.append({
            "source_id": root_grp_id,
            "relation": "CONTAINS_CONDITION",
            "target_id": cid,
            "target_name": conditions_dict[cid]["name"],
            "evidence_document_id": ev_doc,
            "evidence_location": "필수조건",
            "review_status": v_stat
        })
    
    # 선택형 서브그룹 (OR DAG)
    subgroup_configs = [
        ("INC", "소득선택조건그룹", "소득 구간 중 하나 충족 시 지원 가능 (OR)"),
        ("HH", "가구유형선택그룹", "가구 형태 중 하나 충족 시 지원 가능 (OR)"),
        ("JOB", "취업상태선택그룹", "취업 상태 중 하나 충족 시 지원 가능 (OR)"),
        ("EDU", "학력선택그룹", "학력 조건 중 하나 충족 시 지원 가능 (OR)"),
        ("SP", "특화대상선택그룹", "특화 대상자 중 하나 충족 시 지원 가능 (OR)")
    ]
    
    for t_key, t_title, t_desc in subgroup_configs:
        items = type_groups.get(t_key, [])
        if not items:
            continue
        if len(items) == 1:
            cid = items[0]
            handoff_edges.append({
                "source_id": root_grp_id,
                "relation": "CONTAINS_CONDITION",
                "target_id": cid,
                "target_name": conditions_dict[cid]["name"],
                "evidence_document_id": ev_doc,
                "evidence_location": "조건요건",
                "review_status": v_stat
            })
        else:
            sub_grp_id = f"GRP_{pid}_OR_{t_key}"
            all_node_ids.add(sub_grp_id)
            condition_groups.append({
                "group_id": sub_grp_id,
                "policy_id": pid,
                "logic": "OR",
                "is_root": False,
                "description": f"{pname} {t_title}: {t_desc}"
            })
            handoff_edges.append({
                "source_id": root_grp_id,
                "relation": "CONTAINS_GROUP",
                "target_id": sub_grp_id,
                "target_name": f"{t_title} ({sub_grp_id})",
                "evidence_document_id": ev_doc,
                "evidence_location": "선택조건그룹",
                "review_status": v_stat
            })
            for cid in items:
                handoff_edges.append({
                    "source_id": sub_grp_id,
                    "relation": "CONTAINS_CONDITION",
                    "target_id": cid,
                    "target_name": conditions_dict[cid]["name"],
                    "evidence_document_id": ev_doc,
                    "evidence_location": "선택조건",
                    "review_status": v_stat
                })
    
    # 제외 조건 (NOT DAG) — POL_005 국민내일배움카드 등
    if has_exclusion:
        not_grp_id = f"GRP_{pid}_NOT_EXCL"
        all_node_ids.add(not_grp_id)
        condition_groups.append({
            "group_id": not_grp_id,
            "policy_id": pid,
            "logic": "NOT",
            "is_root": False,
            "description": f"{pname} 참여 제외 대상 그룹: {', '.join(req.get('exclusion_criteria', []))[:80]}"
        })
        handoff_edges.append({
            "source_id": root_grp_id,
            "relation": "CONTAINS_GROUP",
            "target_id": not_grp_id,
            "target_name": f"참여제외그룹 ({not_grp_id})",
            "evidence_document_id": ev_doc,
            "evidence_location": "제외조건그룹",
            "review_status": v_stat
        })

with open(NEO4J_DIR / "nodes_condition_group.jsonl", "w", encoding="utf-8") as f:
    for cg in condition_groups:
        f.write(json.dumps(cg, ensure_ascii=False) + "\n")

with open(NEO4J_DIR / "relationships.jsonl", "w", encoding="utf-8") as f:
    for edge in handoff_edges:
        f.write(json.dumps(edge, ensure_ascii=False) + "\n")

# 3. 전수 참조 무결성 검증 (Broken Reference 검사)
print("\n" + "="*50)
print("Verifying Referential Integrity across all edges...")
broken_sources = []
broken_targets = []
for i, edge in enumerate(handoff_edges):
    src = edge["source_id"]
    tgt = edge["target_id"]
    if src not in all_node_ids:
        broken_sources.append((i, edge["relation"], src))
    if tgt not in all_node_ids:
        broken_targets.append((i, edge["relation"], tgt))

print(f"Total Edges in Handoff: {len(handoff_edges)}")
print(f"Total Unique Node IDs: {len(all_node_ids)}")
print(f"Broken Source References: {len(broken_sources)}")
print(f"Broken Target References: {len(broken_targets)}")

if broken_sources or broken_targets:
    raise ValueError(f"CRITICAL: Found broken references! Sources: {broken_sources[:5]}, Targets: {broken_targets[:5]}")
else:
    print("SUCCESS: 100% Referential Integrity Verified! Broken References = 0")
print("="*50 + "\n")

# 4. Qdrant Payload Chunks (557건) — Point ID: RFC 4122 규격 표준 UUIDv5(결정론적 생성)
with open(QDRANT_DIR / "payload_chunks.jsonl", "w", encoding="utf-8") as f:
    for chk in chunks:
        pid = chk.get("policy_id")
        pol = next((p for p in policies if p["policy_id"] == pid), {})
        l_stages = pol.get("life_stages", pol.get("life_cycles", []))
        
        # Qdrant 서버/클라이언트 표준 RFC 4122 규격 UUIDv5 생성 (chunk_id 기준 결정론적)
        uuid_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chk["chunk_id"]))
        
        payload_record = {
            "point_id": uuid_point_id,
            "chunk_id": chk["chunk_id"],
            "policy_id": chk["policy_id"],
            "document_id": chk["document_id"],
            "policy_name": chk["policy_name"],
            "category": chk["category"],
            "region": chk["region"],
            "life_stages": l_stages,
            "target_groups": pol.get("target_groups", []),
            "eligibility_attributes": pol.get("eligibility_attributes", {}),
            "section_title": chk.get("section_title", "본문"),
            "text": chk["text"],
            "source_url": chk["source_url"],
            "verification_status": chk["verification_status"]
        }
        f.write(json.dumps(payload_record, ensure_ascii=False) + "\n")

print(f"Qdrant payload chunks written: {len(chunks)} (with standard UUIDv5 point_ids)")

# 5. Qdrant Collection Config (임베딩 모델 확정 전 고정값 방지: size=null 및 프리셋 명시)
collection_cfg = {
    "collection_name": "lifepolicy_chunks",
    "vectors_config": {
        "size": None,
        "distance": "Cosine",
        "_instruction": "임베딩 모델 확정 시 model_dimension_presets의 차원으로 주입하여 컬렉션을 생성합니다 (OpenAI 1536, BGE-M3 1024 등)."
    },
    "model_dimension_presets": {
        "OpenAI/text-embedding-3-small": 1536,
        "OpenAI/text-embedding-3-large": 3072,
        "BAAI/bge-m3": 1024,
        "intfloat/multilingual-e5-large": 1024
    },
    "on_disk_payload": True,
    "payload_indexes": [
        {"field_name": "policy_id", "field_type": "keyword"},
        {"field_name": "category", "field_type": "keyword"},
        {"field_name": "region", "field_type": "keyword"},
        {"field_name": "life_stages", "field_type": "keyword"},
        {"field_name": "target_groups", "field_type": "keyword"},
        {"field_name": "verification_status", "field_type": "keyword"}
    ]
}
with open(QDRANT_DIR / "collection_config.json", "w", encoding="utf-8") as f:
    json.dump(collection_cfg, f, ensure_ascii=False, indent=2)

# 6. Cypher DDL (9대 노드 라벨 제약조건 및 인덱스 완비)
cypher_ddl = """// ========================================================
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
"""
with open(NEO4J_DIR / "cypher_ddl.cypher", "w", encoding="utf-8") as f:
    f.write(cypher_ddl)

# 7. Neo4j 검증 쿼리 스크립트 (verify_neo4j.cypher)
verify_cypher = """// ========================================================
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
"""
with open(NEO4J_DIR / "verify_neo4j.cypher", "w", encoding="utf-8") as f:
    f.write(verify_cypher)

# 8. Neo4j 파이썬 일괄 적재 및 자동 실측 검증 스크립트 (load_neo4j.py)
load_script = '''"""
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
    cleaned_text = "\\n".join(cleaned_lines)
    statements = [s.strip() for s in cleaned_text.split(";") if s.strip()]
    return statements

def run_pipeline():
    print("=" * 70)
    print("       LifePolicy AI — Neo4j DB 일괄 적재 및 전수 검증 파이프라인")
    print("=" * 70)
    
    if not NEO4J_AVAILABLE:
        print("\\n>> [NOT_TESTED] neo4j 파이썬 드라이버 라이브러리 미설치 ('pip install neo4j' 필요) <<")
        print("   안내: DB 엔지니어(주민혁 님) 인프라 환경에서 패키지 설치 및 서버 기동 후 실행하십시오.")
        return False

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except ServiceUnavailable as e:
        print(f"\\n>> [NOT_TESTED] Neo4j 서버 미실행 환경 ({NEO4J_URI}) <<")
        print("   사유: 로컬/원격 Neo4j 서버가 기동되지 않았습니다.")
        print("   안내: Neo4j 서버 기동 후 'python load_neo4j.py'를 실행하십시오.")
        return False
    except Exception as e:
        print(f"\\n>> [ERROR] Neo4j 접속 실패: {e} <<")
        return False

    with driver.session() as session:
        # 1. DDL 실행
        print("\\n[1/3] Applying DDL Constraints and Indexes...")
        ddl_file = DATA_DIR / "cypher_ddl.cypher"
        with open(ddl_file, "r", encoding="utf-8") as f:
            ddl_statements = parse_cypher_ddl(f.read())
        
        print(f"  Parsed {len(ddl_statements)} DDL statements (Target: 13).")
        for i, stmt in enumerate(ddl_statements, 1):
            session.run(stmt)
        print("  All 13 Constraints & Indexes applied successfully.")

        # 2. 노드 적재 (9대 노드)
        print("\\n[2/3] Loading 9 Node Labels (MERGE Idempotent)...")
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
        print("\\n[3/3] Loading Relationships & Verifying Actual Created Edges...")
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
        print("\\n" + "=" * 50)
        print("Final Full Graph Verification...")
        tot_nodes_db = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        tot_edges_db = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        orphan_nodes_db = session.run("MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt").single()["cnt"]

        print(f"  Total Nodes in DB: {tot_nodes_db} / Expected: {EXPECTED_TOTAL_NODES}")
        print(f"  Total Edges in DB: {tot_edges_db} / Expected: {EXPECTED_TOTAL_RELATIONS}")
        print(f"  Orphan Nodes in DB: {orphan_nodes_db} (Target: 0)")

        if tot_nodes_db == EXPECTED_TOTAL_NODES and tot_edges_db == EXPECTED_TOTAL_RELATIONS and orphan_nodes_db == 0:
            print("\\n>> [SUCCESS] Neo4j 적재 및 실제 건수 100% 검증 통과 (Idempotent PASS) <<")
            driver.close()
            return True
        else:
            print("\\n>> [FAILED] 건수 불일치 또는 고립 노드 발견 <<")
            driver.close()
            return False

if __name__ == "__main__":
    run_pipeline()
'''
with open(NEO4J_DIR / "load_neo4j.py", "w", encoding="utf-8") as f:
    f.write(load_script)

print("build_handoff_package.py successfully written!")
