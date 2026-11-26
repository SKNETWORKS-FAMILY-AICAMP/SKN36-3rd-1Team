"""Issue #102: 정적 데이터 품질 목록의 데이터셋 버전별 선택·검증·S10 영향 항목 안전성.

DB·LLM·임베딩 호출 없음. 목록 파일은 임시 폴더에만 쓰고 저장소의 목록 JSON은 바꾸지 않는다.
가상 정책·보고서는 sample_only. 실제 릴리스 보고서를 쓰는 테스트는 메모리에서만 build_flags를 호출한다.

실행 (프로젝트 루트):
    .venv\\Scripts\\python.exe -m pytest tests/unit/test_data_quality_flags_versions.py -q
"""

import contextlib
import copy
import functools
import hashlib
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.eligibility.checker import check_eligibility  # noqa: E402
from database import data_quality_flags as dqf  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import hybrid_search as hs  # noqa: E402

V140 = "1.4.0-candidate"
LEGACY = "1.3.2"
SHA_A = "a" * 64
SHA_B = "b" * 64
RELEASE_NEO4J = PROJECT_ROOT / "data" / "releases" / "v1.4.0-candidate" / "neo4j"


@pytest.fixture(autouse=True)
def clear_cache():
    dqf._CACHE.clear()
    yield
    dqf._CACHE.clear()


@contextlib.contextmanager
def dataset(version, manifest=SHA_A):
    """현재 데이터셋 버전·manifest를 가상으로 바꾼다 (sample_only)."""
    with mock.patch.object(dqf.hd, "dataset_version", return_value=version), \
            mock.patch.object(dqf.fi, "manifest_sha256", return_value=manifest):
        yield


def write(folder, name, payload):
    path = Path(folder) / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def flag_file(version=V140, data_version=V140, flags_version="neo4j_semantic_flags_v1_4_0", manifest=SHA_A):
    return {"flags_version": flags_version, "data_version": data_version, "manifest_sha256": manifest,
            "flags_by_policy": {"POL_SAMPLE": [{"code": "EXCLUSION_NOT_MODELED", "affects": ["exclusion"]}]}}


def s10_finding(unmodeled, marriage=True, children=True, related=None, policy_id="POL_SAMPLE",
                incomplete=(), review=(), assessment=None):
    """v1.4.0 S10 v2 finding (sample_only). 표시하지 않은 측면은 근거 줄이 없으면 no_text, 있으면 consistent로 둔다
    (assessment를 주면 그 값을 그대로 쓴다)."""
    line = {"source": "DOC_SAMPLE_1", "char_start": 0, "line": "sample_only"}
    related = related if related is not None else {"marriage": [], "children": []}
    classes = {"unmodeled_dimensions": list(unmodeled) if unmodeled is not None else None,
               "incomplete_dimensions": list(incomplete), "review_dimensions": list(review)}
    has_text = {"marriage": marriage, "children": children}
    kinds = {d: k.split("_")[0] for k, dims in classes.items() for d in (dims or [])}
    default = {d: {"classification": kinds.get(d, "consistent" if has_text[d] else "no_text")} for d in dqf.S10_DIMENSIONS}
    evidence = {"marriage": [line] if marriage else [], "children": [line] if children else [],
                **classes, "graph_related_conditions": related,
                "dimension_assessment": assessment if assessment is not None else default}
    return {"policy_id": policy_id, "check": dqf.S10_CHECK, "severity": "low", "handoff_semantic_match": "not_modeled",
            "document_ids": ["DOC_SAMPLE_1"], "evidence": evidence}


def legacy_s10_finding():
    line = {"char_start": 0, "line": "sample_only"}
    return {"policy_id": "POL_SAMPLE", "check": dqf.S10_CHECK, "severity": "low", "handoff_semantic_match": "not_modeled",
            "document_ids": ["DOC_SAMPLE_1"], "evidence": {"marriage": [line], "children": []}}


def report(findings, version=V140, manifest=SHA_A, **extra):
    data = {"created_at": "sample_only", "findings": findings, "manifest_sha256": manifest,
            "s10_rule": dqf.S10_RULE, **extra}
    if version is not None:
        data["dataset_version"] = version
    return data


# --- 1. 버전별 선택 ---------------------------------------------------------------

