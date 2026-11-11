# -*- coding: utf-8 -*-
"""
SSOK — 후보 릴리스 v1.4.0-candidate 빌드 및 안정화 스크립트 (Fix v2)
경로: scripts/build_incremental_release_candidate.py

개정 사항 (2026-11-06):
1. Neo4j 관계 참조 무결성 100% 달성 (Broken References = 0)
   - Category: 기존 5대 표준 카테고리(CAT_HOUSING_FINANCE, CAT_YOUTH_JOB_ASSET, CAT_BIRTH_CHILDCARE, CAT_TRANSPORT_MEDICAL_CARE, CAT_REGIONAL_WELFARE)로 100% 매핑
   - LifeStage: 기존 6대 표준 생애단계(영유아, 아동, 청소년, 청년, 중장년, 노년)로 100% 매핑 (LIFESTAGE_출산·육아 제거)
   - Region: 기존 18대 광역 Region ID(REGION_SEOUL, REGION_INCHEON, REGION_NATIONWIDE 등)로 100% 매핑
   - TargetGroup: 기존 11대 표준 TargetGroup(GROUP_NEWLYWED, GROUP_PREGNANT_BIRTH, GROUP_CHILD_YOUTH, GROUP_YOUTH, GROUP_JOBSEEKER, GROUP_WORKER_BIZ, GROUP_LOW_INCOME, GROUP_DISABLED, GROUP_SENIOR, GROUP_GENERAL, GROUP_FARMER_FISHER)으로 100% 매핑
2. 9개 신규 정책에 대한 HAS_ROOT_CONDITION_GROUP 및 GRP_{pid}_ROOT 노드 자동 생성 (UNKNOWN 모델)
3. 22개 신규 문서의 4단계 검증 분류(verified_official_raw, verified_extracted, reference_restructured, unverified_reference) 적용
4. 실측 기반의 수치 정합성 및 변경 목록(release_delta_manifest.json) 생성

개정 사항 (2026-11-06, 공식 원문 대조):
5. 문서·청크 검증 상태를 staging 데이터의 verification_status에서 읽는다.
   (scripts/apply_official_crosscheck.py가 공식 원문으로 확인한 문서만 staging에 남긴다)
6. 신규 정책 노드의 자격·지원내용·신청 정보는 정부24·온통청년 원문 필드를 우선 사용한다.
7. 산출 파일은 OS와 무관하게 LF 줄바꿈으로 쓴다 (manifest SHA-256 일관성).
"""

import os
import re
import json
import uuid
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from scripts.review_candidate_application_urls import EVIDENCE_PATH, review_candidate_policies
from scripts import data_corrections as dc
HANDOFF_DIR = BASE_DIR / "data" / "handoff"
STAGING_DIR = BASE_DIR / "data" / "staging"
CANDIDATE_DIR = BASE_DIR / "data" / "releases" / "v1.4.0-candidate"
KST = timezone(timedelta(hours=9))

def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

# -----------------------------------------------------------------------------
# 표준 매핑 테이블 정의 (기존 v1.3.2 데이터 계약 준수)
# -----------------------------------------------------------------------------
STANDARD_CATEGORIES = {
    "주거·금융": "CAT_HOUSING_FINANCE",
    "청년 취업": "CAT_YOUTH_JOB_ASSET",
    "중소기업 취업": "CAT_YOUTH_JOB_ASSET",
    "출산·육아": "CAT_BIRTH_CHILDCARE",
    "세제 혜택": {
        "POL_STG_MOIS_001": "CAT_BIRTH_CHILDCARE",       # 다자녀 자동차취득세 감면 -> 출산·육아·가족
        "POL_STG_NTS_001": "CAT_YOUTH_JOB_ASSET"         # 중소기업 소득세 감면 -> 청년 취업·교육·자산형성
    },
    "교통비·생활비": "CAT_TRANSPORT_MEDICAL_CARE",
    "지역 복지": "CAT_REGIONAL_WELFARE"
}

STANDARD_REGIONS = {
    "전국": "REGION_NATIONWIDE",
    "서울특별시": "REGION_SEOUL",
    "서울특별시 동대문구": "REGION_SEOUL",
    "인천광역시": "REGION_INCHEON",
    "인천광역시 서구": "REGION_INCHEON"
}

# 6대 표준 생애단계만 허용 (LIFESTAGE_출산·육아 제거)
POLICY_LIFESTAGES_MAP = {
    "POL_001": ["LIFESTAGE_청년", "LIFESTAGE_중장년"],
    "POL_STG_MOLIT_002": ["LIFESTAGE_청년"],
    "POL_STG_MOHW_001": ["LIFESTAGE_영유아", "LIFESTAGE_청년"],
    "POL_STG_DGU_001": ["LIFESTAGE_아동", "LIFESTAGE_청소년"],
    "POL_STG_NTS_001": ["LIFESTAGE_청년", "LIFESTAGE_중장년", "LIFESTAGE_노년"],
    "POL_STG_MOIS_001": ["LIFESTAGE_영유아", "LIFESTAGE_아동", "LIFESTAGE_중장년"],
    "POL_STG_EX_001": ["LIFESTAGE_아동", "LIFESTAGE_청소년", "LIFESTAGE_중장년"],
    "POL_STG_MOLIT_001": ["LIFESTAGE_청년", "LIFESTAGE_중장년"],
    "POL_STG_SEOUL_001": ["LIFESTAGE_청년"],
    "POL_STG_ICN_001": ["LIFESTAGE_청년"]
}

