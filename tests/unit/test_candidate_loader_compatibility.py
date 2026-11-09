"""v1.4 후보 Git 객체의 읽기 전용 회귀 검사. 실제 DB/모델/API를 호출하지 않는다."""
import copy
import hashlib
import json
import subprocess
import sys
import types
import unittest
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import handoff_data as hd
from database import hybrid_search as hs
from database.neo4j import load_graph as lg
from test_neo4j_loader import FakeGraphRunner, _sample_graph
from test_hybrid_search import FakeHybridRunner

CANDIDATE_COMMIT = "0a091268d2bfb0a781e992e28427d733870ebfa0"
OLD_CANDIDATE = "074d638"
BASELINE_LOADER = "cb55cb7542560210ae41a4fd05be25c461a89c63"
RELEASE_PATH = "data/releases/v1.4.0-candidate/"


@lru_cache(maxsize=None)
def git_blob(path, ref=CANDIDATE_COMMIT):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def git_rows(path, ref=CANDIDATE_COMMIT):
    return [json.loads(line) for line in git_blob(path, ref).decode("utf-8").splitlines() if line.strip()]


def candidate_graph(ref=CANDIDATE_COMMIT):
    nodes = {label: git_rows(RELEASE_PATH + "neo4j/" + name, ref)
             for name, (label, _) in hd.NODE_SPECS.items()}
    return lg.GraphInput(nodes, git_rows(RELEASE_PATH + "neo4j/relationships.jsonl", ref))


def old_loader():
    module = types.ModuleType("_issue5_baseline_loader")
    module.__file__ = str(ROOT / "database/neo4j/load_graph.py")
    sys.modules[module.__name__] = module
    source = git_blob("database/neo4j/load_graph.py", BASELINE_LOADER).decode("utf-8")
    exec(compile(source, "git:baseline_load_graph.py", "exec"), module.__dict__)
    return module


class TestCandidateGitInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = candidate_graph()

    def test_old_31_and_current_250_null_errors_are_reproduced(self):
        baseline = old_loader()
        for ref, expected in ((OLD_CANDIDATE, {"official_apply_url": 5, "law_article": 9,
                                             "effective_date": 9, "version": 8}),
                              (CANDIDATE_COMMIT, {"official_apply_url": 224, "law_article": 9,
                                                  "effective_date": 9, "version": 8})):
            with self.subTest(ref=ref):
                graph = self.graph if ref == CANDIDATE_COMMIT else candidate_graph(ref)
                report = baseline.validate_graph_input(graph)
                self.assertEqual(Counter(item[2] for item in report.errors["invalid_property"]), expected)
                self.assertEqual(sum(report.summary()["error_counts"].values()), sum(expected.values()))
                self.assertTrue(lg.validate_graph_input(graph).ok)

    def test_manifest_git_bytes_and_lf_normalization_match(self):
        manifest = json.loads(git_blob(RELEASE_PATH + "manifest.json"))
        self.assertEqual(manifest["manifest_version"], "1.4.0-candidate")
        self.assertEqual(len(manifest["files"]), 20)
        for path, meta in manifest["files"].items():
            with self.subTest(path=path):
                blob = git_blob(RELEASE_PATH + path)
                self.assertEqual(hashlib.sha256(blob).hexdigest(), meta["sha256"])
                self.assertEqual(len(blob), meta["size_bytes"])
                if path.endswith(".jsonl"):
                    self.assertEqual(sum(bool(line.strip()) for line in blob.splitlines()), meta["record_count"])
                normalized = blob.replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(normalized).hexdigest(), meta["sha256"])
                windows_copy = normalized.replace(b"\n", b"\r\n")
                self.assertEqual(hashlib.sha256(windows_copy.replace(b"\r\n", b"\n")).hexdigest(), meta["sha256"])

    def test_counts_id_and_vector_links(self):
        report = lg.validate_graph_input(self.graph)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.node_counts["Policy"], 246)
        self.assertEqual(report.node_counts["Document"], 261)
        self.assertEqual(sum(report.node_counts.values()), 1074)
        self.assertEqual(sum(report.relationship_counts.values()), 3703)
        policies = {p["policy_id"] for p in self.graph.nodes_by_label["Policy"]}
        documents = {d["document_id"] for d in self.graph.nodes_by_label["Document"]}
        for document in self.graph.nodes_by_label["Document"]:
            self.assertIn(document["policy_id"], policies)
        for edge in self.graph.relationships:
            if edge.get("evidence_document_id") is not None:
                self.assertIn(edge["evidence_document_id"], documents)
        payloads = git_rows(RELEASE_PATH + "qdrant/payload_chunks.jsonl")
        self.assertEqual(len(payloads), 599)
        self.assertEqual(len({p["chunk_id"] for p in payloads}), 599)
        result = lg.check_vector_payload_links(self.graph, payloads)
        self.assertTrue(all(not value for key, value in result.items() if key != "payload_count"), result)

    def test_nullable_revision_is_explicit_in_candidate_contract(self):
        contract = git_blob("docs/data/data_contract.md").decode("utf-8")
        policy, document = contract.split("- **정책(`Policy`):**")[1].split("- **근거 문서(`Document`):**")
        self.assertNotIn("`official_apply_url`", policy.split("**선택(Nullable):**")[0])
        self.assertIn("`official_apply_url`", policy.split("**선택(Nullable):**")[1])
        for name in ("law_article", "effective_date", "version"):
            self.assertIn(f"`{name}`", document.split("**선택(Nullable):**")[1])

    def test_plan_and_fake_load_preserve_input_and_optional_metadata(self):
        snapshot = copy.deepcopy(self.graph)
        plan = lg.build_load_plan(self.graph)
        self.assertTrue(plan)
        runner = FakeGraphRunner()
        result = lg.load_graph(self.graph, runner)
        self.assertTrue(result["ok"], result["verification"])
        self.assertEqual(len(runner.nodes), 1074)
        self.assertEqual(len(runner.relationships), 3703)
        for p in self.graph.nodes_by_label["Policy"]:
            if p.get("official_apply_url", "not_null") is None:
                self.assertNotIn("official_apply_url", runner.nodes[("Policy", p["policy_id"])])
        self.assertEqual(self.graph, snapshot)

    def test_candidate_semantic_suspicions_remain(self):
        # 고정 Git 후보와 비교할 대상도 동결 v1.3.2로 고정한다.
        legacy = hd.LEGACY_HANDOFF_DIR / "neo4j"
        old_groups = hd.read_jsonl(legacy / "nodes_condition_group.jsonl")
        old_conditions = hd.read_jsonl(legacy / "nodes_eligibility_condition.jsonl")
        self.assertEqual(self.graph.nodes_by_label["EligibilityCondition"], old_conditions)
        ids = {g["group_id"] for g in old_groups}
        self.assertEqual([g for g in self.graph.nodes_by_label["ConditionGroup"] if g["group_id"] in ids], old_groups)
        self.assertEqual(next(g["logic"] for g in old_groups if g["group_id"] == "GRP_POL_GOV_011_OR_INC"), "OR")

    def test_current_release_age_links_are_independent_of_historical_candidate(self):
        graph = lg.read_graph_input(hd.DEFAULT_HANDOFF_DIR / "neo4j")
        report = lg.validate_graph_input(graph)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(sum(report.node_counts.values()), 1005)
        self.assertEqual(sum(report.relationship_counts.values()), 2444)
        for policy_id, condition_id in (("POL_GOV_011", "COND_AGE_12_24"),
                                        ("POL_GOV_016", "COND_AGE_19_120"),
                                        ("POL_GOV_023", "COND_AGE_6_64")):
            with self.subTest(policy_id=policy_id):
                self.assertTrue(any(r["relation"] == "REQUIRES_CONDITION" and r["source_id"] == policy_id
                                    and r["target_id"] == condition_id for r in graph.relationships))
                self.assertTrue(any(r["relation"] == "CONTAINS_CONDITION" and r["source_id"] == f"GRP_{policy_id}_ROOT"
                                    and r["target_id"] == condition_id for r in graph.relationships))


