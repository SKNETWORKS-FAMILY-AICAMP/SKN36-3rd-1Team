"""
데이터 담당(장반석)이 전달한 승인 릴리스·인계 패키지를 읽고 점검하는 공통 모듈.

왜 필요한가?
- Qdrant 적재, Neo4j 적재, 학습용 Notebook, 테스트가 모두 "같은 파일을 같은 규칙으로" 읽어야
  두 DB의 policy_id·document_id가 어긋나지 않는다. 그래서 읽기 규칙을 이 파일 한 곳에 모은다.
- 이 모듈은 DB에 접속하지 않는다. 파일만 읽으므로 DB 서버가 꺼져 있어도 실행할 수 있다.

중요한 원칙
- 인계 파일(data/handoff/)은 절대 수정하지 않는다. 읽기만 한다.
- 비정상 청크는 "삭제"가 아니라 "적재 시 선택적으로 제외"한다. 원본 557건은 그대로 남는다.
- 페이지 번호는 원문 페이지 단위로 청킹된 PDF 문서에서만 사용한다. 확인되지 않은 페이지는 None.
"""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 1. 경로 설정
# ---------------------------------------------------------------------------
# 이 파일은 <프로젝트 루트>/database/handoff_data.py 에 있으므로 parents[1]이 프로젝트 루트다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_HANDOFF_DIR = PROJECT_ROOT / "data" / "handoff"
DEFAULT_HANDOFF_DIR = PROJECT_ROOT / "data" / "releases" / "v1.4.0-candidate"
# 기본 릴리스의 manifest_version. 프론트 frontend/src/graph/loadGraph.ts·vite.config.ts의 경로와 같아야 한다
# (tests/unit/test_shared_constants.py가 확인).
RELEASE_V140_VERSION = "1.4.0-candidate"
# 명시한 경로가 우선이며, 미설정·빈 값은 승인 v1.4.0을 사용한다.
# 상대 경로는 프로젝트 루트 기준. v1.3.2는 data/handoff를 명시한다.
DATASET_DIR_ENV = "LIFEPOLICY_DATASET_DIR"


def _dataset_dir() -> Path:
    value = os.environ.get(DATASET_DIR_ENV, "").strip()
    path = Path(value) if value else DEFAULT_HANDOFF_DIR
    path = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()
    if not path.is_dir():
        raise ValueError(f"데이터셋 폴더가 없습니다: {path}")
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest["manifest_version"], str) or not manifest["manifest_version"]:
            raise ValueError("manifest_version 누락")
        for key in ("total_chunks", "total_policies", "total_documents", "total_graph_nodes", "total_graph_edges"):
            count = manifest["summary"][key]
            if type(count) is not int or count < 0:
                raise ValueError(f"summary.{key}는 0 이상 정수여야 합니다")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"데이터셋 manifest가 없거나 잘못되었습니다: {path / 'manifest.json'}") from error
    return path


HANDOFF_DIR = _dataset_dir()
IS_DEFAULT_DATASET = HANDOFF_DIR == DEFAULT_HANDOFF_DIR
IS_LEGACY_DATASET = HANDOFF_DIR == LEGACY_HANDOFF_DIR
NEO4J_HANDOFF_DIR = HANDOFF_DIR / "neo4j"
QDRANT_HANDOFF_DIR = HANDOFF_DIR / "qdrant"

PAYLOAD_CHUNKS_PATH = QDRANT_HANDOFF_DIR / "payload_chunks.jsonl"
RELATIONSHIPS_PATH = NEO4J_HANDOFF_DIR / "relationships.jsonl"
MANIFEST_PATH = HANDOFF_DIR / "manifest.json"
# 패키지의 정책 단위 메타데이터. 없으면 청크에서 모은다.
POLICY_METADATA_PATH = QDRANT_HANDOFF_DIR / "policy_metadata.jsonl"

# 문서 단위 정제본 (재청킹 원천). 반석님 정제 결과이며 읽기만 한다.
# 기존 청크 557건이 모두 이 파일 본문의 부분 문자열임을 2026-11-05에 확인했다.
# 다른 인계 패키지는 패키지 안의 documents.jsonl(본문 + content_hash)을 쓴다.
DOCUMENTS_PATH = (PROJECT_ROOT / "data" / "processed" / "documents.jsonl" if IS_LEGACY_DATASET
                  else HANDOFF_DIR / "documents.jsonl")