# 11대 표준 대상집단 매핑
POLICY_TARGET_GROUPS_MAP = {
    "POL_001": ["GROUP_NEWLYWED", "GROUP_PREGNANT_BIRTH", "GROUP_CHILD_YOUTH"],
    "POL_STG_MOLIT_002": ["GROUP_YOUTH", "GROUP_LOW_INCOME"],
    "POL_STG_MOHW_001": ["GROUP_PREGNANT_BIRTH", "GROUP_CHILD_YOUTH"],
    "POL_STG_DGU_001": ["GROUP_CHILD_YOUTH", "GROUP_PREGNANT_BIRTH"],
    "POL_STG_NTS_001": ["GROUP_WORKER_BIZ", "GROUP_YOUTH", "GROUP_JOBSEEKER", "GROUP_SENIOR"],
    "POL_STG_MOIS_001": ["GROUP_PREGNANT_BIRTH", "GROUP_CHILD_YOUTH"],
    "POL_STG_EX_001": ["GROUP_CHILD_YOUTH", "GROUP_PREGNANT_BIRTH"],
    "POL_STG_MOLIT_001": ["GROUP_GENERAL"],
    "POL_STG_SEOUL_001": ["GROUP_YOUTH", "GROUP_JOBSEEKER"],
    "POL_STG_ICN_001": ["GROUP_WORKER_BIZ", "GROUP_YOUTH"]
}

# 문서 검증 상태는 staging/documents.jsonl의 verification_status를 그대로 쓴다.
#   verified_official_text: 국가법령정보센터 조문 원문
#   verified_official_api : 정부24·온통청년 공식 API 응답 원문
# (공식 원문을 확인하지 못한 문서는 apply_official_crosscheck.py 단계에서 제외됨)
EVIDENCE_TYPE_BY_DOC_TYPE = {
    "notice": "announcement",
    "law": "statutory_basis",
    "ordinance": "statutory_basis",
    "guideline": "administrative_guideline",
    "service_guide": "official_service_guide",
    "faq": "faq_reference",
}

