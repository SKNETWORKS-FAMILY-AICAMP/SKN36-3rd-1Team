"""Read-only contract tests against pinned Issue #5 Git objects (no DB/import side effects).

Known contract gaps remain failing assertions until the underlying contract is fixed.
"""
import ast
import copy
import json
import re
import subprocess
import sys
import types
import unittest
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from scripts.build_clean_dataset import (
    build_condition_edges, infer_region_from_text, rebuild_condition_relationships,
    rebuild_policy_conditions,
)
from scripts.build_handoff_package import build_condition_groups

ROOT = Path(__file__).resolve().parents[2]
CONSUMER_REF = "04a9edc366090f151b3f0ac6f74db8b6206c7ca1"  # feature/5-hybrid-rag-db 최신 (읽기 전용)


def git_text(path, ref=CONSUMER_REF):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT).decode("utf-8")


def read_rows(path):
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines()]


def git_symbols(path, names, **bindings):
    """Execute original selected AST definitions and their dependencies, never imports/entrypoints."""
    source = git_text(path)
    tree = ast.parse(source)
    symbols = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            symbols[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols[target.id] = node
    selected = set()

    def include(name):
        if name not in symbols or name in bindings or name in selected:
            return
        selected.add(name)
        for child in ast.walk(symbols[name]):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                include(child.id)

    for name in names:
        include(name)
    selected_nodes = {id(symbols[name]) for name in selected}
    module = types.ModuleType("_issue21_" + path.replace("/", "_").replace(".", "_"))
    module.__dict__.update(
        re=re, json=json, Counter=Counter, dataclass=dataclass, field=field,
        Path=Path, Any=Any, Protocol=Protocol, __file__=str(ROOT / path), **bindings,
    )
    # dataclasses inspects its defining module. No package or working tree import is replaced.
    sys.modules[module.__name__] = module
    subset = ast.Module(body=[n for n in tree.body if id(n) in selected_nodes], type_ignores=[])
    exec(compile(subset, f"git:{CONSUMER_REF}:{path}", "exec"), module.__dict__)
    return module


def condition_nodes(edges):
    """Use the shared pure production node builder."""
    from scripts.build_handoff_package import build_condition_nodes
    return build_condition_nodes(edges)


def region_nodes(edges):
    """인계 생성기의 Region 노드 구성 부분을 그대로 실행한다."""
    tree = ast.parse((ROOT / "scripts/build_handoff_package.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    loop = next(n for n in main.body if isinstance(n, ast.For)
                and isinstance(n.iter, ast.Name) and n.iter.id == "region_edges")
    ns = {"region_edges": [e for e in edges if e["relation"] == "AVAILABLE_IN"],
          "region_dict": {}, "all_node_ids": set()}
    exec(compile(ast.Module(body=[loop], type_ignores=[]), "production_region_nodes", "exec"), ns)
    return list(ns["region_dict"].values())


def context_for(hybrid, policy, groups, edges, nodes):
    pid = policy["policy_id"]
    rows = {}
    for group in groups:
        if group["policy_id"] != pid:
            continue
        gid = group["group_id"]
        rows[gid] = {"policy_id": pid, "group_id": gid, "group": group,
                     "conditions": [], "child_groups": []}
        for edge in edges:
            if edge["source_id"] != gid:
                continue
            if edge["relation"] == "CONTAINS_CONDITION":
                rows[gid]["conditions"].append({"condition": nodes[edge["target_id"]], "relationship": edge})
            elif edge["relation"] == "CONTAINS_GROUP":
                rows[gid]["child_groups"].append({"group_id": edge["target_id"], "relationship": edge})
    base = {"policy_id": pid, "policy": policy, "root_groups": [
        {"group_id": g["group_id"]} for g in groups if g["policy_id"] == pid and g["is_root"]],
        "requires_condition_ids": [e["target_id"] for e in edges if e["source_id"] == pid and e["relation"] == "REQUIRES_CONDITION"],
        "modifies_condition_of": [], "condition_modified_by": []}
    base.update({key: [] for key, _ in hybrid.POLICY_NEIGHBOR_SPECS})

    class ReadOnlyFixtureRunner:
        def read_rows(self, query, parameters):
            assert parameters == {"policy_ids": [pid]}
            return [base] if query == hybrid.POLICY_CONTEXT_QUERY else list(rows.values())

    return hybrid.get_policy_graph_context([pid], runner=ReadOnlyFixtureRunner())["policies"][pid]


class ConsumerCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hd = git_symbols("database/handoff_data.py", ["NODE_SPECS", "LABEL_ID_FIELDS", "RELATIONSHIP_SPECS"])
        cls.lg = git_symbols("database/neo4j/load_graph.py", ["GraphInput", "validate_graph_input", "_relationship_row", "_checked_relation"], hd=cls.hd)
        cls.hybrid = git_symbols("database/hybrid_search.py", ["get_policy_graph_context", "assess_policy_data_quality"], lg=cls.lg)
        cls.original = read_rows("data/processed/policies.jsonl")
        cls.policies = rebuild_policy_conditions(cls.original)
        cls.edges = rebuild_condition_relationships(cls.policies, read_rows("data/processed/relationships.jsonl"))
        cls.nodes = condition_nodes(cls.edges)
        cls.groups, cls.graph_edges, _ = build_condition_groups(cls.policies, cls.edges, cls.nodes)

    def test_representative_unknowns_hold_without_evaluation(self):
        for pid in ("POL_GOV_006", "POL_GOV_011", "POL_GOV_048", "POL_YC_054", "POL_YC_002"):
            with self.subTest(pid=pid):
                p = next(p for p in self.policies if p["policy_id"] == pid)
                context = context_for(self.hybrid, p, self.groups, self.graph_edges, self.nodes)
                self.assertFalse(context["eligibility"]["evaluated"])
                self.assertTrue(context["eligibility"]["tree_matches_requires_condition"])
                self.assertFalse(context["eligibility"]["structure_issues"])
                self.assertTrue(self.hybrid.assess_policy_data_quality(context)["hold_auto_eligibility"])

    def test_no_conditions_generator_emits_unknown_and_consumer_holds(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "requirements": {}}
        groups, edges, _ = build_condition_groups([p], [], {})
        context = context_for(self.hybrid, p, groups, edges, {})
        self.assertEqual(groups[0]["logic"], "UNKNOWN")
        self.assertTrue(self.hybrid.assess_policy_data_quality(context)["hold_auto_eligibility"])

    def test_missing_root_is_structure_issue_not_eligible(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only"}
        context = context_for(self.hybrid, p, [], [], {})
        self.assertFalse(context["eligibility"]["evaluated"])
        self.assertTrue(self.hybrid.assess_policy_data_quality(context)["hold_auto_eligibility"])

    def test_existing_consumer_preserves_all_group_logic_values(self):
        for logic in ("AND", "OR", "NOT", "UNKNOWN"):
            p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only"}
            group = {"group_id": "GRP_SAMPLE", "policy_id": "POL_SAMPLE", "is_root": True, "logic": logic}
            context = context_for(self.hybrid, p, [group], [], {})
            self.assertEqual(context["eligibility"]["condition_tree"][0]["logic"], logic)
            self.assertFalse(context["eligibility"]["evaluated"])

    def test_changed_graph_passes_loader_structural_validation(self):
        nodes = {label: read_rows("data/handoff/neo4j/" + filename)
                 for filename, (label, _) in self.hd.NODE_SPECS.items()}
        nodes["ConditionGroup"] = self.groups
        nodes["EligibilityCondition"] = list(self.nodes.values())
        # 조건과 지역을 재구성한 결과에는 같은 생성기의 새 Region 노드를 사용한다.
        nodes["Region"] = region_nodes(self.graph_edges)
        report = self.lg.validate_graph_input(self.lg.GraphInput(nodes, self.graph_edges))
        self.assertTrue(report.ok, report.summary())

    def test_youth_income_raw_values_survive_in_node(self):
        income = next(e for e in self.edges if e["target_id"].startswith("COND_YC_INCOME_"))
        node = self.nodes[income["target_id"]]
        self.assertEqual(node["value"], income["raw_value"])
        self.assertEqual(node["operator"], "UNKNOWN")

    def test_blocker_cheonan_region_is_rebuilt_from_preserved_source(self):
        p = next(p for p in self.policies if p["policy_id"] == "POL_GOV_048")
        # 시도 계약·검색은 그대로, 시군구는 별도 필드로 보존한다.
        self.assertEqual((p["region"], p["regions"]), ("충청남도", ["충청남도"]))
        self.assertEqual(p["region_detail"], "충청남도 천안시")
        edges = [e for e in self.edges if e["source_id"] == p["policy_id"] and e["relation"] == "AVAILABLE_IN"]
        self.assertEqual([e["target_id"] for e in edges], ["REGION_CHUNGNAM"])
        node = next(n for n in region_nodes(self.edges) if n["region_id"] == "REGION_CHUNGNAM")
        self.assertEqual(node["name"], p["region"])
        self.assertTrue(all(" " not in n["region_id"] for n in region_nodes(self.edges)))

    def test_blocker_other_income_is_not_typed_as_amount(self):
        # 0043001(공식 코드표 '무관')은 조건 관계를 만들지 않고, 기타(0043003)는 OTHER 원값으로만 남는다.
        p = next(p for p in self.policies if p["policy_id"] == "POL_YC_002")
        self.assertEqual(p["requirements"]["income_condition_code"], "0043001")
        self.assertEqual(p["requirements"]["income_condition_label"], "무관")
        self.assertFalse(any(e["source_id"] == p["policy_id"] and "raw_value" in e for e in self.edges))
        other = next(p for p in self.policies if p["policy_id"] == "POL_YC_054")
        edge = next(e for e in self.edges if e["source_id"] == other["policy_id"] and "raw_value" in e)
        self.assertEqual(self.nodes[edge["target_id"]]["type"], "OTHER")
        self.assertEqual(self.nodes[edge["target_id"]]["operator"], "UNKNOWN")


class GenerationFollowupTests(unittest.TestCase):
    def test_region_rebuild_is_idempotent_and_does_not_infer_from_agency_alone(self):
        source = [{"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "agency": "충청남도 천안시",
                   "regions": ["충청남도"], "region": "충청남도", "requirements": {}, "eligibility": "sample_only"}]
        snapshot = copy.deepcopy(source)
        self.assertEqual(rebuild_policy_conditions(source)[0]["regions"], ["충청남도"])
        self.assertNotIn("region_detail", rebuild_policy_conditions(source)[0])
        source[0]["eligibility"] = "sample_only 천안시에 주민등록을 둔 가정"
        candidate = rebuild_policy_conditions(source)
        self.assertEqual(candidate[0]["regions"], ["충청남도"])
        self.assertEqual(candidate[0]["region"], "충청남도")
        self.assertEqual(candidate[0]["region_detail"], "충청남도 천안시")
        self.assertEqual(rebuild_policy_conditions(candidate), candidate)
        edges = rebuild_condition_relationships(candidate, [])
        self.assertEqual(rebuild_condition_relationships(candidate, edges), edges)
        self.assertEqual(snapshot[0]["regions"], source[0]["regions"])

    def test_raw_income_survives_handoff_json_serialization(self):
        p = {"policy_id": "POL_SAMPLE", "source_document_ids": ["DOC_SAMPLE"], "requirements": {
            "income_condition_code": "0043001", "income_condition_label": "무관",
            "income_min_amount": "0", "income_max_amount": "0", "income_etc_details": "sample_only"}}
        edges = build_condition_edges(p)
        wire_edges = json.loads(json.dumps(edges, ensure_ascii=False))
        nodes = condition_nodes(wire_edges)
        income = next(e for e in wire_edges if "raw_value" in e)
        node = json.loads(json.dumps(nodes[income["target_id"]], ensure_ascii=False))
        self.assertEqual(node["value"], income["raw_value"])
        self.assertEqual(json.loads(node["value"])["income_condition_code"], "0043001")
        self.assertEqual(node["type"], "OTHER")

    def test_vector_payload_uses_rebuilt_policy_region(self):
        tree = ast.parse((ROOT / "scripts/build_handoff_package.py").read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        assignment = next(n for n in ast.walk(main) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "payload_record" for t in n.targets))
        chunk = {"chunk_id": "DOC_SAMPLE_C01", "policy_id": "POL_SAMPLE", "document_id": "DOC_SAMPLE",
                 "policy_name": "sample_only", "category": "sample_only", "region": "충청남도",
                 "target_group": "sample_only", "text": "sample_only", "source_url": "sample_only",
                 "verification_status": "extracted"}
        ns = {"chk": chunk, "pid": "POL_SAMPLE", "pol": {"region": "충청남도 천안시"},
              "l_stages": [], "uuid_point_id": "sample_only"}
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), "production_payload", "exec"), ns)
        self.assertEqual(ns["payload_record"]["region"], "충청남도 천안시")
        self.assertEqual(chunk["region"], "충청남도")