# 승인된 데이터 릴리스(data/releases/v1.4.0-candidate 등): 본문·정책 메타데이터 파일이 없어
# database/release_adapter.py가 원본을 바꾸지 않고 메모리에서 본문·문서별 메타데이터를 만든다.
IS_RELEASE_PACKAGE = not IS_LEGACY_DATASET and not DOCUMENTS_PATH.is_file() and (
    NEO4J_HANDOFF_DIR / "nodes_document.jsonl").is_file() and PAYLOAD_CHUNKS_PATH.is_file()

# 인계 문서(data/handoff/README.md, manifest v1.3.2)에 적힌 기대 건수.
# 테스트는 실제 파일을 세어 이 값과 비교한다. 데이터가 바뀌면 테스트가 FAIL로 알려준다.
EXPECTED_CHUNK_COUNT = 557
EXPECTED_POLICY_COUNT = 237
EXPECTED_DOCUMENT_COUNT = 246
EXPECTED_NODE_COUNT = 1034
EXPECTED_RELATIONSHIP_COUNT = 3615
if not IS_LEGACY_DATASET:
    # 다른 패키지는 그 manifest summary가 기대 건수다 (고정값을 새로 박지 않는다).
    _summary = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["summary"]
    EXPECTED_CHUNK_COUNT = _summary["total_chunks"]
    EXPECTED_POLICY_COUNT = _summary["total_policies"]
    EXPECTED_DOCUMENT_COUNT = _summary["total_documents"]
    EXPECTED_NODE_COUNT = _summary["total_graph_nodes"]
    EXPECTED_RELATIONSHIP_COUNT = _summary["total_graph_edges"]


@lru_cache(maxsize=8)
def _read_dataset_version(path: Path, signature: tuple[int, ...]) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["manifest_version"]


