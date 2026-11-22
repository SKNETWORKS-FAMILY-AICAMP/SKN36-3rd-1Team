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
        assert a["official_requirements"][0]["value"] == "2025년 기준 100% 이하 (sample_only)"
        assert a["official_requirements"][0]["document_id"] == "DOC_POL_A"
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
    search.assert_called_once_with("조건 확인", filters={"region": "서울특별시", "policy_id": "POL_A"}, k=10)
    generate.assert_called_once()
    assert [p.policy_id for p in result.policy_checks] == ["POL_A"]
    assert result.policy_checks[0].required_documents[0].text == "신청서"
    assert result.policy_checks[0].official_requirements[0].value == "2025년 기준"


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
    assert application_details(contexts) == {"required_documents": None, "official_requirements": None}
    contexts[0]["text"] = "[제출서류]\n2025년 서류 A\n[지원대상]\n월 50만원 이하\n[문의처]\n기관 129"
    result = application_details(contexts + contexts)
    assert len(result["required_documents"]) == 1
    assert result["official_requirements"][0]["value"] == "월 50만원 이하"
    assert "문의처" not in result["required_documents"][0]["text"]