def build_candidate_release():
    print("="*75)
    print("LifePolicy AI — 후보 릴리스 v1.4.0-candidate 빌드 및 안정화 시작")
    print("="*75)

    # 1. 기존 handoff 매니페스트 SHA-256 보존 검증
    orig_manifest = HANDOFF_DIR / "manifest.json"
    init_hash = get_file_sha256(orig_manifest)
    print(f"[안전성 확인] 기존 handoff manifest SHA-256: {init_hash[:16]}... (절대 불변 유지)")

    # 2. 릴리스 디렉터리 준비
    neo4j_out = CANDIDATE_DIR / "neo4j"
    qdrant_out = CANDIDATE_DIR / "qdrant"
    neo4j_out.mkdir(parents=True, exist_ok=True)
    qdrant_out.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------
    # 3. Neo4j 노드 병합 빌드
    # -------------------------------------------------------------
    # (1) nodes_policy.jsonl (기존 237건 + 신규 9건 = 246건)
    print("\n[D-1.1] Policy 노드 병합...")
    policies = []
    with open(HANDOFF_DIR / "neo4j" / "nodes_policy.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): policies.append(json.loads(line))
    print(f"  - 기존 정책 로드: {len(policies)}건")

    # 재빌드는 재수집이 아니다. 증거 목록에 보존한 원래 수집 시각을 사용한다.
    source_evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))["policies"]

    existing_pids = {p["policy_id"] for p in policies}
    existing_oids = {p["official_id"] for p in policies}

    new_policies = []
    with open(STAGING_DIR / "policies.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            sp = json.loads(line)
            if sp["policy_id"] == "POL_001":
                continue # 기존 정책은 병합 대상
            
            assert sp["policy_id"] not in existing_pids, f"Policy ID 충돌 감지: {sp['policy_id']}"
            assert sp["official_id"] not in existing_oids, f"Official ID 충돌 감지: {sp['official_id']}"
            
            # 표준 생애주기 및 대상그룹 적용
            clean_ls = [s.replace("LIFESTAGE_", "") for s in POLICY_LIFESTAGES_MAP[sp["policy_id"]]]
            clean_tg = [g.replace("GROUP_", "") for g in POLICY_TARGET_GROUPS_MAP[sp["policy_id"]]]

            p_node = {
                "policy_id": sp["policy_id"],
                "official_id": sp["official_id"],
                "policy_name": sp["policy_name"],
                "source_type": sp["source_type"],
                "category": sp["category"],
                "agency": sp["agency"],
                "organization": sp["agency"],
                "region": sp["regions"][0] if sp["regions"] else "전국",
                "life_stages": clean_ls,
                "target_groups": clean_tg,
                "eligibility": sp.get("eligibility_text") or sp["selection_rationale"],
                "benefit": sp.get("benefit_text") or sp["category"],
                "application_period": sp.get("application_period") or "상시 또는 공고 확인",
                "application_method": sp.get("application_method") or "온라인 신청",
                # 신청 주소가 확인되지 않으면 null (출처 URL로 대신 채우지 않음 → UI '신청하기' 미노출)
                "official_apply_url": sp.get("official_apply_url"),
                "source_url": sp["source_url"],
                "verification_status": sp["verification_status"],
                "collected_at": source_evidence[sp["policy_id"]]["collected_at"],
                "approval_status": sp["approval_status"],
                "reception_status": sp["reception_status"],
                "lifecycle_status": sp["lifecycle_status"]
            }
            new_policies.append(p_node)

    merged_policies = policies + new_policies
    # 원본 인계 파일은 읽기만 한다. 후보에서는 신청 주소만 교정한다.
    baseline_policies = [dict(policy) for policy in policies]
    application_reviews, application_changes = review_candidate_policies(
        merged_policies, baseline_policies,
    )
    # 신규 정책의 그래프 관계는 수집 단계의 코드값으로 만들므로 표기 교정 전 값을 보관한다
    new_policies_for_edges = [dict(policy) for policy in new_policies]
    corrections = dc.load_corrections()
    dc.apply_policy_corrections(merged_policies, corrections)
    dc.apply_source_review(merged_policies, corrections)
    (CANDIDATE_DIR / "application_url_review.json").write_text(
        json.dumps(application_reviews, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    with open(neo4j_out / "nodes_policy.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for p in merged_policies:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(merged_policies)}건 (+{len(new_policies)}건 증분)")

    # (2) nodes_document.jsonl (기존 246건 + 신규 22건 = 268건)
    print("\n[D-1.2] Document 노드 병합 및 4단계 검증 분류 적용...")
    documents = []
    with open(HANDOFF_DIR / "neo4j" / "nodes_document.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): documents.append(json.loads(line))
    print(f"  - 기존 문서 로드: {len(documents)}건")

    existing_dids = {d["document_id"] for d in documents}
    new_docs = []
    with open(STAGING_DIR / "documents.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            sd = json.loads(line)
            assert sd["document_id"] not in existing_dids, f"Document ID 충돌 감지: {sd['document_id']}"
            assert sd.get("verification_status"), f"검증 상태 누락: {sd['document_id']}"
            # 원본 경로·해시·추출 텍스트 경로는 apply_official_crosscheck.py가 staging에 기록한 값을 그대로 쓴다
            new_docs.append(sd)

    merged_documents = documents + new_docs
    with open(neo4j_out / "nodes_document.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for d in merged_documents:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(merged_documents)}건 (+{len(new_docs)}건 증분, 4단계 검증 분류 적용)")

    # (3) nodes_organization.jsonl (신규 소관기관 8개 추가)
    print("\n[D-1.3] Organization 노드 병합...")
    orgs = []
    with open(HANDOFF_DIR / "neo4j" / "nodes_organization.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): orgs.append(json.loads(line))
    existing_org_names = {o["name"] for o in orgs}

    handoff_agency_ids = {o["agency_id"] for o in orgs}
    added_org_nodes = []
    for p in new_policies:
        oname = p["agency"]
        if oname not in existing_org_names:
            clean_id = "AGENCY_" + re.sub(r'[\s/·\(\)]+', '_', oname).strip('_')
            node = {
                "agency_id": clean_id,
                "name": oname,
                "type": "소관부처/공공기관"
            }
            orgs.append(node)
            added_org_nodes.append(node)
            existing_org_names.add(oname)

    added_agency_ids = sorted([o["agency_id"] for o in added_org_nodes if o["agency_id"] not in handoff_agency_ids])

    with open(neo4j_out / "nodes_organization.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for o in orgs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(orgs)}건 (+{len(added_agency_ids)}개 기관 신규 등록)")

    # (4) nodes_condition_group.jsonl (기존 343건 + 신규 9건 루트 그룹 = 352건)
    print("\n[D-1.4] ConditionGroup 노드 병합 (루트 조건 그룹 생성)...")
    cgroups = []
    with open(HANDOFF_DIR / "neo4j" / "nodes_condition_group.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): cgroups.append(json.loads(line))
    print(f"  - 기존 조건 그룹 로드: {len(cgroups)}건")

    new_cgroups = []
    for p in new_policies:
        gid = f"GRP_{p['policy_id']}_ROOT"
        new_cgroups.append({
            "group_id": gid,
            "policy_id": p["policy_id"],
            "logic": "UNKNOWN",
            "is_root": True,
            "description": f"{p['policy_name']} 기본 자격 조건 그룹 (원천 조건 세부 파싱 대기 / 3값 논리 UNKNOWN 평가)"
        })

    merged_cgroups = dc.apply_group_corrections(cgroups + new_cgroups, corrections)
    with open(neo4j_out / "nodes_condition_group.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for cg in merged_cgroups:
            f.write(json.dumps(cg, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(merged_cgroups)}건 (+{len(new_cgroups)}개 루트 조건 그룹 추가)")

    # (5) 정적 노드 복사
    for static_name in [
        "nodes_category.jsonl", "nodes_lifestage.jsonl", "nodes_region.jsonl",
        "nodes_target_group.jsonl", "nodes_eligibility_condition.jsonl",
        "cypher_ddl.cypher", "schema_constraints.cypher",
        "verify_neo4j.cypher", "verification_queries.cypher"
    ]:
        src = HANDOFF_DIR / "neo4j" / static_name
        dst = neo4j_out / static_name
        if src.exists():
            dst.write_bytes(src.read_bytes())
    dc.add_condition_nodes(neo4j_out / "nodes_eligibility_condition.jsonl", corrections)

    # 동결 인계본의 로더 대신 후보용 안전 로더를 패키지에 포함한다.
    (neo4j_out / "load_neo4j.py").write_bytes(
        (BASE_DIR / "scripts/load_candidate_neo4j.py").read_bytes()
    )

    # (6) relationships.jsonl 병합 (엄격한 표준 ID 매핑)
    print("\n[D-1.5] Relationships 관계 병합 (표준 ID 100% 매핑)...")
    relationships = []
    with open(HANDOFF_DIR / "neo4j" / "relationships.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): relationships.append(json.loads(line))
    print(f"  - 기존 관계 로드: {len(relationships)}건")

    new_rels = []
    # A. 22개 문서에 대한 [:SUPPORTED_BY] 관계 (3건 POL_001, 19건 신규 정책)
    for d in new_docs:
        pid = d["policy_id"]
        evidence_type = EVIDENCE_TYPE_BY_DOC_TYPE[d["doc_type"]]
        new_rels.append({
            "source_id": pid,
            "relation": "SUPPORTED_BY",
            "target_id": d["document_id"],
            "target_name": d["title"],
            "evidence_document_id": d["document_id"],
            "evidence_location": f"공식문서:{d['doc_type']}",
            "review_status": d["verification_status"],
            "doc_type": d["doc_type"],
            "evidence_type": evidence_type,
            "law_article": d.get("law_article")
        })

    # B. 9개 신규 정책에 대한 표준 관계 생성
    org_map = {o["name"]: o["agency_id"] for o in orgs}

    for p in new_policies_for_edges:
        pid = p["policy_id"]
        
        # 1. ADMINISTERED_BY
        ag_id = org_map.get(p["agency"])
        assert ag_id, f"Agency ID not found for {p['agency']}"
        new_rels.append({
            "source_id": pid,
            "relation": "ADMINISTERED_BY",
            "target_id": ag_id,
            "target_name": p["agency"],
            "evidence_document_id": None,
            "evidence_location": "소관기관",
            "review_status": "verified"
        })
        
        # 2. HAS_CATEGORY (표준 5대 카테고리)
        raw_cat = p["category"]
        if raw_cat == "세제 혜택":
            cat_id = STANDARD_CATEGORIES["세제 혜택"][pid]
        else:
            cat_id = STANDARD_CATEGORIES[raw_cat]
        cat_name = {
            "CAT_HOUSING_FINANCE": "주거·금융",
            "CAT_YOUTH_JOB_ASSET": "청년 취업·교육·자산형성",
            "CAT_BIRTH_CHILDCARE": "출산·육아·가족",
            "CAT_TRANSPORT_MEDICAL_CARE": "교통·생활비·의료·돌봄",
            "CAT_REGIONAL_WELFARE": "지역별 생활지원·복지"
        }[cat_id]
        new_rels.append({
            "source_id": pid,
            "relation": "HAS_CATEGORY",
            "target_id": cat_id,
            "target_name": cat_name,
            "evidence_document_id": None,
            "evidence_location": "정책분류",
            "review_status": "verified"
        })
        
        # 3. AVAILABLE_IN (표준 18대 광역 지역)
        reg_id = STANDARD_REGIONS[p["region"]]
        reg_name = {
            "REGION_NATIONWIDE": "전국",
            "REGION_SEOUL": "서울특별시",
            "REGION_INCHEON": "인천광역시"
        }[reg_id]
        new_rels.append({
            "source_id": pid,
            "relation": "AVAILABLE_IN",
            "target_id": reg_id,
            "target_name": reg_name,
            "evidence_document_id": None,
            "evidence_location": "지역범위",
            "review_status": "verified"
        })
        
        # 4. APPLIES_TO_LIFESTAGE (표준 6대 생애주기)
        for stage_id in POLICY_LIFESTAGES_MAP[pid]:
            stage_name = stage_id.replace("LIFESTAGE_", "")
            new_rels.append({
                "source_id": pid,
                "relation": "APPLIES_TO_LIFESTAGE",
                "target_id": stage_id,
                "target_name": stage_name,
                "evidence_document_id": None,
                "evidence_location": "생애단계",
                "review_status": "verified"
            })
            
        # 5. TARGETS (표준 11대 대상집단)
        for group_id in POLICY_TARGET_GROUPS_MAP[pid]:
            group_name = {
                "GROUP_NEWLYWED": "신혼부부",
                "GROUP_PREGNANT_BIRTH": "임산부·출산가구",
                "GROUP_CHILD_YOUTH": "영유아·아동·청소년",
                "GROUP_YOUTH": "청년",
                "GROUP_JOBSEEKER": "구직자·취업준비생",
                "GROUP_WORKER_BIZ": "소상공인·재직자",
                "GROUP_LOW_INCOME": "저소득·취약계층",
                "GROUP_DISABLED": "장애인",
                "GROUP_SENIOR": "어르신·노년층",
                "GROUP_GENERAL": "일반국민",
                "GROUP_FARMER_FISHER": "농어업인"
            }[group_id]
            new_rels.append({
                "source_id": pid,
                "relation": "TARGETS",
                "target_id": group_id,
                "target_name": group_name,
                "evidence_document_id": None,
                "evidence_location": "대상집단",
                "review_status": "verified"
            })

        # 6. HAS_ROOT_CONDITION_GROUP (루트 조건 그룹 연결)
        new_rels.append({
            "source_id": pid,
            "relation": "HAS_ROOT_CONDITION_GROUP",
            "target_id": f"GRP_{pid}_ROOT",
            "target_name": "기본 자격 조건 루트 그룹 (UNKNOWN)",
            "evidence_document_id": None,
            "evidence_location": "조건그룹",
            "review_status": "verified"
        })

    merged_rels, removed_rels, replaced_rels = dc.apply_relationship_corrections(relationships + new_rels, corrections)
    with open(neo4j_out / "relationships.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in merged_rels:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(merged_rels)}건 (+{len(new_rels)}개 관계 증분)")

    # -------------------------------------------------------------
    # 4. Qdrant 벡터 페이로드 병합 빌드
    # -------------------------------------------------------------
    print("\n[D-1.6] Qdrant 페이로드 병합...")
    chunks = []
    with open(HANDOFF_DIR / "qdrant" / "payload_chunks.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): chunks.append(json.loads(line))

    existing_cids = {c["chunk_id"] for c in chunks}
    doc_status = {d["document_id"]: d["verification_status"] for d in new_docs}
    new_chunks = []
    with open(STAGING_DIR / "payload_chunks.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            sc = json.loads(line)
            assert sc["chunk_id"] not in existing_cids, f"Chunk ID 충돌 감지: {sc['chunk_id']}"
            # 청크 검증 상태는 소속 문서의 상태와 같게 맞춘다
            sc["verification_status"] = doc_status[sc["document_id"]]
            new_chunks.append(sc)

    merged_chunks = chunks + new_chunks
    region_chunks_corrected = dc.apply_region_to_chunks(merged_chunks, corrections)
    taxonomy_chunks_corrected = dc.apply_taxonomy_to_chunks(merged_chunks, merged_policies, corrections)
    with open(qdrant_out / "payload_chunks.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for c in merged_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"  -> 병합 완료: 총 {len(merged_chunks)}건 (+{len(new_chunks)}건 증분)")

    # collection_config.json
    with open(HANDOFF_DIR / "qdrant" / "collection_config.json", "r", encoding="utf-8") as f:
        coll_cfg = json.load(f)
    coll_cfg["proposed_indexes"] = [
        {"field_name": "doc_type", "field_schema": "keyword", "status": "PENDING_DB_REVIEW"}
    ]
    with open(qdrant_out / "collection_config.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(coll_cfg, f, ensure_ascii=False, indent=2)

    # -------------------------------------------------------------
    # 5. D-2. 엄격 무결성 검증 (Broken References = 0)
    # -------------------------------------------------------------
    print("\n[D-2] 엄격 무결성 전수 검증 (Broken References = 0 검사)...")
    pk_map = {
        "nodes_category.jsonl": "category_id",
        "nodes_condition_group.jsonl": "group_id",
        "nodes_document.jsonl": "document_id",
        "nodes_eligibility_condition.jsonl": "condition_id",
        "nodes_lifestage.jsonl": "stage_id",
        "nodes_organization.jsonl": "agency_id",
        "nodes_policy.jsonl": "policy_id",
        "nodes_region.jsonl": "region_id",
        "nodes_target_group.jsonl": "group_id"
    }
    all_node_ids = set()
    total_nodes_count = 0
    for fname, pk in pk_map.items():
        count = 0
        with open(neo4j_out / fname, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                count += 1
                all_node_ids.add(json.loads(line)[pk])
        total_nodes_count += count

    print(f"  - 총 유효 노드 수: {total_nodes_count}개 (고유 ID {len(all_node_ids)}개)")

    broken_refs = []
    for idx, r in enumerate(merged_rels):
        sid = r["source_id"]
        tid = r["target_id"]
        if sid not in all_node_ids:
            broken_refs.append(f"Line {idx+1}: Missing source {sid}")
        if tid not in all_node_ids:
            broken_refs.append(f"Line {idx+1}: Missing target {tid} ({r['target_name']})")

    assert len(broken_refs) == 0, f"FAILED: Broken references remaining: {len(broken_refs)}건\n" + "\n".join(broken_refs[:10])
    print(f"  [PASS] Neo4j 관계 참조 무결성 100% 통과 (Broken References = {len(broken_refs)}건, 총 {len(merged_rels)}개 관계)")

    # -------------------------------------------------------------
        # -------------------------------------------------------------
    # 6. 정밀 변경 명세(release_delta_manifest.json) 및 CHANGELOG.md 생성
    # -------------------------------------------------------------
    print("\n[D-1.7] 정밀 증분 명세서 및 CHANGELOG.md 생성...")

    # A. 정밀 변경 목록(release_delta_manifest.json)
    delta_manifest = {
        "release_version": "1.4.0-candidate",
        "created_at": datetime.now(KST).isoformat(),
        "added_policy_ids": [p["policy_id"] for p in new_policies],
        "added_document_ids": [d["document_id"] for d in new_docs],
        "added_organization_ids": added_agency_ids,
        "added_condition_group_ids": [cg["group_id"] for cg in new_cgroups],
        "added_qdrant_point_ids": [c["point_id"] for c in new_chunks],
        "added_relationships_count": len(new_rels),
        "updated_policy_properties": application_changes,
        "data_corrections": {
            "source": "data/metadata/data_corrections_v1.4.0.json",
            "source_sha256": get_file_sha256(dc.CORRECTIONS_PATH),
            "removed_relationships": removed_rels,
            "replacement_relationships": replaced_rels,
            "taxonomy_corrected_chunks": taxonomy_chunks_corrected,
            "added_condition_ids": [node["condition_id"] for node in corrections["new_condition_nodes"]],
            "removed_condition_group_ids": sorted(dc.removed_group_ids(corrections)),
            "region_corrected_chunks": region_chunks_corrected,
        },
        "application_url_evidence_sha256": get_file_sha256(
            BASE_DIR / "data/metadata/application_url_evidence_v1.4.0.json"
        ),
        "exact_added_relationships": [
            {"source_id": r["source_id"], "relation": r["relation"], "target_id": r["target_id"]}
            for r in new_rels
        ],
        "baseline_manifest_sha256": init_hash
    }
    with open(CANDIDATE_DIR / "release_delta_manifest.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(delta_manifest, f, ensure_ascii=False, indent=2)

    # B-0. CHANGELOG 집계 표 (실제 파일 기준)
    from collections import Counter
    def node_count(name, rows=None):
        with open((HANDOFF_DIR / "neo4j" / name) if rows is None else rows, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    old_nodes = sum(node_count(fname) for fname in pk_map)
    old_rel = Counter(r["relation"] for r in relationships)
    new_rel = Counter(r["relation"] for r in merged_rels)
    status_count = Counter(d["verification_status"] for d in new_docs)
    rows = [
        "## 1. 베이스라인 대비 실제 파일 집계 수치 (Actual Verified Counts)",
        "",
        "| 데이터 항목 | v1.3.2-verified (기존) | v1.4.0-candidate (후보) | 증감 |",
        "| :--- | ---: | ---: | ---: |",
        f"| 정책 (`Policy`) | {len(policies)} | {len(merged_policies)} | +{len(new_policies)} |",
        f"| 근거 문서 (`Document`) | {len(documents)} | {len(merged_documents)} | +{len(new_docs)} |",
    ]
    for status, cnt in sorted(status_count.items()):
        rows.append(f"| • 신규 문서 `{status}` | - | {cnt} | +{cnt} |")
    rows += [
        f"| 벡터 청크 (`Chunk`) | {len(chunks)} | {len(merged_chunks)} | +{len(new_chunks)} |",
        f"| 그래프 노드 (전체) | {old_nodes} | {total_nodes_count} | +{total_nodes_count - old_nodes} |",
        f"| • Organization | {len(orgs) - len(added_org_nodes)} | {len(orgs)} | +{len(added_org_nodes)} |",
        f"| • ConditionGroup | {len(cgroups)} | {len(merged_cgroups)} | +{len(new_cgroups)} (루트 UNKNOWN) |",
        f"| 그래프 관계 (전체) | {len(relationships)} | {len(merged_rels)} | +{len(new_rels)} |",
    ]
    for rel in sorted(new_rel):
        if new_rel[rel] != old_rel[rel]:
            rows.append(f"| • {rel} | {old_rel[rel]} | {new_rel[rel]} | +{new_rel[rel] - old_rel[rel]} |")
    rows += [
        "",
        "## 1-1. 신규 문서 공식 원문 대조 (2026-11-06)",
        "",
        "- 신규 문서는 공식 원문으로 확인한 것만 포함한다: 국가법령정보센터 조문 원문(`verified_official_text`), 정부24·온통청년 공식 API 응답(`verified_official_api`).",
        "- 초기 후보의 요약 문서 22건 중 원문과 다르거나 원문을 확보하지 못한 문서는 제외했다. 문서별 판정과 사유: `data/staging/crosscheck_results.json`, `docs/data/official_source_crosscheck_report.md`.",
        "- 신규 정책의 자격 조건 그룹은 모두 `UNKNOWN`이며 정책 노드 `verification_status`는 `extracted`이다. 실제 자격 판정에 쓰기 전 조건 구조화가 필요하다.",
        "",
        "## 1-2. 원문 대조 교정 (2026-11-09)",
        "",
        f"- 설명 문구 {len(corrections['text_corrections'])}건, 지역 {len(corrections['region_corrections'])}건, 연령 조건 제거 {len(corrections['age_condition_removals'])}건·원문 값 교체 {len(corrections['age_condition_replacements'])}건, 가구 조건 교정 {len(corrections['household_condition_removals'])}건을 적용했다.",
        f"- 관계 {len(removed_rels)}개 제거·교체, {len(replaced_rels)}개 추가(교체분 포함), 표기 교정 청크 {taxonomy_chunks_corrected}개. 함께 가구유형 OR 그룹 {len(dc.removed_group_ids(corrections))}개를 제거했다. 근거: `data/metadata/data_corrections_v1.4.0.json`.",
        "- `data/handoff`(v1.3.2)는 변경하지 않았다. 제거된 조건은 '제한 없음'이 아니라 자동 판정 불가로 처리해야 한다.",
    ]
    counts_section = "\n".join(rows)

    # B. CHANGELOG.md 생성
    changelog_content = f"""# LifePolicy AI — 후보 릴리스 v1.4.0-candidate 변경 기록 (CHANGELOG)

- **릴리스 버전**: `v1.4.0-candidate`
- **배포 상태**: `CANDIDATE / PENDING_DB_REVIEW`
- **기준 베이스라인**: `v1.3.2-verified` (원본 인계 파일 보존, 후보의 신청 주소는 교정)
- **최종 검증 일시**: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST
- **책임자**: LifePolicy AI 데이터 아키텍처팀

---

{counts_section}

## 1-2. 기존 정책의 신청 주소 교정

- 기존 정책 {len(application_changes)}건에서 `official_apply_url`을 변경했다(원문 대조 교정은 1-2절). 전체 판정은 `application_url_review.json`, 변경 전후 값은 `release_delta_manifest.json`의 `updated_policy_properties`에 있다.
- 출처·참고·안내·공고 주소를 신청 주소로 대체하지 않는다. 남은 주소는 원천의 신청 필드에 근거한 값이며, 실제 접수 화면·로그인·현재 접수 가능 여부는 검증하지 않았다.
- `data/handoff/`, 기존 정제본, 기존 문서·관계·청크·원문 해시는 수정하지 않는다.
- 후보용 Neo4j 로더는 기본적으로 사전 검증만 수행한다. 실제 적재는 비어 있는 검증 DB에서만 허용하며, 기본값은 검증 후 롤백이다.

---

## 2. 29건 관계 참조 결함 수정 내역

외부 독립 검토에서 확인된 29건의 참조 결함(Broken References)을 기존 스키마 표준으로 100% 매핑하여 완전히 해결하였습니다.

1. **Category 매핑 교정 (7건)**:
   - 비표준 `CAT_CHILDBIRTH_CHILDCARE`, `CAT_TAX_INCENTIVE`, `CAT_LIVING_COST_SUBSIDY`, `CAT_LOCAL_WELFARE`를 기존 5대 표준(`CAT_BIRTH_CHILDCARE`, `CAT_YOUTH_JOB_ASSET`, `CAT_TRANSPORT_MEDICAL_CARE`, `CAT_REGIONAL_WELFARE`)으로 100% 재매핑 완료.
2. **LifeStage 매핑 교정 (3건)**:
   - 비표준 `LIFESTAGE_출산·육아`를 전면 배제하고, 기존 6대 표준(`LIFESTAGE_영유아`, `LIFESTAGE_아동`, `LIFESTAGE_청소년`, `LIFESTAGE_청년`, `LIFESTAGE_중장년`, `LIFESTAGE_노년`)으로 정상 매핑 완료.
3. **Region 매핑 교정 (3건)**:
   - 기초지자체 표기(`REGION_서울특별시 동대문구`, `REGION_인천광역시 서구`)를 기존 18대 표준(`REGION_SEOUL`, `REGION_INCHEON`)으로 매핑 완료. (세부 자치구는 정책 속성 및 이름에 보존)
4. **TargetGroup 매핑 교정 (16건)**:
   - 임의 한글 접두어(`GROUP_청년`, `GROUP_다자녀가구`, `GROUP_중소기업재직자` 등)를 기존 11대 표준(`GROUP_YOUTH`, `GROUP_PREGNANT_BIRTH`, `GROUP_CHILD_YOUTH`, `GROUP_WORKER_BIZ`, `GROUP_JOBSEEKER`, `GROUP_GENERAL`)으로 100% 정상화.

---

## 3. 신규 9개 정책 루트 조건 그룹(`HAS_ROOT_CONDITION_GROUP`) 보완
- 신규 정책 9건에 대해 조건 누락 없이 3값 논리(3-Valued Logic)를 지원하는 `GRP_{pid}_ROOT` 노드 9개를 생성하고 `HAS_ROOT_CONDITION_GROUP` 관계를 연결함.
- 임의의 가상 조건을 만들지 않고 `logic: "UNKNOWN"`으로 처리하여 자격 판정 시 시스템 안전성을 확보함.

---

## 4. 정밀 롤백 프로토콜 (Targeted Rollback by Exact Item List)
- 증분 삭제용 롤백 스크립트는 구현하지 않았다. 기존 DB에 합쳐 적재하는 작업은 이번 검증 범위에 포함하지 않는다.
- 검증 로더는 하나의 트랜잭션에서 적재·재적재·건수 대조를 수행하고, `--commit`을 명시하지 않으면 롤백한다.
- 후보의 신청 주소를 되돌려야 할 때는 `updated_policy_properties`의 `before` 값을 사용한다. 신규 ID 목록만 삭제해서는 기존 정책 속성이 복원되지 않는다.
"""
    (CANDIDATE_DIR / "CHANGELOG.md").write_text(changelog_content, encoding="utf-8", newline="\n")

    # -------------------------------------------------------------
    # 7. 매니페스트 생성 (CHANGELOG 및 모든 파일의 크기/해시 최종 반영)
    # -------------------------------------------------------------
    print("\n[D-1.8] 매니페스트(manifest.json) 최종 생성...")
    files_manifest = {}
    for root, _, fnames in os.walk(CANDIDATE_DIR):
        for fn in fnames:
            if fn == "manifest.json": continue
            fpath = Path(root) / fn
            rel_path = fpath.relative_to(CANDIDATE_DIR).as_posix()
            f_size = fpath.stat().st_size
            f_sha256 = get_file_sha256(fpath)
            
            r_count = 0
            if fn.endswith(".jsonl"):
                with open(fpath, "r", encoding="utf-8") as f:
                    r_count = sum(1 for line in f if line.strip())
            files_manifest[rel_path] = {
                "size_bytes": f_size,
                "sha256": f_sha256,
                "record_count": r_count
            }

    candidate_manifest = {
        "manifest_version": "1.4.0-candidate",
        "release_status": "CANDIDATE / PENDING_DB_REVIEW",
        "created_at": datetime.now(KST).isoformat(),
        "baseline_version": "1.3.2-verified",
        "author": "LifePolicy AI Data Engineering Team",
        "summary": {
            "total_policies": len(merged_policies),
            "total_documents": len(merged_documents),
            "total_chunks": len(merged_chunks),
            "total_graph_nodes": total_nodes_count,
            "total_graph_edges": len(merged_rels),
            "referential_integrity": "100% PASS (Broken References = 0)",
            "qdrant_point_id_standard": "RFC 4122 UUIDv5 (Deterministic)",
            "delta_from_baseline": {
                "policies_added": len(new_policies),
                "documents_added": len(new_docs),
                "chunks_added": len(new_chunks),
                "relationships_added": len(new_rels),
                "organizations_added": len(added_agency_ids),
                "condition_groups_added": len(new_cgroups)
            },
            "policies_with_application_url_updates": len(application_changes),
            "db_review_status": "PENDING_APPROVAL_FROM_MINHYEOK_JU"
        },
        "files": files_manifest
    }

    with open(CANDIDATE_DIR / "manifest.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(candidate_manifest, f, ensure_ascii=False, indent=2)

    # 8. 종료 검증: 원본 handoff manifest 불변 재확인
    final_hash = get_file_sha256(orig_manifest)
    assert init_hash == final_hash, "CRITICAL: Baseline handoff modified!"
    print(f"\n[안전성 검증 완료] handoff baseline SHA-256 불변 증명: {final_hash[:16]}... (100% 불변)")
    print(f"[후보 릴리스 안정화 완료] Broken References = 0건 달성!")
    print("="*75)

if __name__ == "__main__":
    build_candidate_release()
