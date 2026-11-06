"""
정적 데이터 품질 의심 목록 — Neo4j 의미 점검(neo4j_semantic_check.py) 결과 중 "그래프만 보고는 알 수 없는" 항목.

왜 정적 목록인가
- 취업 속성·원문 연령·제외 문구처럼 인계 원문(documents.jsonl)·인계 속성(policies.jsonl)을 봐야 알 수 있는 의심은
  검색할 때마다 다시 계산하지 않고, 점검 결과를 manifest 해시와 묶어 저장해 둔다.
- 그래프 구조만으로 알 수 있는 의심(무주택 승격, 소득 5구간 OR, 지역 단위, 빈 NOT 그룹 등)은
  hybrid_search.assess_policy_data_quality가 검색 때마다 직접 계산한다 (이 목록은 그 경고에 인계 대조 결과만 덧붙인다).

원칙
- 모든 항목은 "자동 점검 의심 후보"다. 확정된 정책 오류가 아니다. 공식 원문 검증은 하지 않았다(official_source_checked=False).
- evidence_level로 근거의 종류를 구분한다:
    source_text_explicit  원문에 명시된 수치·문구와 그래프가 다름 (정규식으로 찾은 명시 표현)
    handoff_attribute     인계 속성(eligibility_attributes)과 그래프가 다름
    keyword_detected      키워드로 찾은 근거 후보 (오탐 가능 → 사람이 확인해야 함)
    graph_vs_source_absent 그래프에는 값이 있는데 원문에서 해당 표현을 찾지 못함
- 데이터 manifest가 바뀌면(sha256 불일치) 목록을 적용하지 않는다 (status="stale").

만들기 (읽기 전용 점검 결과 → 목록 파일):
    .venv\\Scripts\\python.exe -c "from database import data_quality_flags as d; d.write_flags()"
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from database import full_index as fi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_REPORT = PROJECT_ROOT / "docs" / "development" / "experiments" / "neo4j_semantic_20261106" / "neo4j_semantic_check.json"
FLAGS_PATH = PROJECT_ROOT / "database" / "data_quality" / "neo4j_semantic_flags_v1_3_2.json"

# 점검 항목 → (경고 코드 또는 기존 경고에 붙일 대조 결과, 근거 수준, 판정 영향 항목)
CHECK_RULES: dict[str, dict[str, Any]] = {
    "S6_EMPLOYMENT_MISSING": {"code": "EMPLOYMENT_NOT_MODELED", "evidence_level": "handoff_attribute",
                              "affects": ["employment"]},
    "S7_AGE_RANGE_MISMATCH": {"code": "AGE_SOURCE_GRAPH_MISMATCH", "evidence_level": "source_text_explicit",
                              "affects": ["age"]},
    "S7_AGE_GRAPH_ONLY": {"code": "AGE_LOWER_BOUND_WITHOUT_SOURCE", "evidence_level": "graph_vs_source_absent",
                          "affects": ["age"]},
    "S8_EXCLUSION_NOT_MODELED": {"code": "EXCLUSION_NOT_MODELED", "evidence_level": "keyword_detected",
                                 "affects": ["exclusion"]},
    "S10_MARRIAGE_CHILD_NOT_MODELED": {"code": "MARRIAGE_CHILD_NOT_MODELED", "evidence_level": "keyword_detected",
                                       "affects": ["marriage", "children"]},
    "S11_SOURCE_AGE_ERROR_MARK": {"code": "SOURCE_AGE_ERROR_MARK", "evidence_level": "source_text_explicit",
                                  "affects": ["age"]},
    # 아래 셋은 새 경고가 아니라 기존(그래프 계산) 경고에 붙이는 인계 대조 결과다
    "S1_UNKNOWN_WITH_CONDITION_TEXT": {"enrich": "UNKNOWN_CONDITION_GROUP", "evidence_level": "keyword_detected"},
    "S2_INCOME_ALL_BANDS_OR": {"enrich": "SUSPECTED_INCOME_ALL_BANDS_OR", "evidence_level": "handoff_attribute"},
    "S4_HOMELESS_REQUIRED_PROMOTED": {"enrich": "SUSPECTED_HOMELESS_REQUIRED_WITH_HOUSEHOLD_OR",
                                      "evidence_level": "handoff_attribute"},
}
DISCLAIMER = ("자동 점검(키워드·인계 속성 대조)으로 찾은 의심 후보다. 확정된 정책 오류가 아니며 공식 원문(정부 공고)과 "
              "대조하지 않았다. keyword_detected 항목은 오탐 가능성이 있다.")


def build_flags(report: dict[str, Any], report_sha256: str) -> dict[str, Any]:
    """의미 점검 결과(dict)에서 정적 목록을 만든다. 근거 줄은 150자 이내 그대로, 원문 전체는 넣지 않는다."""
    by_policy: dict[str, list[dict[str, Any]]] = {}
    for finding in report["findings"]:
        rule = CHECK_RULES.get(finding["check"])
        if rule is None:
            continue
        entry = {"check": finding["check"], "severity": finding["severity"],
                 "handoff_semantic_match": finding["handoff_semantic_match"],
                 "evidence_level": rule["evidence_level"], "evidence": finding["evidence"],
                 "document_ids": finding["document_ids"], "official_source_checked": False}
        entry.update({"code": rule["code"], "affects": rule["affects"]} if "code" in rule else {"enrich": rule["enrich"]})
        by_policy.setdefault(finding["policy_id"], []).append(entry)
    return {"flags_version": "neo4j_semantic_flags_v1_3_2", "data_version": "v1.3.2-verified",
            "manifest_sha256": fi.manifest_sha256(), "source_report": "docs/development/experiments/neo4j_semantic_20261106/"
                                                                       "neo4j_semantic_check.json",
            "source_report_sha256": report_sha256, "created_from_report_at": report.get("created_at"),
            "disclaimer": DISCLAIMER, "policies": len(by_policy), "flags_by_policy": by_policy}


def write_flags(report_path: Path = SEMANTIC_REPORT, out_path: Path = FLAGS_PATH) -> dict[str, Any]:
    data = report_path.read_bytes()
    flags = build_flags(json.loads(data.decode("utf-8")), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    return flags


_CACHE: dict[str, dict[str, Any]] = {}


def load_flags(path: Path = FLAGS_PATH) -> dict[str, Any]:
    """
    목록을 읽는다 (한 번 읽으면 캐시). 반환: {"status": applied|stale|missing, "flags_by_policy", "manifest_sha256", ...}
    현재 manifest와 다르면 stale → flags_by_policy를 비워 적용하지 않는다.
    """
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    if not Path(path).is_file():
        result = {"status": "missing", "file": Path(path).name, "flags_by_policy": {}}
    else:
        flags = json.loads(Path(path).read_text(encoding="utf-8"))
        current = fi.manifest_sha256()
        status = "applied" if flags.get("manifest_sha256") == current else "stale"
        result = {"status": status, "file": Path(path).name, "flags_version": flags.get("flags_version"),
                  "data_version": flags.get("data_version"), "manifest_sha256": flags.get("manifest_sha256"),
                  "disclaimer": flags.get("disclaimer"),
                  "flags_by_policy": flags.get("flags_by_policy", {}) if status == "applied" else {}}
    _CACHE[key] = result
    return result


def status_summary(loaded: dict[str, Any]) -> dict[str, Any]:
    """검색 결과에 남길 목록 상태 (목록 내용은 빼고)."""
    return {key: loaded.get(key) for key in ("status", "file", "flags_version", "data_version", "manifest_sha256")}
