"""Invite tokens for a closed demo.

No network and no provider calls. Invite mode is off unless a test turns it on.
The LAN token, Host allowlist, Origin check, and content security policy stay.
"""

from __future__ import annotations

import calendar
import hmac
import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from backend import comparison, config, invites, llm

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "sk-ant-invite-test-DO-NOT-LEAK"
SENTINEL_OAI = "sk-openai-invite-test-DO-NOT-LEAK"
LAN_TOKEN = "lan-access-token-value"
BILL = "Section 1. " * 20


@pytest.fixture(autouse=True)
def invite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_BILL_BUDGET", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_BILL_DAILY", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_LLM_BUDGET", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_TTL_HOURS", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_BASE_URL", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    main.reset_rate_limits()
    invites.close_store()
    yield
    invites.close_store()


@pytest.fixture
def client():
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def https_client():
    return TestClient(main.app, base_url="https://127.0.0.1:8000")


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")


def _scope(method: str, path: str, headers: list[tuple[bytes, bytes]], query: bytes = b""):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": headers,
        "client": ("127.0.0.1", 40000),
        "server": ("127.0.0.1", 8000),
    }


def _cookie_flags(header: str) -> list[str]:
    return [part.strip() for part in header.split(";")]


def _redeem(client: TestClient, issued: invites.IssuedInvite):
    return client.get("/", params={"token": issued.token}, follow_redirects=False)