def test_flag_sets_select_per_dataset_version():
    assert dqf.flag_set(V140)["flags_path"] == PROJECT_ROOT / "database/data_quality/neo4j_semantic_flags_v1_4_0.json"
    assert dqf.flag_set(LEGACY)["flags_path"] == PROJECT_ROOT / "database/data_quality/neo4j_semantic_flags_v1_3_2.json"
    assert dqf.flag_set("9.9.9") is None


def test_v140_file_applied_for_v140():
    with tempfile.TemporaryDirectory() as folder, dataset(V140):
        loaded = dqf.load_flags(write(folder, "f.json", flag_file()))
    assert loaded["status"] == "applied"
    assert loaded["flags_by_policy"]["POL_SAMPLE"]


def test_legacy_file_applied_only_for_legacy_with_version_alias():
    legacy = flag_file(data_version="v1.3.2-verified", flags_version="neo4j_semantic_flags_v1_3_2")
    with tempfile.TemporaryDirectory() as folder:
        path = write(folder, "legacy.json", legacy)
        with dataset(LEGACY):
            assert dqf.load_flags(path)["status"] == "applied"      # v1.3.2-verified ≡ manifest_version 1.3.2
        dqf._CACHE.clear()
        with dataset(V140):
            loaded = dqf.load_flags(path)                             # 같은 manifest여도 레거시 목록은 오적용 금지
    assert (loaded["status"], loaded["flags_by_policy"]) == ("stale", {})
    assert {"data_version_mismatch", "flags_version_mismatch"} <= set(loaded["reason"])


def test_v140_file_not_applied_to_legacy_dataset():
    with tempfile.TemporaryDirectory() as folder, dataset(LEGACY):
        loaded = dqf.load_flags(write(folder, "f.json", flag_file()))
    assert (loaded["status"], loaded["flags_by_policy"]) == ("stale", {})


def test_unsupported_version_not_applicable_even_with_valid_file():
    with tempfile.TemporaryDirectory() as folder, dataset("9.9.9"):
        loaded = dqf.load_flags(write(folder, "f.json", flag_file()))
    assert (loaded["status"], loaded["flags_by_policy"]) == ("not_applicable", {})


def test_missing_and_manifest_stale():
    with tempfile.TemporaryDirectory() as folder, dataset(V140, manifest=SHA_B):
        assert dqf.load_flags(Path(folder) / "none.json")["status"] == "missing"
        loaded = dqf.load_flags(write(folder, "f.json", flag_file(manifest=SHA_A)))
    assert (loaded["status"], loaded["flags_by_policy"], loaded["reason"]) == ("stale", {}, ["manifest_mismatch"])


# --- 2. 캐시 ---------------------------------------------------------------------

def test_cache_reflects_file_manifest_and_dataset_changes():
    with tempfile.TemporaryDirectory() as folder:
        path = write(folder, "f.json", flag_file())
        with dataset(V140):
            assert dqf.load_flags(path)["status"] == "applied"
        with dataset(V140, manifest=SHA_B):                           # manifest 변경
            assert dqf.load_flags(path)["status"] == "stale"
        with dataset(LEGACY):                                         # 데이터셋 변경
            assert dqf.load_flags(path)["status"] == "stale"
        with dataset(V140):
            assert dqf.load_flags(path)["status"] == "applied"
            write(folder, "f.json", {**flag_file(manifest=SHA_B), "padding": "x" * 40})   # 파일 변경
            assert dqf.load_flags(path)["status"] == "stale"
        assert len([k for k in dqf._CACHE if k[0] == str(path)]) == 1


# --- 3. build_flags 보고서 검증 ----------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"version": LEGACY},                       # 다른 데이터셋 보고서
    {"version": None},                         # v1.4.0인데 dataset_version 없음
    {"manifest": SHA_B},                       # 다른 manifest 기준 보고서 (현재 manifest로 덮어쓰지 않음)
    {"official_source_checked": True},
    {"s10_rule": None},                        # Phase 1 규칙 보고서 (관련 조건 보유 정책 S10 누락)
    {"s10_rule": "S10_v2_unmodeled_incomplete_review"},   # v2 보고서 (그래프 추가 연령 범위를 consistent로 분류 가능)
])
def test_build_flags_rejects_report_from_other_data(kwargs):
    with dataset(V140), pytest.raises(dqf.FlagBuildError):
        dqf.build_flags(report([], **kwargs), "0" * 64)


