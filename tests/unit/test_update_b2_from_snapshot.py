"""Issue #91 2단계(scripts/update_b2_from_snapshot.py) 테스트.

FakeQdrant는 snapshot 복원처럼 벡터를 비트 그대로 보관하는 테스트 더블이다(정규화 없음).
합성 데이터는 sample_only 가상 값이며 DB·Docker·Ollama에 접속하지 않는다.
"""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qdrant_client import models

from database import full_index as fi, handoff_data as hd
from database.chunking import make_point_id
from database.qdrant import store
from scripts import restore_final_v140 as restore
from scripts import seed_b2_from_r1_vectors as seed
from scripts import update_b2_from_snapshot as update

COLLECTION = update.COLLECTION
RUN = dict(seed.DUMP_RUN)


class FakeQdrant:
    """필요한 Qdrant 메서드만 흉내 낸다. writes에 쓰기 호출을 기록하고, fail로 부분 실패를 만든다."""

    def __init__(self, points, extra_collections=(), size=1024, version=update.EXPECTED_QDRANT_VERSION):
        self.collections = {COLLECTION: copy.deepcopy(points)}
        for name in extra_collections:
            self.collections[name] = {}
        self.size, self.version, self.writes, self.fail = size, version, [], {}

    def info(self):
        return SimpleNamespace(title="sample_only", version=self.version, commit=None)

    def _check_fail(self, op):
        if op in self.fail:
            if self.fail[op] == 0:
                raise RuntimeError(f"sample_only injected failure: {op}")
            self.fail[op] -= 1

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.collections])

    def collection_exists(self, name):
        return name in self.collections

    def get_collection(self, name):
        vectors = SimpleNamespace(size=self.size, distance=models.Distance.COSINE)
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)),
                               points_count=len(self.collections[name]))

    def _record(self, point_id, point, with_payload, with_vectors):
        return SimpleNamespace(id=point_id, payload=copy.deepcopy(point["payload"]) if with_payload else None,
                               vector=list(point["vector"]) if with_vectors else None)

    def scroll(self, collection_name, scroll_filter=None, limit=10, offset=None, with_payload=True,
               with_vectors=False, **_):
        assert scroll_filter is None
        ids = sorted(self.collections[collection_name])
        start = ids.index(offset) if offset is not None else 0
        page = ids[start:start + limit]
        following = ids[start + limit] if start + limit < len(ids) else None
        data = self.collections[collection_name]
        return [self._record(p, data[p], with_payload, with_vectors) for p in page], following

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False, **_):
        data = self.collections[collection_name]
        return [self._record(p, data[p], with_payload, with_vectors) for p in ids if p in data]

    def count(self, collection_name, count_filter=None, exact=True):
        return SimpleNamespace(count=len(self.collections[collection_name]))

    def overwrite_payload(self, collection_name, payload, points, wait=True):
        self._check_fail("overwrite_payload")
        self.writes.append(("overwrite_payload", tuple(points)))
        for point_id in points:
            self.collections[collection_name][point_id]["payload"] = copy.deepcopy(payload)

    def delete(self, collection_name, points_selector, wait=True):
        self.writes.append(("delete", tuple(points_selector)))
        for point_id in points_selector:
            self._check_fail("delete")
            self.collections[collection_name].pop(point_id)

    def upsert(self, collection_name, points, wait=True):
        self.writes.append(("upsert", tuple(p.id for p in points)))
        for point in points:
            self.collections[collection_name][str(point.id)] = {"vector": list(point.vector),
                                                                "payload": copy.deepcopy(point.payload)}

    def close(self):
        pass


def sample_chunk(chunk_id, text, document, policy, start=0):
    return {"chunk_id": chunk_id, "point_id": make_point_id(chunk_id), "policy_id": policy, "document_id": document,
            "text": text, "section_title": "sample_only 섹션", "source_url": "https://example.org/sample_only",
            "char_start": start, "char_end": start + len(text), "page_start": None, "page_end": None,
            "chunking_version": "B2_sample_only"}


def sample_meta(category="sample_category"):
    return {"policy_name": "sample_only 정책", "category": category, "region": "전국", "life_stages": ["sample"],
            "target_groups": ["sample"], "eligibility_attributes": {}, "verification_status": "sample_only"}


def sample_vector(value):
    return [math.sin(value * 7 + index) / 32 for index in range(1024)]


