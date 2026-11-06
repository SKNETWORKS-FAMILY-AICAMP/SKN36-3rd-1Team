"""저장된 정제본으로 Issue #21 조건만 복원한다. 네트워크/DB/원문 수집 없음.

출력은 새 data/candidates 하위만 허용한다. 기존 문서 노드는 바이트 단위로
복사하고 청크의 ID/본문 및 검토된 정책 메타데이터를 원본과 대조한다.
"""
import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collectors.api.youthcenter_collector import normalize_youthcenter_item
from scripts.build_clean_dataset import rebuild_policy_conditions, rebuild_condition_relationships
from scripts.build_handoff_package import build_condition_groups, build_condition_nodes
from scripts.verify_candidate_sources import verify_sources
from scripts.load_candidate_neo4j import validate_package

DAG = {"HAS_ROOT_CONDITION_GROUP", "CONTAINS_GROUP", "CONTAINS_CONDITION"}
YOUTH_FREE_TEXT_FIELDS = ("plcyNm", "plcyExplnCn", "plcySprtCn", "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "srngMthdCn", "etcMttrCn")


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_rows(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8", newline="\n")


def output_path(path):
    path = path.resolve()
    base = (ROOT / "data/candidates").resolve()
    if path == base or not path.is_relative_to(base) or path.exists():
        raise ValueError("출력은 존재하지 않는 data/candidates 하위 경로여야 합니다")
    return path


def hydrate(package):
    """인계본의 검토된 값이 우선. 정제본은 생략된 요건의 근거로만 사용한다."""
    old = {p["policy_id"]: p for p in rows(ROOT / "data/processed/policies.jsonl")}
    staged = {p["policy_id"]: p for p in rows(ROOT / "data/staging/policies.jsonl")}
    gov = {str(p["service_id"]): p for p in rows(ROOT / "data/staging/official/gov24_crosscheck_services.jsonl")}
    youth = {str(p["plcyNo"]): p for p in rows(ROOT / "data/processed/youthcenter_policies.jsonl")}
    codes = json.loads((ROOT / "data/metadata/youthcenter_code_definitions.json").read_text(encoding="utf-8"))
    docs = defaultdict(list)
    documents = rows(package / "neo4j/nodes_document.jsonl")
    for d in documents:
        docs[d["policy_id"]].append(d["document_id"])
    result = []
    for node in rows(package / "neo4j/nodes_policy.jsonl"):
        pid = node["policy_id"]
        p = copy.deepcopy(old.get(pid, staged.get(pid, {})))
        p.update(copy.deepcopy(node))
        # JA/API 조건 근거가 같은 정책의 법령 문서로 잘못 연결되지 않도록 한다.
        primary = [d["document_id"] for d in documents if d["policy_id"] == pid and d["source_type"] == node["source_type"]]
        p["source_document_ids"] = primary + [did for did in docs[pid] if did not in primary]
        p.setdefault("regions", [node["region"]])
        p.setdefault("requirements", {})
        p.setdefault("eligibility_attributes", {})
        official = str(node["official_id"])
        if official not in youth and node["source_type"] == "youthcenter_api":
            # 정책 official_id가 원천에 없을 때, 같은 정책의 온통청년 문서가 가리키는 원천 항목과
            # 신청기간·지원내용이 정확히 같을 때만 그 항목을 조건 근거로 쓴다. official_id는 바꾸지 않는다.
            for d in documents:
                cand = str(d.get("official_id") or "")
                raw = (youth.get(cand) or {}).get("raw_attributes") or {}
                if d["policy_id"] == pid and raw and raw.get("aplyYmd") == node.get("application_period")                         and str(raw.get("plcySprtCn") or "").strip() == str(node.get("benefit") or "").strip():
                    p["requirements"]["source_identity"] = {
                        "policy_official_id": official, "matched_source_id": cand, "document_id": d["document_id"],
                        "matched_fields": ["aplyYmd=application_period", "plcySprtCn=benefit"],
                        "note": "정책 official_id는 원천에 없음. 데이터 담당 정정 필요"}
                    official = cand
                    break
        if official in youth and (pid.startswith("POL_YC_") or node["source_type"] == "youthcenter_api"):
            saved = youth[official]
            raw = saved.get("raw_attributes")
            req = normalize_youthcenter_item(raw, codes, pid, docs[pid][0])["requirements"] if raw else saved["requirements"]
            for key, value in req.items():
                # 기존 정제값을 before로 남기고 이번 규칙으로 갱신한다.
                p["requirements"].setdefault(key, copy.deepcopy(value))
            if raw:
                # 연령 교차 확인용 원본 자유 서술 필드. 생성된 eligibility 문장은 쓰지 않는다.
                p["requirements"]["source_free_text"] = {
                    k: raw[k] for k in YOUTH_FREE_TEXT_FIELDS if str(raw.get(k) or "").strip()}
        elif pid not in old and official in gov:
            saved = gov[official]
            p["requirements"].update({
                "structured_support_conditions": saved["structured_support_conditions"],
                "selection_criteria": saved.get("selection_criteria"),
            })
        # 정규화기의 출력으로 기존 신청 URL·표시 본문을 덮어쓰지 않는다.
        result.append(p)
    return result


def source_kind(p):
    req = p.get("requirements") or {}
    if p["policy_id"].startswith("POL_GOV_") or p["source_type"] == "gov24_api":
        return "gov24"
    if "age_limit_yn" in req or p["source_type"] == "youthcenter_api":
        return "youthcenter"
    return "other"


def document_records(documents):
    """Document 노드 + 본문(text). 본문은 저장소 추적 파일에서 verify_candidate_sources와 같은 규칙으로 읽는다.

    Issue #5 재청킹(handoff_data.load_documents)이 쓰는 형식이다. content_hash가 다르면 중단한다.
    """
    processed = {d["document_id"]: d for d in rows(ROOT / "data/processed/documents.jsonl")}
    gov = {str(r["service_id"]): r for r in rows(ROOT / "data/staging/official/gov24_crosscheck_services.jsonl")}
    youth = {str(r["plcyNo"]): r for r in rows(ROOT / "data/processed/youthcenter_policies.jsonl")}
    result = []
    for doc in documents:
        did = doc["document_id"]
        if did in processed:
            text = processed[did]["text"]
        elif doc.get("extracted_text_path"):
            text = (ROOT / doc["extracted_text_path"]).read_text(encoding="utf-8").strip()
        elif doc["source_type"] == "gov24_api":
            text = gov[str(doc["official_id"])]["full_text"]
        elif doc["source_type"] == "youthcenter_api":
            text = youth[str(doc["official_id"])]["full_text"]
        elif doc.get("doc_type") in {"law", "ordinance"}:
            lines = (ROOT / doc["local_raw_path"]).read_text(encoding="utf-8").splitlines()
            text = "\n".join(line for line in lines if not line.startswith(">")).strip()
        else:
            raise ValueError(f"본문 규칙이 없는 문서: {did}")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != doc["content_hash"]:
            raise ValueError(f"문서 본문 해시 불일치: {did}")
        result.append({**doc, "text": text})
    return result


def policy_metadata(policy_nodes, lookup):
    """정책 단위 검색 메타데이터 (Qdrant payload 조인용).

    v1.4.0 청크에는 문서별 값(법령 조문 eligibility_attributes, 문서 verification_status)이 섞여
    청크에서 정책 메타데이터를 모으면 정책 5개가 불일치한다. 정책 값은 Policy 노드와 정제 정책에서만 가져온다.
    """
    result = []
    for node in policy_nodes:
        row = {key: node[key] for key in ("policy_id", "policy_name", "category", "region", "life_stages",
                                          "target_groups", "verification_status", "eligibility_review_status")}
        row["eligibility_attributes"] = lookup[node["policy_id"]].get("eligibility_attributes") or {}
        if node.get("region_detail"):
            row["region_detail"] = node["region_detail"]
        result.append(row)
    return result


def rebuild_chunk_metadata(chunk, policy):
    """문서별 법조문 등 기존 메타데이터는 유지하고 조건 관련 값만 갱신한다."""
    result = copy.deepcopy(chunk)
    attrs = result["eligibility_attributes"]
    if "income_level" in policy["eligibility_attributes"]:
        attrs["income_level"] = copy.deepcopy(policy["eligibility_attributes"]["income_level"])
    result.update(region=policy["region"], eligibility_review_status=policy["eligibility_review_status"])
    if policy.get("region_detail"):
        # 값이 있을 때만 둔다. null 속성은 Graph/Vector 계약 검증에서 허용되지 않는다.
        result["region_detail"] = policy["region_detail"]
    return result


def with_policy_review_status(policies, groups):
    """불확실한 그룹이나 기존 검토 표시를 보존하고 정상 정책은 강제 보류하지 않는다."""
    result = copy.deepcopy(policies)
    unknown = {g["policy_id"] for g in groups if g["logic"] == "UNKNOWN"}
    for p in result:
        if p["policy_id"] in unknown:
            p["eligibility_review_status"] = "needs_review"
        else:
            p.setdefault("eligibility_review_status", p.get("verification_status", "needs_review"))
    return result


def age_conflicts(policies):
    """명시적 세 범위와 구조화 범위 대조 후보. 다중 대상/예외는 사람 검토 필요."""
    result = []
    pattern = r"(?:만\s*)?(\d{1,2})\s*세?\s*[~∼～]\s*(?:만\s*)?(\d{1,2})\s*세"
    for p in policies:
        req = p.get("requirements") or {}
        age = (req.get("structured_support_conditions") or {}).get("연령조건") or {}
        low, high = age.get("대상연령_시작"), age.get("대상연령_종료")
        if low is None or high is None:
            continue
        text = "\n".join(str(x or "") for x in (p.get("eligibility"), req.get("selection_criteria")))
        ranges = {(int(a), int(b)) for a, b in re.findall(pattern, text)}
        different = sorted(r for r in ranges if r != (low, high))
        if different:
            result.append({"policy_id": p["policy_id"], "structured": [low, high], "text_ranges": different,
                           "evidence": text, "status": "needs_review", "scope": "명시 범위 충돌 후보, 정답 확정 아님"})
    return result


def metrics(policies, groups, edges, conditions):
    by_group = defaultdict(list)
    for e in edges:
        by_group[e["source_id"]].append(e)
    unknown = {g["policy_id"] for g in groups if g["logic"] == "UNKNOWN"}
    income5 = {g["policy_id"] for g in groups if g["logic"] == "OR" and
               len([e for e in by_group[g["group_id"]] if e["target_id"].startswith("COND_INC_")]) == 5}
    homeless = {g["policy_id"] for g in groups if g["logic"] == "AND" and
                any(e["target_id"].startswith("COND_HH_무주택") for e in by_group[g["group_id"]])}
    return {
        "income_five_band_OR_policies": len(income5), "homeless_mandatory_AND_policies": len(homeless),
        "income_five_band_any_logic_policies": len({g["policy_id"] for g in groups if
             len([e for e in by_group[g["group_id"]] if e["target_id"].startswith("COND_INC_")]) == 5}),
        "ja0410_relationship_policies": len({e["source_id"] for e in edges if
             e["relation"] == "REQUIRES_CONDITION" and e["target_id"] == "COND_HH_해당사항없음"}),
        "unknown_any_group_policies": len(unknown),
        "unknown_root_policies": sum(g["is_root"] and g["logic"] == "UNKNOWN" for g in groups),
        "unknown_child_only_policies": len(unknown - {g["policy_id"] for g in groups if g["is_root"] and g["logic"] == "UNKNOWN"}),
        "group_logic_counts": dict(Counter(g["logic"] for g in groups)),
        "unknown_by_source": dict(Counter(source_kind(p) for p in policies if p["policy_id"] in unknown)),
        "empty_NOT_groups": sum(g["logic"] == "NOT" and not by_group[g["group_id"]] for g in groups),
        "needs_review_policies": sum(p.get("eligibility_review_status") == "needs_review" for p in policies),
        "needs_review_condition_nodes": sum(c.get("review_status") == "needs_review" for c in conditions),
        "needs_review_condition_edges": sum(e["relation"] == "REQUIRES_CONDITION" and e.get("review_status") == "needs_review" for e in edges),
        "district_metadata_policies": sum(bool((p.get("requirements") or {}).get("region_evidence")) for p in policies),
        "explicit_age_conflict_candidates": len(age_conflicts(policies)),
    }


def uncertainty_audit(policies, groups, edges, conflicts, identity_conflicts):
    """정책 전체 보류와 개별 조건의 검토 범위를 구분해 기록한다."""
    condition_edges = defaultdict(list)
    policy_groups = defaultdict(list)
    for e in edges:
        if e["relation"] == "REQUIRES_CONDITION":
            condition_edges[e["source_id"]].append(e)
    for g in groups:
        policy_groups[g["policy_id"]].append(g)
    age_ids = {c["policy_id"] for c in conflicts}
    identity_ids = {c["policy_id"] for c in identity_conflicts}
    result = []
    for p in policies:
        pid = p["policy_id"]
        req = p.get("requirements") or {}
        es = condition_edges[pid]
        reasons = []
        if not es:
            reasons.append("missing_atomic_conditions")
        if source_kind(p) == "gov24" and es:
            reasons.append("ja_code_meaning_and_combination_unconfirmed")
        if any(e["target_id"].startswith("COND_YC_INCOME_") and e.get("review_status") == "needs_review" for e in es):
            reasons.append("youth_income_interpretation_unconfirmed")
        if "age_limit_yn" in req and req["age_limit_yn"] != "Y":
            reasons.append("age_flag_meaning_unconfirmed")
        if req.get("age_review_needed"):
            reasons.append("age_field_needs_review")
        if req.get("region_review_needed"):
            reasons.append("region_metadata_needs_review")
        if req.get("excluded_participants") or req.get("exclusion_criteria"):
            reasons.append("unparsed_exclusions")
        if req.get("additional_qualification"):
            reasons.append("additional_qualification_text_not_fully_structured")
        if pid in age_ids:
            reasons.append("explicit_age_range_conflict")
        if pid in identity_ids:
            reasons.append("policy_document_official_id_conflict")
        result.append({"policy_id": pid, "source": source_kind(p), "review_reasons": reasons,
                       "root_logic": next(g["logic"] for g in policy_groups[pid] if g["is_root"]),
                       "unknown_child_ids": [g["group_id"] for g in policy_groups[pid] if not g["is_root"] and g["logic"] == "UNKNOWN"],
                       "preserved_and_or_group_ids": [g["group_id"] for g in policy_groups[pid] if g["logic"] in {"AND", "OR"}],
                       "pending_condition_ids": [e["target_id"] for e in es if e.get("review_status") == "needs_review"],
                       "unflagged_condition_ids": [e["target_id"] for e in es if e.get("review_status") != "needs_review"]})
    return result


def validate(package, original):
    """DB 접근 없이 참조·DAG·불변 필드·manifest 검사. 실패 시 예외."""
    nodes = {}
    for path in sorted((package / "neo4j").glob("nodes_*.jsonl")):
        data = rows(path)
        keys = [next(k for k in r if k.endswith("_id")) for r in data]
        ids = [r[k] for r, k in zip(data, keys)]
        if len(ids) != len(set(ids)) or any(i in nodes for i in ids):
            raise ValueError(f"중복 ID: {path.name}")
        nodes.update(zip(ids, data))
    edges = rows(package / "neo4j/relationships.jsonl")
    protected_relations = lambda es: [e for e in es if e["relation"] not in DAG | {"REQUIRES_CONDITION", "AVAILABLE_IN"}]
    if protected_relations(edges) != protected_relations(rows(original / "neo4j/relationships.jsonl")):
        raise ValueError("Non-eligibility relationships changed")
    for e in edges:
        if e["source_id"] not in nodes or e["target_id"] not in nodes:
            raise ValueError(f"Broken reference: {e}")
        if e.get("evidence_document_id") and e["evidence_document_id"] not in nodes:
            raise ValueError("Broken evidence reference")
        if e["relation"] == "REQUIRES_CONDITION" and "raw_value" in e:
            condition = nodes[e["target_id"]]
            if not isinstance(e["raw_value"], str) or condition["value"] != e["raw_value"]:
                raise ValueError("Raw condition value was lost")
            if condition["type"] != e.get("condition_type"):
                raise ValueError("Condition type mismatch")
    pids = {p["policy_id"] for p in rows(package / "neo4j/nodes_policy.jsonl")}
    roots = Counter(e["source_id"] for e in edges if e["relation"] == "HAS_ROOT_CONDITION_GROUP")
    if set(roots) != pids or set(roots.values()) != {1}:
        raise ValueError("Policy root count mismatch")
    children = defaultdict(list)
    for e in edges:
        if e["relation"] == "CONTAINS_GROUP":
            children[e["source_id"]].append(e["target_id"])
    def visit(gid, stack):
        if gid in stack:
            raise ValueError("DAG cycle")
        for child in children[gid]:
            visit(child, stack | {gid})
    for gid in list(children):
        visit(gid, set())
    old = rows(original / "qdrant/payload_chunks.jsonl")
    new = rows(package / "qdrant/payload_chunks.jsonl")
    invariant = lambda r: {k: v for k, v in r.items() if k not in {"region_detail", "eligibility_attributes", "eligibility_review_status"}}
    if [invariant(r) for r in old] != [invariant(r) for r in new]:
        raise ValueError("Chunk immutable fields changed")
    for c in new:
        if c["policy_id"] not in pids or c["document_id"] not in nodes or nodes[c["document_id"]]["policy_id"] != c["policy_id"]:
            raise ValueError("Chunk reference mismatch")
    for a, b in zip(old, new):
        for key, value in a["eligibility_attributes"].items():
            if key != "income_level" and b["eligibility_attributes"].get(key) != value:
                raise ValueError(f"Chunk unrelated attribute changed: {key}")
    a = rows(original / "neo4j/nodes_policy.jsonl")
    b = rows(package / "neo4j/nodes_policy.jsonl")
    strip = lambda r: {k: v for k, v in r.items() if k not in {"region_detail", "eligibility_review_status"}}
    if [strip(r) for r in a] != [strip(r) for r in b]:
        raise ValueError("Policy protected metadata changed")
    texts = rows(package / "documents.jsonl")
    if [{k: v for k, v in d.items() if k != "text"} for d in texts] != rows(package / "neo4j/nodes_document.jsonl") or any(
            hashlib.sha256(d["text"].encode("utf-8")).hexdigest() != d["content_hash"] for d in texts):
        raise ValueError("documents.jsonl 본문/메타데이터 불일치")
    if [m["policy_id"] for m in rows(package / "qdrant/policy_metadata.jsonl")] != [p["policy_id"] for p in rows(package / "neo4j/nodes_policy.jsonl")]:
        raise ValueError("policy_metadata.jsonl 정책 목록 불일치")
    if digest(original / "neo4j/nodes_document.jsonl") != digest(package / "neo4j/nodes_document.jsonl"):
        raise ValueError("Document bytes changed")
    for p in rows(package / "processed/policies.jsonl"):
        req = p.get("requirements") or {}
        if "age_limit_yn" in req and req["age_limit_yn"] != "Y" and req.get("age_limit") is not None:
            raise ValueError("Unverified N age flag was interpreted")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        if digest(package / name) != meta["sha256"]:
            raise ValueError(f"Checksum mismatch: {name}")
    # 현 데이터 팀의 계약 검증기를 그대로 적용. DB 클라이언트를 생성하지 않는다.
    _, contract_report, _, _ = validate_package(package)
    return {"policies": len(pids), "documents": len(rows(package / "neo4j/nodes_document.jsonl")),
            "nodes": len(nodes), "relationships": len(edges), "chunks": len(new),
            "manifest_files_checked": len(manifest["files"]), "immutable_fields": "PASS", "references_and_DAG": "PASS",
            "graph_payload_contract": contract_report}


README = """# Issue #21 자격 조건 재구성 후보 (REVIEW_ONLY_NOT_FOR_DB)

v1.4.0-candidate의 정책·문서·청크 본문은 그대로 두고 자격 조건·지역 상세 메타데이터만 재구성했다.

- neo4j/: Graph 노드·관계 (Issue #5 load_graph 입력)
- qdrant/payload_chunks.jsonl: 청크 payload (v1.4.0과 ID·본문 동일), qdrant/policy_metadata.jsonl: 정책 단위 메타데이터
- documents.jsonl: 문서 261건 본문 (content_hash 검증, Issue #5 재청킹 입력)
- processed/, policy_changes.jsonl, uncertainty_audit.jsonl, comparison.json: 변경 근거와 보류 사유

UNKNOWN은 적격(TRUE)이 아니다. 사용법·판정 규칙: docs/development/ISSUE21_RAG_HANDOFF_20261107.md
검증: python scripts/rebuild_eligibility_candidate.py --verify-only <이 폴더>
"""


GENERATORS = ("scripts/rebuild_eligibility_candidate.py", "scripts/build_clean_dataset.py",
              "scripts/build_handoff_package.py", "collectors/api/gov24_collector.py",
              "collectors/api/youthcenter_collector.py")


def lf_bytes(content):
    return content.replace(bytes([13, 10]), bytes([10]))


def generator_provenance(names=GENERATORS):
    """실제 HEAD와 생성 코드의 LF 기준 해시. 미커밋 코드로 만든 후보를 커밋 결과로 표기하지 않는다."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    result = {}
    for name in names:
        work = lf_bytes((ROOT / name).read_bytes())
        shown = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=ROOT, capture_output=True)
        committed = lf_bytes(shown.stdout) if shown.returncode == 0 else None
        result[name] = {"sha256_lf": hashlib.sha256(work).hexdigest(), "matches_head": committed == work}
    return head, result


def build(output):
    output = output_path(output)
    original = ROOT / "data/releases/v1.4.0-candidate"
    protected = [p for base in (ROOT / "data", ROOT / "docs/development/experiments")
                 for p in base.rglob("*") if p.is_file() and not p.is_relative_to(ROOT / "data/candidates")]
    snapshot = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    # 기존 후보의 줄바꿈 정규화도 기록하고 바이트 불일치를 숨기지 않는다.
    original_manifest = json.loads((original / "manifest.json").read_text(encoding="utf-8"))
    input_checksums = {}
    for name, meta in original_manifest["files"].items():
        content = (original / name).read_bytes()
        status = "byte_exact" if hashlib.sha256(content).hexdigest() == meta["sha256"] else "LF_normalized_only"
        if status != "byte_exact" and hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() != meta["sha256"]:
            raise ValueError(f"Input checksum mismatch: {name}")
        input_checksums[name] = status
    head, generators = generator_provenance()
    before = hydrate(original)
    policies = rebuild_policy_conditions(before)
    original_edges = rows(original / "neo4j/relationships.jsonl")
    flat = rebuild_condition_relationships(policies, [e for e in original_edges if e["relation"] not in DAG])
    conditions = build_condition_nodes(flat)
    groups, edges, _ = build_condition_groups(policies, flat, conditions)
    policies = with_policy_review_status(policies, groups)
    output.mkdir(parents=True, exist_ok=False)
    for path in (original / "neo4j").glob("nodes_*.jsonl"):
        dest = output / "neo4j" / path.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, dest)
    lookup = {p["policy_id"]: p for p in policies}
    policy_nodes = rows(original / "neo4j/nodes_policy.jsonl")
    for n in policy_nodes:
        p = lookup[n["policy_id"]]
        n.update(region=p["region"], eligibility_review_status=p["eligibility_review_status"])
        if p.get("region_detail"):
            n["region_detail"] = p["region_detail"]
    regions = {r["region_id"]: r for r in rows(original / "neo4j/nodes_region.jsonl")}
    for e in flat:
        if e["relation"] == "AVAILABLE_IN":
            regions.setdefault(e["target_id"], {"region_id": e["target_id"], "name": e["target_name"]})
    for filename, records in (("nodes_policy", policy_nodes), ("nodes_region", regions.values()),
                              ("nodes_eligibility_condition", conditions.values()), ("nodes_condition_group", groups),
                              ("relationships", edges)):
        write_rows(output / f"neo4j/{filename}.jsonl", records)
    chunks = [rebuild_chunk_metadata(c, lookup[c["policy_id"]]) for c in rows(original / "qdrant/payload_chunks.jsonl")]
    write_rows(output / "qdrant/payload_chunks.jsonl", chunks)
    write_rows(output / "documents.jsonl", document_records(rows(original / "neo4j/nodes_document.jsonl")))
    write_rows(output / "qdrant/policy_metadata.jsonl", policy_metadata(policy_nodes, lookup))
    write_rows(output / "processed/policies.jsonl", policies)
    write_rows(output / "processed/condition_relationships.jsonl", flat)
    write_rows(output / "age_conflict_candidates.jsonl", age_conflicts(policies))
    original_groups = rows(original / "neo4j/nodes_condition_group.jsonl")
    original_conditions = rows(original / "neo4j/nodes_eligibility_condition.jsonl")
    diffs = []
    for previous, p in zip(before, policies):
        pid = p["policy_id"]
        entry = {"policy_id": pid, "source": source_kind(p)}
        for label, pool, updated in (("relations", original_edges, edges), ("groups", original_groups, groups)):
            select = (lambda e: e["source_id"] == pid and e["relation"] == "REQUIRES_CONDITION") if label == "relations" else (lambda e: e["policy_id"] == pid)
            entry[label] = {"before": [e for e in pool if select(e)], "after": [e for e in updated if select(e)]}
        for stage, pool in (("before", original_edges), ("after", edges)):
            gids = {g["group_id"] for g in entry["groups"][stage]}
            entry.setdefault("region_relationships", {})[stage] = [e for e in pool if e["source_id"] == pid and e["relation"] == "AVAILABLE_IN"]
            entry.setdefault("group_relationships", {})[stage] = [e for e in pool if e["source_id"] in gids or
                (e["source_id"] == pid and e["relation"] == "HAS_ROOT_CONDITION_GROUP")]
            cids = {e["target_id"] for e in entry["relations"][stage]}
            entry.setdefault("condition_nodes", {})[stage] = [c for c in (original_conditions if stage == "before" else conditions.values()) if c["condition_id"] in cids]
        entry["metadata"] = {k: {"before": previous.get(k), "after": p.get(k)} for k in
                             ("region", "region_detail", "requirements", "eligibility_attributes", "eligibility_review_status") if previous.get(k) != p.get(k)}
        diffs.append(entry)
    write_rows(output / "policy_changes.jsonl", diffs)
    additions = {}
    for source, prefix in (("gov_employment", "COND_JOB_"), ("youth_age", "COND_AGE_"), ("youth_income", "COND_YC_INCOME_")):
        ids = []
        for d in diffs:
            if d["source"] != ("gov24" if source == "gov_employment" else "youthcenter"):
                continue
            select = lambda e: e["target_id"].startswith(prefix)
            oldids = {e["target_id"] for e in d["relations"]["before"] if select(e)}
            newids = {e["target_id"] for e in d["relations"]["after"] if select(e)}
            if newids - oldids:
                ids.append(d["policy_id"])
        additions[source] = {"count": len(ids), "policy_ids": ids}
    source_report = verify_sources()
    documents = rows(original / "neo4j/nodes_document.jsonl")
    identity_conflicts = [{"policy_id": p["policy_id"], "policy_official_id": p["official_id"],
                          "document_id": d["document_id"], "document_official_id": d["official_id"]}
                         for p in policies for d in documents if d["policy_id"] == p["policy_id"]
                         and d.get("official_id") and str(p["official_id"]) != str(d["official_id"])]
    audit = uncertainty_audit(policies, groups, edges, age_conflicts(policies), identity_conflicts)
    write_rows(output / "uncertainty_audit.jsonl", audit)
    report = {"input": str(original.relative_to(ROOT)), "input_manifest_sha256": digest(original / "manifest.json"),
              "generator_sha256": {name: meta["sha256_lf"] for name, meta in generators.items()},
              "generator_matches_source_commit": {name: meta["matches_head"] for name, meta in generators.items()},
              "input_checksums": input_checksums,
              "before": metrics(before, original_groups, original_edges, original_conditions),
              "after": metrics(policies, groups, edges, list(conditions.values())), "added_condition_policies": additions,
              "source_verification": source_report, "official_id_conflicts": identity_conflicts,
              "review_reason_counts": dict(Counter(reason for item in audit for reason in item["review_reasons"])),
              "limitations": ["원본 255개 누락. 저장된 문서 본문 261개의 해시 검사는 수행",
                              "JA 전체 Y/age_limit_yn=N의 의미는 미확인",
                              "연령 충돌 후보는 정답 확정이 아님",
                              "서울 청년수당 정책/문서 official_id 불일치로 추가 정책의 요건 복원 보류",
                              "계약 §3.2의 N=제한없음 규정은 이번 사용자 지침과 충돌하므로 적용하지 않음",
                              "DB 적재 전 소비자의 UNKNOWN 처리와 계약 검토 필요"]}
    write_json(output / "comparison.json", report)
    (output / "README.md").write_text(README, encoding="utf-8", newline="\n")
    files = {p.relative_to(output).as_posix(): {"sha256": digest(p), "size_bytes": p.stat().st_size,
             "record_count": len(rows(p)) if p.suffix == ".jsonl" else 0} for p in sorted(output.rglob("*")) if p.is_file()}
    summary = {"total_policies": len(policy_nodes), "total_documents": len(documents), "total_chunks": len(chunks),
               "total_graph_nodes": sum(len(rows(p)) for p in (output / "neo4j").glob("nodes_*.jsonl")),
               "total_graph_edges": len(edges)}
    write_json(output / "manifest.json", {"manifest_version": "issue21-v1.4.0-review", "release_status": "REVIEW_ONLY_NOT_FOR_DB", "summary": summary,
               "source_commit": head,
               "generator_uncommitted": sorted(n for n, m in generators.items() if not m["matches_head"]), "files": files})
    validation = validate(output, original)
    if (validation["policies"], validation["documents"], validation["chunks"]) != (246, 261, 599):
        raise ValueError("Unexpected counts")
    changed = [name for name, value in snapshot.items() if digest(ROOT / name) != value]
    if changed:
        raise ValueError(f"Protected input changed: {changed}")
    validation["protected_files_unchanged"] = len(snapshot)
    validation["protected_sha256"] = snapshot
    # 이 보고서 자체를 manifest에 추가한 뒤의 최종 파일 수를 기록한다.
    validation["manifest_files_checked"] += 1
    validation["graph_payload_contract"]["manifest_files_checked"] += 1
    write_json(output / "validation.json", validation)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    path = output / "validation.json"
    manifest["files"]["validation.json"] = {"sha256": digest(path), "size_bytes": path.stat().st_size, "record_count": 0}
    write_json(output / "manifest.json", manifest)
    validation = validate(output, original)
    print(json.dumps({"output": str(output), "validation": validation, "protected_files_unchanged": len(snapshot),
                      "before": report["before"], "after": report["after"], "additions": {k: v["count"] for k,v in additions.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--output-dir", type=Path, help="새 후보 생성 경로 (존재하지 않는 data/candidates 하위)")
    target.add_argument("--verify-only", type=Path, help="받은 후보 폴더를 생성 없이 검증 (manifest SHA-256 바이트 일치 포함)")
    args = parser.parse_args()
    if args.verify_only:
        result = validate(args.verify_only.resolve(), ROOT / "data/releases/v1.4.0-candidate")
        print(json.dumps({k: v for k, v in result.items() if k != "graph_payload_contract"}, ensure_ascii=False, indent=2))
    else:
        build(args.output_dir)
