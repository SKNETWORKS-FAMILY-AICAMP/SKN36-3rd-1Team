"""Issue #105 r3 Neo4j 격리 적재·B2/B3 교차 검증(scripts/load_neo4j_r3_isolated.py) 테스트.

현재 체크아웃된 릴리스 그래프·청크를 읽어 메모리 FakeNeo4j·R3Qdrant로 확인한다(벡터는 sample_only).
특정 릴리스 건수를 기대값으로 고정하지 않는다 → 최신 데이터 병합 전후 모두 같은 테스트가 성립한다.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import (FakeDocker, R3Qdrant, RUN_OLD, build_container, good_build_docker, pending_pins,  # noqa: E402
                         final_sample_pins, stage1_pins)
from test_load_neo4j_r2_isolated import FakeNeo4j  # noqa: E402

from database import handoff_data as hd  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import load_neo4j_r3_isolated as r3  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402

URL = f"bolt://127.0.0.1:{r3.ISOLATED_BOLT_PORT}"


def fingerprint(runner, ref, docker):
    """r3 dry-run과 같은 방법: Neo4j 서버 상태 지문 + build Docker 식별 정보."""
    return r3.dry_run(runner, ref, docker=docker)["fingerprint"]


class Shared:
    ref = points = None

    @classmethod
    def get(cls):
        if cls.ref is None:
            if hd.dataset_version() != profiles.DATASET_VERSION:
                raise unittest.SkipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
            cls.ref = r3.load_reference(profiles.r3_pins())
            release = cls.ref["release"]
            cls.points = {}
            for key, entry in release["collections"].items():
                points = {}
                for index, chunk in enumerate(entry["chunks"]):
                    payload = store.build_point_payload(
                        chunk, policy_meta=store.metadata_for_chunk(release["metadata"], chunk),
                        content_hash=release["content_hashes"][chunk["document_id"]], run_info=RUN_OLD)
                    points[chunk["point_id"]] = {"payload": payload, "vector": [float(index % 5) / 5 + 0.01] * 1024}
                cls.points[key] = points
        return cls.ref, cls.points


def finalized_pins(ref, points):
    pins = profiles.r3_pins()
    pins["manifest_lf_sha256"] = ref["manifest_lf_sha256"]
    pins["release_counts"] = dict(ref["release"]["counts"])
    for key in profiles.COLLECTION_KEYS:
        pins["collections"][key].update(points=len(points[key]), vector_digest=restore.vector_digest(points[key]),
                                        reuse_count=0, reuse_vector_digest="0" * 64, embed_count=0, delete_count=0,
                                        remove_count=0, plan_sha256="0" * 64)
    return pins


class GuardTests(unittest.TestCase):
    def test_only_the_r3_build_bolt_port_is_allowed(self):
        self.assertEqual(r3.require_isolated_bolt_url(URL), URL)
        for url in ("bolt://127.0.0.1:7687", "bolt://127.0.0.1:56487", "bolt://127.0.0.1:56587",
                    "bolt://127.0.0.1:56687", "neo4j://127.0.0.1:56677", "bolt://10.0.0.5:56677",
                    "bolt://neo4j:pw@127.0.0.1:56677", "bolt://127.0.0.1:56677/db"):
            with self.subTest(url=url), self.assertRaises((r3.Neo4jR3Error, ValueError)):
                r3.require_isolated_bolt_url(url)

    def test_cli_write_modes_require_flags_and_bad_url_never_connects(self):
        for extra in (["--load"], ["--load", "--confirm-write"], ["--resume", "--expect-fingerprint", "x"],
                      ["--cross-db"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                r3.main(["--neo4j-url", URL] + extra)
        with patch("neo4j.GraphDatabase.driver", side_effect=AssertionError("접속 금지")):
            self.assertEqual(r3.main(["--neo4j-url", "bolt://127.0.0.1:7687"]), 1)


class ReferenceGuardTests(unittest.TestCase):
    """r2 ReferenceTests의 그래프 입력 오류·청크 연결 불일치 차단을 r3 기준값 경로에서도 확인한다."""

    def setUp(self):
        if hd.dataset_version() != profiles.DATASET_VERSION:
            self.skipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")

    def test_invalid_graph_input_stops(self):
        bad = SimpleNamespace(ok=False, node_counts={}, relationship_counts={},
                              summary=lambda: {"error_counts": {"broken_reference": 1}})
        with patch.object(lg, "validate_graph_input", return_value=bad):
            with self.assertRaisesRegex(planner.PlanError, "그래프 입력 검증 실패"):
                r3.load_reference(profiles.r3_pins())

    def test_document_hash_or_chunk_link_mismatch_stops(self):
        broken = {"payload_count": 1, "missing_documents": ["DOC_SAMPLE_ONLY"], "hash_mismatch": []}
        with patch.object(lg, "check_vector_payload_links", return_value=broken):
            with self.assertRaisesRegex(r3.Neo4jR3Error, "연결 불일치"):
                r3.load_reference(profiles.r3_pins())

    def test_pinned_manifest_or_count_mismatch_stops(self):
        pins = profiles.r3_pins()
        pins["release_counts"]["neo4j_nodes"] = -1
        with self.assertRaisesRegex(planner.PlanError, "건수"):
            r3.load_reference(pins)
        pins = profiles.r3_pins()
        pins["manifest_lf_sha256"] = "0" * 64
        with self.assertRaisesRegex(planner.PlanError, "manifest"):
            r3.load_reference(pins)


class ReferenceAndLoadTests(unittest.TestCase):
    def setUp(self):
        self.ref, self.points = Shared.get()
        self.pins = finalized_pins(self.ref, self.points)
        self.docker = good_build_docker()

    def test_reference_uses_the_r2_count_order_with_current_release(self):
        c = self.ref["release"]["counts"]
        self.assertEqual(self.ref["counts"], (c["policies"], c["documents"], c["release_chunks"],
                                              len(self.ref["release"]["collections"]["b2"]["chunks"]),
                                              c["neo4j_nodes"], c["neo4j_relationships"], c["requires_condition"]))

    def test_dry_run_on_empty_db_has_no_writes(self):
        runner = FakeNeo4j()
        output = r3.dry_run(runner, self.ref, docker=self.docker)
        self.assertEqual((output["state"], output["db_writes"], output["r3_pins_finalized"]), ("empty", 0, profiles.R3_PIN_STAGE == "stage2"))   # 실제 R3_PINS 단계 (C0에서는 False)
        self.assertEqual(runner.writes(), [])

    def test_load_requires_finalized_matching_pins(self):
        runner = FakeNeo4j()
        with self.assertRaises(profiles.ProfileError):
            r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=pending_pins(),
                    docker=self.docker)
        wrong = dict(self.pins, manifest_lf_sha256="0" * 64)
        with self.assertRaisesRegex(r3.Neo4jR3Error, "r3 고정값"):
            r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=wrong,
                    docker=self.docker)
        with self.assertRaisesRegex(r3.Neo4jR3Error, "fingerprint"):
            r3.load(runner, self.ref, True, "0" * 64, pins=self.pins, docker=self.docker)
        self.assertEqual(runner.writes(), [])

    def test_load_and_cross_check_both_collections(self):
        runner = FakeNeo4j()
        result = r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=self.pins,
                         docker=self.docker)
        self.assertEqual(result["status"], "LOADED")
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        output = r3.cross_check(qdrant, runner, self.ref, pins=self.pins)
        self.assertEqual(output["status"], "PASS")
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                item = output["collections"][key]
                self.assertEqual(item["cross_db"]["status"], "PASS")
                self.assertEqual(item["cross_db"]["qdrant_points"], len(self.points[key]))
                self.assertTrue(item["full_index_verification"]["passed"])
                self.assertTrue(item["qdrant_unchanged"])
        self.assertEqual(qdrant.writes, [])

    def test_cross_check_rejects_each_wrong_digest_with_matching_point_counts(self):
        runner = FakeNeo4j()
        r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=self.pins,
                docker=self.docker)
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        self.assertEqual(r3.cross_check(qdrant, runner, self.ref, pins=self.pins)["status"], "PASS")
        for key in profiles.COLLECTION_KEYS:
            bad = json.loads(json.dumps(self.pins))
            bad["collections"][key]["vector_digest"] = "0" * 64
            with self.subTest(collection=key), self.assertRaisesRegex(r3.Neo4jR3Error, f"{key} Point .*벡터 digest"):
                r3.cross_check(qdrant, runner, self.ref, pins=bad)
        self.assertEqual(qdrant.writes, [])

    def test_load_is_allowed_before_final_vector_digests_are_known(self):
        pins = json.loads(json.dumps(self.pins))
        for key in profiles.COLLECTION_KEYS:
            pins["collections"][key]["vector_digest"] = None               # 임베딩 전 실제 상황
        runner = FakeNeo4j()
        result = r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=pins,
                         docker=self.docker)
        self.assertEqual(result["status"], "LOADED")
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            r3.cross_check(qdrant, runner, self.ref, pins=pins)            # 교차 검증은 최종값까지 필요

    def test_stage1_pins_pass_the_load_gate_but_not_cross_check(self):
        """1차 상태 R3_PINS(manifest·건수가 현재 릴리스와 같고 최종 digest만 None)는 메모리 FakeNeo4j 적재 게이트를
        통과하지만 교차 검증 게이트는 거부한다 (실제 DB 아님)."""
        runner = FakeNeo4j()
        result = r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=stage1_pins(),
                         docker=self.docker)
        self.assertEqual(result["status"], "LOADED")
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            r3.cross_check(qdrant, runner, self.ref, pins=stage1_pins())

    def test_final_shape_pins_pass_load_and_cross_check_gates_then_compare_digests(self):
        """2차 확정 모양(현재 릴리스 manifest·건수 + sample_only 결과 값): 적재·교차 검증의 기준값 게이트를 통과하고
        (ProfileError 아님), 그 다음 단계에서 sample_only 벡터가 고정 Point 수·digest와 달라 거부된다."""
        runner = FakeNeo4j()
        result = r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), pins=final_sample_pins(),
                         docker=self.docker)
        self.assertEqual(result["status"], "LOADED")
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        with self.assertRaisesRegex(r3.Neo4jR3Error, "b2 Point .*r3 고정값과 다릅니다"):
            r3.cross_check(qdrant, runner, self.ref, pins=final_sample_pins())
        self.assertEqual(qdrant.writes, [])

    def test_real_final_pins_pass_load_and_cross_check_gates_then_compare_digests(self):
        """실제 R3_PINS(C2 final): 메모리 FakeNeo4j 적재와 교차 검증의 기준값 게이트를 통과하고, sample_only 벡터는 실제 고정
        Point 수(1233)·digest와 달라 교차 검증 단계에서 거부된다. 실제 교차 검증 성공을 뜻하지 않는다."""
        runner = FakeNeo4j()
        result = r3.load(runner, self.ref, True, fingerprint(runner, self.ref, self.docker), docker=self.docker)
        self.assertEqual(result["status"], "LOADED")
        qdrant = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        with self.assertRaisesRegex(r3.Neo4jR3Error, "b2 Point .*r3 고정값과 다릅니다"):
            r3.cross_check(qdrant, runner, self.ref)
        self.assertEqual(qdrant.writes, [])

    def test_load_stops_before_writing_without_build_ownership(self):
        runner = FakeNeo4j()
        fp = fingerprint(runner, self.ref, self.docker)
        for name, containers in (
                ("other_project_same_port", [build_container("b" * 64, "qdrant"),
                                             build_container("c" * 64, "neo4j", project="lifepolicy")]),
                ("same_project_foreign_volume", [build_container("b" * 64, "qdrant"),
                                                 build_container("c" * 64, "neo4j",
                                                                 volumes=["issue91-r2-neo4j-20261119_neo4j_data",
                                                                          "issue91-r2-neo4j-20261119_neo4j_logs"])])):
            with self.subTest(name=name):
                with self.assertRaisesRegex(r3.Neo4jR3Error, "Docker 소유"):
                    r3.load(runner, self.ref, True, fp, pins=self.pins, docker=FakeDocker(containers))
                with self.assertRaisesRegex(r3.Neo4jR3Error, "Docker 소유"):
                    r3.dry_run(runner, self.ref, docker=FakeDocker(containers))
        restarted = good_build_docker()
        restarted.containers["c" * 64]["State"]["StartedAt"] = "2026-11-22T11:00:00Z"
        with self.assertRaisesRegex(r3.Neo4jR3Error, "fingerprint"):
            r3.load(runner, self.ref, True, fp, pins=self.pins, docker=restarted)   # 컨테이너 재시작도 fingerprint 변화
        self.assertEqual(runner.writes(), [])

    def test_cross_check_refusals(self):
        runner = FakeNeo4j()
        only_b2 = R3Qdrant({profiles.R3_COLLECTIONS["b2"]["collection"]: self.points["b2"]})
        for pins in (pending_pins(), stage1_pins()):
            with self.assertRaises(profiles.ProfileError):
                r3.cross_check(only_b2, runner, self.ref, pins=pins)
        with self.assertRaisesRegex(r3.Neo4jR3Error, "두 컬렉션"):
            r3.cross_check(only_b2, runner, self.ref, pins=self.pins)
        both = R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.points[k] for k in profiles.COLLECTION_KEYS})
        with self.assertRaisesRegex(r3.Neo4jR3Error, "최신 그래프"):
            r3.cross_check(both, runner, self.ref, pins=self.pins)        # Neo4j가 비어 있음


if __name__ == "__main__":
    unittest.main()