class Sample:
    """A 동일, B 메타데이터 변경, C 해시 변경(핵심 payload), D1·D2 본문 변경(삭제 대상), E 신규."""

    def __init__(self):
        old_meta = {"POL_S0": sample_meta(), "POL_S1": sample_meta(), "POL_S2": sample_meta()}
        self.metadata = dict(old_meta, POL_S1=sample_meta(category="sample_category_new"))
        a = sample_chunk("CHK_B2_DOC_S0_C001", "sample_only A", "DOC_S0", "POL_S0")
        b = sample_chunk("CHK_B2_DOC_S1_C001", "sample_only B", "DOC_S1", "POL_S1")
        c = sample_chunk("CHK_B2_DOC_S2_C001", "sample_only C", "DOC_S2", "POL_S2")
        d1 = sample_chunk("CHK_B2_DOC_S2_C002", "sample_only D1", "DOC_S2", "POL_S2", 20)
        d2 = sample_chunk("CHK_B2_DOC_S2_C003", "sample_only D2", "DOC_S2", "POL_S2", 40)
        old_hashes = {"DOC_S0": "0" * 64, "DOC_S1": "1" * 64, "DOC_S2": "2" * 64}
        self.hashes = dict(old_hashes, DOC_S2="3" * 64)
        self.records = {}
        for index, chunk in enumerate((a, b, c, d1, d2)):
            payload = store.build_point_payload(chunk, policy_meta=old_meta[chunk["policy_id"]],
                                                content_hash=old_hashes[chunk["document_id"]], run_info=RUN)
            self.records[chunk["point_id"]] = {"payload": payload, "vector": sample_vector(index)}
        e = sample_chunk("CHK_B2_DOC_S2_C004", "sample_only E", "DOC_S2", "POL_S2", 70)
        self.chunks = [a, b, c, dict(d1, text="sample_only D1 수정"), dict(d2, text="sample_only D2 수정"), e]
        self.ids = {name: chunk["point_id"] for name, chunk in zip("ABC", (a, b, c))}
        self.ids.update(D1=d1["point_id"], D2=d2["point_id"], E=e["point_id"])
        plan = seed.classify(self.records, self.chunks, self.metadata, self.hashes)
        self.pins = {
            "delete": {self.ids[k]: {"chunk_id": self.records[self.ids[k]]["payload"]["chunk_id"],
                                     "text_sha256": update.text_sha(self.records[self.ids[k]]["payload"]["text"]),
                                     "vector_digest": restore.digest([(self.ids[k], self.records[self.ids[k]]["vector"])])}
                       for k in ("D1", "D2")},
            "new": {self.ids["E"]},
            "reuse_digest": restore.vector_digest({self.ids[k]: self.records[self.ids[k]] for k in "ABC"}),
            "total": 6}
        self.ref = update.reference_from_plan(plan, self.records, {"chunks": self.chunks, "content_hashes": self.hashes},
                                              self.pins)

    def client(self, **kwargs):
        return FakeQdrant(self.records, **kwargs)


def fingerprint(client, ref):
    """dry-run과 같은 방법으로 현재 fingerprint를 계산한다."""
    return update.server_fingerprint(client, update.inspect_state(client, ref))


def apply(client, ref):
    return update.apply_updates(client, ref, confirm_write=True, expected_fingerprint=fingerprint(client, ref))


