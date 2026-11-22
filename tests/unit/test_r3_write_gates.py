"""Issue #105: R3_PINS 확정 단계별 쓰기 허용·금지 경계 테스트.

- 미확정(전부 None): WRITES_REQUIRING_PINS의 모든 진입점이 쓰기 전에 ProfileError로 멈추고,
  WRITES_WITHOUT_PINS의 원본 snapshot 복원만 (build Docker 소유 확인을 거쳐) 허용된다.
  실제 R3_PINS가 확정된 뒤에도 이 경계를 계속 검증하도록 profiles.R3_PINS를 '전부 미확정' 값으로 바꿔 끼운다.
- 1차 확정(stage1_pins, 최종 vector_digest 두 칸만 None): 갱신 단계(--apply·--index·Neo4j --load)만 기준값 게이트를
  통과하고(그 뒤 계획·fingerprint·소유 확인이 계속 지킨다), snapshot·번들·번들 복원·교차 검증은 계속 거부된다.
- 2차 확정(final_sample_pins, 미확정 0개): 모든 단계가 기준값 게이트를 통과하고, 다른 릴리스·잘못된 값은 다음 검사에서 멈춘다.
- 실제 R3_PINS(C0, #110 반영 2026-11-23): 선언 단계(R3_PIN_STAGE)와 계산 단계(pin_stage)가 같고, 그 단계에서 허용되지 않은
  쓰기 진입점이 모두 쓰기 전에 거부된다 (바꿔 끼우지 않고 실제 값으로 확인).
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

from r3_fixtures import (R3Qdrant, Sample, final_sample_pins, good_build_docker, pending_pins,  # noqa: E402
                         stage1_pins)

from database import full_index as fi  # noqa: E402
from scripts import build_restore_bundle_r3 as bundle  # noqa: E402
from scripts import load_neo4j_r3_isolated as neo4j_r3  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140_r3 as restore_r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402
from scripts import restore_r3_isolated as wrap  # noqa: E402
from scripts import update_r3_collections as update  # noqa: E402

URL = f"http://127.0.0.1:{update.ISOLATED_QDRANT_PORT}"
ENV = {update.API_KEY_ENV: "sample_only_r3_build_key"}


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Sample()
        self.plan = self.s.plan("b2")
        self.client = self.s.client()
        self.docker = good_build_docker()
        patcher = patch.dict(profiles.R3_SOURCES, self.s.sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entries(self, folder, runner, never):
        full = {
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
        update_stage = {
            "update --apply": lambda: update.apply_updates(self.client, self.plan, self.s.release, True, "x",
                                                           docker=self.docker),
            "update --index": lambda: update.index_missing(self.client, self.plan, self.s.release, lambda t: t,
                                                           True, "x", docker=self.docker),
            "neo4j --load": lambda: neo4j_r3.load(runner, {"counts": (0,) * 7, "manifest_lf_sha256": "0" * 64}, True,
                                                  "x", docker=self.docker),
        }
        return update_stage, full


class PendingPinTests(Base):
    """R3_PINS가 전부 미확정일 때 (최신 데이터 병합 전 상태)."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(profiles, "R3_PINS", pending_pins())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertFalse(profiles.is_finalized())

    def test_every_pin_requiring_write_is_refused_before_writing(self):
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder, patch.object(fi, "execute_full_index", side_effect=never):
            update_stage, full = self.entries(folder, runner, never)
            for name, call in {**update_stage, **full}.items():
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