class Issue5ConsumerBlockerTests(unittest.TestCase):
    """Issue #5 후속 항목. 실패를 skip/expectedFailure로 숨기지 않는다."""

    @classmethod
    def setUpClass(cls):
        ConsumerCompatibilityTests.setUpClass.__func__(cls)

    def test_blocker_new_relationship_fields_are_loaded_losslessly(self):
        income = next(e for e in self.edges if "raw_value" in e)
        loaded = self.lg._relationship_row(income)["properties"]
        self.assertIn("raw_value", loaded, "Issue #5 관계 속성 허용 목록에서 누락")
        self.assertEqual(loaded["raw_value"], income["raw_value"])
        self.assertEqual(loaded["condition_type"], income["condition_type"])

    def test_blocker_empty_and_group_must_hold(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only"}
        group = {"group_id": "GRP_SAMPLE", "policy_id": "POL_SAMPLE", "is_root": True, "logic": "AND"}
        context = context_for(self.hybrid, p, [group], [], {})
        self.assertTrue(self.hybrid.assess_policy_data_quality(context)["hold_auto_eligibility"])

    def test_blocker_review_needed_alone_must_hold(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "eligibility_review_status": "needs_review"}
        group = {"group_id": "GRP_SAMPLE", "policy_id": "POL_SAMPLE", "is_root": True, "logic": "AND"}
        node = {"condition_id": "COND_SAMPLE", "review_status": "needs_review"}
        edges = [{"source_id": "GRP_SAMPLE", "relation": "CONTAINS_CONDITION", "target_id": "COND_SAMPLE", "review_status": "needs_review"}]
        context = context_for(self.hybrid, p, [group], edges, {"COND_SAMPLE": node})
        self.assertTrue(self.hybrid.assess_policy_data_quality(context)["hold_auto_eligibility"])



if __name__ == "__main__":
    unittest.main()
