"""Issue #105 r3 테스트 공용 fixture (sample_only 가상 값만, DB·Docker·Ollama 접속 없음).

- R3Qdrant: 여러 컬렉션을 담는 FakeQdrant (snapshot 복원처럼 벡터를 비트 그대로 보관, 정규화 없음)
- Sample: B2·B3 각각 A 동일, B 메타데이터 변경, C 문서 해시 변경(핵심 payload), D 본문 변경(삭제),
  R 정책명 변경(삭제), E 신규. 원본 Point는 실행 ID 2개(RUN_OLD·RUN_OTHER)를 섞어 실행 정보 보존을 확인한다.
- pins_for(): 계획에서 만든 "확정된" R3_PINS 모양 (테스트 안에서만 쓰는 sample_only 값)
- build_container()·good_build_docker(): issue105-r3-build 프로젝트 Docker 소유 확인용 FakeDocker (docker ps·inspect만 응답)
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_restore_r2_isolated import FakeDocker  # noqa: E402
from test_update_b2_from_snapshot import FakeQdrant  # noqa: E402

from database import full_index as fi  # noqa: E402
from database.chunking import make_point_id  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402

RUN_OLD = {"embedding_model": profiles.EMBEDDING_MODEL, "embedding_dim": 1024,
           "embedding_input_rule": fi.EMBEDDING_INPUT_RULE, "index_run_id": "RUN_SAMPLE_ONLY_OLD"}
RUN_OTHER = dict(RUN_OLD, index_run_id="RUN_SAMPLE_ONLY_OTHER")
MANIFEST = "a" * 64
OLD_HASHES = {"DOC_S0": "0" * 64, "DOC_S1": "1" * 64, "DOC_S2": "2" * 64, "DOC_S3": "4" * 64}
NEW_HASHES = dict(OLD_HASHES, DOC_S2="3" * 64)
BUILD_PROJECT = profiles.R3_ENVIRONMENTS["build"]["project"]
BUILD_SERVICES = profiles.services_for("build")


def build_container(cid, service, *, project=BUILD_PROJECT, running=True, host_ip="127.0.0.1", image=None,
                    volumes=None, mount_type="volume", port=None, started="2026-11-22T00:00:00Z"):
    spec = BUILD_SERVICES[service]
    return {"Id": cid, "Image": image or spec["image"],
            "Config": {"Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service}},
            "State": {"Running": running, "StartedAt": started},
            "NetworkSettings": {"Ports": {spec["container_port"]: [{"HostIp": host_ip,
                                                                     "HostPort": str(port or spec["port"])}]}},
            "Mounts": [{"Type": mount_type, "Name": name} for name in (volumes or sorted(spec["volumes"]))]}


def good_build_docker():
    return FakeDocker([build_container("b" * 64, "qdrant"), build_container("c" * 64, "neo4j")])


class R3Qdrant(FakeQdrant):
    """컬렉션 이름 → {point_id: {payload, vector}}. get_aliases·create_snapshot 지원."""

    def __init__(self, collections=None, size=1024, version=profiles.QDRANT_VERSION):
        super().__init__({}, size=size, version=version)
        self.collections = {name: copy.deepcopy(points) for name, points in (collections or {}).items()}
        self.aliases, self.snapshots = [], []

    def get_aliases(self):
        return SimpleNamespace(aliases=[SimpleNamespace(alias_name=a, collection_name="x") for a in self.aliases])

    def set_payload(self, collection_name, payload, points, wait=True):
        self.writes.append(("set_payload", tuple(points)))
        for point_id in points:
            self.collections[collection_name][point_id]["payload"].update(copy.deepcopy(payload))

    def create_snapshot(self, collection_name, wait=True):
        self.writes.append(("create_snapshot", collection_name))
        self.snapshots.append(collection_name)
        return SimpleNamespace(name=f"{collection_name}-sample_only.snapshot", checksum=None, size=None)


def sample_vector(value):
    return [math.sin(value * 7 + index) / 32 for index in range(1024)]


def sample_chunk(prefix, name, text, document, policy, start=0):
    chunk_id = f"{prefix}{document}_{name}"
    return {"chunk_id": chunk_id, "point_id": make_point_id(chunk_id), "policy_id": policy, "document_id": document,
            "text": text, "section_title": "sample_only 섹션", "source_url": "https://example.org/sample_only",
            "char_start": start, "char_end": start + len(text), "page_start": None, "page_end": None,
            "chunking_version": f"{prefix[4:6]}_sample_only"}


def sample_meta(name="sample_only 정책", category="sample_category"):
    return {"policy_name": name, "category": category, "region": "전국", "life_stages": ["sample"],
            "target_groups": ["sample"], "eligibility_attributes": {}, "verification_status": "sample_only"}


class Sample:
    def __init__(self):
        self.old_meta = {"POL_S0": sample_meta(), "POL_S1": sample_meta(), "POL_S2": sample_meta(),
                         "POL_S3": sample_meta("sample_only 옛 이름")}
        self.metadata = dict(self.old_meta, POL_S1=sample_meta(category="sample_category_new"),
                             POL_S3=sample_meta("sample_only 새 이름(옛 이름)"))
        self.documents = [{"document_id": d, "policy_id": f"POL_S{d[-1]}", "content_hash": NEW_HASHES[d]}
                          for d in sorted(NEW_HASHES)]
        self.records, self.chunks, self.ids = {}, {}, {}
        for offset, key in enumerate(profiles.COLLECTION_KEYS):
            prefix = profiles.R3_COLLECTIONS[key]["chunk_prefix"]
            a = sample_chunk(prefix, "C001", f"sample_only {key} A", "DOC_S0", "POL_S0")
            b = sample_chunk(prefix, "C001", f"sample_only {key} B", "DOC_S1", "POL_S1")
            c = sample_chunk(prefix, "C001", f"sample_only {key} C", "DOC_S2", "POL_S2")
            d = sample_chunk(prefix, "C002", f"sample_only {key} D", "DOC_S2", "POL_S2", 30)
            r = sample_chunk(prefix, "C001", f"sample_only {key} R", "DOC_S3", "POL_S3")
            e = sample_chunk(prefix, "C003", f"sample_only {key} E", "DOC_S2", "POL_S2", 60)
            records = {}
            for index, (chunk, run) in enumerate(((a, RUN_OLD), (b, RUN_OTHER), (c, RUN_OLD), (d, RUN_OLD),
                                                  (r, RUN_OLD))):
                payload = store.build_point_payload(chunk, policy_meta=self.old_meta[chunk["policy_id"]],
                                                    content_hash=OLD_HASHES[chunk["document_id"]], run_info=run)
                records[chunk["point_id"]] = {"payload": payload, "vector": sample_vector(offset * 10 + index)}
            self.records[key] = records
            self.chunks[key] = [a, b, c, dict(d, text=f"sample_only {key} D 수정"), r, e]
            self.ids[key] = {n: ch["point_id"] for n, ch in zip("ABCDRE", (a, b, c, d, r, e))}
        self.sources = {key: {"label": f"sample_only {key}", "points": len(self.records[key]),
                              "vector_digest": restore.vector_digest(self.records[key]),
                              "snapshot_sha256": "5" * 64, "default_path": "sample_only"}
                        for key in profiles.COLLECTION_KEYS}
        self.release = {
            "manifest_lf_sha256": MANIFEST, "documents": self.documents, "metadata": self.metadata,
            "content_hashes": dict(NEW_HASHES),
            "collections": {key: {"spec": profiles.collection_spec(key), "chunks": self.chunks[key],
                                  "chunk_fingerprint": fi.chunk_fingerprint(self.chunks[key])}
                            for key in profiles.COLLECTION_KEYS},
            "counts": {"policies": 4, "documents": 4, "release_chunks": 6, "neo4j_nodes": 10,
                       "neo4j_relationships": 12, "requires_condition": 1}}

    def plan(self, key, basis=profiles.PLAN_BASIS_SERVER):
        """기본은 build 서버에 복원한 snapshot을 scan한 계획과 같은 기준 (최종 고정·쓰기용)."""
        return planner.build_plan(key, self.records[key], self.release, self.sources, basis=basis)

    def collection(self, key):
        return profiles.R3_COLLECTIONS[key]["collection"]

    def client(self, keys=profiles.COLLECTION_KEYS, **kwargs):
        return R3Qdrant({self.collection(k): self.records[k] for k in keys}, **kwargs)

    def pins_for(self, plans, final_digests=None):
        pins = planner.proposed_pins(self.release, plans, final_digests)
        pins.pop("note")
        pins.pop("plan_basis")
        for key in profiles.COLLECTION_KEYS:
            if pins["collections"][key]["vector_digest"] is None:
                pins["collections"][key]["vector_digest"] = "f" * 64
        return pins