def test_legacy_report_without_dataset_version_only_for_legacy():
    with dataset(LEGACY):
        flags = dqf.build_flags(report([legacy_s10_finding()], version=None, s10_rule=None), "0" * 64)
    assert flags["data_version"] == "v1.3.2-verified"
    assert "report_finding_index" not in flags["flags_by_policy"]["POL_SAMPLE"][0]
    assert flags["flags_by_policy"]["POL_SAMPLE"][0]["affects"] == ["marriage", "children"]   # 레거시 동작 유지


def test_write_flags_refuses_mismatched_report_without_writing():
    with tempfile.TemporaryDirectory() as folder, dataset(V140):
        source = write(folder, "report.json", report([], manifest=SHA_B))
        out = Path(folder) / "out.json"
        with pytest.raises(dqf.FlagBuildError):
            dqf.write_flags(source, out)
        assert not out.exists()


def test_build_flags_metadata_and_evidence_link():
    finding = s10_finding(["marriage"], children=False)
    other = {"policy_id": "POL_SAMPLE", "check": "S6_EMPLOYMENT_MISSING", "severity": "medium",
             "handoff_semantic_match": "suspected", "document_ids": ["DOC_SAMPLE_1", "DOC_SAMPLE_2"],
             "evidence": {"eligibility_attributes.employment_status_by_document": [
                 {"document_id": "DOC_SAMPLE_1", "source_type": "pdf", "attribute_source": None, "employment_status": ["재직자"]},
                 {"document_id": "DOC_SAMPLE_2", "source_type": "faq", "attribute_source": "faq",
                  "employment_status": "attribute_absent"}]}}
    with tempfile.TemporaryDirectory() as folder, dataset(V140):
        source = write(folder, "report.json", report([{"check": "S9_REGION_GRANULARITY", "policy_id": "POL_SAMPLE"},
                                                      finding, other]))
        flags = dqf.write_flags(source, Path(folder) / "out.json")
        saved = json.loads((Path(folder) / "out.json").read_text(encoding="utf-8"))
        source_sha = hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert saved == flags
    assert (flags["flags_version"], flags["data_version"], flags["manifest_sha256"]) == (
        "neo4j_semantic_flags_v1_4_0", V140, SHA_A)
    assert (flags["source_report_sha256"], flags["source_report_dataset_version"],
            flags["source_report_manifest_sha256"]) == (source_sha, V140, SHA_A)
    assert flags["official_source_checked"] is False and "확정된 정책 오류가 아니며" in flags["disclaimer"]
    assert (flags["policies"], flags["flag_count"]) == (1, 2)
    entries = flags["flags_by_policy"]["POL_SAMPLE"]
    assert [e["report_finding_index"] for e in entries] == [1, 2]
    assert entries[1]["evidence"] == other["evidence"]                 # 문서별 근거·source 그대로
    assert entries[1]["document_ids"] == other["document_ids"]
    assert all(e["official_source_checked"] is False for e in entries)


# --- 4. S10 affects 안전성 ----------------------------------------------------------

CHILD_REL = {"marriage": [], "children": ["COND_CHILD_SAMPLE"]}
MARRIAGE_REL = {"marriage": ["COND_TYPE_SAMPLE_NEWLYWED"], "children": []}


def _drop(key):
    finding = s10_finding(["marriage"], children=False)
    del finding["evidence"][key]
    return finding