class StateAndPlanTests(unittest.TestCase):
    def setUp(self):
        self.s = Sample()
        self.client = self.s.client()

    def test_restored_snapshot_is_payload_pending_without_writes(self):
        state = update.inspect_state(self.client, self.s.ref)
        self.assertEqual(state["state"], "payload_pending")
        self.assertEqual(state["points"], 5)
        self.assertEqual(set(state["payload_pending"]), {self.s.ids["B"], self.s.ids["C"]})
        self.assertEqual(state["payload_pending_fields"], {"category": 1, "content_hash": 1})
        self.assertEqual(set(state["delete_present"]), {self.s.ids["D1"], self.s.ids["D2"]})
        self.assertEqual(state["full_vector_digest"], restore.vector_digest(self.s.records))
        output = update.run(self.client, self.s.ref)
        self.assertEqual((output["mode"], output["db_writes"], self.client.writes), ("dry-run", 0, []))

    def test_reference_selects_only_pinned_delete_targets(self):
        self.assertEqual(self.s.ref["delete"], {self.s.ids["D1"], self.s.ids["D2"]})
        self.assertEqual(self.s.ref["new"], {self.s.ids["E"]})
        self.assertEqual(set(self.s.ref["reuse_payloads"]), {self.s.ids[k] for k in "ABC"})

    def test_reference_rejects_wrong_pins(self):
        plan = seed.classify(self.s.records, self.s.chunks, self.s.metadata, self.s.hashes)
        release = {"chunks": self.s.chunks, "content_hashes": self.s.hashes}
        broken = copy.deepcopy(self.s.pins)
        broken["delete"][self.s.ids["D1"]]["text_sha256"] = "0" * 64
        cases = {"해시": broken, "삭제·신규": dict(self.s.pins, new=set()),
                 "부분 digest": dict(self.s.pins, reuse_digest="0" * 64), "최종 Point": dict(self.s.pins, total=7)}
        for message, pins in cases.items():
            with self.subTest(message), self.assertRaisesRegex(update.UpdateError, message):
                update.reference_from_plan(plan, self.s.records, release, pins)

    def test_apply_updates_payloads_deletes_targets_and_keeps_vectors(self):
        before = {k: v["vector"] for k, v in self.client.collections[COLLECTION].items()}
        result = apply(self.client, self.s.ref)
        data = self.client.collections[COLLECTION]
        self.assertEqual(result["status"], "UPDATED")
        self.assertEqual(result["payload_updates"], 2)
        self.assertEqual(set(result["deleted"]), {self.s.ids["D1"], self.s.ids["D2"]})
        self.assertEqual(set(data), {self.s.ids[k] for k in "ABC"})
        for point_id in data:
            self.assertEqual(data[point_id]["vector"], before[point_id])
            self.assertEqual(data[point_id]["payload"], self.s.ref["reuse_payloads"][point_id])
        self.assertEqual(restore.vector_digest(data), self.s.pins["reuse_digest"])
        self.assertEqual([op for op, _ in self.client.writes], ["overwrite_payload", "overwrite_payload", "delete"])
        self.assertEqual(set(result["missing_for_index"]), {self.s.ids[k] for k in ("D1", "D2", "E")})
        inspection = update.check_full_index_ready(self.client, self.s.ref)
        self.assertEqual((inspection["state"], inspection["missing"]), ("partial", 3))

    def test_write_requires_confirmation(self):
        with self.assertRaisesRegex(update.UpdateError, "confirm_write"):
            update.apply_updates(self.client, self.s.ref)
        self.assertEqual(self.client.writes, [])

    def test_rerun_after_success_is_a_no_op(self):
        apply(self.client, self.s.ref)
        writes = list(self.client.writes)
        self.assertEqual(apply(self.client, self.s.ref)["status"], "ALREADY_READY")
        self.assertEqual(self.client.writes, writes)

    def test_partial_payload_failure_is_detected_and_resumed(self):
        stale_fingerprint = fingerprint(self.client, self.s.ref)
        self.client.fail = {"overwrite_payload": 1}
        with self.assertRaisesRegex(RuntimeError, "injected"):
            apply(self.client, self.s.ref)
        state = update.inspect_state(self.client, self.s.ref)
        self.assertEqual((state["state"], len(state["payload_pending"])), ("payload_pending", 1))
        self.client.fail = {}
        writes = list(self.client.writes)
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):    # 실패 전 dry-run 값으로는 다시 쓰지 않음
            update.apply_updates(self.client, self.s.ref, True, stale_fingerprint)
        self.assertEqual(self.client.writes, writes)
        self.assertEqual(apply(self.client, self.s.ref)["payload_updates"], 1)
        self.assertEqual(update.inspect_state(self.client, self.s.ref)["state"], "ready_for_index")

    def test_partial_delete_failure_is_detected_and_resumed(self):
        self.client.fail = {"delete": 1}
        with self.assertRaisesRegex(RuntimeError, "injected"):
            apply(self.client, self.s.ref)
        state = update.inspect_state(self.client, self.s.ref)
        self.assertEqual((state["state"], len(state["delete_present"])), ("delete_pending", 1))
        self.client.fail = {}
        result = apply(self.client, self.s.ref)
        self.assertEqual((result["payload_updates"], len(result["deleted"])), (0, 1))
        self.assertEqual(restore.vector_digest(self.client.collections[COLLECTION]), self.s.pins["reuse_digest"])

    def assert_stops(self, expected_state):
        state = update.inspect_state(self.client, self.s.ref)
        self.assertEqual(state["state"], expected_state)
        writes = list(self.client.writes)
        with self.assertRaisesRegex(update.UpdateError, expected_state):
            apply(self.client, self.s.ref)
        self.assertEqual(self.client.writes, writes)

    def test_unexpected_point_stops_before_write(self):
        self.client.collections[COLLECTION][make_point_id("CHK_SAMPLE_ONLY_EXTRA")] = {
            "vector": sample_vector(9), "payload": {}}
        self.assert_stops("unexpected_points")

    def test_vector_digest_mismatch_stops_before_write(self):
        self.client.collections[COLLECTION][self.s.ids["A"]]["vector"][0] += 1e-7
        self.assert_stops("vector_mismatch")

    def test_missing_reused_point_stops(self):
        self.client.collections[COLLECTION].pop(self.s.ids["B"])
        self.assert_stops("missing_reused_points")

    def test_delete_target_hash_mismatch_stops(self):
        self.client.collections[COLLECTION][self.s.ids["D1"]]["payload"]["text"] = "sample_only 다른 본문"
        self.assert_stops("delete_target_mismatch")

    def test_delete_before_payload_update_is_unexpected_order(self):
        self.client.collections[COLLECTION].pop(self.s.ids["D1"])
        self.assert_stops("unexpected_order")

    def test_new_point_before_delete_is_unexpected_mix(self):
        self.client.collections[COLLECTION][self.s.ids["E"]] = {"vector": sample_vector(8), "payload": {}}
        self.assert_stops("unexpected_mix")

    def test_delete_points_rejects_missing_or_unlisted_ids(self):
        apply(self.client, self.s.ref)
        writes = list(self.client.writes)
        with self.assertRaisesRegex(update.UpdateError, "존재하지 않는 Point"):
            update.delete_points(self.client, [self.s.ids["D1"]], self.s.ref["delete"])
        with self.assertRaisesRegex(update.UpdateError, "삭제 허용 대상"):
            update.delete_points(self.client, [self.s.ids["A"]], self.s.ref["delete"])
        with self.assertRaisesRegex(update.UpdateError, "빈 목록"):
            update.delete_points(self.client, [], self.s.ref["delete"])
        self.assertEqual(self.client.writes, writes)

    def test_index_partial_and_complete_states(self):
        apply(self.client, self.s.ref)
        data = self.client.collections[COLLECTION]
        data[self.s.ids["E"]] = {"vector": sample_vector(10), "payload": {}}
        self.assertEqual(update.inspect_state(self.client, self.s.ref)["state"], "index_partial")
        for index, key in enumerate(("D1", "D2")):
            data[self.s.ids[key]] = {"vector": sample_vector(20 + index), "payload": {}}
        state = update.inspect_state(self.client, self.s.ref)
        self.assertEqual((state["state"], state["points"]), ("complete", 6))
        self.assertEqual(state["reuse_vector_digest"], self.s.pins["reuse_digest"])
        with self.assertRaisesRegex(update.UpdateError, "complete"):
            apply(self.client, self.s.ref)