def _fresh(monkeypatch) -> TestClient:
    _enable(monkeypatch)
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def _run_cli(tmp_path: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AVATAR_INVITE_DATA_DIR"] = str(tmp_path)
    env["AVATAR_INVITE_BASE_URL"] = "http://127.0.0.1:8000"
    env["ANTHROPIC_API_KEY"] = SENTINEL
    env["OPENAI_API_KEY"] = SENTINEL_OAI
    env.pop("AVATAR_INVITE_MODE", None)
    if extra_env:
        env.update(extra_env)
    invites.close_store()
    return subprocess.run(
        [sys.executable, "-m", "backend.invites", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_mode_off_leaves_the_app_open(client, tmp_path):
    assert invites.invite_mode_enabled() is False
    assert client.get("/api/health").status_code == 200
    home = client.get("/")
    assert home.status_code == 200
    assert "home-hero" in home.text
    assert 'id="public-landing"' not in home.text
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/invite").status_code == 404
    probed = client.get("/", params={"token": "not-a-token"})
    assert probed.status_code == 200
    assert "set-cookie" not in probed.headers
    issued = invites.create_invite(label="ada")
    denied = client.post("/api/analyze", files={"text": (None, "hi")})
    assert denied.status_code == 400
    assert invites.get_invite(issued.invite.id).bills_used == 0
    assert invites.get_invite(issued.invite.id).llm_calls_used == 0
    assert not (tmp_path / "invite-usage.jsonl").exists()
    assert "referrer-policy" not in client.get("/").headers


@pytest.mark.parametrize(
    ("value", "enabled"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        (" 1 ", True),
        ("0", False),
        ("false", False),
        ("", False),
    ],
)
def test_invite_mode_flag(monkeypatch, value, enabled):
    monkeypatch.setenv("AVATAR_INVITE_MODE", value)
    assert invites.invite_mode_enabled() is enabled


def test_token_uses_32_bytes_and_is_not_stored(monkeypatch, tmp_path):
    seen: list[int] = []
    real = invites.secrets.token_urlsafe

    def spy(nbytes: int = 32) -> str:
        seen.append(nbytes)
        return real(nbytes)

    monkeypatch.setattr(invites.secrets, "token_urlsafe", spy)
    issued = invites.create_invite(label="ada")
    assert seen == [invites.TOKEN_BYTES]
    assert invites.TOKEN_BYTES >= 32
    assert len(issued.token) >= 43
    blob = (tmp_path / "invites.sqlite").read_bytes()
    assert issued.token.encode() not in blob
    columns = [row[1] for row in sqlite3.connect(tmp_path / "invites.sqlite").execute("PRAGMA table_info(invites)")]
    assert "token" not in columns
    assert "token_hash" in columns
    key_mode = stat.S_IMODE((tmp_path / "invite-hmac.key").stat().st_mode)
    assert key_mode == 0o600
    db_mode = stat.S_IMODE((tmp_path / "invites.sqlite").stat().st_mode)
    assert db_mode == 0o600
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert not hasattr(row, "token")
    assert not hasattr(row, "token_hash")


def test_defaults_come_from_env_and_can_be_overridden(monkeypatch):
    monkeypatch.setenv("AVATAR_INVITE_BILL_BUDGET", "7")
    monkeypatch.setenv("AVATAR_INVITE_BILL_DAILY", "3")
    monkeypatch.setenv("AVATAR_INVITE_LLM_BUDGET", "9")
    monkeypatch.setenv("AVATAR_INVITE_TTL_HOURS", "2")
    issued = invites.create_invite(label="ada")
    assert issued.invite.bill_budget == 7
    assert issued.invite.bill_daily_budget == 3
    assert issued.invite.llm_budget == 9
    assert issued.invite.expires_at - issued.invite.created_at == pytest.approx(7200, abs=2)
    custom = invites.create_invite(label="bea", bills=0, bills_daily=1, calls=4, ttl_seconds=50)
    assert custom.invite.bill_budget == 0
    assert custom.invite.bill_daily_budget == 1
    assert custom.invite.llm_budget == 4
    monkeypatch.setenv("AVATAR_INVITE_BILL_BUDGET", "nope")
    assert invites.default_bill_budget() == invites.DEFAULT_BILL_BUDGET
    monkeypatch.setenv("AVATAR_INVITE_BILL_BUDGET", "0")
    assert invites.default_bill_budget() == invites.DEFAULT_BILL_BUDGET
    monkeypatch.setenv("AVATAR_INVITE_BILL_BUDGET", "-3")
    assert invites.default_bill_budget() == invites.DEFAULT_BILL_BUDGET
    monkeypatch.setenv("AVATAR_INVITE_BILL_DAILY", "nope")
    assert invites.default_bill_daily() == invites.DEFAULT_BILL_DAILY
    monkeypatch.setenv("AVATAR_INVITE_BILL_DAILY", "0")
    assert invites.default_bill_daily() == invites.DEFAULT_BILL_DAILY
    assert invites.DEFAULT_BILL_BUDGET == 30
    assert invites.DEFAULT_BILL_DAILY == 10
    assert invites.DEFAULT_LLM_BUDGET == 60
    with pytest.raises(ValueError):
        invites.create_invite(label="nope", bills=-1, calls=1)
    with pytest.raises(ValueError):
        invites.create_invite(label="nope", bills_daily=-1)
    with pytest.raises(ValueError):
        invites.create_invite(label="")
    with pytest.raises(ValueError):
        invites.create_invite(label="bad\nlabel")


def test_base_url_rejects_tricks():
    issued = invites.create_invite(label="ada", base_url="http://127.0.0.1:8000")
    assert issued.link == f"http://127.0.0.1:8000/?token={issued.token}"
    for bad in (
        "javascript:alert(1)",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:8000/?token=already",
        "http://127.0.0.1:8000/extra",
        "http://127.0.0.1:8000#frag",
    ):
        with pytest.raises(ValueError):
            invites.create_invite(label="ada", base_url=bad)


def test_compare_digest_covers_unknown_expired_and_revoked(monkeypatch):
    issued = invites.create_invite(label="live")
    expired = invites.create_invite(label="old")
    assert invites.expire_invite(expired.invite.id) is True
    revoked = invites.create_invite(label="gone")
    assert invites.revoke_invite(revoked.invite.id) is True
    calls: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(invites.hmac, "compare_digest", spy)
    assert invites.authenticate("not-a-real-token-value") is None
    assert invites.authenticate(expired.token) is None
    assert invites.authenticate(revoked.token) is None
    assert invites.authenticate(issued.token) is not None
    assert invites.authenticate(issued.token + "x") is None
    assert len(calls) == 5
    for left, right in calls:
        assert len(left) == len(right) == 64


def test_failure_pages_and_api_errors_do_not_leak_a_reason(monkeypatch):
    _enable(monkeypatch)
    expired = invites.create_invite(label="old")
    invites.expire_invite(expired.invite.id)
    revoked = invites.create_invite(label="gone")
    invites.revoke_invite(revoked.invite.id)
    pages = []
    apis = []
    no_token = None
    for token in (None, "not-a-real-token-value", expired.token, revoked.token):
        page_client = TestClient(main.app, base_url="http://127.0.0.1:8000")
        api_client = TestClient(main.app, base_url="http://127.0.0.1:8000")
        if token is None:
            no_token = page_client.get("/")
            apis.append(api_client.get("/api/health"))
        else:
            pages.append(page_client.get("/", params={"token": token}))
            api_client.cookies.set(invites.COOKIE_NAME, token)
            apis.append(api_client.get("/api/health"))
    assert no_token is not None and no_token.status_code == 200
    assert 'id="public-landing"' in no_token.text
    assert "home-hero" not in no_token.text
    assert "set-cookie" not in no_token.headers
    bodies = {response.text for response in pages}
    assert len(bodies) == 1
    assert bodies == {no_token.text}
    assert {response.status_code for response in pages} == {403}
    page = pages[0]
    assert "text/html" in page.headers["content-type"]
    assert "this demo is closed" in page.text.lower()
    for word in ("expir", "revok", "invalid", "unknown", "token", SENTINEL.lower()):
        assert word not in page.text.lower()
    assert expired.token not in page.text
    assert revoked.token not in page.text
    assert page.text.lower().count("<script") == 1
    assert 'src="/landing/illustration.js"' in page.text
    assert "javascript:" not in page.text.lower()
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert {response.status_code for response in apis} == {403}
    assert {response.json()["detail"] for response in apis} == {"Forbidden"}
    for response in apis:
        assert expired.token not in response.text
        assert revoked.token not in response.text


def test_happy_path_strips_the_token_and_sets_cookie_flags(monkeypatch, client, https_client):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada", ttl_seconds=3600)
    response = client.get("/", params={"token": issued.token, "stay": "1"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?stay=1"
    assert issued.token not in response.headers["location"]
    assert not response.headers["location"].startswith("//")
    assert not response.headers["location"].startswith("http")
    flags = {part.lower() for part in _cookie_flags(response.headers["set-cookie"])}
    assert "httponly" in flags
    assert "samesite=strict" in flags
    assert "secure" not in flags
    assert "path=/" in flags
    max_age = next(int(part.split("=", 1)[1]) for part in flags if part.startswith("max-age="))
    assert 3500 <= max_age <= 3600
    assert response.headers["referrer-policy"] == "no-referrer"
    followed = client.get("/", params={"token": issued.token}, follow_redirects=True)
    assert followed.status_code == 200
    assert issued.token not in str(followed.url)
    assert "home-hero" in followed.text
    assert 'id="public-landing"' not in followed.text
    assert client.get("/api/health").status_code == 200
    for path in ("/analyze", "/avatar", "/vote", "/history", "/bills", "/chamber"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert issued.token not in page.text

    secure = https_client.get("/", params={"token": issued.token}, follow_redirects=False)
    secure_flags = {part.lower() for part in _cookie_flags(secure.headers["set-cookie"])}
    assert "secure" in secure_flags
    assert "httponly" in secure_flags
    assert "samesite=strict" in secure_flags


def test_invite_alias_and_token_on_another_path_does_not_sign_in(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada")
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    alias = client.get("/invite", params={"token": issued.token}, follow_redirects=False)
    assert alias.status_code == 303
    assert alias.headers["location"] == "/"
    assert issued.token not in alias.headers["location"]
    assert client.get("/api/health").status_code == 200

    other = TestClient(main.app, base_url="http://127.0.0.1:8000")
    leaked = other.get("/analyze", params={"token": issued.token})
    assert leaked.status_code == 403
    assert "set-cookie" not in leaked.headers
    assert issued.token not in leaked.text
    assert other.get("/api/health").status_code == 403

    doubled = TestClient(main.app, base_url="http://127.0.0.1:8000")
    both = doubled.get("/", params=[("token", issued.token), ("token", issued.token)])
    assert both.status_code == 403
    assert "set-cookie" not in both.headers


def test_static_assets_skip_the_cookie_and_pages_do_not(monkeypatch, client):
    _enable(monkeypatch)
    for path in ("/static/api.js", "/static/style.css", "/manifest.webmanifest", "/sw.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "default-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/analyze").status_code == 403
    assert client.get("/api/health").status_code == 403
    blocked = main.guard_api_request(
        Request(_scope("GET", "/static/../.env", [(b"host", b"127.0.0.1:8000")]))
    )
    assert blocked is not None and blocked.status_code == 403
    allowed = main.guard_api_request(
        Request(_scope("GET", "/static/api.js", [(b"host", b"127.0.0.1:8000")]))
    )
    assert allowed is None


def test_duplicate_cookie_is_rejected(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada")
    headers = [
        (b"host", b"127.0.0.1:8000"),
        (b"cookie", f"{invites.COOKIE_NAME}={issued.token}; {invites.COOKIE_NAME}=second".encode()),
    ]
    blocked = main.guard_api_request(Request(_scope("GET", "/api/health", headers)))
    assert blocked is not None and blocked.status_code == 403


def test_replay_after_revoke_and_extend_does_not_clear_revoke(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada", bills=5, calls=5, ttl_seconds=3600)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert _redeem(client, issued).status_code == 303
    assert client.get("/api/health").status_code == 200
    assert invites.expire_invite(issued.invite.id) is True
    assert client.get("/api/health").status_code == 403
    assert invites.extend_invite(issued.invite.id, hours=2) is True
    assert client.get("/api/health").status_code == 200
    assert invites.revoke_invite(issued.invite.id) is True
    assert client.get("/api/health").status_code == 403
    assert client.post("/api/analyze", files={"text": (None, "hi")}).status_code == 403
    again = client.get("/", params={"token": issued.token}, follow_redirects=False)
    assert again.status_code == 403
    assert "set-cookie" not in again.headers
    assert invites.extend_invite(issued.invite.id, hours=3) is True
    assert client.get("/api/health").status_code == 403
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.revoked is True
    assert row.bills_used == 0
    assert row.llm_calls_used == 0


def test_admission_cost_follows_the_spending_routes(monkeypatch):
    assert main.invite_admission_cost("POST", "/api/analyze") == (1, 1)
    assert main.invite_admission_cost("POST", "/api/avatar/vote") == (0, 1)
    assert main.invite_admission_cost("POST", "/api/bills/abc/vote") == (0, 1)
    assert main.invite_admission_cost("POST", "/api/bills/abc/endorse") == (0, 1)
    assert main.invite_admission_cost("POST", "/api/bills/abc/simulate-endorsements") == (0, 1)
    assert main.invite_admission_cost("GET", "/api/health") == (0, 0)
    assert main.invite_admission_cost("POST", "/api/bills") == (0, 0)
    assert main.invite_admission_cost("POST", "/api/avatar/override") == (0, 0)
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    assert comparison.comparison_configured() is True
    assert main.invite_admission_cost("POST", "/api/analyze") == (1, 2)


def test_budget_exhausted_on_each_spending_route(monkeypatch):
    calls = [
        ("analyze-text", lambda http: http.post("/api/analyze", files={"text": (None, "hi")})),
        ("analyze-url", lambda http: http.post("/api/analyze", files={"url": (None, "https://evil.example/bill")})),
        ("analyze-file", lambda http: http.post("/api/analyze", files={"file": ("b.pdf", b"%PDF", "application/pdf")})),
        ("avatar-vote", lambda http: http.post("/api/avatar/vote", json={"analysis": {}})),
        ("bill-vote", lambda http: http.post("/api/bills/some-bill/vote")),
        ("endorse", lambda http: http.post("/api/bills/some-bill/endorse")),
        ("simulate", lambda http: http.post("/api/bills/some-bill/simulate-endorsements", json={"count": 1})),
    ]
    for name, call in calls:
        http = _fresh(monkeypatch)
        issued = invites.create_invite(label=name, bills=1, calls=1)
        assert _redeem(http, issued).status_code == 303
        first = call(http)
        assert first.status_code != 429, (name, first.status_code, first.text)
        second = call(http)
        assert second.status_code == 429, name
        assert second.json()["detail"] in {
            "Invite bill budget exhausted",
            "Invite model-call budget exhausted",
        }
        row = invites.get_invite(issued.invite.id)
        assert row is not None
        assert row.llm_calls_used == 1
        if name.startswith("analyze"):
            assert row.bills_used == 1
        else:
            assert row.bills_used == 0


def test_text_and_url_share_the_bill_budget(monkeypatch):
    _enable(monkeypatch)
    for first, second in (
        (
            lambda http: http.post("/api/analyze", files={"text": (None, "hi")}),
            lambda http: http.post("/api/analyze", files={"url": (None, "https://evil.example/bill")}),
        ),
        (
            lambda http: http.post("/api/analyze", files={"url": (None, "https://evil.example/bill")}),
            lambda http: http.post("/api/analyze", files={"text": (None, "hi")}),
        ),
    ):
        http = TestClient(main.app, base_url="http://127.0.0.1:8000")
        issued = invites.create_invite(label="ada", bills=1, calls=10)
        assert _redeem(http, issued).status_code == 303
        assert first(http).status_code != 429
        blocked = second(http)
        assert blocked.status_code == 429
        assert blocked.json()["detail"] == "Invite bill budget exhausted"
        vote = http.post("/api/avatar/vote", json={"analysis": {}})
        assert vote.status_code != 429


def test_comparison_reserves_a_second_model_call(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    broke = invites.create_invite(label="one", bills=5, calls=1)
    assert _redeem(http, broke).status_code == 303
    blocked = http.post("/api/analyze", files={"text": (None, "hi")})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Invite model-call budget exhausted"
    row = invites.get_invite(broke.invite.id)
    assert row is not None and row.bills_used == 0 and row.llm_calls_used == 0

    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    enough = invites.create_invite(label="two", bills=5, calls=2)
    assert _redeem(http, enough).status_code == 303
    assert http.post("/api/analyze", files={"text": (None, "hi")}).status_code != 429
    again = http.post("/api/analyze", files={"text": (None, "hi")})
    assert again.status_code == 429
    row = invites.get_invite(enough.invite.id)
    assert row is not None and row.bills_used == 1 and row.llm_calls_used == 2


def test_rejected_requests_do_not_spend_budget(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada", bills=3, calls=3)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert _redeem(http, issued).status_code == 303
    wrong_type = http.post("/api/analyze", json={"text": "hello"})
    assert wrong_type.status_code == 415
    bad_origin = http.post(
        "/api/avatar/vote",
        json={"analysis": {}},
        headers={"Origin": "http://evil.example:8000"},
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["detail"] == "Forbidden"
    bad_host = http.get("/api/health", headers={"Host": "evil.example"})
    assert bad_host.status_code == 421
    health = http.get("/api/health")
    assert health.status_code == 200
    questions = http.get("/api/questionnaire")
    assert questions.status_code == 200
    bills = http.get("/api/bills")
    assert bills.status_code == 200
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == 0 and row.llm_calls_used == 0


def test_reserve_is_all_or_nothing():
    issued = invites.create_invite(label="ada", bills=1, calls=1)
    assert invites.try_reserve(issued.invite.id, 0, 1) == "ok"
    assert invites.try_reserve(issued.invite.id, 1, 1) == "calls"
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == 0 and row.llm_calls_used == 1
    assert invites.revoke_invite(issued.invite.id) is True
    assert invites.try_reserve(issued.invite.id, 0, 1) == "closed"


def test_zero_budgets_block_before_a_provider_call(monkeypatch):
    _enable(monkeypatch)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    issued = invites.create_invite(label="ada", bills=0, calls=0)
    assert _redeem(http, issued).status_code == 303
    analyze = http.post("/api/analyze", files={"text": (None, BILL)})
    assert analyze.status_code == 429
    assert analyze.json()["detail"] == "Invite bill budget exhausted"
    vote = http.post("/api/avatar/vote", json={"analysis": {}})
    assert vote.status_code == 429
    assert vote.json()["detail"] == "Invite model-call budget exhausted"
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == 0 and row.llm_calls_used == 0


def test_usage_log_records_id_and_label_not_the_token(monkeypatch, tmp_path):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada') drop table invites;--")
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")

    class Probe(llm.LLMProvider):
        provider_id = "probe"
        model = "probe"

        def complete(self, **kwargs):
            self.note_usage(llm.TokenUsage(input_tokens=2, output_tokens=3, reasoning_tokens=1))
            return "{}"

    def fake_analyze(text):
        Probe().complete(user="x")
        return {
            "title": "T",
            "token_usage": [
                {"calls": 2, "input_tokens": 10, "output_tokens": 1, "reasoning_tokens": 0, "role": "primary"}
            ],
        }

    monkeypatch.setattr(comparison, "analyze_for_api", fake_analyze)
    assert _redeem(http, issued).status_code == 303
    analyzed = http.post("/api/analyze", files={"text": (None, BILL)})
    assert analyzed.status_code == 200, analyzed.text
    assert SENTINEL not in analyzed.text
    health = http.get("/api/health")
    assert health.status_code == 200
    raw = (tmp_path / "invite-usage.jsonl").read_text(encoding="utf-8")
    assert issued.token not in raw
    assert "token=" not in raw
    assert SENTINEL not in raw
    assert SENTINEL_OAI not in raw
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert lines[0]["route"] == "GET /"
    assert lines[0]["invite_id"] == issued.invite.id
    assert lines[0]["label"] == issued.invite.label
    assert lines[0]["status"] == 303
    analysis = next(line for line in lines if line["route"] == "POST /api/analyze")
    assert analysis["llm_calls"] == 2
    assert analysis["llm_tokens"] == 11
    assert analysis["invite_id"] == issued.invite.id
    health_line = next(line for line in lines if line["route"] == "GET /api/health")
    assert health_line["llm_calls"] == 0
    assert health_line["llm_tokens"] is None
    prefix = raw
    http.get("/api/questionnaire")
    later = (tmp_path / "invite-usage.jsonl").read_text(encoding="utf-8")
    assert later.startswith(prefix)
    assert len(later.splitlines()) == len(prefix.splitlines()) + 1
    assert invites.get_invite(issued.invite.id) is not None


def test_log_filter_redacts_token_query_and_cookie():
    secret = "invite-secret-token-value-0123456789abcdEFGH"
    logger = logging.getLogger("uvicorn.access")
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Grab()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info('%s - "%s %s"', "127.0.0.1", "GET", "/?token=" + secret + " HTTP/1.1")
        logger.info("Cookie: %s=%s", invites.COOKIE_NAME, secret)
    finally:
        logger.removeHandler(handler)
    assert records
    for line in records:
        assert secret not in line
        assert "[redacted]" in line


def test_provider_key_is_not_echoed(monkeypatch, tmp_path):
    _enable(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OAI)
    issued = invites.create_invite(label="ada", bills=1, calls=1)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    blobs = []
    blobs.append(http.get("/").text)
    blobs.append(http.get("/static/api.js").text)
    blobs.append(http.get("/api/health").text)
    redeemed = _redeem(http, issued)
    blobs.append(redeemed.text)
    blobs.append(str(redeemed.headers))
    blobs.append(http.get("/api/health").text)
    blobs.append(http.get("/").text)
    blobs.append(http.get("/openapi.json").text)
    blobs.append(http.post("/api/analyze", files={"text": (None, "hi")}).text)
    blocked = http.post("/api/analyze", files={"text": (None, "hi")})
    assert blocked.status_code == 429
    blobs.append(blocked.text)
    for name in ("index.html", "analyze.html", "avatar.html", "vote.html", "bills.html", "api.js"):
        blobs.append((ROOT / "frontend" / name).read_text(encoding="utf-8"))
    if (tmp_path / "invite-usage.jsonl").exists():
        blobs.append((tmp_path / "invite-usage.jsonl").read_text(encoding="utf-8"))
    blobs.append((tmp_path / "invites.sqlite").read_bytes().decode("utf-8", "ignore"))
    combined = "\n".join(blobs)
    assert SENTINEL not in combined
    assert SENTINEL_OAI not in combined
    assert "api_key_configured" in http.get("/api/health").text
    assert issued.token not in http.get("/api/health").text
    assert issued.token not in (tmp_path / "invite-usage.jsonl").read_text(encoding="utf-8")


def test_admin_is_not_reachable_over_http(monkeypatch):
    _enable(monkeypatch)
    paths = {getattr(route, "path", "") for route in main.app.routes}
    assert "/api/invites" not in paths
    assert "/api/admin" not in paths
    for path in paths:
        assert "revoke" not in path
    issued = invites.create_invite(label="ada")
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    before = len(invites.list_invites())
    missing = http.post("/api/invites", json={"label": "smuggled"})
    assert missing.status_code == 403
    assert _redeem(http, issued).status_code == 303
    present = http.post("/api/invites", json={"label": "smuggled"})
    assert present.status_code == 404
    assert issued.token not in present.text
    method = http.post("/invite")
    assert method.status_code == 405
    assert len(invites.list_invites()) == before
    schema = http.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/invites" not in schema.json()["paths"]
    assert SENTINEL not in schema.text


def test_lan_token_host_origin_and_rate_limit_still_apply(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("APP_ACCESS_TOKEN", LAN_TOKEN)
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    main.reset_rate_limits()
    clock = {"now": 1_000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])
    issued = invites.create_invite(label="ada", bills=100, calls=100)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    no_invite = http.get("/api/health", headers={"X-Access-Token": LAN_TOKEN})
    assert no_invite.status_code == 403
    assert _redeem(http, issued).status_code == 303
    no_lan = http.get("/api/health")
    assert no_lan.status_code == 401
    assert no_lan.json()["detail"] == "Unauthorized"
    both = http.get("/api/health", headers={"X-Access-Token": LAN_TOKEN})
    assert both.status_code == 200
    assert "default-src 'self'" in both.headers["content-security-policy"]
    misdirected = http.get(
        "/api/health",
        headers={"Host": "evil.example", "X-Access-Token": LAN_TOKEN},
    )
    assert misdirected.status_code == 421
    statuses = []
    for _ in range(main.RATE_LIMIT + 1):
        response = http.post(
            "/api/analyze",
            files={"text": (None, "hi")},
            headers={"X-Access-Token": LAN_TOKEN},
        )
        statuses.append(response.status_code)
    assert statuses[:-1] == [400] * main.RATE_LIMIT
    assert statuses[-1] == 429
    assert response.json()["detail"] == "Too Many Requests"
    assert response.headers["retry-after"] == "60"
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == main.RATE_LIMIT

    clock["now"] += main.RATE_WINDOW_SECONDS + 1
    tight = invites.create_invite(label="bea", bills=2, calls=2)
    other = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert _redeem(other, tight).status_code == 303
    headers = {"X-Access-Token": LAN_TOKEN}
    assert other.post("/api/analyze", files={"text": (None, "hi")}, headers=headers).status_code == 400
    assert other.post("/api/analyze", files={"text": (None, "hi")}, headers=headers).status_code == 400
    budget = other.post("/api/analyze", files={"text": (None, "hi")}, headers=headers)
    assert budget.status_code == 429
    assert budget.json()["detail"] == "Invite bill budget exhausted"
    assert "retry-after" not in budget.headers


def test_concurrent_reserve_cannot_overspend():
    issued = invites.create_invite(label="race", bills=0, calls=5)
    barrier = threading.Barrier(32)
    results: list[str] = []

    def worker() -> None:
        barrier.wait()
        results.append(invites.try_reserve(issued.invite.id, 0, 1))

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("ok") == 5
    assert results.count("calls") == 27
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.llm_calls_used == 5 and row.bills_used == 0


def test_concurrent_gate_cannot_overspend(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="race", bills=0, calls=5)
    headers = [
        (b"host", b"127.0.0.1:8000"),
        (b"cookie", f"{invites.COOKIE_NAME}={issued.token}".encode()),
        (b"content-type", b"application/json"),
    ]
    barrier = threading.Barrier(32)
    results: list[int | None] = []

    def worker() -> None:
        barrier.wait()
        blocked = main.guard_api_request(Request(_scope("POST", "/api/avatar/vote", headers)))
        results.append(None if blocked is None else blocked.status_code)

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(None) == 5
    assert results.count(429) == 27
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.llm_calls_used == 5 and row.bills_used == 0


def test_path_traversal_is_rejected(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(invites.InvitePathError):
        invites.resolve_data_dir("link", project_root=root)
    with pytest.raises(invites.InvitePathError):
        invites.resolve_data_dir("data/../../etc", project_root=root)
    with pytest.raises(invites.InvitePathError):
        invites.resolve_data_dir(str(tmp_path / ".." / "etc"))
    with pytest.raises(invites.InvitePathError):
        invites.resolve_data_dir("/tmp/avatar/../../etc/passwd")
    with pytest.raises(invites.InvitePathError):
        invites.resolve_data_dir("data/\x00evil", project_root=root)
    assert invites.resolve_data_dir("", project_root=root) == (root / "data").resolve()
    assert invites.resolve_data_dir(str(outside)) == outside.resolve()
    issued = invites.create_invite(label="../../etc/passwd")
    names = {path.name for path in tmp_path.iterdir()}
    assert "passwd" not in names
    assert "etc" not in names
    assert issued.invite.label == "../../etc/passwd"
    assert invites.list_invites()


def test_bad_data_dir_fails_closed(monkeypatch, client):
    _enable(monkeypatch)
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", "data/../../etc")
    with pytest.raises(invites.InvitePathError):
        invites.create_invite(label="ada")
    response = client.get("/api/health")
    assert response.status_code == 403
    assert "etc" not in response.text
    assert ".." not in response.text


def test_cli_roundtrip_hides_the_token_and_the_api_key(monkeypatch, tmp_path):
    _enable(monkeypatch)
    created = _run_cli(tmp_path, "create", "--label", "ada", "--bills", "3", "--calls", "4", "--ttl-hours", "1")
    assert created.returncode == 0, created.stderr
    assert SENTINEL not in created.stdout + created.stderr
    assert SENTINEL_OAI not in created.stdout + created.stderr
    link = next(line for line in created.stdout.splitlines() if "?token=" in line)
    token = link.split("token=", 1)[1].strip()
    invite_id = next(line for line in created.stdout.splitlines() if line.startswith("id=")).split()[0].split("=", 1)[1]
    assert token.encode() not in (tmp_path / "invites.sqlite").read_bytes()
    assert invites.authenticate(token) is not None

    listed = _run_cli(tmp_path, "list")
    assert listed.returncode == 0
    assert token not in listed.stdout
    assert "ada" in listed.stdout
    assert "token_hash" not in listed.stdout
    assert SENTINEL not in listed.stdout

    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert http.get("/", params={"token": token}, follow_redirects=False).status_code == 303
    assert http.get("/api/health").status_code == 200
    usage = _run_cli(tmp_path, "usage", invite_id)
    assert usage.returncode == 0
    assert token not in usage.stdout + usage.stderr
    assert "GET /api/health" in usage.stdout
    assert SENTINEL not in usage.stdout

    expired = _run_cli(tmp_path, "expire", invite_id)
    assert expired.returncode == 0
    assert token not in expired.stdout
    assert invites.authenticate(token) is None
    extended = _run_cli(tmp_path, "extend", invite_id, "--hours", "2")
    assert extended.returncode == 0
    assert invites.authenticate(token) is not None
    revoked = _run_cli(tmp_path, "revoke", invite_id)
    assert revoked.returncode == 0
    assert token not in revoked.stdout + revoked.stderr
    assert invites.authenticate(token) is None
    again = _run_cli(tmp_path, "revoke", invite_id)
    assert again.returncode == 0
    missing = _run_cli(tmp_path, "revoke", "does-not-exist")
    assert missing.returncode == 1
    assert "not found" in missing.stderr
    assert token not in missing.stderr

    help_text = _run_cli(tmp_path, "--help")
    create_help = _run_cli(tmp_path, "create", "--help")
    assert help_text.returncode == 0
    assert "--token" not in help_text.stdout
    assert "--token" not in create_help.stdout
    assert "--bills-daily" in create_help.stdout
    assert "Not an HTTP service" in help_text.stdout


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return float(calendar.timegm((year, month, day, hour, minute, 0)))


def test_daily_bill_limit_resets_at_the_utc_boundary(monkeypatch):
    _enable(monkeypatch)
    clock = {"now": _utc(2026, 11, 5, 23, 30)}
    monkeypatch.setattr(invites.time, "time", lambda: clock["now"])
    issued = invites.create_invite(
        label="ada", bills=30, bills_daily=1, calls=10, ttl_seconds=7 * 24 * 3600,
    )
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert _redeem(http, issued).status_code == 303
    first = http.post("/api/analyze", files={"text": (None, "hi")})
    assert first.status_code != 429
    blocked = http.post("/api/analyze", files={"text": (None, "hi")})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Invite daily bill limit reached. It resets at UTC midnight."
    assert blocked.json()["detail"] != "Invite bill budget exhausted"
    vote = http.post("/api/avatar/vote", json={"analysis": {}})
    assert vote.status_code != 429
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 1
    assert row.bills_used_today == 1
    assert row.bills_day == "2026-11-05"
    assert row.llm_calls_used == 2
    assert "1/1" in invites.format_list(invites.list_invites())

    clock["now"] = _utc(2026, 11, 6, 0, 0)
    assert invites.effective_bills_today(invites.get_invite(issued.invite.id)) == 0
    assert "0/1" in invites.format_list(invites.list_invites())
    again = http.post("/api/analyze", files={"text": (None, "hi")})
    assert again.status_code != 429
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 2
    assert row.bills_used_today == 1
    assert row.bills_day == "2026-11-06"
    nxt = http.post("/api/analyze", files={"text": (None, "hi")})
    assert nxt.status_code == 429
    assert "UTC midnight" in nxt.json()["detail"]
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == 2 and row.bills_used_today == 1


def test_total_bill_budget_exhausted_while_daily_credit_remains(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada", bills=1, bills_daily=10, calls=5)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert _redeem(http, issued).status_code == 303
    assert http.post("/api/analyze", files={"text": (None, "hi")}).status_code != 429
    blocked = http.post("/api/analyze", files={"text": (None, "hi")})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Invite bill budget exhausted"
    assert "midnight" not in blocked.json()["detail"]
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 1
    assert row.bills_used_today == 1
    assert row.bills_used_today < row.bill_daily_budget


def test_cli_overrides_total_and_daily_bill_budgets(monkeypatch, tmp_path):
    created = _run_cli(
        tmp_path, "create", "--label", "ada", "--bills", "4", "--bills-daily", "2", "--calls", "9",
    )
    assert created.returncode == 0, created.stderr
    assert "bills=4" in created.stdout
    assert "daily=2" in created.stdout
    assert "calls=9" in created.stdout
    plain = _run_cli(tmp_path, "create", "--label", "bea")
    assert plain.returncode == 0, plain.stderr
    assert "bills=30" in plain.stdout
    assert "daily=10" in plain.stdout
    assert "calls=60" in plain.stdout
    invite_id = next(line for line in created.stdout.splitlines() if line.startswith("id=")).split()[0].split("=", 1)[1]
    assert invites.try_reserve(invite_id, 1, 1) == "ok"
    listed = _run_cli(tmp_path, "list")
    assert listed.returncode == 0
    assert "daily" in listed.stdout.splitlines()[0]
    assert "1/4" in listed.stdout
    assert "1/2" in listed.stdout
    usage = _run_cli(tmp_path, "usage", invite_id)
    assert usage.returncode == 0
    assert "daily 1/2" in usage.stdout
    assert "bills 1/4" in usage.stdout
    assert "calls 1/9" in usage.stdout


def test_old_invite_store_gains_daily_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    invites.close_store()
    db = tmp_path / "invites.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE invites (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            bill_budget INTEGER NOT NULL,
            llm_budget INTEGER NOT NULL,
            bills_used INTEGER NOT NULL DEFAULT 0,
            llm_calls_used INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()
    issued = invites.create_invite(label="ada", bills=4, bills_daily=2)
    assert issued.invite.bill_daily_budget == 2
    assert invites.try_reserve(issued.invite.id, 1, 0) == "ok"
    row = invites.get_invite(issued.invite.id)
    assert row is not None and row.bills_used == 1 and row.bills_used_today == 1
