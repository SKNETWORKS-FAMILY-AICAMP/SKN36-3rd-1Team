"""데이터셋 선택은 import 시 결정되므로 독립 프로세스로 기본값·명시 경로를 검증한다."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DatasetDefaultsTests(unittest.TestCase):
    def probe(self, dataset=None, statement=""):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        env.pop("LIFEPOLICY_DATASET_DIR", None)
        if dataset is not None:
            env["LIFEPOLICY_DATASET_DIR"] = str(dataset)
        code = """
import json
from database import handoff_data as hd, hybrid_search as hs, full_index as fi, data_quality_flags as dqf
from database.neo4j import load_graph as lg
""" + statement + """
print(json.dumps({'version':hd.dataset_version(),'release':hd.IS_RELEASE_PACKAGE,
 'path':str(hd.HANDOFF_DIR),'profile':hs.default_profile(),'collection':hs.resolve_search_settings()['collection'],
 'nodes':hd.EXPECTED_NODE_COUNT,'flags':dqf.load_flags()['status'],'configs':fi.available_configs()}))
"""
        return subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code],
                              cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")

    def success(self, dataset=None):
        result = self.probe(dataset)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_unset_uses_approved_release_and_matching_search_profile(self):
        result = self.success()
        self.assertEqual(result["version"], "1.4.0-candidate")
        self.assertEqual(Path(result["path"]), ROOT / "data/releases/v1.4.0-candidate")
        self.assertEqual((result["profile"], result["collection"]),
                         ("b2_06b_v140", "exp_b2_qwen3emb06b_v140_v1"))
        self.assertTrue(result["release"])
        self.assertEqual(result["nodes"], 1043)
        self.assertEqual(result["flags"], "not_applicable")
        self.assertEqual(set(result["configs"]), {"A_B2_V140", "B_B3_V140"})

    def test_blank_uses_approved_release(self):
        self.assertEqual(self.success("  ")["version"], "1.4.0-candidate")

    def test_explicit_legacy_keeps_metadata_profile_and_quality_flags(self):
        result = self.success("data/handoff")
        self.assertEqual((result["version"], result["nodes"], result["profile"]),
                         ("1.3.2", 1034, "b2_06b_baseline"))
        self.assertEqual(result["flags"], "applied")
        self.assertFalse(result["release"])

    def test_explicit_absolute_path_has_priority(self):
        self.assertEqual(self.success(ROOT / "data/handoff")["version"], "1.3.2")

    def test_missing_directory_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.probe(Path(folder) / "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("데이터셋 폴더가 없습니다", result.stderr)

    def test_missing_manifest_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.probe(folder)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("데이터셋 manifest", result.stderr)

    def test_malformed_manifest_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "manifest.json").write_text("{invalid", encoding="utf-8")
            result = self.probe(folder)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("데이터셋 manifest", result.stderr)

    def test_invalid_manifest_summary_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "manifest.json").write_text('{"manifest_version":"1.4.0-candidate","summary":{}}', encoding="utf-8")
            result = self.probe(folder)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("데이터셋 manifest", result.stderr)

    def test_unset_dataset_rejects_legacy_profile_and_collection(self):
        for statement in ("hs.resolve_search_settings(profile='b2_06b_baseline')",
                          "hs.resolve_search_settings(collection='exp_b2_qwen3emb06b_full_v1')"):
            with self.subTest(statement=statement):
                result = self.probe(statement=statement)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FullIndexError", result.stderr)


if __name__ == "__main__":
    unittest.main()