class Stage1PinTests(Base):
    """1차 확정 상태 (최종 vector_digest 두 칸만 미확정). 실제 R3_PINS가 2차 확정된 뒤에도 stage1_pins()로 바꿔 끼워 검증한다."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(profiles, "R3_PINS", stage1_pins())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pins_are_stage1(self):
        self.assertEqual(sorted(profiles.missing_pins()), sorted(profiles.UPDATE_STAGE_DEFERRED_PINS))
        self.assertEqual(profiles.pin_stage(), "stage1")

    def test_update_stage_passes_the_pin_gate_and_later_guards_still_stop_foreign_input(self):
        """갱신 단계는 ProfileError 없이 다음 검사로 넘어가고, 다른 릴리스 계획·그래프 기준은 그 검사에서 멈춘다."""
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder, patch.object(fi, "execute_full_index", side_effect=never), \
                patch.object(r2w, "run_docker", side_effect=never):
            update_stage, _full = self.entries(folder, runner, never)
            expected = {"update --apply": planner.PlanError, "update --index": planner.PlanError,
                        "neo4j --load": neo4j_r3.Neo4jR3Error}
            for name, call in update_stage.items():
                with self.subTest(entry=name):
                    try:
                        call()
                        self.fail("멈춰야 한다")
                    except profiles.ProfileError as error:
                        self.fail(f"1차 확정 기준값인데 기준값 게이트에서 멈춤: {error}")
                    except expected[name]:
                        pass
        self.assertEqual(self.client.writes, [])

    def test_final_stage_writes_are_still_refused_for_missing_vector_digest(self):
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder:
            _update_stage, full = self.entries(folder, runner, never)
            for name, call in full.items():
                with self.subTest(entry=name), patch.dict(os.environ, {}), \
                        self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
                    call()
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(self.client.writes, [])

    def test_cli_apply_with_stage1_pins_stops_at_the_plan_check_before_docker_or_writes(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(r2w, "run_docker", side_effect=AssertionError("Docker 호출 금지")), \
                patch.dict(os.environ, ENV):
            plan_path = Path(folder) / "plan.json"
            plan_path.write_text(json.dumps(self.plan), encoding="utf-8")      # 다른(sample_only) 릴리스 계획
            report = Path(folder) / "report.json"
            code = update.main(["--qdrant-url", URL, "--collection", "b2", "--plan", str(plan_path), "--apply",
                                "--confirm-write", "--expect-fingerprint", "0" * 64, "--report", str(report)])
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((code, output["status"], output["error_type"]), (1, "FAILED", "PlanError"))
        self.assertNotIn(ENV[update.API_KEY_ENV], json.dumps(output))


class FinalPinTests(Base):
    """2차 확정 모양(final_sample_pins: 미확정 0개). 모든 단계가 기준값 게이트를 통과하고, 그 뒤의 검사가 계속 지킨다.
    여기서는 '기준값 때문에 멈추지 않는다'와 '다른 릴리스·잘못된 값은 다음 검사에서 멈춘다'만 확인한다 (실제 쓰기 없음)."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(profiles, "R3_PINS", final_sample_pins())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pins_are_final(self):
        self.assertTrue(profiles.is_finalized())
        self.assertEqual((profiles.missing_pins(), profiles.pin_stage()), ([], "final"))

    def test_every_stage_passes_the_pin_gate_and_later_guards_still_stop(self):
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder, patch.object(fi, "execute_full_index", side_effect=never), \
                patch.object(r2w, "run_docker", side_effect=never):
            update_stage, full = self.entries(folder, runner, never)
            for name, call in {**update_stage, **full}.items():
                with self.subTest(entry=name), patch.dict(os.environ, {}):
                    try:
                        call()
                        self.fail("멈춰야 한다")
                    except profiles.ProfileError as error:
                        self.fail(f"2차 확정 기준값인데 기준값 게이트에서 멈춤: {error}")
                    except Exception:      # 계획·릴리스·번들·Docker 등 다음 검사에서 멈춘다
                        pass
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(self.client.writes, [])

    def test_wrong_digest_manifest_or_plan_sha_in_final_pins_are_refused(self):
        """확정된 값이라도 실제 계획·릴리스와 다르면 거부된다 (기준값 게이트 통과 ≠ 무조건 허용)."""
        from scripts import plan_r3_vector_reuse as planner_mod
        plan = self.s.plan("b2")
        pins = self.s.pins_for({k: self.s.plan(k) for k in profiles.COLLECTION_KEYS})   # sample_only 완전 확정 값
        planner_mod.validate_plan(plan, self.s.release, pins, self.s.sources, require_pins=True)
        for field, value in (("plan_sha256", "0" * 64), ("reuse_vector_digest", "0" * 64), ("points", 999)):
            with self.subTest(field=field):
                bad = json.loads(json.dumps(pins))
                bad["collections"]["b2"][field] = value
                with self.assertRaises(planner_mod.PlanError):
                    planner_mod.validate_plan(plan, self.s.release, bad, self.s.sources, require_pins=True)
        bad = json.loads(json.dumps(pins))
        bad["manifest_lf_sha256"] = "0" * 64
        with self.assertRaisesRegex(planner_mod.PlanError, "manifest"):
            planner_mod.load_release(bad)


