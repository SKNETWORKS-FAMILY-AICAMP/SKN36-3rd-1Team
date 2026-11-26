"""sample_only 릴리스로 목록·캐시·원문 추적·선택 정책 연결을 검증. DB/LLM 호출 없음."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.policy_catalog import FILES, PolicyCatalog
from backend.api.schemas import AskRequest
from backend.eligibility.checker import KST, _reception_status, application_schedule
from backend.rag.pipeline import run_ask_pipeline
from backend.rag.source_sections import application_details
from test_rag_pipeline import policy, checker, answer


def write_package(root, version="sample_only"):
    names = ["POL_A", "POL_B", "POL_C", "POL_D", "POL_E"]
    dates = [{"application_start": "2026-11-01", "application_end": "2026-11-25"},
             {"application_start": "2026-11-25", "application_end": "2026-11-30"},
             {"application_is_ongoing": True}, {},
             {"application_start": "2026-11-01", "application_end": "2026-11-21"}]
    tables = {name: [] for name in FILES}
    tables[FILES[0]] = [{"policy_id": pid, "policy_name": pid, "agency": "sample_only", "source_url": "https://example.org/notice", "official_apply_url": None, **date} for pid, date in zip(names, dates)]
    tables[FILES[1]] = [{"region_id": "R", "name": "서울"}]
    tables[FILES[2]] = [{"category_id": "C", "name": "주거"}]
    tables[FILES[3]] = [{"document_id": f"DOC_{pid}", "policy_id": pid} for pid in names]
    tables[FILES[4]] = [{"source_id": pid, "target_id": target, "relation": relation} for pid in names for target, relation in [("R", "AVAILABLE_IN"), ("C", "HAS_CATEGORY")]]
    tables[FILES[5]] = [{"policy_id": pid, "document_id": f"DOC_{pid}", "chunk_id": f"CHK_{pid}", "source_url": "https://example.org/notice",
        "text": "[구비서류]\n신청서 (sample_only)\n[지원대상]\n2025년 기준 100% 이하 (sample_only)\n[문의처]\n기관 (129)"} for pid in names]
    manifest = {"manifest_version": version, "summary": {"total_policies": len(names)}, "files": {}}
    for name, rows in tables.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        data = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
        path.write_bytes(data); manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest()}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def package(tmp_path):
    write_package(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def today():
    with patch("backend.eligibility.checker.datetime", wraps=datetime) as clock, patch("backend.api.policy_catalog.datetime", wraps=datetime) as catalog_clock:
        clock.now.return_value = catalog_clock.now.return_value = datetime(2026, 11, 22, 12, tzinfo=KST)
        yield


def test_catalog_pagination_filters_and_no_network(package):
    session = Mock()
    generator = Mock(side_effect=AssertionError("LLM must not run"))
    with TestClient(create_app(session_factory=lambda: session, generate_fn=generator,
                               catalog_factory=lambda: PolicyCatalog(package))) as client:
        response = client.get("/api/policies")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5 and body["source_kind"] == "release_files"
        assert body["dataset_version"] == "sample_only" and len(body["manifest_sha256"]) == 64
        assert body["evaluated_on"] == "2026-11-22"
        assert [p["policy_id"] for p in body["items"]] == ["POL_A", "POL_B", "POL_C", "POL_D", "POL_E"]
        a = body["items"][0]
        assert a["application_schedule"]["days_to_end"] == 3
        assert a["required_documents"][0]["text"] == "신청서 (sample_only)"
        assert a["requirement_excerpts"][0]["text"] == "2025년 기준 100% 이하 (sample_only)"
        assert a["requirement_excerpts"][0]["label"] == "지원대상"
        assert a["requirement_excerpts"][0]["document_id"] == "DOC_POL_A"
        # 검수된 공식 기준(#76) 자리는 목록에 없다. 원문 발췌와 섞지 않는다.
        assert "official_requirements" not in a
        assert "conditions" not in a
        assert client.get("/api/policies?status=soon").json()["total"] == 1
        assert client.get("/api/policies?status=ongoing").json()["items"][0]["policy_id"] == "POL_C"
        assert client.get("/api/policies?region=부산").json()["total"] == 0
        assert client.get("/api/policies?category=주거&q=pol_a").json()["total"] == 1
        assert client.get("/api/policies?offset=1&limit=2").json()["total"] == 5
        assert len(client.get("/api/policies?offset=1&limit=2").json()["items"]) == 2
        for query in ["limit=0", "limit=501", "offset=-1", "status=bad", "sort=bad"]:
            assert client.get("/api/policies?" + query).status_code == 422
        invalid = client.post("/api/ask", json={"question": "조건 확인", "profile": {"age": None, "region": "", "income": None}, "history": [], "selected_policy_id": "../../secret"})
        assert invalid.status_code == 422
    session.search_policy_context.assert_not_called(); generator.assert_not_called(); session.close.assert_called_once()


def test_cache_reads_once_reloads_changed_data_and_rejects_partial_update(package):
    catalog = PolicyCatalog(package)
    original = Path.read_bytes
    with patch.object(Path, "read_bytes", autospec=True, side_effect=original) as read:
        catalog.list(); count = read.call_count
        assert count == len(FILES) + 1
        catalog.list(q="A"); catalog.list(status="soon")
        assert read.call_count == count
    write_package(package, "sample_only_v2")
    assert catalog.list().dataset_version == "sample_only_v2"
    (package / FILES[0]).write_text('{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="해시 불일치"):
        catalog.list()


def test_catalog_error_is_safe_and_does_not_serve_old_cache(package):
    catalog = PolicyCatalog(package); catalog.list()
    (package / FILES[0]).unlink()
    with TestClient(create_app(session_factory=Mock, catalog_factory=lambda: catalog)) as client:
        response = client.get("/api/policies")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "POLICY_CATALOG_UNAVAILABLE"
        assert str(package) not in response.text


def test_new_day_recalculates_schedule_without_reparsing(package):
    catalog = PolicyCatalog(package)
    assert catalog.list(status="soon").items[0].application_schedule.days_to_end == 3
    with patch("backend.eligibility.checker.datetime", wraps=datetime) as clock, patch("backend.api.policy_catalog.datetime", wraps=datetime) as outer:
        clock.now.return_value = outer.now.return_value = datetime(2026, 11, 26, tzinfo=KST)
        result = catalog.list()
        assert next(p for p in result.items if p.policy_id == "POL_A").reception_status == "closed"
        assert result.evaluated_on == "2026-11-26"


def test_catalog_uses_one_evaluation_day_across_midnight(package):
    before_midnight = datetime(2026, 11, 25, 23, 59, 59, tzinfo=KST)
    after_midnight = datetime(2026, 11, 26, tzinfo=KST)
    with patch("backend.api.policy_catalog.datetime", wraps=datetime) as catalog_clock, patch(
        "backend.eligibility.checker.datetime", wraps=datetime
    ) as checker_clock:
        catalog_clock.now.side_effect = [before_midnight, after_midnight]
        checker_clock.now.return_value = after_midnight
        result = PolicyCatalog(package).list(status="soon")

    catalog_clock.now.assert_called_once_with(KST)
    checker_clock.now.assert_not_called()
    assert result.evaluated_on == "2026-11-25"
    assert [item.policy_id for item in result.items] == ["POL_A", "POL_B"]
    first = result.items[0]
    assert first.reception_status == "open"
    assert first.application_schedule.evaluated_on == result.evaluated_on
    assert first.application_schedule.days_to_end == 0


def test_concurrent_first_requests_share_one_file_load(package):
    from concurrent.futures import ThreadPoolExecutor
    catalog = PolicyCatalog(package)
    original = Path.read_bytes
    with patch.object(Path, "read_bytes", autospec=True, side_effect=original) as read:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: catalog.list(), range(2)))
        assert [result.total for result in results] == [5, 5]
        assert read.call_count == len(FILES) + 1


def test_selected_policy_one_search_no_other_card_and_no_extra_generation():
    selected, other = policy("POL_A"), policy("POL_B")
    selected["graph"]["policy"].update(application_start="2026-11-01", application_end="2026-11-25")
    selected["evidence"][0]["text"] = "[구비서류]\n신청서\n[지원대상]\n2025년 기준\n[문의처]\n기관 (129)"
    search = Mock(return_value={"policies": [selected, other]}); generate = Mock(return_value=answer())
    request = AskRequest(question="조건 확인", profile={"age": 25, "region": "서울", "income": None}, history=[], selected_policy_id="POL_A")
    result = run_ask_pipeline(request, search_fn=search, check_fn=checker, generate_fn=generate)
    # 선택 정책은 지역 필터 없이 정책 ID로만 찾는다. 지역은 Checker가 판정한다.
    search.assert_called_once_with("조건 확인", filters={"policy_id": "POL_A"}, k=10)
    generate.assert_called_once()
    assert [p.policy_id for p in result.policy_checks] == ["POL_A"]
    assert result.policy_checks[0].required_documents[0].text == "신청서"
    assert result.policy_checks[0].requirement_excerpts[0].text == "2025년 기준"
    assert result.policy_checks[0].official_requirements is None


def _regional_policy(pid, region_names):
    """sample_only: 실제 검색 결과처럼 지역 제한은 조건 트리가 아니라 graph.regions(AVAILABLE_IN)에만 있다."""
    from backend.eligibility.checker import check_eligibility
    item = policy(pid)
    item["graph"]["regions"] = [{"region_id": f"R{i}", "name": name} for i, name in enumerate(region_names)]
    item["graph"]["eligibility"] = {"condition_tree": [{"group_id": "G", "logic": "AND", "groups": [],
        "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~34"}]}],
        "unknown_logic_groups": [], "structure_issues": []}
    return item, check_eligibility


def _ask_selected(item, check_fn, region, selected=True):
    def search(question, filters=None, k=10):
        # 검색 계층처럼 지역 필터가 있으면 다른 지역 정책을 걸러 낸다.
        if filters and "region" in filters and filters["region"] not in {r["name"] for r in item["graph"]["regions"]}:
            return {"policies": []}
        return {"policies": [item]}
    generate = Mock(return_value=answer())
    request = AskRequest(question="신청할 수 있나요?", profile={"age": 25, "region": region, "income": None},
                         history=[], selected_policy_id=item["policy_id"] if selected else None)
    return run_ask_pipeline(request, search_fn=search, check_fn=check_fn, generate_fn=generate), generate


def test_selected_policy_outside_profile_region_is_held_not_hidden():
    """부산 사용자가 서울 한정 정책을 고르면 근거 없음으로 숨기지 않고, 지역 확인 필요와 종합 보류로 보여 준다."""
    item, check_fn = _regional_policy("POL_A", ["서울특별시"])
    result, generate = _ask_selected(item, check_fn, "부산")
    assert result.status != "no_evidence" and [p.policy_id for p in result.policy_checks] == ["POL_A"]
    rows = {c.field: c for c in result.policy_checks[0].conditions}
    assert rows["region"].status == "needs_check"
    assert "서울특별시" in rows["region"].reason and "부산광역시" in rows["region"].reason
    assert rows["age"].status == "eligible"  # 지역과 무관한 판정은 그대로 둔다.
    assert rows["eligibility"].status == "needs_check"  # 나이만 맞아도 종합 충족으로 안내하지 않는다.
    # LLM도 같은 보류 결과를 받는다.
    sent = generate.call_args.args[2][0]["conditions"]
    assert any(row["field"] == "region" and row["status"] == "needs_check" for row in sent)


@pytest.mark.parametrize("names, region", [(["서울특별시"], "서울"), (["전국"], "부산"), (["서울특별시"], "")])
def test_selected_policy_region_match_nationwide_or_missing_input_is_unchanged(names, region):
    item, check_fn = _regional_policy("POL_A", names)
    result, _ = _ask_selected(item, check_fn, region)
    rows = {c.field: c for c in result.policy_checks[0].conditions}
    assert "region" not in rows and rows["eligibility"].status == "eligible"


def test_selected_region_hold_keeps_checker_needs_check_reason():
    """계약 §4.3: Checker가 needs_check로 준 지역 사유는 지우거나 바꾸지 않고, 지역 불일치 행만 따로 더한다."""
    item, check_fn = _regional_policy("POL_A", ["서울특별시"])
    item["data_quality"] = {"hold_auto_eligibility": True, "affected_attributes": ["region"]}
    raw = check_fn({"age": 25, "region": "부산광역시", "income": None}, item)
    original = [row["reason"] for row in raw["conditions"] if row["field"] == "region"]
    result, _ = _ask_selected(item, check_fn, "부산")
    region = [c for c in result.policy_checks[0].conditions if c.field == "region"]
    assert original and all(reason in [c.reason for c in region] for reason in original)
    assert any("서울특별시" in c.reason and "부산광역시" in c.reason for c in region)
    assert all(c.status == "needs_check" for c in region)


def test_selected_region_hold_downgrades_eligible_region_and_appends_reason():
    item, _ = _regional_policy("POL_A", ["서울특별시"])

    def check_fn(profile, policy_item):
        return {"policy_id": policy_item["policy_id"], "reception_status": "open", "conditions": [
            {"field": "region", "label": "거주 지역", "reason": "가상 지역 조건 충족 (sample_only).", "status": "eligible"},
            {"field": "eligibility", "label": "종합", "reason": "가상 종합 결과 (sample_only).", "status": "eligible"}]}

    result, _ = _ask_selected(item, check_fn, "부산")
    rows = [c for c in result.policy_checks[0].conditions]
    region = [c for c in rows if c.field == "region"]
    assert len(region) == 1 and region[0].status == "needs_check"
    assert region[0].reason.startswith("가상 지역 조건 충족 (sample_only).") and "부산광역시" in region[0].reason
    root = [c for c in rows if c.field == "eligibility"][-1]
    assert root.status == "needs_check" and root.reason.startswith("가상 종합 결과 (sample_only).")


def test_region_hold_applies_only_to_selected_policy_requests():
    # 선택 정책이 아닌 기존 상담은 지역 필터로 이미 걸러지므로 판정 결과를 바꾸지 않는다.
    item, check_fn = _regional_policy("POL_A", ["서울특별시", "부산광역시"])
    result, _ = _ask_selected(item, check_fn, "부산", selected=False)
    rows = {c.field: c for c in result.policy_checks[0].conditions}
    assert "region" not in rows and rows["eligibility"].status == "eligible"


def test_selected_policy_absent_does_not_answer_another_policy():
    generate = Mock()
    request = AskRequest(question="조건 확인", profile={"age": None, "region": "", "income": None}, history=[], selected_policy_id="POL_A")
    result = run_ask_pipeline(request, search_fn=lambda *a, **k: {"policies": [policy("POL_B")]}, check_fn=checker, generate_fn=generate)
    assert result.status == "no_evidence" and not result.policy_checks
    generate.assert_not_called()


def test_schedule_reuses_checker_and_unknown_is_not_invented():
    for node in [{}, {"application_start": "2026-02-30"}, {"application_end": "2026-11-25"},
                 {"application_is_ongoing": True, "application_end": "2026-11-25"}]:
        p = {"graph": {"policy": node}}
        assert _reception_status(p) == "unknown" and application_schedule(p, "unknown") is None
    p = {"graph": {"policy": {"application_start": " ", "application_is_ongoing": True}}}
    assert application_schedule(p, "open")["start_date"] is None


def test_source_sections_exact_blocks_preserve_units_and_do_not_guess():
    contexts = [{"document_id": "D", "chunk_id": "C", "url": "https://example.org/", "text": "설명 중 서류가 필요합니다. 소득은 50%"}]
    assert application_details(contexts) == {"required_documents": None, "requirement_excerpts": None}
    contexts[0]["text"] = "[제출서류]\n2025년 서류 A\n[지원대상]\n월 50만원 이하\n[문의처]\n기관 129"
    result = application_details(contexts + contexts)
    assert len(result["required_documents"]) == 1
    assert result["requirement_excerpts"] == [{"label": "지원대상", "text": "월 50만원 이하", "document_id": "D",
                                               "chunk_id": "C", "source_url": "https://example.org/"}]
    assert "official_requirements" not in result
    assert "문의처" not in result["required_documents"][0]["text"]
