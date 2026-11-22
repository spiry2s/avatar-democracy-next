"""Issues #11 and #12: behind a trusted proxy, use the real client and scheme.

Uvicorn's ProxyHeadersMiddleware is what `python main.py` runs in front of the
app. These tests wrap the app in it directly, with requests arriving from a
simulated proxy address, so they exercise the same code path.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import main
from backend import invites

LAN_TOKEN = "lan-token-for-proxy-tests-0123456789"
PROXY = ("10.1.2.3", 40000)


# ── the setting ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    (None, None),
    ("", None),
    ("  ", None),
    ("10.0.0.0/8", "10.0.0.0/8"),
    (" 10.0.0.5 , 192.168.0.0/16 ", "10.0.0.5,192.168.0.0/16"),
    ("10.1.2.3/8", "10.0.0.0/8"),
    ("fd00::/8", "fd00::/8"),
])
def test_setting_accepts_addresses_and_ranges(raw, expected):
    assert main.trusted_proxy_setting(raw) == expected


@pytest.mark.parametrize("raw", ["*", "10.0.0.0/8,*", "proxy.internal", "10.0.0.0/99"])
def test_setting_refuses_wildcards_and_garbage(raw):
    with pytest.raises(ValueError):
        main.trusted_proxy_setting(raw)


def _run_main(monkeypatch, trusted: str | None) -> dict:
    captured: dict = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr(main, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setenv("HOST", "127.0.0.1")
    if trusted is None:
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_IPS", trusted)
    main.main()
    return captured


def test_main_passes_the_trusted_proxies_to_uvicorn(monkeypatch):
    options = _run_main(monkeypatch, "10.0.0.0/8")
    assert options["forwarded_allow_ips"] == "10.0.0.0/8"
    assert options["proxy_headers"] is True


def test_main_keeps_uvicorn_default_without_the_setting(monkeypatch):
    assert "forwarded_allow_ips" not in _run_main(monkeypatch, None)


def test_main_refuses_to_start_with_a_wildcard(monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, "*")


# ── #11: the per-IP limit sees real clients ──────────────────────────────────


@pytest.fixture
def lan(monkeypatch, tmp_path):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setenv("APP_ACCESS_TOKEN", LAN_TOKEN)
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setattr(main, "bind_port", 8000)
    main.reset_rate_limits()
    yield
    main.reset_rate_limits()


def _statuses(trusted: str, forwarded_for: list[str]) -> list[int]:
    app = ProxyHeadersMiddleware(main.app, trusted_hosts=trusted)

    async def run():
        transport = httpx.ASGITransport(app=app, client=PROXY)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            out = []
            for ip in forwarded_for:
                response = await client.post(
                    "/api/analyze",
                    files={"text": (None, "hi")},
                    headers={"X-Access-Token": LAN_TOKEN, "X-Forwarded-For": ip},
                )
                out.append(response.status_code)
            return out

    return asyncio.run(run())


def test_trusted_proxy_gives_each_client_its_own_limit(lan):
    statuses = _statuses("10.0.0.0/8", ["203.0.113.7"] * main.RATE_LIMIT + ["203.0.113.7", "198.51.100.9"])
    assert statuses[: main.RATE_LIMIT] == [400] * main.RATE_LIMIT  # spends, then fails validation
    assert statuses[main.RATE_LIMIT] == 429  # the same client is now limited
    assert statuses[-1] == 400  # a different client is not


def test_untrusted_peer_cannot_dodge_the_limit_with_a_forged_header(lan):
    forged = [f"203.0.113.{i}" for i in range(main.RATE_LIMIT + 1)]
    statuses = _statuses("192.168.99.0/24", forged)  # the peer 10.1.2.3 is not trusted
    assert statuses[-1] == 429  # every request counted against the real peer


# ── #12: the invite cookie is Secure on an https deployment ─────────────────


@pytest.fixture
def invite_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("AVATAR_INVITE_BASE_URL", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    invites.close_store()
    yield
    invites.close_store()


def _invite_cookie(trusted: str, headers: dict[str, str] | None = None) -> str:
    issued = invites.create_invite(label="ada")
    app = ProxyHeadersMiddleware(main.app, trusted_hosts=trusted)

    async def run():
        transport = httpx.ASGITransport(app=app, client=PROXY)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            response = await client.get("/", params={"token": issued.token}, headers=headers or {})
            return response.headers.get("set-cookie", "")

    return asyncio.run(run())


def test_cookie_is_secure_when_a_trusted_proxy_says_https(invite_mode):
    cookie = _invite_cookie("10.0.0.0/8", {"X-Forwarded-Proto": "https"})
    assert "avatar_invite=" in cookie and "secure" in cookie.lower()


def test_cookie_is_secure_when_the_invite_base_url_is_https(invite_mode, monkeypatch):
    monkeypatch.setenv("AVATAR_INVITE_BASE_URL", "https://demo.example.org")
    cookie = _invite_cookie("192.168.99.0/24")  # proxy not trusted
    assert "secure" in cookie.lower()


def test_plain_http_without_either_signal_stays_non_secure(invite_mode):
    cookie = _invite_cookie("192.168.99.0/24", {"X-Forwarded-Proto": "https"})  # untrusted claim
    assert "avatar_invite=" in cookie and "secure" not in cookie.lower()
