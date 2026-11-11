"""Issue #10: uploads are capped like fetched bill links (15 MB)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from backend import extractors, invites

BASE = "http://127.0.0.1:8000"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    main.reset_rate_limits()
    invites.close_store()
    yield TestClient(main.app, base_url=BASE)
    invites.close_store()


@pytest.fixture
def no_extraction(monkeypatch):
    seen = []

    def fake_extract(source, source_type):
        seen.append(len(source))
        return "Section 1. " + "Bill text. " * 20

    monkeypatch.setattr(main.extractors, "extract_text", fake_extract)
    monkeypatch.setattr(main.comparison, "analyze_for_api", lambda text: {"title": "t", "sections": []})
    return seen


def test_cap_matches_the_bill_link_cap():
    assert extractors.MAX_UPLOAD_BYTES == 15 * 1024 * 1024


def test_file_over_the_cap_is_refused_before_extraction(client, no_extraction, monkeypatch):
    monkeypatch.setattr(extractors, "MAX_UPLOAD_BYTES", 1000)

    response = client.post("/api/analyze", files={"file": ("bill.pdf", b"%PDF" + b"0" * 1500, "application/pdf")})

    assert response.status_code == 413
    assert response.json() == {"detail": extractors.MSG_UPLOAD_TOO_BIG, "code": "too_big"}
    assert no_extraction == []


def test_file_at_the_cap_is_accepted(client, no_extraction, monkeypatch):
    monkeypatch.setattr(extractors, "MAX_UPLOAD_BYTES", 1000)

    response = client.post("/api/analyze", files={"file": ("bill.pdf", b"%PDF" + b"0" * 996, "application/pdf")})

    assert response.status_code == 200
    assert no_extraction == [1000]


def _request(content_length: str | None) -> Request:
    headers = [(b"host", b"127.0.0.1:8000"), (b"content-type", b"multipart/form-data; boundary=x")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    return Request({
        "type": "http", "method": "POST", "path": "/api/analyze", "raw_path": b"/api/analyze",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1",
    })


def test_declared_oversize_body_is_refused_before_parsing(monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)

    blocked = main.guard_api_request(_request(str(500 * 1024 * 1024)))

    assert blocked is not None and blocked.status_code == 413


@pytest.mark.parametrize("length", [None, "not-a-number", "1000"])
def test_missing_bad_or_small_length_is_left_to_the_capped_read(monkeypatch, length):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    assert main.guard_api_request(_request(length)) is None


def test_oversized_upload_costs_an_invitee_nothing(client, no_extraction, monkeypatch):
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    monkeypatch.setattr(extractors, "MAX_UPLOAD_BYTES", 1000)
    issued = invites.create_invite(label="ada", bills=1, calls=1)
    assert client.get("/", params={"token": issued.token}, follow_redirects=False).status_code == 303

    response = client.post("/api/analyze", files={"file": ("bill.pdf", b"0" * 5000, "application/pdf")})

    assert response.status_code == 413
    row = invites.get_invite(issued.invite.id)
    assert row is not None and (row.bills_used, row.llm_calls_used) == (0, 0)
