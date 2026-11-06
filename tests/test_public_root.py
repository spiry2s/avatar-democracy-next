"""Public landing at / and the invite gate behind it.

No network and no provider calls. A missing, unmatched, or expired invite
sees the landing page and cannot call the API. A valid invite still opens
the prototype. A LAN access token does not replace an invite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

import main
from backend import invites

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
LAN = "lan-access-token-value"
PUBLIC_ASSETS = {
    "/landing/style.css": "text/css",
    "/landing/illustration.js": "javascript",
    "/landing/favicon.svg": "image/svg",
}
APP_PAGES = ("/analyze", "/avatar", "/vote", "/history", "/bills", "/chamber")
DOC_PATHS = ("/docs", "/docs/", "/redoc", "/openapi.json")


@pytest.fixture(autouse=True)
def invite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    main.reset_rate_limits()
    invites.close_store()
    yield
    invites.close_store()


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")


def _client() -> TestClient:
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def _redeem(client: TestClient, issued: invites.IssuedInvite) -> None:
    response = client.get("/", params={"token": issued.token}, follow_redirects=False)
    assert response.status_code == 303
    assert "token" not in response.headers["location"]
    assert response.headers["location"].startswith("/")


def _routes() -> list[Route]:
    found: list[Route] = []

    def walk(routes) -> None:
        for route in routes:
            nested = getattr(route, "routes", None)
            if nested is not None and not isinstance(route, Route):
                walk(nested)
                continue
            if isinstance(route, Route):
                found.append(route)

    walk(main.app.routes)
    return found


def test_landing_files_have_no_network_and_no_signup():
    raw = LANDING.lower()
    assert 'id="public-landing"' in LANDING
    assert "home-hero" not in LANDING
    assert "<form" not in raw and "<input" not in raw
    assert "subscribe" not in raw and "newsletter" not in raw
    assert raw.count("<script") == 1
    assert 'src="/landing/illustration.js"' in LANDING
    script = (ROOT / "landing" / "illustration.js").read_text(encoding="utf-8").lower()
    css = (ROOT / "landing" / "style.css").read_text(encoding="utf-8").lower()
    for blob in (script, css):
        assert "fetch(" not in blob
        assert "xmlhttprequest" not in blob
        assert "websocket" not in blob
        assert "localstorage" not in blob
        assert "document.cookie" not in blob
        assert "http://" not in blob and "https://" not in blob
    assert "prefers-reduced-motion" in css
    assert "the government should reduce taxes" not in raw


def test_no_invite_sees_only_the_landing_and_blocked_apis(monkeypatch):
    _enable(monkeypatch)
    client = _client()
    home = client.get("/")
    assert home.status_code == 200
    assert home.text == LANDING
    assert home.headers["cache-control"] == "no-store"
    assert "set-cookie" not in home.headers

    for path, media in PUBLIC_ASSETS.items():
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert media in asset.headers["content-type"]
        assert client.post(path).status_code == 403

    for path in APP_PAGES + ("/invite", "/static/index.html", "/static/analyze.html", "/static/avatar.html"):
        blocked = client.get(path)
        assert blocked.status_code == 403, path
        assert blocked.text == LANDING
        assert "home-hero" not in blocked.text
        assert "Bill Analyzer" not in blocked.text

    for path in ("/static/api.js", "/static/style.css", "/manifest.webmanifest", "/sw.js"):
        assert client.get(path).status_code == 200, path

    health = client.get("/api/health")
    assert health.status_code == 403
    assert health.json()["detail"] == "Forbidden"
    assert client.post("/api/analyze", files={"text": (None, "hi")}).status_code == 403
    assert client.get("/api/profile").status_code == 403
    assert client.get("/api/questionnaire").status_code == 403
    assert client.get("/api/audit").status_code == 403
    assert client.get("/api/bills").status_code == 403
    assert client.get("/api/population").status_code == 403

    for path in DOC_PATHS:
        docs = client.get(path)
        assert docs.status_code == 403, path
        assert "openapi" not in docs.text.lower()
        assert docs.text == LANDING

    slipped = client.get("/ai_avatar_democracy_v1.1.md")
    assert slipped.status_code == 403
    assert slipped.text == LANDING
    assert client.get("/landing/other.js").status_code == 403
    assert client.get("/landing/style.css/../../.env").status_code == 403


def test_every_route_without_an_invite_stays_closed(monkeypatch):
    _enable(monkeypatch)
    client = _client()
    public = {"/", "/manifest.webmanifest", "/sw.js", *PUBLIC_ASSETS}
    for route in _routes():
        path = route.path
        if "{" in path:
            path = (
                path.replace("{bill_id}", "missing")
                .replace("{issue_area}", "housing")
                .replace("{source_id}", "missing")
            )
        methods = route.methods or set()
        for method in methods:
            if method == "HEAD":
                continue
            response = client.request(method, path)
            if method == "GET" and path in public:
                assert response.status_code == 200, path
                continue
            assert response.status_code == 403, f"{method} {path}"
            if path == "/api" or path.startswith("/api/"):
                assert response.json()["detail"] == "Forbidden"
            else:
                assert response.text == LANDING


def test_invalid_and_expired_invites_stay_on_the_landing(monkeypatch):
    _enable(monkeypatch)
    expired = invites.create_invite(label="old")
    invites.expire_invite(expired.invite.id)
    revoked = invites.create_invite(label="gone")
    invites.revoke_invite(revoked.invite.id)
    samples = ("not-a-real-token-value", expired.token, revoked.token)
    bodies = []
    for presented in samples:
        client = _client()
        page = client.get("/", params={"token": presented})
        assert page.status_code == 403
        assert "set-cookie" not in page.headers
        assert presented not in page.text
        assert page.text == LANDING
        assert client.get("/api/health").status_code == 403
        assert client.get("/avatar").text == LANDING
        bodies.append(page.text)
    assert len(set(bodies)) == 1


def test_valid_invite_opens_the_existing_demo(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada")
    client = _client()
    _redeem(client, issued)
    home = client.get("/")
    assert home.status_code == 200
    assert "home-hero" in home.text
    assert 'id="public-landing"' not in home.text
    assert 'href="/avatar#compass"' in home.text
    for path in APP_PAGES:
        page = client.get(path)
        assert page.status_code == 200, path
        assert "public-landing" not in page.text
    avatar = client.get("/avatar")
    assert 'id="compass"' in avatar.text
    assert "not a validated psychometric test" in avatar.text
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/questionnaire").status_code == 200
    assert client.get("/api/profile").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower() or "openapi" in docs.text.lower()
    static_html = client.get("/static/avatar.html")
    assert static_html.status_code == 200
    assert 'id="compass"' in static_html.text


def test_bad_token_does_not_open_the_demo_for_a_live_session(monkeypatch):
    _enable(monkeypatch)
    issued = invites.create_invite(label="ada")
    client = _client()
    _redeem(client, issued)
    blocked = client.get("/", params={"token": "not-a-real-token-value"})
    assert blocked.status_code == 403
    assert blocked.text == LANDING
    assert "home-hero" not in blocked.text
    assert "home-hero" in client.get("/").text


def test_lan_token_does_not_replace_an_invite(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("APP_ACCESS_TOKEN", LAN)
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    client = _client()
    headers = {"X-Access-Token": LAN}
    assert client.get("/", headers=headers).text == LANDING
    assert client.get("/avatar", headers=headers).status_code == 403
    assert client.get("/api/profile", headers=headers).status_code == 403
    assert client.get("/api/health", headers=headers).status_code == 403
    issued = invites.create_invite(label="ada")
    _redeem(client, issued)
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers=headers).status_code == 200
    assert "home-hero" in client.get("/", headers=headers).text