class GuardTests(unittest.TestCase):
    def test_fingerprint_is_required_and_detects_change_after_dry_run(self):
        s = Sample()
        client = s.client()
        reviewed = update.dry_run(client, s.ref)["fingerprint"]
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.apply_updates(client, s.ref, confirm_write=True)
        client.collections[COLLECTION][s.ids["A"]]["payload"]["region"] = "sample_only 다른 값"   # 다른 작업이 변경
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.apply_updates(client, s.ref, True, reviewed)
        other = s.client(version="1.13.0")
        with self.assertRaisesRegex(update.UpdateError, "서버 버전"):
            update.apply_updates(other, s.ref, True, reviewed)
        self.assertEqual((client.writes, other.writes), ([], []))

    def test_isolated_client_ignores_env_url_and_dotenv(self):
        captured = []
        environ = {"QDRANT_URL": "http://10.0.0.5:6333", update.API_KEY_ENV: "sample_only_key"}
        blocked = AssertionError(".env 로드 금지")
        with patch("qdrant_client.QdrantClient", side_effect=lambda **kw: captured.append(kw) or "client"), \
                patch.object(store, "load_project_env", side_effect=blocked), \
                patch.object(store, "get_client", side_effect=blocked):
            self.assertEqual(update.make_isolated_client("http://127.0.0.1:56446", environ), "client")
        self.assertEqual(captured[0]["url"], "http://127.0.0.1:56446")
        self.assertEqual(captured[0]["api_key"], "sample_only_key")
        with patch("qdrant_client.QdrantClient", side_effect=blocked):
            with self.assertRaisesRegex(update.UpdateError, "QDRANT_API_KEY"):
                update.make_isolated_client("http://127.0.0.1:56446", {"QDRANT_URL": "http://127.0.0.1:56446"})
            with self.assertRaises(ValueError):
                update.make_isolated_client("http://127.0.0.1:6333", environ)

    def test_index_uses_the_verified_client_and_limits_embeddings(self):
        s = Sample()
        client = s.client()
        apply(client, s.ref)
        ref = dict(s.ref, documents=[{"document_id": "DOC_SAMPLE_ONLY"}], metadata={})
        seen = {}

        def fake_execute(name, *, client, embedder, documents, policy_metadata, confirm_write, chunks):
            seen.update(client=client, chunks=chunks, documents=documents, name=name)
            vectors = embedder(["sample_only"] * 3)
            for index, key in enumerate(("D1", "D2", "E")):
                client.collections[COLLECTION][s.ids[key]] = {"vector": vectors[index], "payload": {}}
            return {"written": 3, "verification": {"passed": True}, "run_info": {}}

        with patch.object(fi, "execute_full_index", side_effect=fake_execute):
            result = update.index_missing(client, ref, lambda texts: [sample_vector(30)] * len(texts),
                                          True, fingerprint(client, ref))
        self.assertIs(seen["client"], client)
        self.assertIs(seen["chunks"], ref["chunks"])
        self.assertEqual((seen["name"], result["state"], result["embedded_texts"]), (seed.CONFIG, "complete", 3))
        guarded = update.limited_embedder(lambda texts: self.fail("초과 요청이 전달되면 안 됨"), 2)
        with self.assertRaisesRegex(update.UpdateError, "예상 2개"):
            guarded(["a", "b", "c"])

    def test_index_refuses_wrong_state_or_fingerprint_without_writes(self):
        s = Sample()
        client = s.client()
        ref = dict(s.ref, documents=[{"document_id": "DOC_SAMPLE_ONLY"}], metadata={})
        with patch.object(fi, "execute_full_index", side_effect=AssertionError("실행 금지")):
            with self.assertRaisesRegex(update.UpdateError, "payload_pending"):
                update.index_missing(client, ref, lambda texts: [], True, fingerprint(client, ref))
            apply(client, ref)
            writes = list(client.writes)
            with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
                update.index_missing(client, ref, lambda texts: [], True, "0" * 64)
            with self.assertRaisesRegex(update.UpdateError, "confirm_write"):
                update.index_missing(client, ref, lambda texts: [])
        self.assertEqual(client.writes, writes)

    def test_cli_write_modes_require_fingerprint_and_ollama_url(self):
        base = ["--qdrant-url", "http://127.0.0.1:56446"]
        for extra in (["--apply", "--confirm-write"], ["--index", "--confirm-write", "--expect-fingerprint", "x"],
                      ["--apply", "--index", "--confirm-write", "--expect-fingerprint", "x"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                update.main(base + extra)

    def test_server_with_other_collections_is_rejected(self):
        s = Sample()
        for client in (s.client(extra_collections=("exp_b2_qwen3emb06b_v1",)), s.client(size=2560)):
            with self.subTest(size=client.size), self.assertRaises(update.UpdateError):
                update.apply_updates(client, s.ref, confirm_write=True)
            self.assertEqual(client.writes, [])
        empty = FakeQdrant({})
        empty.collections = {}
        with self.assertRaisesRegex(update.UpdateError, "격리 서버"):
            update.require_isolated_server(empty)

    def test_url_must_be_loopback_with_dedicated_port(self):
        self.assertEqual(update.require_isolated_url("http://127.0.0.1:56446"), "http://127.0.0.1:56446")
        for url in ("http://10.0.0.5:56446", "http://127.0.0.1:6333", "http://localhost",
                    "http://user:pw@127.0.0.1:56446"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                update.require_isolated_url(url)

    def test_cli_requires_confirm_and_never_overwrites_report(self):
        with self.assertRaises(SystemExit):
            update.main(["--qdrant-url", "http://127.0.0.1:56446", "--apply"])
        with tempfile.NamedTemporaryFile() as existing, self.assertRaises(SystemExit):
            update.main(["--qdrant-url", "http://127.0.0.1:56446", "--report", existing.name])

    def test_cli_rejects_shared_port_before_connecting(self):
        with patch.object(store, "get_client", side_effect=AssertionError("접속 금지")):
            self.assertEqual(update.main(["--qdrant-url", "http://127.0.0.1:6333"]), 1)


class SnapshotRestoreTests(unittest.TestCase):
    """빈 격리 서버에만 snapshot을 올리는지 (가짜 HTTP·가짜 Qdrant, 실제 업로드 없음)."""

    URL = "http://127.0.0.1:56446"
    ENV = {update.API_KEY_ENV: "sample_only_key"}

    def setUp(self):
        self.s = Sample()
        self.client = self.s.client()
        self.client.collections = {}                       # 새 볼륨 = 컬렉션 0개
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.snapshot = Path(folder.name) / "sample_only.snapshot"
        self.snapshot.write_bytes(b"sample_only snapshot bytes")
        self.sha = restore.file_sha(self.snapshot)
        self.posts = []

    def post(self, status=200, restored=None):
        def fake_post(url, **kwargs):
            self.posts.append({"url": url, **{k: v for k, v in kwargs.items() if k != "files"},
                               "file_name": kwargs["files"]["snapshot"][0]})
            if status == 200:
                self.client.collections[COLLECTION] = copy.deepcopy(
                    self.s.records if restored is None else restored)
            return SimpleNamespace(status_code=status)
        return fake_post

    def restore(self, **kwargs):
        options = dict(confirm_write=True, environ=self.ENV, http_post=self.post(),
                       expected_sha256=self.sha, expected_points=len(self.s.records))
        options.update(kwargs)
        return update.restore_snapshot(self.client, self.URL, self.snapshot, **options)

    def test_restores_only_into_empty_isolated_server(self):
        self.assertEqual(update.dry_run(self.client, self.s.ref)["state"], "empty_server")
        result = self.restore()
        self.assertEqual((result["status"], result["points"]), ("RESTORED", 5))
        self.assertEqual(len(self.posts), 1)
        post = self.posts[0]
        self.assertEqual(post["url"], f"{self.URL}/collections/{COLLECTION}/snapshots/upload")
        self.assertEqual(post["params"], {"wait": "true", "priority": "snapshot", "checksum": self.sha})
        self.assertEqual((post["headers"], post["file_name"]), ({"api-key": "sample_only_key"}, self.snapshot.name))
        self.assertEqual(update.inspect_state(self.client, self.s.ref)["state"], "payload_pending")
        self.assertEqual(restore.file_sha(self.snapshot), self.sha)     # snapshot 파일은 바뀌지 않음

    def test_refuses_when_any_collection_exists(self):
        for existing in ({COLLECTION: {}}, {"exp_b2_qwen3emb06b_v1": {}}):
            self.client.collections = copy.deepcopy(existing)
            with self.subTest(existing=list(existing)), self.assertRaisesRegex(update.UpdateError, "빈 서버"):
                self.restore()
        self.assertEqual(self.posts, [])

    def test_refuses_bad_snapshot_key_version_port_or_confirmation(self):
        cases = {"SHA-256": dict(expected_sha256="0" * 64), "QDRANT_API_KEY": dict(environ={}),
                 "confirm_write": dict(confirm_write=False)}
        for message, options in cases.items():
            with self.subTest(message), self.assertRaisesRegex(update.UpdateError, message):
                self.restore(**options)
        self.client.version = "1.13.0"
        with self.assertRaisesRegex(update.UpdateError, "서버 버전"):
            self.restore()
        with self.assertRaises(ValueError):
            update.restore_snapshot(self.client, "http://127.0.0.1:6333", self.snapshot, True, self.ENV, self.post())
        self.assertEqual(self.posts, [])

    def test_failed_upload_or_wrong_point_count_is_reported(self):
        with self.assertRaisesRegex(update.UpdateError, "HTTP 500"):
            self.restore(http_post=self.post(status=500))
        partial = dict(list(self.s.records.items())[:2])
        with self.assertRaisesRegex(update.UpdateError, "Point 2"):
            self.restore(http_post=self.post(restored=partial))

    def test_port_is_pinned_to_compose_port(self):
        for port in (56447, 56336, 6333):
            with self.subTest(port=port), self.assertRaises(ValueError):
                update.require_isolated_url(f"http://127.0.0.1:{port}")


class ComposeStaticTests(unittest.TestCase):
    """docs/development/issue91_r2_qdrant.compose.yaml 정적 검사 (Docker 실행 없음, PyYAML 없이 줄 단위)."""

    PATH = Path(update.ROOT) / "docs" / "development" / "issue91_r2_qdrant.compose.yaml"
    DIGEST = "sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636"

    def setUp(self):
        self.lines = [line.split("#", 1)[0].rstrip() for line in self.PATH.read_text(encoding="utf-8").splitlines()]
        self.body = [line for line in self.lines if line.strip()]

    def test_project_image_port_and_limits(self):
        self.assertIn("name: issue91-r2-qdrant-20261119", self.body)
        self.assertIn(f"    image: qdrant/qdrant:v{update.EXPECTED_QDRANT_VERSION}@{self.DIGEST}", self.body)
        ports = [line.strip() for line in self.body if line.strip().startswith("- \"") and ":6333" in line]
        self.assertEqual(ports, [f"- \"127.0.0.1:{update.ISOLATED_QDRANT_PORT}:6333\""])
        self.assertIn("    mem_limit: 1g", self.body)
        self.assertIn("    cpus: 2", self.body)
        services = self.body[self.body.index("services:") + 1:self.body.index("volumes:")]
        self.assertEqual([line for line in services if line.startswith("  ") and not line.startswith("   ")],
                         ["  qdrant:"])                                            # 서비스는 qdrant 하나

    def test_digest_matches_documented_1_12_4_image(self):
        restore_compose = (self.PATH.parent / "issue43_restore.compose.yaml").read_text(encoding="utf-8")
        record = (self.PATH.parent / "ISSUE41_EMBEDDING_AB_EVAL.md").read_text(encoding="utf-8")
        self.assertIn(self.DIGEST, restore_compose)
        self.assertRegex(record, r"Qdrant 1\.12\.4, digest `" + self.DIGEST + "`")

    def test_only_new_project_volume_and_no_reused_resources(self):
        text = "\n".join(self.body)
        for forbidden in ("external", "container_name", "network_mode", "networks:", "privileged", "6334"):
            self.assertNotIn(forbidden, text)
        mounts = [line.strip() for line in self.body if line.strip().startswith("- ") and ":/" in line]
        self.assertEqual(mounts, ["- qdrant_storage:/qdrant/storage"])         # 바인드 마운트·다른 볼륨 없음
        self.assertEqual(self.body[-2:], ["volumes:", "  qdrant_storage:"])

    def test_api_key_comes_only_from_session_environment(self):
        key_lines = [line for line in self.body if "QDRANT__SERVICE__API_KEY" in line]
        self.assertEqual(len(key_lines), 1)
        self.assertRegex(key_lines[0], r"\$\{" + update.API_KEY_ENV + r":\?[^}]+\}$")   # 없으면 기동 거부, 기본값 없음
        self.assertNotIn(":-", key_lines[0])
        self.assertFalse((self.PATH.parent / ".env").exists())   # compose가 자동으로 읽을 .env가 같은 폴더에 없음


@unittest.skipUnless(seed.DEFAULT_DUMP.is_file(), "NOT_RUN: 기존 B2 덤프(venv/issue31_20261109)가 이 PC에 없음")
class RealDumpFlowTests(unittest.TestCase):
    """실제 덤프를 비트 그대로 담은 FakeQdrant(= snapshot 복원 가정)로 전체 흐름과 full_index 4개 적재를 확인한다."""

    @classmethod
    def setUpClass(cls):
        if hd.dataset_version() != seed.DATASET_VERSION:
            raise unittest.SkipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from r2_release_gate import SKIP_REASON, r2_release_is_current
        if not r2_release_is_current():            # #105: r2 기대값은 r2 릴리스에만 적용
            raise unittest.SkipTest(SKIP_REASON)
        cls.ref = update.build_reference()
        cls.dump = seed.load_dump(seed.DEFAULT_DUMP)

    def test_snapshot_flow_reuses_1135_and_full_index_embeds_only_4(self):
        client = FakeQdrant(self.dump)
        with patch.object(store, "get_client", side_effect=AssertionError("실제 DB 접속 금지")):
            dry = update.run(client, self.ref)
        self.assertEqual((dry["state"], dry["points"], dry["db_writes"], client.writes),
                         ("payload_pending", 1138, 0, []))
        self.assertTrue(dry["full_vector_digest_matches_r1"])
        self.assertEqual(dry["payload_pending"], 135)
        self.assertEqual(dry["payload_pending_fields"],
                         {"category": 33, "char_end": 1, "char_start": 1, "content_hash": 2, "life_stages": 121})
        self.assertEqual({d["point_id"] for d in dry["delete_present"]}, set(update.DELETE_TARGETS))

        result = update.apply_updates(client, self.ref, True, dry["fingerprint"])
        self.assertEqual((result["payload_updates"], set(result["deleted"])), (135, set(update.DELETE_TARGETS)))
        self.assertEqual(result["reuse_vector_digest"], update.REUSE_SUBSET_DIGEST)
        self.assertEqual(update.check_full_index_ready(client, self.ref)["missing"], 4)

        calls = []

        def embedder(texts):
            calls.append(len(texts))
            return [[math.cos(i + j) / 32 for j in range(1024)] for i in range(len(texts))]   # sample_only

        ready = update.dry_run(client, self.ref)
        self.assertEqual((ready["state"], ready["full_index_inspection"]["missing"]), ("ready_for_index", 4))
        indexed = update.index_missing(client, self.ref, embedder, True, ready["fingerprint"])
        self.assertEqual((indexed["written"], indexed["embedded_texts"], calls), (4, 4, [4]))
        self.assertTrue(indexed["verification"]["passed"], indexed["verification"])
        upserted = {point_id for op, ids in client.writes if op == "upsert" for point_id in ids}
        self.assertEqual(upserted, set(update.DELETE_TARGETS) | update.NEW_POINT_IDS)
        final = update.inspect_state(client, self.ref)
        self.assertEqual((final["state"], final["points"]), ("complete", 1139))
        self.assertEqual(final["reuse_vector_digest"], update.REUSE_SUBSET_DIGEST)
        data = client.collections[COLLECTION]
        self.assertTrue(all(data[p]["vector"] == self.dump[p]["vector"] for p in self.ref["reuse_payloads"]))
        self.assertEqual({data[p]["payload"]["index_run_id"] for p in self.ref["reuse_payloads"]},
                         {seed.DUMP_RUN["index_run_id"]})
        writes = list(client.writes)
        with self.assertRaisesRegex(update.UpdateError, "complete"):
            update.apply_updates(client, self.ref, confirm_write=True)   # 완료 후 재실행은 쓰지 않고 거부
        self.assertEqual(client.writes, writes)

    def test_cli_dry_run_targets_only_the_argument_url(self):
        captured = []

        def fake_client(**kwargs):
            captured.append(kwargs)
            return FakeQdrant(self.dump)

        environ = {"QDRANT_URL": "http://10.0.0.5:6333", update.API_KEY_ENV: "sample_only_key",
                   "OLLAMA_BASE_URL": "http://10.0.0.6:11434"}
        blocked = AssertionError("다른 경로 접속·.env 로드 금지")
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", environ), \
                patch("qdrant_client.QdrantClient", side_effect=fake_client), \
                patch.object(store, "get_client", side_effect=blocked), \
                patch.object(store, "load_project_env", side_effect=blocked):
            report = Path(folder) / "dry.json"
            code = update.main(["--qdrant-url", "http://127.0.0.1:56446", "--report", str(report)])
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, output)
        self.assertEqual([kw["url"] for kw in captured], ["http://127.0.0.1:56446"])
        self.assertEqual((output["mode"], output["state"], output["db_writes"]), ("dry-run", "payload_pending", 0))
        self.assertNotIn("sample_only_key", json.dumps(output))
        self.assertRegex(output["fingerprint"], r"^[0-9a-f]{64}$")

    def test_dump_and_restore_record_are_not_modified(self):
        self.assertEqual(restore.file_sha(seed.DEFAULT_DUMP), seed.DUMP_SHA256)


if __name__ == "__main__":
    unittest.main()
