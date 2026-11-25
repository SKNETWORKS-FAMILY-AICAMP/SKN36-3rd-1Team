"""
정적 데이터 품질 의심 목록 — Neo4j 의미 점검(neo4j_semantic_check.py) 결과 중 "그래프만 보고는 알 수 없는" 항목.

왜 정적 목록인가
- 취업 속성·원문 연령·제외 문구처럼 인계 원문·인계 속성(eligibility_attributes)을 봐야 알 수 있는 의심은
  검색할 때마다 다시 계산하지 않고, 점검 결과를 manifest 해시와 묶어 저장해 둔다.
- 그래프 구조만으로 알 수 있는 의심(무주택 승격, 소득 5구간 OR, 지역 단위, 빈 NOT 그룹 등)은
  hybrid_search.assess_policy_data_quality가 검색 때마다 직접 계산한다 (이 목록은 그 경고에 인계 대조 결과만 덧붙인다).

데이터셋 버전별 목록 (FLAG_SETS)
- 1.4.0-candidate (기본): neo4j_semantic_flags_v1_4_0.json ← neo4j_semantic_check_v1_4_0.json
- 1.3.2 (명시적 레거시, LIFEPOLICY_DATASET_DIR=data/handoff): neo4j_semantic_flags_v1_3_2.json (동결, 다시 만들지 않음)
  레거시 파일의 data_version 표기 "v1.3.2-verified"는 manifest_version "1.3.2"와 같은 데이터로 명시적으로 호환한다.
- 그 밖의 데이터셋 버전: not_applicable (빈 목록)

load_flags 상태
- applied        : 현재 데이터셋 버전용 파일이고, 파일의 flags_version·data_version이 그 버전과 맞고,
                   manifest가 같거나 기록된 동등 manifest(manifest_equivalence.json)일 때만
- stale          : manifest 불일치, 또는 파일의 flags_version·data_version이 현재 데이터셋과 다름 (reason에 이유) → 빈 목록
- missing        : 파일 없음 → 빈 목록
- not_applicable : 지원하지 않는 데이터셋 버전 → 빈 목록

원칙
- 모든 항목은 "자동 점검 의심 후보"다. 확정된 정책 오류가 아니다. 공식 원문 검증은 하지 않았다(official_source_checked=False).
- evidence_level로 근거의 종류를 구분한다:
    source_text_explicit  원문에 명시된 수치·문구와 그래프가 다름 (정규식으로 찾은 명시 표현)
    handoff_attribute     인계 속성(eligibility_attributes)과 그래프가 다름
    keyword_detected      키워드로 찾은 근거 후보 (오탐 가능 → 사람이 확인해야 함)
    graph_vs_source_absent 그래프에는 값이 있는데 원문에서 해당 표현을 찾지 못함
- build_flags는 보고서의 dataset_version·manifest_sha256이 현재 데이터와 맞을 때만 만든다
  (다른 데이터로 만든 보고서에 현재 manifest를 붙여 정상 목록으로 만들지 않는다).

만들기 (읽기 전용 점검 결과 → 목록 파일, 현재 데이터셋 버전 기준):
    .venv\\Scripts\\python.exe -c "from database import data_quality_flags as d; d.write_flags()"
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from database import full_index as fi
from database import handoff_data as hd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_QUALITY_DIR = PROJECT_ROOT / "database" / "data_quality"
SEMANTIC_DIR = PROJECT_ROOT / "docs" / "development" / "experiments" / "neo4j_semantic_20261106"
LEGACY_DATASET_VERSION = "1.3.2"

# 데이터셋 manifest_version → 목록 사양
FLAG_SETS: dict[str, dict[str, Any]] = {
    hd.RELEASE_V140_VERSION: {
        "flags_path": DATA_QUALITY_DIR / "neo4j_semantic_flags_v1_4_0.json",
        "report_path": SEMANTIC_DIR / "neo4j_semantic_check_v1_4_0.json",
        "flags_version": "neo4j_semantic_flags_v1_4_0",
        "data_version": hd.RELEASE_V140_VERSION,
        "accepted_data_versions": frozenset({hd.RELEASE_V140_VERSION}),
        "legacy": False,
    },
    LEGACY_DATASET_VERSION: {
        "flags_path": DATA_QUALITY_DIR / "neo4j_semantic_flags_v1_3_2.json",
        "report_path": SEMANTIC_DIR / "neo4j_semantic_check.json",
        "flags_version": "neo4j_semantic_flags_v1_3_2",
        # 레거시 파일 표기. manifest_version "1.3.2"와 같은 데이터를 뜻한다 (명시적 호환)
        "data_version": "v1.3.2-verified",
        "accepted_data_versions": frozenset({"v1.3.2-verified", LEGACY_DATASET_VERSION}),
        "legacy": True,
    },
}

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
S10_CHECK = "S10_MARRIAGE_CHILD_NOT_MODELED"
S10_DIMENSIONS = ("marriage", "children")
# v1.4.0 보고서가 따라야 하는 S10 규칙 (neo4j_semantic_check.S10_RULE과 같아야 한다)
S10_RULE = "S10_v2_unmodeled_incomplete_review"
S10_CLASS_KEYS = {"unmodeled_dimensions": "unmodeled", "incomplete_dimensions": "incomplete",
                  "review_dimensions": "review"}
S10_UNFLAGGED = {"consistent", "no_text"}
DISCLAIMER = ("자동 점검(키워드·인계 속성 대조)으로 찾은 의심 후보다. 확정된 정책 오류가 아니며 공식 원문(정부 공고)과 "
              "대조하지 않았다. keyword_detected 항목은 오탐 가능성이 있다.")
DISCLAIMER_V140 = DISCLAIMER + (" 인계 속성은 문서(document_id)별 값이며, 속성 부재(attribute_absent)는 '제한 없음'이 아니다. "
                                "목록에 없는 정책도 문제가 없다는 뜻이 아니다.")


class FlagBuildError(ValueError):
    """보고서가 현재 데이터와 맞지 않거나 형식이 잘못되어 목록을 만들 수 없음."""


def flag_set(version: str | None = None) -> dict[str, Any] | None:
    """데이터셋 버전(기본: 현재)의 목록 사양. 지원하지 않으면 None."""
    return FLAG_SETS.get(hd.dataset_version() if version is None else version)


_CURRENT_SET = flag_set()
# 하위 호환 상수: 현재 데이터셋 버전의 기본 경로 (지원하지 않는 버전이면 레거시 경로)
FLAGS_PATH: Path = (_CURRENT_SET or FLAG_SETS[LEGACY_DATASET_VERSION])["flags_path"]
SEMANTIC_REPORT: Path = (_CURRENT_SET or FLAG_SETS[LEGACY_DATASET_VERSION])["report_path"]


def _relative(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.name


def _s10_affects(finding: dict[str, Any], spec: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    """
    S10 판정 영향 항목. 보호 범위를 조용히 줄이지 않는다.
    - 레거시 보고서(unmodeled_dimensions 없음): 기존과 같이 ["marriage", "children"]
    - v1.4.0 (s10_rule v2): unmodeled/incomplete/review_dimensions·dimension_assessment·graph_related_conditions를
      검증하고, 형식이 하나라도 잘못되면 FlagBuildError (보호를 조용히 빼지 않음)
      affects = 표시된 측면(미모델링·불완전·검토) ∪ 원문 근거나 관련 조건이 있는 측면
                단, dimension_assessment가 명시적으로 consistent인 측면만 제외
                (관련 조건이 있다고 요건 전체가 모델링된 것은 아니므로 그 밖에는 보호를 유지한다)
    """
    evidence = finding["evidence"]
    policy_id = finding["policy_id"]
    if "unmodeled_dimensions" not in evidence:
        if spec["legacy"]:
            return list(CHECK_RULES[S10_CHECK]["affects"]), None
        raise FlagBuildError(f"{policy_id} S10: evidence.unmodeled_dimensions 없음")
    classes: dict[str, list[str]] = {}
    for key in S10_CLASS_KEYS:
        dims = evidence.get(key)
        if (not isinstance(dims, list) or len(set(dims)) != len(dims)
                or any(d not in S10_DIMENSIONS for d in dims)):
            raise FlagBuildError(f"{policy_id} S10: {key} 값이 잘못됨: {dims!r}")
        classes[key] = dims
    flagged = [d for dims in classes.values() for d in dims]
    if not flagged or len(set(flagged)) != len(flagged):
        raise FlagBuildError(f"{policy_id} S10: 표시된 측면이 없거나 분류가 겹침: {classes!r}")
    related = evidence.get("graph_related_conditions")
    if (not isinstance(related, dict) or set(related) != set(S10_DIMENSIONS)
            or any(not isinstance(related[d], list) for d in S10_DIMENSIONS)):
        raise FlagBuildError(f"{policy_id} S10: graph_related_conditions 형식이 잘못됨")
    assessment = evidence.get("dimension_assessment")
    if (not isinstance(assessment, dict) or set(assessment) != set(S10_DIMENSIONS)
            or any(not isinstance(assessment[d], dict) for d in S10_DIMENSIONS)):
        raise FlagBuildError(f"{policy_id} S10: dimension_assessment 형식이 잘못됨")
    for dim in S10_DIMENSIONS:
        text = evidence.get(dim)
        if not isinstance(text, list):
            raise FlagBuildError(f"{policy_id} S10: evidence.{dim} 형식이 잘못됨")
        kind = assessment[dim].get("classification")
        expected = next((S10_CLASS_KEYS[key] for key, dims in classes.items() if dim in dims), None)
        if expected is not None:
            if kind != expected:
                raise FlagBuildError(f"{policy_id} S10: {dim} 분류 불일치 ({kind!r} ≠ {expected!r})")
            if not text:
                raise FlagBuildError(f"{policy_id} S10: 근거 없는 표시 측면 {dim}")
        elif kind not in S10_UNFLAGGED:
            raise FlagBuildError(f"{policy_id} S10: {dim} 분류 값이 잘못됨: {kind!r}")
        elif kind == "no_text" and text:
            raise FlagBuildError(f"{policy_id} S10: 근거 줄이 있는데 no_text로 표시된 {dim}")
        if kind == "unmodeled" and related[dim]:
            raise FlagBuildError(f"{policy_id} S10: 관련 조건이 있는데 unmodeled로 표시된 {dim}")
        if kind in ("incomplete", "consistent") and not related[dim]:
            raise FlagBuildError(f"{policy_id} S10: 관련 조건 없이 {kind}로 표시된 {dim}")
    consistent = [d for d in S10_DIMENSIONS if assessment[d]["classification"] == "consistent"]
    affects = [d for d in S10_DIMENSIONS if d in flagged or ((evidence[d] or related[d]) and d not in consistent)]
    basis = {**{key: list(dims) for key, dims in classes.items()},
             "kept_without_flag": [d for d in affects if d not in flagged],
             "released_as_consistent": [d for d in consistent if d not in affects],
             "note": ("자동 의심 후보·검토 대상(확정 오류 아님). 관련 조건이 있어도 충분성을 확인하지 못한 측면은 보호 유지. "
                      "consistent(세부 요건 없음 + 자녀 연령 일치)만 제외")}
    return affects, basis


def _validate_report(report: dict[str, Any], spec: dict[str, Any], current_version: str) -> str:
    """보고서가 현재 데이터 기준인지 확인하고 보고서의 manifest_sha256을 돌려준다."""
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
        raise FlagBuildError("보고서 형식 오류: findings 목록 없음")
    report_version = report.get("dataset_version")
    if report_version is None:
        if not spec["legacy"]:
            raise FlagBuildError("보고서에 dataset_version이 없음 (레거시 1.3.2 보고서만 허용)")
    elif report_version != current_version:
        raise FlagBuildError(f"보고서 dataset_version {report_version!r} ≠ 현재 데이터셋 {current_version!r}")
    report_manifest = report.get("manifest_sha256")
    current_manifest = fi.manifest_sha256()
    if not isinstance(report_manifest, str) or not report_manifest:
        raise FlagBuildError("보고서에 manifest_sha256이 없음")
    if not fi.manifest_equivalent(report_manifest, current_manifest):
        raise FlagBuildError(f"보고서 manifest {report_manifest[:12]}… ≠ 현재 manifest {current_manifest[:12]}…")
    if not spec["legacy"] and report.get("s10_rule") != S10_RULE:
        # Phase 1 규칙 보고서는 관련 조건이 있는 정책의 S10을 누락했다 → 다시 점검한 보고서만 받는다
        raise FlagBuildError(f"보고서 s10_rule {report.get('s10_rule')!r} ≠ {S10_RULE!r} (의미 점검 재실행 필요)")
    if report.get("official_source_checked") is True:
        raise FlagBuildError("보고서가 official_source_checked=True로 표기됨 (이 목록은 공식 원문 미검증 전제)")
    return report_manifest


def build_flags(report: dict[str, Any], report_sha256: str, *, report_path: Path | None = None,
                dataset_version: str | None = None) -> dict[str, Any]:
    """
    의미 점검 결과(dict)에서 정적 목록을 만든다. 근거 줄은 보고서 값 그대로(150자 이내), 원문 전체는 넣지 않는다.
    보고서의 dataset_version·manifest_sha256이 현재 데이터와 맞지 않으면 FlagBuildError.
    """
    current_version = hd.dataset_version() if dataset_version is None else dataset_version
    spec = flag_set(current_version)
    if spec is None:
        raise FlagBuildError(f"정적 목록을 지원하지 않는 데이터셋 버전: {current_version!r}")
    report_manifest = _validate_report(report, spec, current_version)
    by_policy: dict[str, list[dict[str, Any]]] = {}
    for index, finding in enumerate(report["findings"]):
        rule = CHECK_RULES.get(finding.get("check")) if isinstance(finding, dict) else None
        if rule is None:
            continue
        missing = [key for key in ("policy_id", "check", "severity", "handoff_semantic_match", "evidence", "document_ids")
                   if key not in finding]
        if missing or not isinstance(finding["evidence"], dict) or not isinstance(finding["document_ids"], list):
            raise FlagBuildError(f"findings[{index}] 형식 오류 (누락 {missing})")
        entry = {"check": finding["check"], "severity": finding["severity"],
                 "handoff_semantic_match": finding["handoff_semantic_match"],
                 "evidence_level": rule["evidence_level"], "evidence": finding["evidence"],
                 "document_ids": finding["document_ids"], "official_source_checked": False}
        if not spec["legacy"]:
            entry["report_finding_index"] = index         # 보고서 근거와 1:1 연결
        if "code" in rule:
            affects = list(rule["affects"])
            if finding["check"] == S10_CHECK:
                affects, basis = _s10_affects(finding, spec)
                if basis is not None:
                    entry["affects_basis"] = basis
            entry.update({"code": rule["code"], "affects": affects})
        else:
            entry["enrich"] = rule["enrich"]
        by_policy.setdefault(finding["policy_id"], []).append(entry)
    source_report = (_relative(report_path) if report_path is not None
                     else _relative(spec["report_path"]))
    flags = {"flags_version": spec["flags_version"], "data_version": spec["data_version"],
             "manifest_sha256": fi.manifest_sha256(), "source_report": source_report,
             "source_report_sha256": report_sha256, "created_from_report_at": report.get("created_at"),
             "disclaimer": DISCLAIMER if spec["legacy"] else DISCLAIMER_V140,
             "official_source_checked": False,
             "policies": len(by_policy), "flags_by_policy": by_policy}
    if not spec["legacy"]:
        flags.update({"source_report_dataset_version": report.get("dataset_version"),
                      "source_report_manifest_sha256": report_manifest,
                      "flag_count": sum(len(items) for items in by_policy.values())})
    return flags


def write_flags(report_path: Path | None = None, out_path: Path | None = None) -> dict[str, Any]:
    """현재 데이터셋 버전의 보고서로 목록 파일을 만든다 (경로를 주면 그 경로). 검증 실패 시 파일을 쓰지 않는다."""
    spec = flag_set()
    if spec is None:
        raise FlagBuildError(f"정적 목록을 지원하지 않는 데이터셋 버전: {hd.dataset_version()!r}")
    report_path = Path(report_path) if report_path is not None else spec["report_path"]
    out_path = Path(out_path) if out_path is not None else spec["flags_path"]
    data = report_path.read_bytes()
    flags = build_flags(json.loads(data.decode("utf-8")), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
                        report_path=report_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE.clear()
    return flags


_CACHE: dict[tuple, dict[str, Any]] = {}


def _cache_key(path: Path, version: str, current_manifest: str) -> tuple:
    try:
        signature = hd.file_signature(path)
    except OSError:
        signature = None
    return str(path), str(hd.HANDOFF_DIR), version, current_manifest, signature


def load_flags(path: Path | None = None) -> dict[str, Any]:
    """
    현재 데이터셋 버전의 목록을 읽는다. 반환: {"status": applied|stale|missing|not_applicable, "flags_by_policy", ...}
    path를 주면 그 파일을 현재 데이터셋 기준으로 검증한다.
    캐시 키: 파일 경로·데이터셋 폴더·데이터셋 버전·현재 manifest SHA-256·파일 서명(크기·수정 시각 등).
    """
    version = hd.dataset_version()
    spec = flag_set(version)
    path = Path(path) if path is not None else (spec["flags_path"] if spec else FLAGS_PATH)
    current = fi.manifest_sha256()
    key = _cache_key(path, version, current)
    if key in _CACHE:
        return _CACHE[key]
    if spec is None:
        result = {"status": "not_applicable", "file": path.name, "dataset": version, "flags_by_policy": {}}
    elif not path.is_file():
        result = {"status": "missing", "file": path.name, "dataset": version, "flags_by_policy": {}}
    else:
        flags = json.loads(path.read_text(encoding="utf-8"))
        reasons = []
        # 같은 manifest이거나, 데이터 파일이 그대로인 기록된 manifest 변경(manifest_equivalence.json)만 적용
        if not fi.manifest_equivalent(str(flags.get("manifest_sha256") or ""), current):
            reasons.append("manifest_mismatch")
        if flags.get("data_version") not in spec["accepted_data_versions"]:
            reasons.append("data_version_mismatch")
        if flags.get("flags_version") != spec["flags_version"]:
            reasons.append("flags_version_mismatch")
        status = "stale" if reasons else "applied"
        result = {"status": status, "file": path.name, "dataset": version,
                  "flags_version": flags.get("flags_version"),
                  "data_version": flags.get("data_version"), "manifest_sha256": flags.get("manifest_sha256"),
                  "current_manifest_sha256": current,
                  "disclaimer": flags.get("disclaimer"),
                  "flags_by_policy": flags.get("flags_by_policy", {}) if status == "applied" else {}}
        if reasons:
            result["reason"] = reasons
    for old in [k for k in _CACHE if k[0] == key[0]]:
        del _CACHE[old]
    _CACHE[key] = result
    return result


def status_summary(loaded: dict[str, Any]) -> dict[str, Any]:
    """검색 결과에 남길 목록 상태 (목록 내용은 빼고)."""
    return {key: loaded.get(key) for key in ("status", "file", "flags_version", "data_version", "manifest_sha256")}