class TestNullableAndRelationshipContract(unittest.TestCase):
    def test_only_document_and_policy_allowlisted_nulls_are_accepted(self):
        for label, fields in lg.NULLABLE_NODE_STRING_FIELDS.items():
            for name in fields:
                with self.subTest(label=label, name=name):
                    graph = _sample_graph()
                    node = graph.nodes_by_label[label][0]
                    node[name] = None
                    self.assertTrue(lg.validate_graph_input(graph).ok)
                    self.assertIsNone(node[name])
                    for bad in (False, 7, [], {}):
                        node[name] = bad
                        self.assertFalse(lg.validate_graph_input(graph).ok)

    def test_required_missing_null_and_wrong_type_are_blocked_before_db(self):
        for label, key in (("Policy", "policy_id"), ("Policy", "source_url"), ("Policy", "policy_name"),
                           ("Document", "content_hash"), ("Document", "policy_id"), ("Document", "title")):
            for mode in ("missing", "null", "wrong_type"):
                with self.subTest(label=label, key=key, mode=mode):
                    graph = _sample_graph()
                    node = graph.nodes_by_label[label][0]
                    if mode == "missing":
                        del node[key]
                    else:
                        node[key] = None if mode == "null" else 123
                    runner = FakeGraphRunner()
                    with self.assertRaises(lg.GraphValidationError):
                        lg.load_graph(graph, runner)
                    self.assertEqual(runner.calls, [])
        graph = _sample_graph()
        graph.nodes_by_label["Policy"][0]["uncontracted_null"] = None
        self.assertFalse(lg.validate_graph_input(graph).ok)

    def test_raw_value_and_condition_type_are_optional_strings_and_preserved(self):
        for values in ({}, {"raw_value": None, "condition_type": None},
                       {"raw_value": '{"sample_only":true}', "condition_type": "OTHER"}):
            graph = _sample_graph()
            graph.relationships[0].update(values)
            runner = FakeGraphRunner()
            result = lg.load_graph(graph, runner)
            self.assertTrue(result["ok"])
            props = runner.relationship_properties[("SUPPORTED_BY", ("Policy", "POL_S1"), ("Document", "DOC_S1"))]
            for key, value in values.items():
                if value is None:
                    self.assertNotIn(key, props)
                else:
                    self.assertEqual(props[key], value)
        for key in ("raw_value", "condition_type"):
            for bad in ({"sample_only": True}, ["sample_only"], 42, False):
                graph = _sample_graph()
                graph.relationships[0][key] = bad
                self.assertFalse(lg.validate_graph_input(graph).ok)


class TestSafeEligibilityHold(unittest.TestCase):
    def context(self, runner):
        return hs.get_policy_graph_context(["POL_S1"], runner=runner)["policies"]["POL_S1"]

    def test_empty_and_root_and_nested_group_hold_without_rewriting_logic(self):
        for gid in ("GRP_S1_ROOT", "GRP_S1_OR"):
            runner = FakeHybridRunner()
            runner.groups[gid].update(child_groups=[], conditions=[])
            runner.groups[gid]["group"]["logic"] = "AND"
            snapshot = copy.deepcopy(runner.groups)
            context = self.context(runner)
            result = hs.assess_policy_data_quality(context)
            self.assertTrue(result["hold_auto_eligibility"])
            self.assertIn(hs.QUALITY_EMPTY_AND_GROUP, result["hold_reasons"])
            self.assertFalse(context["eligibility"]["evaluated"])
            self.assertEqual(runner.groups, snapshot)

    def test_review_status_in_policy_condition_group_relation_or_document_holds(self):
        for where in ("policy", "condition", "group", "relationship", "document"):
            with self.subTest(where=where):
                runner = FakeHybridRunner()
                if where == "policy":
                    runner.policies["POL_S1"]["eligibility_review_status"] = "needs_review"
                elif where == "condition":
                    runner.groups["GRP_S1_ROOT"]["conditions"][0]["condition"]["review_status"] = "needs_review"
                elif where == "group":
                    runner.groups["GRP_S1_ROOT"]["group"]["review_status"] = "needs_review"
                elif where == "relationship":
                    runner.groups["GRP_S1_ROOT"]["conditions"][0]["relationship"]["review_status"] = "needs_review"
                else:
                    runner.neighbors.setdefault("POL_S1", {})["documents"] = [
                        {"node": {"document_id": "DOC_S1", "review_status": "needs_review"}, "relationship": {}}]
                context = self.context(runner)
                self.assertIn(hs.QUALITY_NEEDS_REVIEW, hs.assess_policy_data_quality(context)["hold_reasons"])
                self.assertFalse(context["eligibility"]["evaluated"])

    def test_verified_nonempty_and_and_explicit_unrestricted_metadata_are_not_unknown(self):
        runner = FakeHybridRunner()
        runner.policies["POL_S1"]["income_condition_label"] = "무관"
        context = self.context(runner)
        result = hs.assess_policy_data_quality(context)
        self.assertNotIn(hs.QUALITY_NEEDS_REVIEW, result["hold_reasons"])
        self.assertNotIn(hs.QUALITY_EMPTY_AND_GROUP, result["hold_reasons"])
        self.assertEqual(context["policy"]["income_condition_label"], "무관")


if __name__ == "__main__":
    unittest.main()
