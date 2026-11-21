"""Issue #105: R3_PINS 미확정(None) 상태의 쓰기 허용·금지 경계 테스트.

restore_profiles_v140.WRITES_REQUIRING_PINS의 모든 진입점은 실제 R3_PINS(미확정)로 부르면 쓰기 전에 ProfileError로 멈추고,
WRITES_WITHOUT_PINS의 원본 snapshot 복원만 (build Docker 소유 확인을 거쳐) 허용되는지 확인한다.
DB·Docker·Ollama 접속 없음 (sample_only 테스트 더블).
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import R3Qdrant, Sample, good_build_docker  # noqa: E402

from database import full_index as fi  # noqa: E402
from scripts import build_restore_bundle_r3 as bundle  # noqa: E402
from scripts import load_neo4j_r3_isolated as neo4j_r3  # noqa: E402
from scripts import restore_final_v140_r3 as restore_r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402
from scripts import restore_r3_isolated as wrap  # noqa: E402
from scripts import update_r3_collections as update  # noqa: E402

URL = f"http://127.0.0.1:{update.ISOLATED_QDRANT_PORT}"
ENV = {update.API_KEY_ENV: "sample_only_r3_build_key"}


class WriteGateTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(profiles.is_finalized(), "이 테스트는 실제 R3_PINS가 미확정일 때의 경계를 확인한다")
        self.s = Sample()
        self.plan = self.s.plan("b2")
        self.client = self.s.client()
        self.docker = good_build_docker()
        patcher = patch.dict(profiles.R3_SOURCES, self.s.sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_pin_requiring_write_is_refused_before_writing(self):
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(fi, "execute_full_index", side_effect=never):
            entries = {
                "update --apply": lambda: update.apply_updates(self.client, self.plan, self.s.release, True, "x",
                                                               docker=self.docker),
                "update --index": lambda: update.index_missing(self.client, self.plan, self.s.release, lambda t: t,
                                                               True, "x", docker=self.docker),
                "neo4j --load": lambda: neo4j_r3.load(runner, {}, True, "x", docker=self.docker),
                "neo4j --cross-db": lambda: neo4j_r3.cross_check(self.client, runner, {}),
                "bundle --create-snapshot": lambda: bundle.create_snapshot(
                    self.client, URL, self.plan, self.s.release, Path(folder), confirm_write=True,
                    expected_fingerprint="x", environ=ENV, docker=self.docker),
                "bundle --build": lambda: bundle.build_bundle({}, "0" * 40, Path(folder), confirm_write=True),
                "restore check_bundle": lambda: restore_r3.check_bundle(folder),
                "restore --apply": lambda: restore_r3.restore(
                    Namespace(repo=wrap.ROOT, bundle=Path(folder), apply=True, verify_only=False),
                    client_factory=lambda s: never, driver_factory=lambda s: never),
                "wrapper --apply": lambda: wrap.check_source_and_bundle(wrap.ROOT, Path(folder)),
            }
            for name, call in entries.items():
                with self.subTest(entry=name), patch.dict(os.environ, {}), \
                        self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
                    call()
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(self.client.writes, [])
        self.assertEqual(len(profiles.WRITES_REQUIRING_PINS), 7)

    def test_source_snapshot_restore_is_the_only_server_write_without_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "source.snapshot"
            snapshot.write_bytes(b"sample_only source")
            profiles.R3_SOURCES["b3"]["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            client = R3Qdrant()

            def post(url, params, headers, files, timeout):
                client.collections[self.s.collection("b3")] = dict(self.s.records["b3"])
                return SimpleNamespace(status_code=200)
            result = update.restore_source_snapshot(client, URL, "b3", snapshot, True, ENV, post, docker=self.docker)
        self.assertEqual(result["status"], "RESTORED")
        self.assertIn("update_r3_collections --restore-snapshot", profiles.WRITES_WITHOUT_PINS)

    def test_cli_apply_with_pending_pins_fails_without_docker_or_qdrant_writes(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(r2w, "run_docker", side_effect=AssertionError("Docker 호출 금지")), \
                patch.dict(os.environ, ENV):
            plan_path = Path(folder) / "plan.json"
            plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
            report = Path(folder) / "report.json"
            code = update.main(["--qdrant-url", URL, "--collection", "b2", "--plan", str(plan_path), "--apply",
                                "--confirm-write", "--expect-fingerprint", "0" * 64, "--report", str(report)])
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((code, output["status"], output["error_type"]), (1, "FAILED", "ProfileError"))
        self.assertNotIn(ENV[update.API_KEY_ENV], json.dumps(output))


class UpdateStageDeferralTests(unittest.TestCase):
    """최종 vector_digest는 임베딩 후에만 정해진다 → 갱신 단계에서만 그 두 칸의 미확정을 허용한다 (닭과 달걀 방지)."""

    def setUp(self):
        self.s = Sample()
        self.plans = {k: self.s.plan(k) for k in profiles.COLLECTION_KEYS}
        self.pins = self.s.pins_for(self.plans)
        for key in profiles.COLLECTION_KEYS:
            self.pins["collections"][key]["vector_digest"] = None          # 갱신 전 실제 상황
        self.docker = good_build_docker()
        patcher = patch.dict(profiles.R3_SOURCES, self.s.sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_require_finalized_defers_only_final_vector_digests(self):
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            profiles.require_finalized(self.pins)
        self.assertIs(profiles.require_finalized(self.pins, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS), self.pins)
        other = json.loads(json.dumps(self.pins))
        other["collections"]["b3"]["plan_sha256"] = None
        with self.assertRaisesRegex(profiles.ProfileError, "plan_sha256"):
            profiles.require_finalized(other, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
        with self.assertRaisesRegex(profiles.ProfileError, "미룰 수 없는"):
            profiles.require_finalized(self.pins, deferred=("manifest_lf_sha256",))
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):     # 실제 R3_PINS(전부 None)는 여전히 거부
            profiles.require_finalized(deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)

    def test_apply_runs_but_snapshot_bundle_and_cross_check_still_need_final_digests(self):
        client, plan = self.s.client(), self.plans["b2"]
        identity = update.require_build_ownership(self.docker)
        fp = update.server_fingerprint(client, update.inspect_state(client, plan, self.s.release), identity)
        result = update.apply_updates(client, plan, self.s.release, True, fp, self.pins, docker=self.docker)
        self.assertEqual(result["status"], "UPDATED")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
                bundle.create_snapshot(client, URL, plan, self.s.release, Path(folder), confirm_write=True,
                                       expected_fingerprint="x", pins=self.pins, environ=ENV, docker=self.docker)
            with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
                bundle.build_bundle({}, "0" * 40, Path(folder), confirm_write=True, pins=self.pins)
            with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
                restore_r3.check_bundle(folder, self.pins)
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            neo4j_r3.cross_check(client, SimpleNamespace(), {}, pins=self.pins)


if __name__ == "__main__":
    unittest.main()