def file_signature(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def dataset_version() -> str:
    """현재 인계 패키지의 manifest_version (v1.3.2는 "1.3.2")."""
    return _read_dataset_version(MANIFEST_PATH, file_signature(MANIFEST_PATH))

# ---------------------------------------------------------------------------
# 2. Neo4j 노드·관계 명세
# ---------------------------------------------------------------------------
# 노드 파일 → (Neo4j 레이블, 고유 ID 필드)
# 고유 ID 필드는 cypher_ddl.cypher의 UNIQUE 제약조건과 같아야 한다.
NODE_SPECS: dict[str, tuple[str, str]] = {
    "nodes_policy.jsonl": ("Policy", "policy_id"),
    "nodes_document.jsonl": ("Document", "document_id"),
    "nodes_organization.jsonl": ("Organization", "agency_id"),
    "nodes_region.jsonl": ("Region", "region_id"),
    "nodes_category.jsonl": ("Category", "category_id"),
    "nodes_lifestage.jsonl": ("LifeStage", "stage_id"),
    "nodes_target_group.jsonl": ("TargetGroup", "group_id"),
    "nodes_condition_group.jsonl": ("ConditionGroup", "group_id"),
    "nodes_eligibility_condition.jsonl": ("EligibilityCondition", "condition_id"),
}

# 관계 타입 → (출발 레이블, 도착 레이블)
#
# 왜 이 표가 필요한가? (인계 로더 결함 수정의 핵심)
# - 인계 로더(data/handoff/neo4j/load_neo4j.py)는 레이블 없이
#   "src.policy_id = row.source_id OR src.document_id = ..." 처럼 매칭했다.
# - 그런데 Document·ConditionGroup 노드에도 policy_id 속성이 있어서
#   source_id = "POL_001" 하나가 Policy + Document + ConditionGroup 여러 노드에 동시에 매칭됐다.
# - 그래서 관계 타입마다 "어느 레이블에서 어느 레이블로 가는지"를 명시하고,
#   각 레이블의 고유 ID 필드로만 매칭한다. (2026-11-05 오프라인 확인: 11개 타입 모두 조합이 1개뿐)
RELATIONSHIP_SPECS: dict[str, tuple[str, str]] = {
    "ADMINISTERED_BY": ("Policy", "Organization"),
    "AVAILABLE_IN": ("Policy", "Region"),
    "HAS_CATEGORY": ("Policy", "Category"),
    "APPLIES_TO_LIFESTAGE": ("Policy", "LifeStage"),
    "TARGETS": ("Policy", "TargetGroup"),
    "SUPPORTED_BY": ("Policy", "Document"),
    "REQUIRES_CONDITION": ("Policy", "EligibilityCondition"),
    "HAS_ROOT_CONDITION_GROUP": ("Policy", "ConditionGroup"),
    "MODIFIES_CONDITION_OF": ("Policy", "Policy"),
    "CONTAINS_GROUP": ("ConditionGroup", "ConditionGroup"),
    "CONTAINS_CONDITION": ("ConditionGroup", "EligibilityCondition"),
}

# 레이블 → 고유 ID 필드 (NODE_SPECS에서 자동으로 만든다)
LABEL_ID_FIELDS: dict[str, str] = {label: id_field for label, id_field in NODE_SPECS.values()}

# ---------------------------------------------------------------------------
# 3. 비정상 청크(검토 완료 목록)
# ---------------------------------------------------------------------------
# 이 길이 미만인 청크를 "비정상 후보"로 본다. (2026-11-05 측정: 정확히 27건)
SHORT_CHUNK_THRESHOLD = 120

# 후보 27건을 한 건씩 직접 확인한 결과다. 직전 청크와 겹치는 80자를 뺀 나머지 내용이
# URL 꼬리 / 최종수정일시 / 게시판 메뉴뿐이며, 자격 조건·금액·기간 같은 정책 내용은 없었다.
# 출처 URL은 payload의 source_url 필드에 이미 온전히 들어 있으므로 정보 손실이 없다.
#
# 왜 규칙만 쓰지 않고 목록을 따로 두는가?
# - "120자 미만이면 무조건 제외" 같은 규칙은 데이터가 바뀌면 정상 청크까지 지울 수 있다.
# - 그래서 사람이 확인한 목록만 제외하고, 테스트에서 "규칙 후보 == 검토 목록"을 확인한다.
#   데이터가 바뀌어 둘이 달라지면 테스트가 FAIL이 되고, 그때 다시 검토한다.
# TODO(주민혁): 제외 방침은 데이터 담당(장반석)과 최종 합의 필요. 합의 전까지 제외는 선택 옵션.
REASON_URL_TAIL = "출처 URL 꼬리만 남음"
REASON_URL_TAIL_AND_TIMESTAMP = "출처 URL 꼬리 + 최종수정일시만 남음"
REASON_BOARD_NAVIGATION = "게시판 이전글/다음글 메뉴만 남음"

REVIEWED_NOISE_CHUNKS: dict[str, str] = {
    "DOC_BOK_015_C03": REASON_URL_TAIL,
    "DOC_BOK_022_C02": REASON_URL_TAIL,
    "DOC_BOK_036_C02": REASON_URL_TAIL,
    "DOC_BOK_041_C03": REASON_URL_TAIL,
    "DOC_BOK_042_C04": REASON_URL_TAIL,
    "DOC_BOK_043_C03": REASON_URL_TAIL,
    "DOC_BOK_062_C02": REASON_URL_TAIL,
    "DOC_BOK_075_C02": REASON_URL_TAIL,
    "DOC_BOK_079_C02": REASON_URL_TAIL,
    "DOC_BOK_082_C02": REASON_URL_TAIL,
    "DOC_GOV_011_C02": REASON_URL_TAIL,
    "DOC_GOV_017_C02": REASON_URL_TAIL,
    "DOC_GOV_030_C03": REASON_URL_TAIL,
    "DOC_GOV_035_C02": REASON_URL_TAIL,
    "DOC_GOV_049_C02": REASON_URL_TAIL,
    "DOC_MOLIT_001_C05": REASON_URL_TAIL,
    "DOC_YC_002_C03": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_005_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_011_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_013_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_014_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_015_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_016_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_021_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_033_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_YC_049_C02": REASON_URL_TAIL_AND_TIMESTAMP,
    "DOC_NHUF_001_C02": REASON_BOARD_NAVIGATION,
}

# ---------------------------------------------------------------------------
# 4. 페이지 번호 규칙
# ---------------------------------------------------------------------------
# 원문 PDF를 "페이지 단위로" 나눈 문서만 페이지 번호를 믿을 수 있다.
# 근거: scripts/build_clean_dataset.py [Step 4] 4.1
#   - DOC_LH_001, DOC_NHUF_002 : 페이지별로 순회하며 청킹 → chunk_id의 _P03_ 이 실제 페이지
#   - DOC_LH_002 : PDF 전체 텍스트를 한 번에 넣고 page_number=1을 고정값으로 기록 → 실제 페이지 미확인
#   - HTML·API 문서 : page_number=1은 자리채움 값 → 페이지 없음
# 따라서 아래 두 문서만 페이지를 돌려주고, 나머지는 None(페이지 정보 없음)으로 둔다.
PAGE_VERIFIED_DOCUMENT_IDS = frozenset({"DOC_LH_001", "DOC_NHUF_002"})

# chunk_id 예: DOC_LH_001_P03_C02 → document_id=DOC_LH_001, 페이지=3, 순번=2
#              DOC_GOV_001_C01    → document_id=DOC_GOV_001, 페이지=없음, 순번=1
CHUNK_ID_PATTERN = re.compile(r"^(?P<document_id>DOC_[A-Za-z0-9_]+?)(?:_P(?P<page>\d+))?_C(?P<seq>\d+)$")


# ---------------------------------------------------------------------------
# 5. 파일 읽기 함수
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일(한 줄에 JSON 하나)을 읽어 dict 리스트로 돌려준다. 빈 줄은 건너뛴다."""
    records = []
    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                # 몇 번째 줄이 깨졌는지 알려줘야 데이터 담당에게 정확히 전달할 수 있다.
                raise ValueError(f"{path.name} {line_number}번째 줄 JSON 파싱 실패: {error}") from error
    return records


def load_payload_chunks() -> list[dict[str, Any]]:
    """Qdrant에 적재할 청크 557건을 읽는다. (원본 그대로, 수정하지 않음)"""
    return read_jsonl(PAYLOAD_CHUNKS_PATH)


def load_neo4j_nodes() -> dict[str, list[dict[str, Any]]]:
    """Neo4j 노드 파일 9종을 읽어 {레이블: [노드, ...]} 형태로 돌려준다."""
    nodes_by_label = {}
    for file_name, (label, _id_field) in NODE_SPECS.items():
        nodes_by_label[label] = read_jsonl(NEO4J_HANDOFF_DIR / file_name)
    return nodes_by_label


def load_neo4j_relationships() -> list[dict[str, Any]]:
    """Neo4j 관계 파일을 읽는다. 각 줄은 source_id, relation, target_id 등을 가진다."""
    return read_jsonl(RELATIONSHIPS_PATH)


def load_documents(verify_hash: bool = True) -> list[dict[str, Any]]:
    """
    문서 단위 정제본 246건(data/processed/documents.jsonl)을 읽는다. 재청킹(B·C안)의 원천이다.

    verify_hash=True 이면 각 문서의 content_hash가 본문(text)의 SHA-256과 같은지 확인한다.
    다르면 "본문이 바뀌었는데 해시는 옛날 값"이라는 뜻이므로, 어떤 문서인지 알려주고 멈춘다.
    (청크의 char_start/char_end는 이 본문 기준 위치이므로, 본문이 바뀌면 위치도 믿을 수 없다.)

    승인된 릴리스 패키지(IS_RELEASE_PACKAGE)는 release_adapter가 Document 노드 순서대로 본문을 조립하고
    content_hash를 항상 검증한다 (verify_hash와 무관하게 불일치·누락·중복이면 멈춘다).
    """
    if IS_RELEASE_PACKAGE:
        from database import release_adapter
        return release_adapter.load_release_documents(HANDOFF_DIR, PROJECT_ROOT)
    documents = read_jsonl(DOCUMENTS_PATH)
    if verify_hash:
        mismatched = [
            doc["document_id"]
            for doc in documents
            if doc.get("content_hash") != _sha256(doc["text"].encode("utf-8"))
        ]
        if mismatched:
            raise ValueError(f"content_hash와 본문이 다른 문서 {len(mismatched)}건: {mismatched[:5]}")
    return documents


# ---------------------------------------------------------------------------
# 6. 청크 관련 함수
# ---------------------------------------------------------------------------
def parse_chunk_id(chunk_id: str) -> dict[str, Any]:
    """chunk_id를 document_id / page_in_id / seq 로 나눈다. 형식이 틀리면 ValueError."""
    match = CHUNK_ID_PATTERN.match(chunk_id)
    if match is None:
        raise ValueError(f"chunk_id 형식이 계약과 다릅니다: {chunk_id}")
    page_text = match.group("page")
    return {
        "document_id": match.group("document_id"),
        "page_in_id": int(page_text) if page_text else None,
        "seq": int(match.group("seq")),
    }


def get_verified_page_number(chunk: dict[str, Any]) -> int | None:
    """
    검색 결과에 보여줄 페이지 번호를 돌려준다.
    페이지 단위로 청킹된 PDF 문서(PAGE_VERIFIED_DOCUMENT_IDS)만 숫자를, 나머지는 None을 돌려준다.
    페이지를 추측해서 만들어내지 않기 위한 함수다.
    """
    if chunk["document_id"] not in PAGE_VERIFIED_DOCUMENT_IDS:
        return None
    return parse_chunk_id(chunk["chunk_id"])["page_in_id"]


def find_short_chunk_candidates(
    chunks: list[dict[str, Any]], threshold: int = SHORT_CHUNK_THRESHOLD
) -> list[str]:
    """본문 길이가 threshold 미만인 청크의 chunk_id 목록 (비정상 '후보'일 뿐, 자동 제외하지 않음)."""
    return sorted(chunk["chunk_id"] for chunk in chunks if len(chunk["text"]) < threshold)


def select_chunks_for_embedding(
    chunks: list[dict[str, Any]], exclude_noise: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    임베딩·적재할 청크와 제외할 청크를 나눈다.

    입력: 원본 청크 리스트 (557건)
    출력: (적재할 청크, 제외된 청크) — 두 리스트의 합은 항상 원본 건수와 같다.
    exclude_noise=False(기본값) 이면 아무것도 제외하지 않는다.
    기본값을 False로 둔 이유: 27건 제외는 데이터 담당과 아직 합의 전이므로,
    호출하는 쪽이 exclude_noise=True 를 명시했을 때만 제외한다.
    원본 dict는 바꾸지 않는다.
    """
    if not exclude_noise:
        return list(chunks), []

    selected, excluded = [], []
    for chunk in chunks:
        if chunk["chunk_id"] in REVIEWED_NOISE_CHUNKS:
            excluded.append(chunk)
        else:
            selected.append(chunk)
    return selected, excluded


# ---------------------------------------------------------------------------
# 7. Neo4j 관계 오프라인 매칭 (DB 없이 미리 확인)
# ---------------------------------------------------------------------------
def match_relationships_offline(
    nodes_by_label: dict[str, list[dict[str, Any]]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    수정 로더와 같은 규칙(레이블 + 고유 ID)으로 관계를 노드에 연결해 보고 결과를 요약한다.
    실제 DB에 쓰기 전에 "관계 1건 = 노드 쌍 1개"가 되는지 확인하는 용도다.

    출력 예: {"matched": 3615, "unknown_relation": [], "missing_source": [], "missing_target": [],
              "duplicate_triples": 0, "matched_by_relation": {"TARGETS": 424, ...}}
    """
    # 레이블별로 "고유 ID 집합"을 만든다. 예: ids_by_label["Policy"] = {"POL_001", ...}
    ids_by_label = {
        label: {node[LABEL_ID_FIELDS[label]] for node in nodes}
        for label, nodes in nodes_by_label.items()
    }

    report: dict[str, Any] = {
        "matched": 0,
        "unknown_relation": [],
        "missing_source": [],
        "missing_target": [],
        "duplicate_triples": 0,
        "matched_by_relation": {},
    }
    seen_triples = set()

    for relationship in relationships:
        relation = relationship["relation"]
        if relation not in RELATIONSHIP_SPECS:
            report["unknown_relation"].append(relation)
            continue

        source_label, target_label = RELATIONSHIP_SPECS[relation]
        source_ok = relationship["source_id"] in ids_by_label[source_label]
        target_ok = relationship["target_id"] in ids_by_label[target_label]
        if not source_ok:
            report["missing_source"].append((relation, relationship["source_id"]))
        if not target_ok:
            report["missing_target"].append((relation, relationship["target_id"]))
        if not (source_ok and target_ok):
            continue

        # MERGE는 (타입, 출발, 도착)이 같으면 관계를 하나로 합친다.
        # 같은 줄이 두 번 있으면 DB 관계 수가 파일 줄 수보다 적어지므로 미리 센다.
        triple = (relation, relationship["source_id"], relationship["target_id"])
        if triple in seen_triples:
            report["duplicate_triples"] += 1
        seen_triples.add(triple)

        report["matched"] += 1
        report["matched_by_relation"][relation] = report["matched_by_relation"].get(relation, 0) + 1

    return report


# ---------------------------------------------------------------------------
# 8. manifest 체크섬 확인
# ---------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_manifest_checksums() -> list[dict[str, Any]]:
    """
    manifest.json의 SHA-256과 실제 파일을 비교한다.

    왜 줄바꿈을 바꿔서도 비교하는가?
    - Windows Git(core.autocrlf=true)은 체크아웃할 때 줄바꿈을 LF → CRLF로 바꿀 수 있다.
    - 실제로 manifest는 파일마다 CRLF 기준 / LF 기준 해시가 섞여 있다. (2026-11-05 확인)
    - 그래서 "있는 그대로 / LF로 통일 / CRLF로 통일" 세 가지 중 하나라도 같으면
      내용은 같다고 보고, 어떤 기준으로 일치했는지(matched_as)를 함께 기록한다.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    results = []
    for relative_path, info in manifest["files"].items():
        raw_bytes = (HANDOFF_DIR / relative_path).read_bytes()
        lf_bytes = raw_bytes.replace(b"\r\n", b"\n")
        crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")

        matched_as = None
        for name, candidate in (("raw", raw_bytes), ("lf", lf_bytes), ("crlf", crlf_bytes)):
            if _sha256(candidate) == info["sha256"]:
                matched_as = name
                break

        results.append({"file": relative_path, "matched": matched_as is not None, "matched_as": matched_as})
    return results


# ---------------------------------------------------------------------------
# 9. 정책 메타데이터 (Qdrant payload 조인용)
# ---------------------------------------------------------------------------
# 새 청크(B1/B2)에는 정책명·분야·지역 같은 검색 필터 필드가 없다.
# 인계 payload(557건)에는 청크마다 같은 정책 메타데이터가 들어 있으므로, policy_id 기준으로 한 번만 모아 둔다.
# (2026-11-05 확인: 정책 237개 모두 청크끼리 값이 같고, 문서 246건 모두 조인된다)
POLICY_METADATA_FIELDS = (
    "policy_name", "category", "region", "life_stages", "target_groups",
    "eligibility_attributes", "verification_status",
)
OPTIONAL_POLICY_METADATA_FIELDS = ("region_detail", "eligibility_review_status")


def load_policy_metadata(chunks: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """
    policy_id → 정책 메타데이터 dict 를 만든다.

    입력: 인계 payload 청크 리스트 (생략하면 payload_chunks.jsonl을 읽는다)
    반환: {"POL_001": {"policy_name": ..., "category": ..., ...}, ...}
    같은 policy_id인데 청크마다 값이 다르면 ValueError를 낸다. (어느 값이 맞는지 추측하지 않는다)

    패키지에 qdrant/policy_metadata.jsonl이 있으면 그 정책 단위 값을 우선한다.
    (v1.4.0부터 청크에 문서별 값이 섞여 청크로 모으면 정책 5개가 불일치한다.)
    이때도 청크의 policy_id가 모두 있어야 하며, 선택 필드 region_detail·eligibility_review_status는 있을 때만 담는다.
    """
    if POLICY_METADATA_PATH.is_file():
        metadata = {}
        for row in read_jsonl(POLICY_METADATA_PATH):
            values = {field: row[field] for field in POLICY_METADATA_FIELDS}
            values.update({field: row[field] for field in OPTIONAL_POLICY_METADATA_FIELDS if field in row})
            metadata[row["policy_id"]] = values
        missing = sorted({chunk["policy_id"] for chunk in (chunks if chunks is not None else load_payload_chunks())}
                         - set(metadata))
        if missing:
            raise ValueError(f"policy_metadata.jsonl에 없는 정책: {missing[:5]}")
        return metadata
    if chunks is None:
        chunks = load_payload_chunks()
    metadata: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        values = {field: chunk[field] for field in POLICY_METADATA_FIELDS}
        policy_id = chunk["policy_id"]
        if policy_id in metadata and metadata[policy_id] != values:
            raise ValueError(f"정책 메타데이터 불일치: {policy_id} (청크 {chunk['chunk_id']})")
        metadata[policy_id] = values
    return metadata


def load_payload_metadata(chunks: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """
    Qdrant payload·임베딩 입력에 붙일 메타데이터.

    - 승인된 릴리스 패키지: document_id → 메타데이터 (release_adapter, 릴리스 청크 값 그대로. 정책 단위로 합치지 않음)
    - 그 밖(v1.3.2 기본, documents.jsonl이 있는 패키지): 기존과 같은 policy_id → 메타데이터 (load_policy_metadata)
    청크에서 값을 찾을 때는 store.metadata_for_chunk를 쓴다. 릴리스(DocumentMetadata)는 document_id로만,
    정책 단위 dict는 policy_id로만 찾고 서로 대체하지 않는다 (없으면 오류).
    """
    if IS_RELEASE_PACKAGE:
        from database import release_adapter
        return release_adapter.load_release_document_metadata(HANDOFF_DIR, chunks)
    return load_policy_metadata(chunks)