def _set(finding, path, value):
    target = finding["evidence"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return finding


@pytest.mark.parametrize("finding", [
    _drop("unmodeled_dimensions"),                                         # 키 없음 (Phase 1 이전 형식)
    _drop("incomplete_dimensions"),                                        # v2 필드 없음
    _drop("review_dimensions"),
    _drop("dimension_assessment"),
    s10_finding([], children=False, marriage=False),                       # 표시 측면 없음
    s10_finding(["spouse"]),                                               # 허용되지 않은 값
    s10_finding(["marriage", "marriage"], children=False),                 # 중복
    s10_finding(["marriage"], review=["marriage"], children=False),        # 분류 겹침
    s10_finding(["children"], children=False),                             # 근거 없는 표시 측면
    s10_finding(["marriage"], children=False, related={"marriage": []}),   # 관련 조건 형식 오류
    s10_finding(["children"], marriage=False, related=CHILD_REL),          # 관련 조건 있는데 unmodeled
    s10_finding([], incomplete=["children"], marriage=False),              # 관련 조건 없이 incomplete
    _set(s10_finding(["marriage"], children=False), ["dimension_assessment", "marriage", "classification"],
         "consistent"),                                                    # 분류 불일치
    _set(s10_finding(["marriage"], children=True, related=CHILD_REL),
         ["dimension_assessment", "children", "classification"], "no_text"),   # 근거 줄 있는데 no_text
    _set(s10_finding(["marriage"]), ["dimension_assessment", "children", "classification"],
         "consistent"),                                                    # 관련 조건 없이 consistent
])
def test_s10_invalid_fields_raise_instead_of_dropping_protection(finding):
    with dataset(V140), pytest.raises(dqf.FlagBuildError):
        dqf.build_flags(report([finding]), "0" * 64)


@pytest.mark.parametrize("finding, expected", [
    # 관련 조건 없음 + 원문 근거 → 미모델링
    (s10_finding(["marriage"], children=False), ["marriage"]),
    (s10_finding(["children"], marriage=False), ["children"]),
    (s10_finding(["marriage", "children"]), ["marriage", "children"]),
    # 관련 조건은 있으나 세부 요건(자녀 수 등) 누락 → 불완전 모델링도 보호
    (s10_finding([], incomplete=["children"], marriage=False, related=CHILD_REL), ["children"]),
    (s10_finding(["marriage"], incomplete=["children"], related=CHILD_REL), ["marriage", "children"]),
    # 충분성 판단 불가 → 검토 대상도 보호 (POL_001 형태: 혼인 관련 조건 + 자녀 미모델링)
    (s10_finding(["children"], review=["marriage"], related=MARRIAGE_REL), ["marriage", "children"]),
    # 명시적 consistent(세부 요건 없음 + 자녀 연령 일치)만 보호에서 제외
    (s10_finding(["marriage"], children=True, related=CHILD_REL), ["marriage"]),
    # 근거 줄이 없어도 그래프 자녀 조건이 있으면 자녀 보호 유지
    (s10_finding(["marriage"], children=False, related=CHILD_REL), ["marriage", "children"]),
])
def test_s10_affects_cover_unmodeled_incomplete_review(finding, expected):
    with dataset(V140):
        entry = dqf.build_flags(report([finding]), "0" * 64)["flags_by_policy"]["POL_SAMPLE"][0]
    assert (entry["code"], entry["affects"]) == ("MARRIAGE_CHILD_NOT_MODELED", expected)


def _sample_entry(conditions, quality):
    root = {"group_id": "ROOT", "logic": "AND", "groups": [], "conditions": conditions}
    return {"policy_id": "POL_SAMPLE", "graph_status": "FOUND",
            "graph": {"policy": {"application_period": "상시신청"},
                      "eligibility": {"condition_tree": [root], "unknown_logic_groups": [], "structure_issues": []}},
            "data_quality": quality}


AGE = {"type": "AGE", "operator": "BETWEEN", "value": "19~35", "condition_id": "COND_AGE_SAMPLE"}
CHILD = {"type": "SPECIAL_TARGET", "unit": "개월", "operator": "LT", "value": "24", "condition_id": "COND_CHILD_AGE_UNDER_24M"}
PROFILE = {"age": 25, "region": None, "income": None, "child_age_months": 10}   # sample_only


def _rows(conditions, affects):
    flags = [] if affects is None else [{"code": "MARRIAGE_CHILD_NOT_MODELED", "evidence_level": "keyword_detected",
                                         "severity": "low", "handoff_semantic_match": "not_modeled",
                                         "affects": affects, "evidence": {}}]
    quality = hs.assess_policy_data_quality({"policy": {}}, flags)
    result = check_eligibility(PROFILE, _sample_entry(conditions, quality))
    return [(row["field"], row["status"]) for row in result["conditions"]]


def test_s10_children_scope_holds_child_condition_but_not_age():
    assert ("children", "eligible") in _rows([AGE, CHILD], None)
    rows = _rows([AGE, CHILD], ["marriage", "children"])
    assert ("children", "needs_check") in rows
    assert ("age", "eligible") in rows                                 # 정상 조건 불필요 보류 없음


def test_s10_review_child_age_reaches_checker_hold():
    # v3에서 review가 된 자녀 연령(예: 그래프에만 추가 연령 범위) → children 보호 → 자녀 행 needs_check, 나이 행은 유지
    finding = s10_finding([], review=["children"], marriage=False, related=CHILD_REL)
    with dataset(V140):
        entry = dqf.build_flags(report([finding]), "0" * 64)["flags_by_policy"]["POL_SAMPLE"][0]
    assert entry["affects"] == ["children"]
    rows = _rows([AGE, CHILD], entry["affects"])
    assert ("children", "needs_check") in rows and ("age", "eligible") in rows


def test_s10_narrowed_scope_equals_legacy_when_no_child_condition():
    # 자녀 측면을 뺄 수 있는 경우(근거 줄·관련 조건 모두 없음)의 Checker 결과는 레거시 두 측면과 같다
    assert _rows([AGE], ["marriage"]) == _rows([AGE], ["marriage", "children"])


def test_release_child_month_conditions_are_counted_as_related():
    # Checker가 children 필드로 보는 조건(SPECIAL_TARGET·개월)은 모두 COND_CHILD_* → S10 관련 조건으로 잡힌다
    rows = [json.loads(line) for line in (RELEASE_NEO4J / "nodes_eligibility_condition.jsonl").open(encoding="utf-8")
            if line.strip()]
    months = [r["condition_id"] for r in rows
              if str(r.get("type")).upper() == "SPECIAL_TARGET" and str(r.get("unit") or "").strip() == "개월"]
    assert months and all(cid.startswith("COND_CHILD_") for cid in months)


def test_unknown_still_holds_all_with_static_flags():
    entry = _sample_entry([AGE], None)
    entry["graph"]["eligibility"]["unknown_logic_groups"] = ["ROOT"]
    entry["graph"]["eligibility"]["condition_tree"][0]["logic"] = "UNKNOWN"
    flags = [{"code": "MARRIAGE_CHILD_NOT_MODELED", "evidence_level": "keyword_detected", "severity": "low",
              "handoff_semantic_match": "not_modeled", "affects": ["marriage"], "evidence": {}}]
    entry["data_quality"] = hs.assess_policy_data_quality(
        {"policy": {}, "eligibility": entry["graph"]["eligibility"]}, flags)
    result = check_eligibility(PROFILE, entry)
    assert result["conditions"] and all(row["status"] == "needs_check" for row in result["conditions"])


def _child_status(condition, months):
    profile = {"age": 30, "region": None, "income": None, "child_age_months": months}   # sample_only
    rows = check_eligibility(profile, _sample_entry([condition], {"hold_auto_eligibility": False}))["conditions"]
    return [row["status"] for row in rows if row["field"] == "children"]


# POL_GOV_050 형태 (PR #96 조례 기준 교정): COND_CHILD_AGE_LE_24M, SPECIAL_TARGET, LTE 24개월
CHILD_LE_24 = {"type": "SPECIAL_TARGET", "unit": "개월", "operator": "LTE", "value": "24",
               "condition_id": "COND_CHILD_AGE_LE_24M"}


@pytest.mark.parametrize("months, expected", [
    (0, "eligible"), (23, "eligible"), (24, "eligible"),
    (25, "ineligible"), (None, "needs_check"),
])
def test_gov050_lte_child_months_boundary_without_quality_hold(months, expected):
    # PR #112 adds LTE support: 24 months is included, 25 is not.
    assert _child_status(CHILD_LE_24, months) == [expected]


@pytest.mark.parametrize("months", [0, 23, 24, 25, None])
def test_gov050_lte_quality_hold_overrides_automatic_month_comparison(months):
    flags = [{"code": "MARRIAGE_CHILD_NOT_MODELED", "evidence_level": "keyword_detected",
              "severity": "low", "handoff_semantic_match": "not_modeled",
              "affects": ["children"], "evidence": {}}]
    quality = hs.assess_policy_data_quality({"policy": {}}, flags)
    profile = {**PROFILE, "child_age_months": months}
    result = check_eligibility(profile, _sample_entry([AGE, CHILD_LE_24], quality))
    rows = [(row["field"], row["status"]) for row in result["conditions"]]
    assert ("children", "needs_check") in rows
    assert ("age", "eligible") in rows


@pytest.mark.parametrize("months, expected", [(23, "eligible"), (24, "ineligible"), (25, "ineligible"),
                                              (None, "needs_check")])
def test_lt_child_months_boundary_without_quality_hold(months, expected):
    # 정상 자녀 월령 조건(LT 24, sample_only)은 품질 경고가 없으면 불필요하게 보류하지 않는다
    assert _child_status(CHILD, months) == [expected]


# --- 5. 실제 v1.4.0 보고서 (메모리 생성, 파일 쓰기 없음) ------------------------------

@pytest.fixture(scope="module")
def release_flags():
    assert hd.dataset_version() == V140, "기본 데이터셋(1.4.0-candidate)에서 실행해야 한다"
    path = dqf.FLAG_SETS[V140]["report_path"]
    data = path.read_bytes()
    report_data = json.loads(data.decode("utf-8"))
    flags = dqf.build_flags(copy.deepcopy(report_data), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
                            report_path=path)
    return report_data, flags


def test_release_flags_link_to_report_findings(release_flags):
    report_data, flags = release_flags
    assert (flags["data_version"], flags["manifest_sha256"]) == (V140, report_data["manifest_sha256"])
    for policy_id, entries in flags["flags_by_policy"].items():
        for entry in entries:
            finding = report_data["findings"][entry["report_finding_index"]]
            assert (finding["policy_id"], finding["check"]) == (policy_id, entry["check"])
            assert (finding["evidence"], finding["document_ids"]) == (entry["evidence"], entry["document_ids"])


def _affects(flags, policy_id):
    return {a for entry in flags["flags_by_policy"].get(policy_id, []) for a in entry.get("affects", [])}


def test_issue102_representative_policies(release_flags):
    _report, flags = release_flags
    assert {"marriage", "children"} <= _affects(flags, "POL_001")       # 혼인 관련 조건 있어도 보호 유지
    for policy_id in ("POL_GOV_008", "POL_GOV_009", "POL_GOV_010", "POL_GOV_022"):
        assert "children" in _affects(flags, policy_id), policy_id
    assert {"age", "marriage"} <= _affects(flags, "POL_YC_003")
    assert "age" not in _affects(flags, "POL_GOV_011")                  # AGE 12~24 원문 대조 교정


def _root_unknown(policy_id):
    tree = _release_eligibility(policy_id)["condition_tree"]
    return bool(tree) and tree[0].get("logic") == "UNKNOWN"


@pytest.mark.parametrize("policy_id", [
    "POL_STG_EX_001",              # 자녀 연령 조건만 있고 자녀 수·동일 등본 요건 없음
    "POL_BOK_001",                 # 복수 자녀·서비스별 연령·미숙아 예외
    "POL_BOK_068", "POL_BOK_069",  # 만 12세 이하이나 초등학생 연령 초과 허용
])
def test_s10_v2_keeps_child_protection_for_related_but_incomplete(release_flags, policy_id):
    # 관련 자녀 조건이 있어도 보호를 풀지 않는다: S10 children 보호 또는 root UNKNOWN 전체 보류 중 하나는 있어야 한다
    _report, flags = release_flags
    assert "children" in _affects(flags, policy_id) or _root_unknown(policy_id), policy_id


def test_s10_findings_follow_v2_classification(release_flags):
    report_data, _flags = release_flags
    assert report_data["s10_rule"] == dqf.S10_RULE
    for finding in report_data["findings"]:
        if finding["check"] != dqf.S10_CHECK:
            continue
        evidence = finding["evidence"]
        for dim in dqf.S10_DIMENSIONS:
            kind = evidence["dimension_assessment"][dim]["classification"]
            if kind in ("incomplete", "review", "consistent") and evidence["dimension_assessment"][dim].get("requirement_lines"):
                for line in evidence["dimension_assessment"][dim]["requirement_lines"]:
                    assert {"source", "char_start", "line"} <= set(line)         # 근거 문서·줄 보존
        assert "확정된 정책 오류 아님" in evidence["note"]


def test_root_unknown_policies_hold_all_without_static_flags():
    # 정적 목록과 무관하게 root UNKNOWN 정책은 Checker가 모든 행을 needs_check로 둔다 (S10은 이 정책에 만들지 않음)
    _groups, _conditions, _relations, policies = _release_graph()
    checked = 0
    for policy_id in policies:
        if not _root_unknown(policy_id):
            continue
        eligibility = _release_eligibility(policy_id)
        quality = hs.assess_policy_data_quality({"policy": {}, "eligibility": eligibility})
        entry = {"policy_id": policy_id, "graph_status": "FOUND",
                 "graph": {"policy": {"application_period": "상시신청"}, "eligibility": eligibility},
                 "data_quality": quality}
        rows = check_eligibility({"age": 30, "region": "서울특별시", "child_age_months": 10}, entry)["conditions"]
        assert rows and all(row["status"] == "needs_check" for row in rows), policy_id
        checked += 1
    assert checked > 0


def _read_release(name):
    with (RELEASE_NEO4J / name).open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


@functools.lru_cache(maxsize=1)
def _release_graph():
    groups = {row["group_id"]: row for row in _read_release("nodes_condition_group.jsonl")}
    conditions = {row["condition_id"]: row for row in _read_release("nodes_eligibility_condition.jsonl")}
    relations = defaultdict(list)
    for row in _read_release("relationships.jsonl"):
        relations[row["source_id"]].append(row)
    policies = [row["policy_id"] for row in _read_release("nodes_policy.jsonl")]
    return groups, conditions, relations, policies


def _release_eligibility(policy_id):
    groups, conditions, relations, _policies = _release_graph()
    rows = {gid: {"group": g,
                  "child_groups": [{"group_id": e["target_id"], "relationship": e}
                                   for e in relations[gid] if e["relation"] == "CONTAINS_GROUP"],
                  "conditions": [{"condition": conditions[e["target_id"]], "relationship": e}
                                 for e in relations[gid] if e["relation"] == "CONTAINS_CONDITION"]}
            for gid, g in groups.items() if g["policy_id"] == policy_id}
    roots = [e for e in relations[policy_id] if e["relation"] == "HAS_ROOT_CONDITION_GROUP"]
    if len(roots) != 1:      # fullscan과 같이: 루트 누락·중복은 구조 오류로 둔다 (전체 보류)
        return {"condition_tree": [], "unknown_logic_groups": [], "structure_issues": ["invalid_root_count"]}
    issues = []
    root = hs._build_condition_tree(policy_id, roots[0]["target_id"], rows, roots[0], issues)
    unknown = []

    def collect(group):
        if group.get("logic") == "UNKNOWN":
            unknown.append(group.get("group_id"))
        for child in group.get("groups", []):
            collect(child)

    collect(root)
    return {"condition_tree": [root], "unknown_logic_groups": sorted(unknown), "structure_issues": issues}


@pytest.mark.parametrize("policy_id", ["POL_001", "POL_GOV_008", "POL_GOV_009", "POL_GOV_010", "POL_GOV_022",
                                       "POL_YC_003"])
def test_issue102_affected_rows_need_check_and_others_unchanged(release_flags, policy_id):
    _report, flags = release_flags
    eligibility = _release_eligibility(policy_id)
    profile = {"age": 30, "region": "서울특별시", "child_age_months": 10}   # sample_only

    def run(static):
        quality = hs.assess_policy_data_quality({"policy": {}, "eligibility": eligibility}, static)
        entry = {"policy_id": policy_id, "graph_status": "FOUND",
                 "graph": {"policy": {"application_period": "상시신청"}, "eligibility": eligibility},
                 "data_quality": quality}
        return quality, check_eligibility(profile, entry)["conditions"]

    quality, with_flags = run(flags["flags_by_policy"].get(policy_id, []))
    _quality, without = run([])
    affected = set(quality["affected_attributes"])
    holds_all = bool(eligibility["unknown_logic_groups"]) and "all" in hs.assess_policy_data_quality(
        {"policy": {}, "eligibility": eligibility})["affected_attributes"]
    for row in with_flags:
        if holds_all or row["field"] in affected:
            assert row["status"] == "needs_check", (policy_id, row)
    # 영향 항목이 아닌 개별 조건(eligibility 묶음 행 제외)은 정적 목록 적용 전후 판정이 같다
    unaffected_before = [(r["field"], r["status"]) for r in without if r["field"] not in affected | {"eligibility"}]
    unaffected_after = [(r["field"], r["status"]) for r in with_flags if r["field"] not in affected | {"eligibility"}]
    assert unaffected_after == unaffected_before
