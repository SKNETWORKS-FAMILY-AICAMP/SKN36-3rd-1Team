"""설정된 릴리스 파일의 읽기 전용 목록. DB·임베딩·LLM 호출과 개인 자격 판정은 없다."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from backend.api.schemas import PolicyListResponse, PolicySummary
from backend.eligibility.checker import KST, _reception_status, application_schedule
from backend.rag.context_builder import valid_source_url
from backend.rag.source_sections import application_details

FILES = ("neo4j/nodes_policy.jsonl", "neo4j/nodes_region.jsonl", "neo4j/nodes_category.jsonl",
         "neo4j/nodes_document.jsonl", "neo4j/relationships.jsonl", "qdrant/payload_chunks.jsonl")
# React policySearchKey(#125)와 같은 비교 규칙. 검색 별칭·정책 원문은 추가하지 않는다.
SEARCH_HYPHENS = "-‐‑–—－"


def policy_search_key(text: str) -> str:
    return "".join(char for char in text.casefold() if not char.isspace() and char not in SEARCH_HYPHENS)


class PolicyCatalog:
    def __init__(self, package_dir: Path | None = None):
        from database.handoff_data import HANDOFF_DIR, file_signature
        self.package_dir = Path(package_dir or HANDOFF_DIR)
        self.file_signature = file_signature
        self._lock = Lock()
        self._signature = None
        self._data = None

    def _current_signature(self):
        return tuple(self.file_signature(self.package_dir / name) for name in ("manifest.json", *FILES))

    def _snapshot(self):
        from database.handoff_data import manifest_checksum_match

        # 여러 요청이 동시에 와도 같은 파일을 중복 파싱하지 않는다. 실패한 갱신은 캐시하지 않는다.
        with self._lock:
            signature = self._current_signature()
            if signature == self._signature:
                return self._data
            manifest_bytes = (self.package_dir / "manifest.json").read_bytes()
            manifest = json.loads(manifest_bytes)
            tables = {}
            for name in FILES:
                raw = (self.package_dir / name).read_bytes()
                expected = manifest["files"][name]["sha256"]
                # handoff_data.verify_manifest_checksums와 동일한 OS 줄바꿈 동등성 기준.
                if manifest_checksum_match(raw, expected) is None:
                    raise ValueError("목록 입력 파일의 manifest 해시 불일치")
                tables[name] = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
            if self._current_signature() != signature:
                raise ValueError("목록을 읽는 동안 데이터가 변경됨")

            def index(name, key):
                rows = tables[name]
                result = {row[key]: row for row in rows}
                if len(result) != len(rows) or any(not isinstance(value, str) or not value for value in result):
                    raise ValueError("목록 식별자 누락 또는 중복")
                return result

            policies = index(FILES[0], "policy_id")
            regions = index(FILES[1], "region_id")
            categories = index(FILES[2], "category_id")
            documents = index(FILES[3], "document_id")
            if len(policies) != manifest["summary"]["total_policies"]:
                raise ValueError("목록 정책 건수 불일치")
            relations = {pid: {"regions": [], "categories": []} for pid in policies}
            for edge in tables[FILES[4]]:
                pid, target = edge["source_id"], edge["target_id"]
                if pid not in relations:
                    continue
                mapping = {"AVAILABLE_IN": ("regions", regions), "HAS_CATEGORY": ("categories", categories)}.get(edge["relation"])
                if mapping:
                    field, nodes = mapping
                    if target not in nodes:
                        raise ValueError("목록 분류 관계의 참조 누락")
                    value = nodes[target]["name"]
                    if value not in relations[pid][field]:
                        relations[pid][field].append(value)
            contexts = {pid: [] for pid in policies}
            seen_chunks = set()
            for chunk in tables[FILES[5]]:
                pid, did, cid = chunk["policy_id"], chunk["document_id"], chunk["chunk_id"]
                if cid in seen_chunks or pid not in policies or did not in documents or documents[did]["policy_id"] != pid:
                    raise ValueError("목록 청크의 정책·문서 연결 불일치")
                seen_chunks.add(cid)
                contexts[pid].append(chunk)
            details = {pid: application_details(rows) for pid, rows in contexts.items()}
            self._data = (manifest["manifest_version"], hashlib.sha256(manifest_bytes).hexdigest(), policies, relations, details)
            self._signature = signature
            return self._data

    def list(self, *, q="", region="", category="", status="all", sort="deadline", offset=0, limit=500):
        today = datetime.now(KST).date()
        version, digest, policies, relations, details = self._snapshot()
        items = []
        query = policy_search_key(q)
        for pid, node in policies.items():
            relation = relations[pid]
            name, agency = node["policy_name"], node.get("agency") or node.get("organization") or ""
            # 하이픈만 입력해 검색 키가 비면 전체 목록 대신 결과 없음으로 처리한다.
            if q.strip() and (not query or query not in policy_search_key(name + agency)):
                continue
            if region and region not in relation["regions"]:
                continue
            if category and category not in relation["categories"]:
                continue
            policy = {"graph": {"policy": node}}
            reception = _reception_status(policy, evaluated_on=today)
            schedule = application_schedule(policy, reception, evaluated_on=today)
            remaining = schedule["days_to_end"] if schedule else None
            ongoing = bool(schedule and schedule["is_ongoing"])
            if status == "soon" and not (reception == "open" and not ongoing and remaining is not None and 0 <= remaining <= 7):
                continue
            if status == "ongoing" and not (reception == "open" and ongoing):
                continue
            if status not in {"all", "soon", "ongoing"} and reception != status:
                continue
            url, apply = node.get("source_url"), node.get("official_apply_url")
            items.append(PolicySummary(policy_id=pid, policy_name=name, agency=agency, **relation,
                source_url=url if valid_source_url(url) else "", official_apply_url=apply if valid_source_url(apply) else None,
                reception_status=reception, application_schedule=schedule,
                **details[pid]))
        def order(item):
            schedule = item.application_schedule
            remaining = schedule.days_to_end if schedule else None
            finite_open = item.reception_status == "open" and schedule and not schedule.is_ongoing and remaining is not None
            rank = 0 if finite_open else {"before": 1, "open": 2, "unknown": 3, "closed": 4}[item.reception_status]
            return (rank, remaining if finite_open else 0, item.policy_name, item.policy_id) if sort == "deadline" else (item.policy_name, item.policy_id)
        items.sort(key=order)
        return PolicyListResponse(items=items[offset:offset + limit], total=len(items), offset=offset, limit=limit,
            dataset_version=version, manifest_sha256=digest, evaluated_on=today.isoformat(),
            regions=sorted({name for relation in relations.values() for name in relation["regions"]}),
            categories=sorted({name for relation in relations.values() for name in relation["categories"]}))
