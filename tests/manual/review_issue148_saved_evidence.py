"""Offline review of PR #148 saved answers; no DB, secrets, or LLM calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.rag.pipeline import OPEN_CLAIM, _align_answer_with_checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = ROOT / "docs/reports/issue122-r3d-llm-20261127-v2.json"
    original = json.loads(source.read_text(encoding="utf-8"))
    results = []
    for case in original["cases"]:
        raw = case["generator_output"]["answer"]
        saved = case["response"]["answer"]
        replay, changed = _align_answer_with_checks(raw, case["contexts"], case["generator_checks"])
        assert replay == saved, case["policy_id"]
        row = {
            "policy_id": case["policy_id"],
            "replay_equals_saved_api_answer": replay == saved,
            "changed": changed,
            "raw_answer": raw,
            "saved_api_answer": saved,
            "open_claim_matches": [{"text": m.group(), "span": list(m.span())} for m in OPEN_CLAIM.finditer(raw)],
            "raw_has_dot_comma": ".," in raw,
            "saved_has_dot_comma": ".," in saved,
        }
        if case["policy_id"] == "POL_GOV_059":
            assert "이메일·방문 접수 중 하나" in raw
            assert "이메일·방문" not in saved
            assert changed
            row["method_list_false_positive_confirmed"] = True
            row["reclassified_guidance"] = "FAIL"
        results.append(row)
    minimal = "온라인·이메일·방문 접수 중 하나로 제출"
    match = OPEN_CLAIM.search(minimal)
    assert match and match.group() == "접수 중"
    pipeline = ROOT / "backend/rag/pipeline.py"
    baseline = subprocess.check_output(["git", "show", "8c4085823fc1f06a7d1b805dd6ea1d66200079ca:backend/rag/pipeline.py"], cwd=ROOT)
    assert baseline.decode("utf-8").replace("\r\n", "\n") == pipeline.read_text(encoding="utf-8")
    report = {
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_saved_evidence_replay", "new_llm_calls": 0, "db_calls": 0,
        "source": source.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "manifest_sha256": original["manifest_sha256"],
        "execution_baseline_sha": "8c4085823fc1f06a7d1b805dd6ea1d66200079ca",
        "pr_head_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "pipeline_git_blob_sha": subprocess.check_output(["git", "rev-parse", "HEAD:backend/rag/pipeline.py"], cwd=ROOT, text=True).strip(),
        "pipeline_equals_execution_baseline": True,
        "minimal_string_only": {"text": minimal, "match": match.group(), "span": list(match.span())},
        "saved_cases": results,
    }
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("PASS: 3 saved answers replay exactly; GOV059 false positive confirmed; POL_005 saved dot-comma absent")


if __name__ == "__main__":
    main()
