"""scripts/download_raw.py의 저장 전 형식 검사. 네트워크는 쓰지 않는다 (sample_only)."""

from scripts.download_raw import HEADERS, looks_like


def test_pdf_must_be_pdf():
    assert looks_like("pdf", "application/pdf", b"%PDF-1.7 sample_only")
    assert looks_like("pdf", "application/octet-stream", b"%PDF-1.4")
    # 서버가 200으로 돌려준 오류 HTML은 PDF로 저장하지 않는다.
    assert not looks_like("pdf", "text/html; charset=utf-8", b"<!DOCTYPE html><title>error</title>")


def test_html_must_be_html():
    assert looks_like("html", "text/html; charset=utf-8", b"<html></html>")
    assert looks_like("html", "", b"  <!doctype html><html>")
    assert not looks_like("html", "application/pdf", b"%PDF-1.7")


def test_user_agent_is_not_browser_disguise():
    assert "Mozilla" not in HEADERS["User-Agent"]
