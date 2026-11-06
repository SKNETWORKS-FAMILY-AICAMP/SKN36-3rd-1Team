"""후보 데이터를 Issue #5 로더/검색 코드(Git 커밋)로 읽기 전용 검사한다. DB·네트워크 없음.

Issue #5 코드는 git archive로 임시 폴더에만 꺼낸다. 작업 트리·브랜치는 바꾸지 않는다.
사용: python scripts/verify_candidate_issue5_compat.py --candidate issue21-v1.4.0-20261107-rag --ref 04a9edc
"""
import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--candidate", required=True, help="data/candidates 아래 폴더 이름")
parser.add_argument("--ref", default="04a9edc", help="Issue #5 커밋")
args = parser.parse_args()
_tmp = tempfile.TemporaryDirectory()
ISSUE5 = Path(_tmp.name).resolve()
archive = subprocess.run(["git", "archive", args.ref, "database", "tests/unit/test_neo4j_loader.py"],
                         cwd=REPO, capture_output=True, check=True).stdout
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(ISSUE5, filter="data")
sys.path[:0] = [str(ISSUE5), str(ISSUE5 / "tests" / "unit")]

from database.neo4j import load_graph as lg  # noqa: E402
from database import hybrid_search as hs  # noqa: E402
from test_neo4j_loader import FakeGraphRunner  # noqa: E402

assert Path(lg.__file__).resolve().is_relative_to(ISSUE5), lg.__file__
candidate = REPO / "data/candidates" / args.candidate
graph = lg.read_graph_input(candidate / "neo4j")
report = lg.validate_graph_input(graph)
out = {"candidate": args.candidate, "issue5_ref": args.ref, "validate_ok": report.ok, "validate_summary": report.summary()}

runner = FakeGraphRunner()
loaded = lg.load_graph(graph, runner)
out["dry_run_ok"] = loaded["ok"]
out["count_mismatches"] = loaded["count_mismatches"]
out["expected_counts"] = loaded["expected_counts"]

payloads = [json.loads(l) for l in (candidate / "qdrant/payload_chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
links = lg.check_vector_payload_links(graph, payloads)
out["payload_links"] = {k: (len(v) if isinstance(v, list) else v) for k, v in links.items()}

# raw_value/condition_type가 적재 관계 속성에 그대로 남는지
rels = [r for r in graph.relationships if "raw_value" in r]
lost = [r for r in rels if lg._relationship_row(r)["properties"].get("raw_value") != r["raw_value"]
        or lg._relationship_row(r)["properties"].get("condition_type") != r.get("condition_type")]
out["raw_value_relationships"] = len(rels)
out["raw_value_lost"] = len(lost)

# 정책별 조건 트리/품질 평가: 파일 행을 Cypher 결과 모양으로만 바꿔 넘긴다.
nodes = {lbl: {n[lg.LABEL_ID_FIELDS[lbl]]: n for n in rows} for lbl, rows in graph.nodes_by_label.items()}
groups = nodes["ConditionGroup"]
conds = nodes["EligibilityCondition"]
by_source = {}
for e in graph.relationships:
    by_source.setdefault(e["source_id"], []).append(e)


def context(pid):
    gids = [g for g, row in groups.items() if row["policy_id"] == pid]
    grows = []
    for gid in gids:
        es = by_source.get(gid, [])
        grows.append({"policy_id": pid, "group_id": gid, "group": groups[gid],
                      "conditions": [{"condition": conds[e["target_id"]], "relationship": e} for e in es if e["relation"] == "CONTAINS_CONDITION"],
                      "child_groups": [{"group_id": e["target_id"], "relationship": e} for e in es if e["relation"] == "CONTAINS_GROUP"]})
    pes = by_source.get(pid, [])
    base = {"policy_id": pid, "policy": nodes["Policy"][pid],
            "root_groups": [{"group_id": e["target_id"]} for e in pes if e["relation"] == "HAS_ROOT_CONDITION_GROUP"],
            "requires_condition_ids": [e["target_id"] for e in pes if e["relation"] == "REQUIRES_CONDITION"],
            "modifies_condition_of": [], "condition_modified_by": []}
    base.update({key: [] for key, _ in hs.POLICY_NEIGHBOR_SPECS})

    class Runner:
        def read_rows(self, query, parameters):
            return [base] if query == hs.POLICY_CONTEXT_QUERY else grows

    return hs.get_policy_graph_context([pid], runner=Runner())["policies"][pid]


stats = Counter()
warn = Counter()
for pid in nodes["Policy"]:
    ctx = context(pid)
    el = ctx["eligibility"]
    stats["tree_matches_requires_condition"] += bool(el.get("tree_matches_requires_condition"))
    stats["evaluated_false"] += not el.get("evaluated")
    stats["structure_issues"] += bool(el.get("structure_issues"))
    q = hs.assess_policy_data_quality(ctx)
    stats["hold_auto_eligibility"] += bool(q["hold_auto_eligibility"])
    stats["root_" + str(el["condition_tree"][0]["logic"])] += 1
    warn.update(w["code"] for w in q["warnings"])
out["policy_context"] = dict(stats)
out["quality_warning_counts"] = dict(warn)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
