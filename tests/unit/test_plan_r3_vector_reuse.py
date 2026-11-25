"""Issue #105 r3 재사용 계획(scripts/plan_r3_vector_reuse.py) 테스트.

합성 데이터(sample_only)로 B2·B3 판정 규칙을 확인하고, 실제 릴리스·덤프는 건수를 고정하지 않는 불변식만 확인한다
(최신 데이터 병합 전후 모두 성립해야 하므로 특정 릴리스의 건수를 기대값으로 쓰지 않는다).
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import RUN_OLD, RUN_OTHER, Sample, pending_pins, sample_vector, stage1_pins  # noqa: E402

from database import full_index as fi, handoff_data as hd  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402

# #31 B3 덤프(로컬, Git 제외)와 그 원본 기록. r3b 구축(#110) 때의 B3 원본이었고, 지금 R3_SOURCES(r3b 최종 snapshot)는 아니다.
# 덤프 사전 검토(dump_preview) 경로가 실제 릴리스·실제 덤프에서 일관된지 확인하는 데만 쓴다 (sources를 명시해 넘긴다).
B3_DUMP = Path(__file__).resolve().parents[2] / "venv/issue31_20261109/exp_b3_qwen3emb06b_v140_v1.after.json"
ISSUE31_B3_SOURCE = {"b3": {"label": "B3 snapshot (#31 before, r3b 구축 때의 원본 기록)", "points": 1220,
                            "vector_digest": "0a794e34a91f4314b6e469d6375177ac745b8b00e5ce3d4f0c2bc2f50d726371",
                            "snapshot_sha256": "502b7b7e1b923183a2992a5450e63470d2da5238317d9883f07382cdbb584ad8",
                            "default_path": "venv/issue31_20261109/exp_b3_qwen3emb06b_v140_v1.before.snapshot"}}


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.s = Sample()

    def test_b2_and_b3_are_classified_separately_with_the_r2_rules(self):
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                plan, ids = self.s.plan(key), self.s.ids[key]
                self.assertEqual(plan["collection"], profiles.R3_COLLECTIONS[key]["collection"])
                self.assertEqual(set(plan["reuse"]), {ids["A"], ids["B"], ids["C"]})
                self.assertEqual(set(plan["delete"]), {ids["D"], ids["R"]})
                self.assertEqual(set(plan["new"]), {ids["E"]})
                self.assertEqual(plan["remove"], {})
                self.assertEqual({p: v["kind"] for p, v in plan["reuse"].items()},
                                 {ids["A"]: "identical", ids["B"]: "metadata_refresh", ids["C"]: "core_refresh"})
                self.assertEqual(plan["delete"][ids["D"]]["changed_parts"], ["text"])
                self.assertEqual(plan["delete"][ids["R"]]["changed_parts"], ["policy_name"])
                self.assertEqual(plan["counts"]["embed"], 3)
                self.assertEqual(plan["counts"]["reuse"] + plan["counts"]["embed"], plan["target_points"])

    def test_reused_points_keep_their_own_run_information(self):
        plan, ids = self.s.plan("b2"), self.s.ids["b2"]
        self.assertEqual(plan["reuse"][ids["A"]]["run"], RUN_OLD)
        self.assertEqual(plan["reuse"][ids["B"]]["run"], RUN_OTHER)
        self.assertEqual(plan["counts"]["reuse_by_index_run_id"],
                         {RUN_OLD["index_run_id"]: 2, RUN_OTHER["index_run_id"]: 1})
        payload = planner.reuse_payload(self.s.chunks["b2"][1], self.s.metadata, self.s.release["content_hashes"],
                                        plan["reuse"][ids["B"]]["run"])
        self.assertEqual(payload["index_run_id"], RUN_OTHER["index_run_id"])

    def test_plan_has_no_vector_values_and_round_trips(self):
        plan = self.s.plan("b3")
        text = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(str(sample_vector(0)[1]), text)
        self.assertEqual(planner.plan_sha256(json.loads(text)), planner.plan_sha256(plan))

    def test_points_not_in_latest_chunks_are_reported_as_remove(self):
        s = self.s
        extra = dict(s.chunks["b2"][0], chunk_id="CHK_B2_DOC_S0_C009")
        extra["point_id"] = planner.chunking.make_point_id(extra["chunk_id"])
        records = dict(s.records["b2"])
        records[extra["point_id"]] = {"payload": dict(records[s.ids["b2"]["A"]]["payload"], chunk_id=extra["chunk_id"],
                                                      point_id=extra["point_id"]), "vector": sample_vector(77)}
        sources = dict(s.sources, b2=dict(s.sources["b2"], points=len(records),
                                          vector_digest=planner.restore.vector_digest(records)))
        plan = planner.build_plan("b2", records, s.release, sources, basis=profiles.PLAN_BASIS_SERVER)
        self.assertEqual(set(plan["remove"]), {extra["point_id"]})
        self.assertEqual(plan["counts"]["remove"], 1)
        self.assertEqual(plan["remove"][extra["point_id"]]["text_sha256"],
                         planner.text_sha(records[extra["point_id"]]["payload"]["text"]))   # 삭제 전 본문 대조용

    def test_vector_from_another_model_or_rule_is_never_reused(self):
        for field, value in (("embedding_model", "qwen3-embedding:4b"), ("embedding_dim", 2560),
                             ("embedding_input_rule", "body_only")):
            with self.subTest(field=field):
                s = Sample()
                s.records["b2"][s.ids["b2"]["A"]]["payload"][field] = value
                with self.assertRaisesRegex(planner.PlanError, "모델·차원·입력 규칙"):
                    planner.classify(s.records["b2"], s.chunks["b2"], s.metadata, s.release["content_hashes"],
                                     profiles.collection_spec("b2"))

    def test_chunk_prefix_and_point_id_rules(self):
        s = self.s
        with self.assertRaisesRegex(planner.PlanError, "접두어"):
            planner.classify(s.records["b2"], s.chunks["b2"], s.metadata, s.release["content_hashes"],
                             profiles.collection_spec("b3"))
        broken = [dict(s.chunks["b2"][0], point_id="00000000-0000-5000-8000-000000000000")] + s.chunks["b2"][1:]
        with self.assertRaisesRegex(planner.PlanError, "Point ID"):
            planner.classify(s.records["b2"], broken, s.metadata, s.release["content_hashes"],
                             profiles.collection_spec("b2"))

    def test_source_must_match_recorded_asset(self):
        s = self.s
        records = copy.deepcopy(s.records["b3"])
        records[s.ids["b3"]["A"]]["vector"][0] += 1e-6
        with self.assertRaisesRegex(planner.PlanError, "원본이 기록된 자산"):
            planner.build_plan("b3", records, s.release, s.sources, basis=profiles.PLAN_BASIS_SERVER)
        with self.assertRaisesRegex(planner.PlanError, "원본이 기록된 자산"):
            planner.check_source("b2", s.records["b2"])            # 실제 R3_SOURCES(r3b 최종 snapshot)와 다르다


class ValidatePlanTests(unittest.TestCase):
    def setUp(self):
        self.s = Sample()
        self.plans = {k: self.s.plan(k) for k in profiles.COLLECTION_KEYS}

    def test_pending_pins_allow_dry_run_but_block_write_paths(self):
        plan = self.plans["b2"]
        planner.validate_plan(plan, self.s.release, pending_pins(), self.s.sources)
        with self.assertRaises(profiles.ProfileError):
            planner.validate_plan(plan, self.s.release, pending_pins(), self.s.sources, require_pins=True)
        with self.assertRaises(profiles.ProfileError):                 # 갱신 단계도 vector_digest 외 미확정은 거부
            planner.validate_plan(plan, self.s.release, pending_pins(), self.s.sources, require_pins=True,
                                  deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)

    def test_stage1_pins_reject_a_plan_from_another_release(self):
        """1차 확정 모양(현재 릴리스 manifest)은 합성(sample_only) 계획을 manifest 불일치로 거부한다 (다른 릴리스 계획 혼입 방지)."""
        with self.assertRaisesRegex(planner.PlanError, "현재 릴리스|r3 고정값"):
            planner.validate_plan(self.plans["b2"], self.s.release, stage1_pins(), self.s.sources,
                                  require_pins=True, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)

    def test_real_final_pins_pass_the_gate_and_still_compare_the_fixed_values(self):
        """실제 R3_PINS(C2 stage2, 미확정 0): 쓰기용 검증(최종·갱신 단계 모두)이 기준값 게이트를 통과한 뒤,
        확정된 계획 값(points·reuse·plan_sha256 등)과 달라 다른(sample_only) 계획을 거부한다. dry-run 검증도 같은 값을 대조한다."""
        for deferred in ((), profiles.UPDATE_STAGE_DEFERRED_PINS):
            with self.subTest(deferred=bool(deferred)), \
                    self.assertRaisesRegex(planner.PlanError, "r3 고정값과 다릅니다.*plan_sha256"):
                planner.validate_plan(self.plans["b2"], self.s.release, profiles.r3_pins(), self.s.sources,
                                      require_pins=True, deferred=deferred)
        with self.assertRaisesRegex(planner.PlanError, "r3 고정값과 다릅니다"):
            planner.validate_plan(self.plans["b2"], self.s.release, profiles.r3_pins(), self.s.sources)

    def test_finalized_pins_must_match_the_plan(self):
        pins = self.s.pins_for(self.plans)
        self.assertTrue(profiles.is_finalized(pins))
        planner.validate_plan(self.plans["b3"], self.s.release, pins, self.s.sources, require_pins=True)
        for field, value in (("reuse_vector_digest", "0" * 64), ("embed_count", 99), ("plan_sha256", "0" * 64),
                             ("remove_count", 1)):
            with self.subTest(field=field):
                changed = copy.deepcopy(pins)
                changed["collections"]["b3"][field] = value
                with self.assertRaisesRegex(planner.PlanError, field):
                    planner.validate_plan(self.plans["b3"], self.s.release, changed, self.s.sources)

    def test_plan_from_another_release_or_source_is_rejected(self):
        release = dict(self.s.release, manifest_lf_sha256="b" * 64)
        with self.assertRaisesRegex(planner.PlanError, "현재 릴리스"):
            planner.validate_plan(self.plans["b2"], release, pending_pins(), self.s.sources)
        sources = dict(self.s.sources, b2=dict(self.s.sources["b2"], vector_digest="0" * 64))
        with self.assertRaisesRegex(planner.PlanError, "원본"):
            planner.validate_plan(self.plans["b2"], self.s.release, pending_pins(), sources)
        tampered = copy.deepcopy(self.plans["b2"])
        tampered["new"].update(tampered["reuse"])
        with self.assertRaisesRegex(planner.PlanError, "겹칩니다"):
            planner.validate_plan(tampered, self.s.release, pending_pins(), self.s.sources)

    def test_plan_basis_server_and_preview_are_never_mixed(self):
        """P1-3: 같은 원본이라도 payload가 다르면 plan_sha256이 달라진다 → 덤프 계획은 고정값·쓰기에 쓰지 않는다."""
        s = self.s
        server = self.plans["b3"]
        preview_records = copy.deepcopy(s.records["b3"])
        for record in preview_records.values():
            record["payload"]["region"] = "sample_only 다른 지역"          # #31 before↔after의 region 차이를 흉내
        preview = planner.build_plan("b3", preview_records, s.release, s.sources, basis=profiles.PLAN_BASIS_PREVIEW)
        self.assertEqual((set(preview["reuse"]), preview["reuse_vector_digest"]),
                         (set(server["reuse"]), server["reuse_vector_digest"]))
        self.assertNotEqual(planner.plan_sha256(preview), planner.plan_sha256(server))
        proposed = planner.proposed_pins(s.release, {"b3": preview})
        self.assertIsNone(proposed["collections"]["b3"]["plan_sha256"])      # 덤프 계획 SHA는 제안하지 않는다
        self.assertEqual(proposed["plan_basis"], {"b3": "dump_preview"})
        pins = s.pins_for(self.plans)
        with self.assertRaisesRegex(planner.PlanError, "server_snapshot"):
            planner.validate_plan(preview, s.release, pins, s.sources, require_pins=True)
        planner.validate_plan(server, s.release, pins, s.sources, require_pins=True)
        for bad in (None, "sample_only"):
            with self.subTest(basis=bad):
                plan = dict(server, basis=bad)
                with self.assertRaisesRegex(planner.PlanError, "basis"):
                    planner.validate_plan(plan, s.release, pending_pins(), s.sources)
        with self.assertRaisesRegex(planner.PlanError, "계획 기준"):
            planner.build_plan("b3", s.records["b3"], s.release, s.sources, basis="sample_only")

    def test_proposed_pins_have_the_r3_pins_shape_and_are_not_written(self):
        before = profiles.r3_pins()
        proposed = planner.proposed_pins(self.s.release, self.plans)
        self.assertEqual(set(proposed) - {"note", "plan_basis"}, set(profiles.R3_PINS))
        self.assertEqual(set(proposed["collections"]), set(profiles.COLLECTION_KEYS))
        self.assertEqual(set(proposed["collections"]["b2"]), set(profiles.R3_PINS["collections"]["b2"]))
        self.assertIsNone(proposed["collections"]["b2"]["vector_digest"])     # 임베딩 후에만 알 수 있다
        self.assertEqual(profiles.r3_pins(), before)


class DumpTests(unittest.TestCase):
    def test_dump_format_errors(self):
        s = Sample()
        rows = [{"id": p, "payload": r["payload"], "vector": r["vector"]} for p, r in s.records["b2"].items()]
        with tempfile.TemporaryDirectory() as folder:
            for name, data, pattern in (("dup", rows + rows[:1], "중복"),
                                        ("dim", [dict(rows[0], vector=[0.1] * 3)], "차원"),
                                        ("id", [dict(rows[0], id="not-a-uuid")], "형식"),
                                        ("shape", {"points": rows}, "목록")):
                with self.subTest(name=name):
                    path = Path(folder) / f"{name}.json"
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaisesRegex(planner.PlanError, pattern):
                        planner.load_dump(path)
            good = Path(folder) / "good.json"
            good.write_text(json.dumps(rows), encoding="utf-8")
            self.assertEqual(set(planner.load_dump(good)), set(s.records["b2"]))


class RealReleaseTests(unittest.TestCase):
    """현재 체크아웃된 릴리스로 불변식만 확인한다 (건수 고정 없음, DB 접속 없음)."""

    @classmethod
    def setUpClass(cls):
        if hd.dataset_version() != profiles.DATASET_VERSION:
            raise unittest.SkipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
        cls.release = planner.load_release(profiles.r3_pins())

    def test_release_matches_full_index_configs(self):
        for key in profiles.COLLECTION_KEYS:
            entry = self.release["collections"][key]
            self.assertEqual(len(entry["chunks"]), fi.get_config(entry["spec"]["config"])["expected_chunks"])
            self.assertTrue(all(c["chunk_id"].startswith(entry["spec"]["chunk_prefix"]) for c in entry["chunks"]))
        self.assertEqual(self.release["manifest_lf_sha256"], fi.manifest_sha256())

    def test_pinned_manifest_mismatch_is_rejected(self):
        pins = profiles.r3_pins()
        pins["manifest_lf_sha256"] = "0" * 64
        with self.assertRaisesRegex(planner.PlanError, "manifest"):
            planner.load_release(pins)

    @unittest.skipUnless(B3_DUMP.is_file(), "NOT_RUN: #31 B3 덤프(venv/issue31_20261109)가 이 PC에 없음")
    def test_real_b3_dump_plan_is_consistent_without_db_or_embedding(self):
        before = planner.restore.file_sha(B3_DUMP)
        with patch.object(planner.em, "make_ollama_embedder", side_effect=AssertionError("임베딩 금지")):
            plan = planner.build_plan("b3", planner.load_dump(B3_DUMP), self.release, ISSUE31_B3_SOURCE,
                                      basis=profiles.PLAN_BASIS_PREVIEW)
        self.assertEqual(plan["source"]["vector_digest"], ISSUE31_B3_SOURCE["b3"]["vector_digest"])
        self.assertNotEqual(plan["source"]["vector_digest"], profiles.R3_SOURCES["b3"]["vector_digest"])  # r3d 원본 아님
        self.assertEqual(plan["counts"]["reuse"] + plan["counts"]["embed"], plan["target_points"])
        self.assertEqual(plan["counts"]["remove"], 0)
        self.assertEqual(planner.restore.file_sha(B3_DUMP), before)


class CliTests(unittest.TestCase):
    def test_cli_is_read_only_and_reports_pending_pins(self):
        if hd.dataset_version() != profiles.DATASET_VERSION:
            self.skipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "plan.json"
            self.assertEqual(planner.main(["--report", str(report)]), 0)
            result = json.loads(report.read_text(encoding="utf-8"))
            with self.assertRaises(SystemExit):
                planner.main(["--report", str(report)])          # 기존 보고서 덮어쓰기 거부
        self.assertEqual((result["db_writes"], result["embedding_calls"], result["r3_pins_finalized"]),
                         (0, 0, profiles.R3_PIN_STAGE == "stage2"))   # C0(pending)에서는 False
        self.assertEqual((result["not_planned"], result["plan_basis"]), (["b2", "b3"], "dump_preview"))
        with self.assertRaises(SystemExit):
            planner.main(["--write-plan-dir", "sample_only"])           # 덤프 계획 파일 저장 옵션은 없다

    def test_module_has_no_database_write_or_embedding_path(self):
        source = Path(planner.__file__).read_text(encoding="utf-8")
        for call in ("upsert", "set_payload", "overwrite_payload", "delete(", "create_collection", "QdrantClient",
                     "GraphDatabase", "make_ollama_embedder", "execute_full_index", "requests."):
            self.assertNotIn(call, source, call)


if __name__ == "__main__":
    unittest.main()
