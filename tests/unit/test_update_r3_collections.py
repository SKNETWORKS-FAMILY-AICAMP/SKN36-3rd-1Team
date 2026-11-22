"""Issue #105 r3 격리 Qdrant 갱신(scripts/update_r3_collections.py) 테스트.

R3Qdrant(벡터 비트 보존 테스트 더블)·FakeDocker(docker ps·inspect만 응답)와 sample_only 합성 데이터만 쓴다.
실제 Qdrant·Docker·Ollama 접속 없음.
"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import (FakeDocker, R3Qdrant, Sample, build_container, good_build_docker,  # noqa: E402
                         pending_pins, sample_vector)

from database import full_index as fi  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import update_r3_collections as update  # noqa: E402

URL = f"http://127.0.0.1:{update.ISOLATED_QDRANT_PORT}"
ENV = {update.API_KEY_ENV: "sample_only_r3_build_key"}


def fingerprint(client, plan, release, docker):
    return update.server_fingerprint(client, update.inspect_state(client, plan, release),
                                     update.require_build_ownership(docker))


class Case(unittest.TestCase):
    def setUp(self):
        self.s = Sample()
        self.plans = {k: self.s.plan(k) for k in profiles.COLLECTION_KEYS}
        self.pins = self.s.pins_for(self.plans)
        self.docker = good_build_docker()
        patcher = patch.dict(profiles.R3_SOURCES, self.s.sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    def apply(self, client, key, docker=None):
        plan, docker = self.plans[key], docker or self.docker
        return update.apply_updates(client, plan, self.s.release, True,
                                    fingerprint(client, plan, self.s.release, docker), self.pins, docker=docker)


class GuardTests(Case):
    def test_only_the_r3_build_port_is_allowed(self):
        self.assertEqual(update.require_isolated_url(URL), URL)
        for url in ("http://127.0.0.1:6333", "http://127.0.0.1:56446", "http://127.0.0.1:56556",
                    "http://127.0.0.1:56656", "http://10.0.0.5:56646", "http://user:pw@127.0.0.1:56646",
                    "https://127.0.0.1:56646", "http://127.0.0.1:56646/collections", "http://127.0.0.1"):
            with self.subTest(url=url), self.assertRaises((update.UpdateError, ValueError)):
                update.require_isolated_url(url)

    def test_secret_comes_only_from_the_dedicated_variable_and_is_not_reused(self):
        self.assertEqual(update.read_secret(update.API_KEY_ENV, ENV), ENV[update.API_KEY_ENV])
        with self.assertRaisesRegex(update.UpdateError, "환경 변수"):
            update.read_secret(update.API_KEY_ENV, {"QDRANT_API_KEY": "sample_only"})
        for other in profiles.FOREIGN_SECRET_ENVS:
            with self.subTest(other=other), self.assertRaisesRegex(update.UpdateError, "재사용"):
                update.read_secret(update.API_KEY_ENV, {**ENV, other: ENV[update.API_KEY_ENV]})

    def test_server_with_foreign_collection_alias_or_wrong_vectors_is_refused(self):
        client = self.s.client()
        self.assertEqual(update.require_isolated_server(client), sorted(self.s.collection(k) for k in self.plans))
        foreign = self.s.client()
        foreign.collections["exp_b2_qwen3emb06b_full_v1"] = {}
        aliased = self.s.client()
        aliased.aliases = ["sample_only_alias"]
        for name, bad in (("foreign", foreign), ("alias", aliased), ("dims", self.s.client(size=2560)),
                          ("version", self.s.client(version="1.13.0"))):
            with self.subTest(name=name), self.assertRaises(update.UpdateError):
                update.require_isolated_server(bad)


class DockerOwnershipTests(Case):
    """P1-2: build 프로젝트 소유가 확인되지 않으면 dry-run·쓰기 모두 멈춘다."""

    BAD = {
        "other_project_same_port": lambda: [build_container("b" * 64, "qdrant", project="issue91-r2-qdrant-20261119"),
                                            build_container("c" * 64, "neo4j")],
        "same_project_foreign_volume": lambda: [
            build_container("b" * 64, "qdrant", volumes=["issue91-r2-qdrant-20261119_qdrant_storage"]),
            build_container("c" * 64, "neo4j")],
        "shared_volume_name": lambda: [build_container("b" * 64, "qdrant", volumes=["skn36-3rd-1team_qdrant_storage"]),
                                       build_container("c" * 64, "neo4j")],
        "bind_mount": lambda: [build_container("b" * 64, "qdrant", mount_type="bind"),
                               build_container("c" * 64, "neo4j")],
        "all_interfaces": lambda: [build_container("b" * 64, "qdrant", host_ip="0.0.0.0"),
                                   build_container("c" * 64, "neo4j")],
        "wrong_image": lambda: [build_container("b" * 64, "qdrant", image="sha256:" + "0" * 64),
                                build_container("c" * 64, "neo4j")],
        "stopped": lambda: [build_container("b" * 64, "qdrant", running=False), build_container("c" * 64, "neo4j")],
        "missing_neo4j": lambda: [build_container("b" * 64, "qdrant")],
        "extra_container_in_project": lambda: [build_container("b" * 64, "qdrant"), build_container("c" * 64, "neo4j"),
                                               {**build_container("d" * 64, "neo4j"),
                                                "NetworkSettings": {"Ports": {}}}],
    }

    def test_exact_build_project_passes(self):
        identity = update.require_build_ownership(self.docker)
        self.assertEqual(set(identity), {"qdrant", "neo4j"})
        self.assertTrue(all(call[0] in ("ps", "inspect") for call in self.docker.calls))   # 읽기 전용 명령만

    def test_foreign_or_misconfigured_build_containers_are_refused(self):
        for name, containers in self.BAD.items():
            with self.subTest(name=name), self.assertRaisesRegex(update.UpdateError, "Docker 소유 확인 실패"):
                update.require_build_ownership(FakeDocker(containers()))

    def test_docker_unavailable_or_malformed_output_fails_closed(self):
        def missing(args):
            raise FileNotFoundError("sample_only docker not installed")

        def malformed(args):
            return "not-json" if args[0] == "inspect" else "b" * 64
        for name, docker in (("missing", missing), ("malformed", malformed)):
            with self.subTest(name=name), self.assertRaisesRegex(update.UpdateError, "Docker 소유 확인 실패"):
                update.require_build_ownership(docker)

    def test_every_write_entry_stops_before_writing_when_ownership_fails(self):
        bad = FakeDocker(self.BAD["other_project_same_port"]())
        client, plan = self.s.client(), self.plans["b2"]
        good_fp = fingerprint(client, plan, self.s.release, self.docker)
        with self.assertRaisesRegex(update.UpdateError, "Docker 소유"):
            update.apply_updates(client, plan, self.s.release, True, good_fp, self.pins, docker=bad)
        with self.assertRaisesRegex(update.UpdateError, "Docker 소유"), \
                patch.object(fi, "execute_full_index", side_effect=AssertionError("실행 금지")):
            update.index_missing(client, plan, self.s.release, lambda t: t, True, good_fp, self.pins, docker=bad)
        with self.assertRaisesRegex(update.UpdateError, "Docker 소유"):
            update.dry_run(client, "b2", self.s.release, plan, self.pins, docker=bad)
        empty = R3Qdrant()
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "s.snapshot"
            snapshot.write_bytes(b"sample_only")
            with self.assertRaisesRegex(update.UpdateError, "Docker 소유"):
                update.restore_source_snapshot(empty, URL, "b2", snapshot, True, ENV,
                                               lambda *a, **k: self.fail("업로드 금지"), docker=bad)
        self.assertEqual((client.writes, empty.writes), ([], []))

    def test_fingerprint_includes_container_identity(self):
        client, plan = self.s.client(), self.plans["b3"]
        before = fingerprint(client, plan, self.s.release, self.docker)
        restarted = good_build_docker()
        restarted.containers["b" * 64]["State"]["StartedAt"] = "2026-11-22T09:00:00Z"
        self.assertNotEqual(fingerprint(client, plan, self.s.release, restarted), before)
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.apply_updates(client, plan, self.s.release, True, before, self.pins, docker=restarted)
        self.assertEqual(client.writes, [])

    def test_container_change_during_write_is_reported(self):
        client, plan = self.s.client(), self.plans["b2"]
        docker = good_build_docker()
        fp = fingerprint(client, plan, self.s.release, docker)
        original = client.delete

        def delete_and_restart(**kwargs):
            docker.containers["c" * 64]["State"]["StartedAt"] = "2026-11-22T09:30:00Z"
            return original(**kwargs)
        client.delete = delete_and_restart
        with self.assertRaisesRegex(update.UpdateError, "식별 정보가 바뀌었습니다"):
            update.apply_updates(client, plan, self.s.release, True, fp, self.pins, docker=docker)


class SnapshotRestoreTests(Case):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshot = Path(self.tmp.name) / "source.snapshot"
        self.snapshot.write_bytes(b"sample_only source snapshot")
        sha = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        for key in profiles.COLLECTION_KEYS:
            profiles.R3_SOURCES[key]["snapshot_sha256"] = sha

    def poster(self, client, key, records=None, status=200):
        calls = []

        def post(url, params, headers, files, timeout):
            calls.append({"url": url, "params": params, "has_key": headers.get("api-key") == ENV[update.API_KEY_ENV]})
            client.collections[self.s.collection(key)] = records if records is not None else dict(self.s.records[key])
            return SimpleNamespace(status_code=status)
        return post, calls

    def restore(self, client, key, post, snapshot=None, confirm=True):
        return update.restore_source_snapshot(client, URL, key, snapshot or self.snapshot, confirm, ENV, post,
                                              docker=self.docker)

    def test_restore_into_absent_collection_and_verify_source_digest(self):
        client = R3Qdrant()
        post, calls = self.poster(client, "b2")
        result = self.restore(client, "b2", post)
        self.assertEqual((result["status"], result["points"], result["docker_project"]),
                         ("RESTORED", 5, "issue105-r3-build"))
        self.assertEqual(calls[0]["params"]["checksum"], profiles.R3_SOURCES["b2"]["snapshot_sha256"])
        self.assertTrue(calls[0]["url"].endswith(f"/collections/{self.s.collection('b2')}/snapshots/upload"))
        post, _ = self.poster(client, "b3")       # 다른 r3 컬렉션이 이미 있어도 대상이 없으면 허용
        self.assertEqual(self.restore(client, "b3", post)["status"], "RESTORED")

    def test_restore_refusals(self):
        existing = self.s.client(keys=("b2",))
        post, calls = self.poster(existing, "b2")
        with self.assertRaisesRegex(update.UpdateError, "confirm_write"):
            self.restore(R3Qdrant(), "b2", post, confirm=False)
        with self.assertRaisesRegex(update.UpdateError, "이미 있습니다"):
            self.restore(existing, "b2", post)
        other = Path(self.tmp.name) / "other.snapshot"
        other.write_bytes(b"sample_only other")
        with self.assertRaisesRegex(update.UpdateError, "SHA-256"):
            self.restore(R3Qdrant(), "b2", post, snapshot=other)
        self.assertEqual(calls, [])
        client = R3Qdrant()
        changed = {p: dict(r, vector=sample_vector(99)) for p, r in self.s.records["b2"].items()}
        post, _ = self.poster(client, "b2", records=changed)
        with self.assertRaisesRegex(update.UpdateError, "원본 기록과 다릅니다"):
            self.restore(client, "b2", post)
        post, _ = self.poster(R3Qdrant(), "b2", status=500)
        with self.assertRaisesRegex(update.UpdateError, "HTTP 500"):
            self.restore(R3Qdrant(), "b2", post)


class StateTests(Case):
    def test_dry_run_states_from_absent_to_source(self):
        client = self.s.client(keys=("b2",))
        self.assertEqual(update.dry_run(client, "b3", self.s.release, docker=self.docker)["state"], "absent")
        output = update.dry_run(client, "b2", self.s.release, docker=self.docker)
        self.assertEqual((output["state"], output["db_writes"], output["r3_pins_finalized"]), ("source", 0, True))   # 실제 R3_PINS 2차 확정
        self.assertEqual(output["plan_object"], self.plans["b2"])
        self.assertEqual(output["plan_object"]["basis"], profiles.PLAN_BASIS_SERVER)   # 서버 계획 = 최종 고정 기준
        self.assertEqual(output["proposed_pins"]["embed_count"], 3)
        self.assertEqual(output["proposed_pins"]["plan_sha256"], planner.plan_sha256(self.plans["b2"]))
        self.assertEqual(client.writes, [])

    def test_full_flow_for_each_collection_with_finalized_pins(self):
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                client, plan, ids = self.s.client(), self.plans[key], self.s.ids[key]
                state = update.inspect_state(client, plan, self.s.release)
                self.assertEqual(state["state"], "payload_pending")
                self.assertEqual(state["delete_present"], sorted([ids["D"], ids["R"]]))
                result = self.apply(client, key)
                self.assertEqual(result["status"], "UPDATED")
                self.assertEqual(sorted(result["deleted"]), sorted([ids["D"], ids["R"]]))
                state = update.inspect_state(client, plan, self.s.release)
                self.assertEqual((state["state"], state["missing_for_index"]),
                                 ("ready_for_index", sorted([ids["D"], ids["R"], ids["E"]])))
                other = [k for k in profiles.COLLECTION_KEYS if k != key][0]
                self.assertEqual(client.collections[self.s.collection(other)], self.s.records[other])  # 다른 컬렉션 불변
                self.assertEqual(self.apply(client, key)["status"], "ALREADY_READY")

    def test_writes_require_finalized_pins_and_matching_fingerprint(self):
        client, plan = self.s.client(), self.plans["b2"]
        current = fingerprint(client, plan, self.s.release, self.docker)
        with self.assertRaises(profiles.ProfileError):
            update.apply_updates(client, plan, self.s.release, True, current, pending_pins(), docker=self.docker)
        with self.assertRaisesRegex(update.UpdateError, "confirm_write"):
            update.apply_updates(client, plan, self.s.release, False, current, self.pins, docker=self.docker)
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.apply_updates(client, plan, self.s.release, True, "0" * 64, self.pins, docker=self.docker)
        changed = dict(self.pins, collections=dict(self.pins["collections"],
                                                  b2=dict(self.pins["collections"]["b2"], embed_count=2)))
        with self.assertRaisesRegex(planner.PlanError, "embed_count"):
            update.apply_updates(client, plan, self.s.release, True, current, changed, docker=self.docker)
        self.assertEqual(client.writes, [])

    def test_unexpected_or_changed_points_stop_the_flow(self):
        client, plan = self.s.client(), self.plans["b3"]
        coll = self.s.collection("b3")
        client.collections[coll]["00000000-0000-5000-8000-000000000001"] = {"payload": {}, "vector": sample_vector(5)}
        self.assertEqual(update.inspect_state(client, plan, self.s.release)["state"], "unexpected_points")
        client = self.s.client()
        client.collections[coll][self.s.ids["b3"]["A"]]["vector"] = sample_vector(55)
        self.assertEqual(update.inspect_state(client, plan, self.s.release)["state"], "vector_mismatch")
        with self.assertRaisesRegex(update.UpdateError, "vector_mismatch"):
            update.apply_updates(client, plan, self.s.release, True,
                                 fingerprint(client, plan, self.s.release, self.docker), self.pins, docker=self.docker)

    def test_delete_before_payload_update_is_unexpected_order(self):
        client, plan = self.s.client(), self.plans["b2"]
        client.collections[self.s.collection("b2")].pop(self.s.ids["b2"]["D"])
        self.assertEqual(update.inspect_state(client, plan, self.s.release)["state"], "unexpected_order")


class DeleteTargetTests(Case):
    """r2와 같은 기준: chunk_id·본문 SHA-256·벡터 SHA-256이 계획과 모두 같을 때만 삭제한다."""

    def test_changed_text_with_same_vector_is_never_deleted(self):
        client, plan = self.s.client(), self.plans["b2"]
        target = client.collections[self.s.collection("b2")][self.s.ids["b2"]["D"]]
        target["payload"]["text"] = "sample_only 다른 본문"
        state = update.inspect_state(client, plan, self.s.release)
        self.assertEqual(state["state"], "delete_target_mismatch")
        with self.assertRaisesRegex(update.UpdateError, "delete_target_mismatch"):
            update.apply_updates(client, plan, self.s.release, True,
                                 fingerprint(client, plan, self.s.release, self.docker), self.pins, docker=self.docker)
        self.assertEqual(client.writes, [])

    def test_delete_points_rechecks_every_target_and_deletes_nothing_on_mismatch(self):
        plan, coll = self.plans["b3"], self.s.collection("b3")
        originals = {**plan["delete"], **plan["remove"]}
        targets = sorted(plan["delete"])
        for name, change in (("chunk_id", lambda p: p.update(chunk_id="CHK_B3_DOC_SX_C999")),
                             ("text", lambda p: p.update(text="sample_only 바뀐 본문")),
                             ("vector", None)):
            with self.subTest(name=name):
                client = self.s.client()
                point = client.collections[coll][targets[0]]
                if change:
                    change(point["payload"])
                else:
                    point["vector"] = sample_vector(321)
                with self.assertRaisesRegex(update.UpdateError, "계획과 다릅니다"):
                    update.delete_points(client, coll, targets, originals)
                self.assertEqual(client.writes, [])
        client = self.s.client()
        with self.assertRaisesRegex(update.UpdateError, "허용 대상"):
            update.delete_points(client, coll, [self.s.ids["b3"]["A"]], originals)
        self.assertEqual(update.delete_points(client, coll, targets, originals), targets)
        self.assertEqual(client.writes, [("delete", tuple(targets))])

    def test_plan_entries_without_text_hash_are_refused(self):
        plan, coll = self.plans["b2"], self.s.collection("b2")
        originals = copy.deepcopy(plan["delete"])
        for item in originals.values():
            item.pop("text_sha256")
        client = self.s.client()
        with self.assertRaisesRegex(update.UpdateError, "계획과 다릅니다"):
            update.delete_points(client, coll, sorted(originals), originals)
        self.assertEqual(client.writes, [])


class PlanBasisTests(Case):
    """P1-3: 이 도구는 build 서버 snapshot 기준 계획만 받는다."""

    def test_preview_plan_is_refused_in_every_mode(self):
        preview = self.s.plan("b2", basis=profiles.PLAN_BASIS_PREVIEW)
        client = self.s.client()
        with self.assertRaisesRegex(update.UpdateError, "server_snapshot"):
            update.dry_run(client, "b2", self.s.release, preview, self.pins, docker=self.docker)
        with self.assertRaisesRegex(planner.PlanError, "server_snapshot"):
            update.apply_updates(client, preview, self.s.release, True, "0" * 64, self.pins, docker=self.docker)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(json.dumps(preview), encoding="utf-8")
            with self.assertRaisesRegex(update.UpdateError, "server_snapshot"):
                update.load_plan(path)
            path.write_text(json.dumps(self.plans["b2"]), encoding="utf-8")
            self.assertEqual(update.load_plan(path), self.plans["b2"])
        self.assertEqual(client.writes, [])


class IndexTests(Case):
    def fake_execute(self, key, seen):
        def execute(name, *, client, embedder, documents, policy_metadata, confirm_write, chunks):
            seen.update(name=name, client=client, chunks=chunks)
            ids = self.s.ids[key]
            vectors = embedder(["sample_only"] * 3)
            chunk_by_id = {c["point_id"]: c for c in chunks}
            for index, point_id in enumerate((ids["D"], ids["R"], ids["E"])):
                payload = planner.reuse_payload(chunk_by_id[point_id], self.s.metadata,
                                                self.s.release["content_hashes"],
                                                {**planner.expected_run(profiles.collection_spec(key)),
                                                 "index_run_id": "RUN_SAMPLE_ONLY_NEW"})
                client.collections[self.s.collection(key)][point_id] = {"vector": vectors[index], "payload": payload}
            return {"written": 3, "verification": {"passed": True}, "run_info": {}}
        return execute

    def test_index_uses_the_verified_client_config_and_embedding_limit(self):
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                client, plan = self.s.client(), self.plans[key]
                self.apply(client, key)
                seen = {}
                with patch.object(fi, "execute_full_index", side_effect=self.fake_execute(key, seen)):
                    result = update.index_missing(client, plan, self.s.release,
                                                  lambda texts: [sample_vector(40)] * len(texts), True,
                                                  fingerprint(client, plan, self.s.release, self.docker), self.pins,
                                                  docker=self.docker)
                self.assertIs(seen["client"], client)
                self.assertEqual(seen["name"], profiles.R3_COLLECTIONS[key]["config"])
                self.assertIs(seen["chunks"], self.s.release["collections"][key]["chunks"])
                self.assertEqual((result["state"], result["embedded_texts"]), ("complete", 3))
                output = update.dry_run(client, key, self.s.release, plan, self.pins, docker=self.docker)
                self.assertEqual(output["proposed_final_pins"]["points"], 6)

    def test_index_refuses_wrong_state_fingerprint_or_too_many_embeddings(self):
        client, plan = self.s.client(), self.plans["b2"]
        with patch.object(fi, "execute_full_index", side_effect=AssertionError("실행 금지")):
            with self.assertRaisesRegex(update.UpdateError, "payload_pending"):
                update.index_missing(client, plan, self.s.release, lambda t: t, True,
                                     fingerprint(client, plan, self.s.release, self.docker), self.pins,
                                     docker=self.docker)
            self.apply(client, "b2")
            with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
                update.index_missing(client, plan, self.s.release, lambda t: t, True, "0" * 64, self.pins,
                                     docker=self.docker)
        guarded = update.limited_embedder(lambda texts: self.fail("초과 요청이 전달되면 안 됨"), 2)
        with self.assertRaisesRegex(update.UpdateError, "예상 2개"):
            guarded(["a", "b", "c"])


class CliTests(unittest.TestCase):
    def test_write_modes_require_flags_and_files_are_never_overwritten(self):
        base = ["--qdrant-url", URL, "--collection", "b2"]
        for extra in (["--restore-snapshot"], ["--apply"], ["--apply", "--confirm-write", "--expect-fingerprint", "x"],
                      ["--index", "--confirm-write", "--expect-fingerprint", "x", "--plan", "p.json"],
                      ["--write-plan", "x.json", "--plan", "p.json"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                update.main(base + extra)
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / "exists.json"
            existing.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                update.main(base + ["--report", str(existing)])

    def test_bad_url_fails_before_any_connection(self):
        with patch("qdrant_client.QdrantClient", side_effect=AssertionError("접속 금지")):
            for url in ("http://127.0.0.1:6333", "http://127.0.0.1:56446"):
                with self.subTest(url=url):
                    self.assertEqual(update.main(["--qdrant-url", url, "--collection", "b3"]), 1)


if __name__ == "__main__":
    unittest.main()