class RealC0PinTests(Base):
    """실제 R3_PINS (바꿔 끼우지 않음). #110 반영 C0: manifest·건수·points만 확정, 계획·결과 값은 미확정."""

    def test_declared_stage_matches_the_values_and_only_plan_results_are_missing(self):
        self.assertEqual((profiles.R3_PIN_STAGE, profiles.pin_stage()), ("pending", "pending"))
        plan_fields = ("vector_digest", "reuse_count", "reuse_vector_digest", "embed_count", "delete_count", "plan_sha256")
        self.assertEqual(sorted(profiles.missing_pins()),
                         sorted(f"collections.{k}.{f}" for k in profiles.COLLECTION_KEYS for f in plan_fields))

    def test_every_pin_requiring_write_is_refused_before_writing_with_real_pins(self):
        never = AssertionError("쓰기·외부 호출 금지")
        runner = SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(never))
        with tempfile.TemporaryDirectory() as folder, patch.object(fi, "execute_full_index", side_effect=never),                 patch.object(r2w, "run_docker", side_effect=never):
            update_stage, full = self.entries(folder, runner, never)
            for name, call in {**update_stage, **full}.items():
                with self.subTest(entry=name), patch.dict(os.environ, {}),                         self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
                    call()
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(self.client.writes, [])

    def test_cli_apply_index_and_load_stop_before_docker_or_connections(self):
        with tempfile.TemporaryDirectory() as folder,                 patch.object(r2w, "run_docker", side_effect=AssertionError("Docker 호출 금지")),                 patch.object(update, "make_isolated_client", side_effect=AssertionError("접속 금지")),                 patch.dict(os.environ, ENV):
            plan_path = Path(folder) / "plan.json"
            plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
            for mode in (["--apply"], ["--index", "--ollama-url", "http://127.0.0.1:11434"]):
                with self.subTest(mode=mode[0]):
                    report = Path(folder) / f"report{mode[0]}.json"
                    code = update.main(["--qdrant-url", URL, "--collection", "b2", "--plan", str(plan_path), *mode,
                                        "--confirm-write", "--expect-fingerprint", "0" * 64, "--report", str(report)])
                    output = json.loads(report.read_text(encoding="utf-8"))
                    self.assertEqual((code, output["error_type"]), (1, "ProfileError"))
            with patch("neo4j.GraphDatabase.driver", side_effect=AssertionError("접속 금지")):
                report = Path(folder) / "neo4j-load.json"
                code = neo4j_r3.main(["--neo4j-url", f"bolt://127.0.0.1:{neo4j_r3.ISOLATED_BOLT_PORT}", "--load",
                                      "--confirm-write", "--expect-fingerprint", "0" * 64, "--report", str(report)])
                output = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual((code, output["error_type"]), (1, "ProfileError"))


class StageMapTests(unittest.TestCase):
    """단계 계산과 단계별 허용 쓰기 표가 기준값 게이트와 같은 결론을 낸다."""

    def test_pin_stage_of_each_fixture(self):
        self.assertEqual([profiles.pin_stage(p) for p in (pending_pins(), stage1_pins(), final_sample_pins())],
                         ["pending", "stage1", "final"])
        broken = final_sample_pins()
        del broken["collections"]["b3"]
        self.assertEqual(profiles.pin_stage(broken), "pending")        # 스키마가 깨지면 확정 쓰기 없음
        self.assertIn(profiles.R3_PIN_STAGE, profiles.PIN_STAGES)

    def test_stage_write_table_matches_the_gates(self):
        table = profiles.STAGE_ALLOWED_WRITES
        self.assertEqual(table["pending"], profiles.WRITES_WITHOUT_PINS)
        self.assertFalse(set(table["pending"]) & set(profiles.WRITES_REQUIRING_PINS))
        update_stage = {"update_r3_collections --apply", "update_r3_collections --index",
                        "load_neo4j_r3_isolated --load/--resume"}
        self.assertEqual(set(table["stage1"]) - set(table["pending"]), update_stage)
        self.assertTrue(set(profiles.WRITES_REQUIRING_PINS) <= set(table["final"]))
        for stage, pins in (("pending", pending_pins()), ("stage1", stage1_pins()), ("final", final_sample_pins())):
            with self.subTest(stage=stage):
                update_ok = not [m for m in profiles.missing_pins(pins) if m not in profiles.UPDATE_STAGE_DEFERRED_PINS]
                self.assertEqual(update_ok, update_stage <= set(table[stage]))
                self.assertEqual(profiles.is_finalized(pins), set(profiles.WRITES_REQUIRING_PINS) <= set(table[stage]))


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
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):     # 전부 미확정이면 갱신 단계도 거부
            profiles.require_finalized(pending_pins(), deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)

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
