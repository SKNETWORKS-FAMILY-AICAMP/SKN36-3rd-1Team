"""후보 패키지 사전 검증과 빈 Neo4j 검증 DB의 트랜잭션 적재 검사.

기본 실행은 파일 검사만 한다. --apply를 주면 실제 DB에서 두 번 MERGE한 뒤
기본적으로 롤백한다. --commit을 함께 준 경우에만 검증 데이터를 남긴다.
이미 데이터가 있는 DB에서는 쓰기를 거절하며, 삭제 쿼리는 사용하지 않는다.
기존 requirements.txt의 requests만 사용한다. 임베딩·운영 적재 도구는 아니다.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import uuid

import requests


# Cypher에 삽입하는 이름은 아래 고정 목록에서만 고른다. 데이터 값은 $rows로 전달한다.
NODE_CONFIG = {
    "Policy": ("policy", "policy_id"),
    "Document": ("document", "document_id"),
    "Organization": ("organization", "agency_id"),
    "Region": ("region", "region_id"),
    "Category": ("category", "category_id"),
    "LifeStage": ("lifestage", "stage_id"),
    "TargetGroup": ("target_group", "group_id"),
    "ConditionGroup": ("condition_group", "group_id"),
    "EligibilityCondition": ("eligibility_condition", "condition_id"),
}
RELATION_LABELS = {
    "ADMINISTERED_BY": ("Policy", "Organization"),
    "HAS_CATEGORY": ("Policy", "Category"),
    "AVAILABLE_IN": ("Policy", "Region"),
    "TARGETS": ("Policy", "TargetGroup"),
    "APPLIES_TO_LIFESTAGE": ("Policy", "LifeStage"),
    "SUPPORTED_BY": ("Policy", "Document"),
    "REQUIRES_CONDITION": ("Policy", "EligibilityCondition"),
    "MODIFIES_CONDITION_OF": ("Policy", "Policy"),
    "HAS_ROOT_CONDITION_GROUP": ("Policy", "ConditionGroup"),
    "CONTAINS_GROUP": ("ConditionGroup", "ConditionGroup"),
    "CONTAINS_CONDITION": ("ConditionGroup", "EligibilityCondition"),
}
NULLABLE_FIELDS = {
    "Policy": {"application_start", "application_end", "lastMdfcnDt", "review_flags", "official_apply_url"},
    "Document": {"published_at", "effective_at", "doc_type", "law_article", "effective_date", "version", "extracted_text_path", "official_id"},
}
REQUIRED_GRAPH_FIELDS = {
    "Policy": {"policy_id", "official_id", "policy_name", "source_type", "source_url", "category", "agency", "organization", "region", "life_stages", "eligibility", "benefit", "application_period", "application_method", "verification_status", "collected_at"},
    "Document": {"document_id", "policy_id", "title", "source_type", "source_url", "local_raw_path", "content_hash", "verification_status"},
}
KST = timezone(timedelta(hours=9))


def read_jsonl(path: Path) -> list[dict]:
    """빈 줄을 제외하고 한 줄을 JSON 한 건으로 읽는다."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_property(value: object) -> None:
    """Neo4j 속성에 딕셔너리나 중첩 리스트를 직접 저장하지 않는다."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        types = {type(item) for item in value}
        if len(types) <= 1 and all(isinstance(item, (str, bool, int, float)) for item in value):
            for item in value:
                validate_property(item)
            return
    raise ValueError("Neo4j 속성으로 저장할 수 없는 값입니다")


def validate_package(package: Path) -> tuple[dict, dict, list[dict], dict]:
    """모든 파일과 참조를 검사한 뒤에만 적재에 사용할 데이터를 반환한다."""
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        path = (package / name).resolve()
        if not path.is_relative_to(package.resolve()):
            raise ValueError("매니페스트 경로가 패키지 밖을 가리킵니다")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != meta["sha256"] or len(content) != meta["size_bytes"]:
            raise ValueError(f"매니페스트 불일치: {name}")
        if name.endswith(".jsonl") and len(read_jsonl(path)) != meta["record_count"]:
            raise ValueError(f"매니페스트 레코드 수 불일치: {name}")

    nodes = {}
    labels_by_id = {}
    nulls = Counter()
    for label, (filename, key) in NODE_CONFIG.items():
        rows = read_jsonl(package / "neo4j" / f"nodes_{filename}.jsonl")
        nodes[label] = rows
        for row in rows:
            if any(field not in row or row[field] is None for field in REQUIRED_GRAPH_FIELDS.get(label, set())):
                raise ValueError(f"Graph용 필수 속성 누락: {label}")
            node_id = row.get(key)
            if not isinstance(node_id, str) or not node_id or node_id in labels_by_id:
                raise ValueError(f"누락되거나 중복된 기본 키: {label}")
            labels_by_id[node_id] = label
            for field, value in row.items():
                if value is None:
                    if field not in NULLABLE_FIELDS.get(label, set()):
                        raise ValueError(f"필수 속성 null: {label}.{field}")
                    nulls[f"{label}.{field}"] += 1
                validate_property(value)

    edges = read_jsonl(package / "neo4j/relationships.jsonl")
    edge_keys = set()
    for edge in edges:
        relation = edge["relation"]
        actual = (labels_by_id.get(edge["source_id"]), labels_by_id.get(edge["target_id"]))
        if relation not in RELATION_LABELS or actual != RELATION_LABELS[relation]:
            raise ValueError(f"관계 끝점의 노드 종류 불일치: {relation}")
        identity = (edge["source_id"], relation, edge["target_id"])
        if identity in edge_keys:
            raise ValueError(f"중복 관계: {relation}")
        edge_keys.add(identity)
        for value in edge.values():
            validate_property(value)

    documents = {row["document_id"]: row for row in nodes["Document"]}
    policies = {row["policy_id"] for row in nodes["Policy"]}
    supported = {(e["source_id"], e["target_id"]) for e in edges if e["relation"] == "SUPPORTED_BY"}
    for document in documents.values():
        if document["policy_id"] not in policies or (document["policy_id"], document["document_id"]) not in supported:
            raise ValueError("문서와 정책의 연결 불일치")
        if "text" in document:
            actual_hash = hashlib.sha256(document["text"].encode("utf-8")).hexdigest()
            if actual_hash != document["content_hash"]:
                raise ValueError("후보 문서 본문 해시 불일치")
    for edge in edges:
        if edge.get("evidence_document_id") and edge["evidence_document_id"] not in documents:
            raise ValueError("관계의 근거 문서 누락")

    chunks = read_jsonl(package / "qdrant/payload_chunks.jsonl")
    point_ids = set()
    chunk_ids = set()
    for chunk in chunks:
        document = documents.get(chunk["document_id"])
        if document is None or chunk["policy_id"] != document["policy_id"] or chunk["policy_id"] not in policies:
            raise ValueError("Graph와 Vector의 정책·문서 연결 불일치")
        if chunk["source_url"] != document["source_url"]:
            raise ValueError("Graph와 Vector의 출처 주소 불일치")
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk["chunk_id"]))
        if chunk["point_id"] != expected or expected in point_ids or chunk["chunk_id"] in chunk_ids:
            raise ValueError("잘못되거나 중복된 Qdrant ID")
        point_ids.add(expected)
        chunk_ids.add(chunk["chunk_id"])

    counts = {label: len(rows) for label, rows in nodes.items()}
    actual_summary = {
        "total_policies": counts["Policy"], "total_documents": counts["Document"],
        "total_chunks": len(chunks), "total_graph_nodes": sum(counts.values()),
        "total_graph_edges": len(edges),
    }
    for key, value in actual_summary.items():
        if manifest["summary"][key] != value:
            raise ValueError(f"매니페스트 요약 건수 불일치: {key}")
    report = {
        "status": "PASS", "counts": actual_summary, "nodes_by_label": counts,
        "relations_by_type": dict(Counter(edge["relation"] for edge in edges)),
        "nullable_properties": dict(nulls), "manifest_files_checked": len(manifest["files"]),
        "document_text_hashes_checked": sum("text" in row for row in documents.values()),
    }
    return nodes, report, edges, {chunk["point_id"]: chunk for chunk in chunks}


def relationship_query(relation: str) -> str:
    """라벨과 기본 키를 함께 지정해 다른 종류의 노드가 매칭되는 일을 막는다."""
    source_label, target_label = RELATION_LABELS[relation]
    source_key = NODE_CONFIG[source_label][1]
    target_key = NODE_CONFIG[target_label][1]
    return (
        f"UNWIND $rows AS row MATCH (src:{source_label} {{{source_key}: row.source_id}}) "
        f"MATCH (tgt:{target_label} {{{target_key}: row.target_id}}) "
        f"MERGE (src)-[r:{relation}]->(tgt) SET r += row.properties"
    )


class Neo4jHttp:
    """명시적으로 설정한 HTTP 주소와 자격증명으로만 연결한다."""

    def __init__(self, url: str, database: str, user: str, password: str):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Neo4j HTTP 주소 형식이 잘못되었습니다")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Neo4j HTTP 주소에는 서버 주소만 지정하세요")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", database) or not user or not password:
            raise ValueError("DB 이름·사용자명·비밀번호 설정이 필요합니다")
        self.base = f"{url.rstrip('/')}/db/{database}/tx"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = (user, password)

    def request(self, method: str, suffix: str = "", statements: list[dict] | None = None) -> dict:
        kwargs = {"json": {"statements": statements}} if statements is not None else {}
        try:
            response = self.session.request(method, self.base + suffix, timeout=30, **kwargs)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            # 예외 전문에는 접속 정보나 서버 응답이 섞일 수 있어 출력하지 않는다.
            raise RuntimeError(f"Neo4j HTTP 요청 실패 ({type(error).__name__})") from None
        if result.get("errors"):
            codes = [item.get("code", "unknown") for item in result["errors"]]
            raise RuntimeError(f"Neo4j 쿼리 실패: {codes}")
        return result

    def query(self, suffix: str, statement: str, parameters: dict | None = None) -> list[list]:
        result = self.request("POST", suffix, [{"statement": statement, "parameters": parameters or {}}])
        return [item["row"] for item in result["results"][0]["data"]]


def node_properties(row: dict) -> dict:
    """Neo4j는 null 속성을 저장하지 않는다. 조회 결과도 같은 기준으로 비교한다."""
    return {key: value for key, value in row.items() if value is not None}


def load_rows(client: Neo4jHttp, transaction: str, nodes: dict, edges: list[dict]) -> None:
    """모든 노드와 관계를 같은 트랜잭션에 넣는다. 실패하면 전체를 되돌릴 수 있다."""
    for label, (_, key) in NODE_CONFIG.items():
        query = f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.{key}}}) SET n += row"
        client.query(transaction, query, {"rows": nodes[label]})
    groups = defaultdict(list)
    for edge in edges:
        groups[edge["relation"]].append({
            "source_id": edge["source_id"], "target_id": edge["target_id"],
            "properties": {key: value for key, value in edge.items() if key not in {"source_id", "target_id", "relation"}},
        })
    for relation, rows in groups.items():
        client.query(transaction, relationship_query(relation), {"rows": rows})


def verify_database(client: Neo4jHttp, transaction: str, nodes: dict, edges: list[dict]) -> None:
    """건수뿐 아니라 기본 키·관계 끝점·모든 저장 속성을 원본과 대조한다."""
    for label, (_, key) in NODE_CONFIG.items():
        actual_rows = client.query(transaction, f"MATCH (n:{label}) RETURN n.{key}, properties(n)")
        actual = {row[0]: row[1] for row in actual_rows}
        expected = {row[key]: node_properties(row) for row in nodes[label]}
        if len(actual_rows) != len(expected) or actual != expected:
            raise ValueError(f"DB 노드 대조 실패: {label}")
    for relation, (source, target) in RELATION_LABELS.items():
        source_key = NODE_CONFIG[source][1]
        target_key = NODE_CONFIG[target][1]
        query = f"MATCH (s:{source})-[r:{relation}]->(t:{target}) RETURN s.{source_key}, t.{target_key}, properties(r)"
        actual_rows = client.query(transaction, query)
        actual = {(row[0], row[1]): row[2] for row in actual_rows}
        expected = {}
        for edge in edges:
            if edge["relation"] == relation:
                properties = {key: value for key, value in edge.items() if key not in {"source_id", "target_id", "relation"}}
                expected[(edge["source_id"], edge["target_id"])] = node_properties(properties)
        if len(actual_rows) != len(expected) or actual != expected:
            raise ValueError(f"DB 관계 대조 실패: {relation}")
    total_nodes = client.query(transaction, "MATCH (n) RETURN count(n)")[0][0]
    total_edges = client.query(transaction, "MATCH ()-[r]->() RETURN count(r)")[0][0]
    if total_nodes != sum(len(rows) for rows in nodes.values()) or total_edges != len(edges):
        raise ValueError("DB 전체 건수가 입력과 다릅니다")


def verify_live(client: Neo4jHttp, nodes: dict, edges: list[dict], commit: bool = False) -> dict:
    """빈 DB에서 적재와 재적재를 확인한다. 기존 데이터는 절대 삭제하지 않는다."""
    opened = client.request("POST", statements=[])
    # 서버가 반환한 URL을 그대로 따라가지 않고 트랜잭션 번호만 사용한다.
    transaction_id = opened["commit"].rstrip("/").split("/")[-2]
    if not transaction_id.isdigit():
        raise ValueError("Neo4j 트랜잭션 번호 형식 오류")
    transaction = "/" + transaction_id
    finished = False
    empty_at_start = False
    try:
        if client.query(transaction, "MATCH (n) RETURN count(n)")[0][0] != 0:
            raise ValueError("기존 데이터가 있는 DB에는 검증 데이터를 쓰지 않습니다")
        empty_at_start = True
        load_rows(client, transaction, nodes, edges)
        verify_database(client, transaction, nodes, edges)
        load_rows(client, transaction, nodes, edges)
        verify_database(client, transaction, nodes, edges)
        if commit:
            client.request("POST", transaction + "/commit", [])
        else:
            client.request("DELETE", transaction)
        finished = True
        remaining = client.query("/commit", "MATCH (n) RETURN count(n)")[0][0]
        expected = sum(len(rows) for rows in nodes.values()) if commit else 0
        if remaining != expected:
            raise ValueError("트랜잭션 종료 후 DB 건수가 예상과 다릅니다")
        return {"status": "PASS", "two_loads_verified": True, "all_properties_verified": True,
                "transaction": "COMMITTED" if commit else "ROLLED_BACK", "remaining_nodes": remaining,
                "schema_constraints_applied": False}
    finally:
        if not finished:
            try:
                client.request("DELETE", transaction)
            except RuntimeError:
                # 쿼리 오류로 서버가 이미 롤백했을 수 있다. 빈 DB 여부를 다시 확인한다.
                if empty_at_start and client.query("/commit", "MATCH (n) RETURN count(n)")[0][0] != 0:
                    raise RuntimeError("실패 후 DB가 비어 있는지 확인하지 못했습니다") from None
        client.session.close()


def load_db_settings(root: Path) -> dict[str, str]:
    """환경변수를 우선하고 .env의 DB 설정만 읽는다. 값은 출력하지 않는다."""
    allowed = {"NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_HTTP_URL", "QDRANT_URL", "QDRANT_API_KEY"}
    settings = {}
    path = root / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in allowed:
                settings[key.strip()] = value.strip().strip("\"'")
    for key in allowed:
        if os.environ.get(key):
            settings[key] = os.environ[key]
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    bundled = Path(__file__).resolve().parent
    default_package = bundled.parent if (bundled / "nodes_policy.jsonl").exists() else bundled.parent / "data/releases/v1.4.0-candidate"
    parser.add_argument("--package", type=Path, default=default_package)
    parser.add_argument("--apply", action="store_true", help="실제 빈 DB에서 검증")
    parser.add_argument("--commit", action="store_true", help="검증 데이터를 롤백하지 않고 보존")
    parser.add_argument("--http-url", default=None, help="Neo4j HTTP 서버 주소")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"checked_at": datetime.now(KST).isoformat(), "neo4j": {"status": "NOT_TESTED"}}
    try:
        nodes, report["offline"], edges, _ = validate_package(args.package.resolve())
        if args.commit and not args.apply:
            raise ValueError("--commit은 --apply와 함께만 사용할 수 있습니다")
        if args.apply:
            root = args.package.resolve().parents[2]
            settings = load_db_settings(root)
            url = args.http_url or settings.get("NEO4J_HTTP_URL")
            if not url or not settings.get("NEO4J_USER") or not settings.get("NEO4J_PASSWORD"):
                raise ValueError("Neo4j HTTP 주소·사용자·비밀번호를 .env 또는 환경변수에 설정하세요")
            client = Neo4jHttp(url, args.database, settings["NEO4J_USER"], settings["NEO4J_PASSWORD"])
            report["neo4j"] = verify_live(client, nodes, edges, args.commit)
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        report["status"] = "FAILED"
        # 데이터 전문이나 자격증명을 출력하지 않도록 위에서 만든 진단만 남긴다.
        report["error"] = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
    else:
        report["status"] = "PASS"
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
