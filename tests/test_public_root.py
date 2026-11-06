"""Public landing at / and the invite gate behind it.

No network and no provider calls. A missing, unmatched, or expired invite
sees the landing page and cannot call the API. A valid invite still opens
the prototype. A LAN access token does not replace an invite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    "/landing/favicon.svg": "image/svg",
}
APP_SOURCE = (
    "/static/api.js",
    "/static/app.js",
    "/static/avatar.js",
    "/static/style.css",
    "/manifest.webmanifest",
    "/sw.js",
)
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
    assert "<form" not in raw and "<input" not in raw and "<script" not in raw
    assert "subscribe" not in raw and "newsletter" not in raw
    assert "github.com" not in raw
    assert "north wharf" not in raw and "example-bill" not in raw
    assert not (ROOT / "landing" / "illustration.js").exists()
    css = (ROOT / "landing" / "style.css").read_text(encoding="utf-8").lower()
    assert "fetch(" not in css and "http://" not in css and "https://" not in css
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

    for path in APP_SOURCE:
        blocked_source = client.get(path)
        assert blocked_source.status_code == 403, path
        assert "const " not in blocked_source.text
        assert blocked_source.text == LANDING

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
    public = {"/", *PUBLIC_ASSETS}
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
    source = client.get("/static/avatar.js")
    assert source.status_code == 200
    assert "questionnaire" in source.text


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


def test_encoded_dot_segments_are_not_public(monkeypatch):
    _enable(monkeypatch)
    client = _client()
    paths = (
        "/static/%2e%2e/requirements.txt",
        "/static/%252e%252e/requirements.txt",
        "/static/icons/%2e%2e/%2e%2e/requirements.txt",
        "/landing/%2e%2e/main.py",
        "/static/../main.py",
    )
    for path in paths:
        assert invites.request_is_exempt("GET", path) is False, path
        response = client.get(path)
        assert response.status_code == 403, path
        assert "fastapi" not in response.text.lower()
        assert "ANTHROPIC" not in response.text
        assert response.text == LANDING


def test_invite_store_reloads_without_a_mint_script(tmp_path):
    assert not (ROOT / "railway_bootstrap_mint.py").is_file()
    assert "railway_bootstrap" not in (ROOT / "main.py").read_text(encoding="utf-8")
    env = os.environ.copy()
    env["AVATAR_INVITE_DATA_DIR"] = str(tmp_path)
    env["AVATAR_INVITE_BASE_URL"] = "http://127.0.0.1:8000"
    env.pop("AVATAR_INVITE_MODE", None)
    created = subprocess.run(
        [sys.executable, "-m", "backend.invites", "create", "--label", "ada"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr
    assert (tmp_path / "invites.sqlite").is_file()
    marker = "/?token="
    line = next(item for item in created.stdout.splitlines() if marker in item)
    token = line.split(marker, 1)[1].strip()
    script = """
import os
from fastapi.testclient import TestClient
from backend import invites
import main
record = invites.authenticate(os.environ["INVITE_TOKEN"])
assert record is not None and record.label == "ada"
client = TestClient(main.app, base_url="http://127.0.0.1:8000")
response = client.get("/", params={"token": os.environ["INVITE_TOKEN"]}, follow_redirects=False)
assert response.status_code == 303
assert "token" not in response.headers["location"]
followed = client.get("/")
assert followed.status_code == 200
assert "home-hero" in followed.text
assert client.get("/api/health").status_code == 200
print("reloaded")
"""
    env["AVATAR_INVITE_MODE"] = "1"
    env["INVITE_TOKEN"] = token
    reloaded = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert reloaded.returncode == 0, reloaded.stderr
    assert reloaded.stdout.strip() == "reloaded"
    assert token not in reloaded.stdout


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
