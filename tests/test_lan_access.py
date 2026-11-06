"""LAN access token, browser request guards, startup banner, and shell update.

No network and no model calls. Loopback stays open. A non-loopback bind
requires APP_ACCESS_TOKEN. The token is the X-Access-Token header only.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from backend import invites

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TOKEN = "lan-access-token-value"
SHORT_TOKEN = "short-token-zzz"
MAIN_CACHE_VERSION = "avatar-democracy-shell-v1"
OVERRIDE = {
    "bill_title": "T",
    "section_id": "s1",
    "position": "yes",
    "previous_position": "no",
}
MASKABLE_BG = (0x1A, 0x1D, 0x27)


@pytest.fixture
def loopback(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    main.reset_rate_limits()
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def lan(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    main.reset_rate_limits()
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def _scope(method: str, path: str, headers: list[tuple[bytes, bytes]], query: bytes = b"", client=("203.0.113.8", 40000)):
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
        "client": client,
        "server": ("testserver", 80),
    }


def _guard(method: str, path: str, headers: list[tuple[bytes, bytes]], query: bytes = b"", client=("203.0.113.8", 40000)):
    return main.guard_api_request(Request(_scope(method, path, headers, query, client)))


def _listener(js: str, name: str) -> str:
    marker = f'addEventListener("{name}"'
    start = js.index(marker)
    rest = js[start + len(marker):]
    nxt = rest.find("addEventListener(")
    if nxt == -1:
        return js[start:]
    return js[start:start + len(marker) + nxt]


def _png_rows(data: bytes) -> tuple[int, int, int, list[bytes]]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    width = height = color_type = None
    idat: list[bytes] = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit, color_type, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            assert bit == 8 and comp == 0 and filt == 0 and inter == 0
        elif tag == b"IDAT":
            idat.append(chunk)
        elif tag == b"IEND":
            break
    assert width and height and color_type in (2, 6)
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows: list[bytes] = []
    index = 0
    prev = bytearray(stride)

    def paeth(a: int, b: int, c: int) -> int:
        estimate = a + b - c
        pa, pb, pc = abs(estimate - a), abs(estimate - b), abs(estimate - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    for _y in range(height):
        filt = raw[index]
        index += 1
        row = bytearray(raw[index:index + stride])
        index += stride
        if filt == 1:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                row[x] = (row[x] + left) & 255
        elif filt == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 255
        elif filt == 3:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 255
        elif filt == 4:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                up = prev[x]
                upper_left = prev[x - channels] if x >= channels else 0
                row[x] = (row[x] + paeth(left, up, upper_left)) & 255
        elif filt != 0:
            raise AssertionError(f"unknown png filter {filt}")
        prev = row
        rows.append(bytes(row))
    return width, height, channels, rows


def test_loopback_api_works_without_a_token(loopback, monkeypatch):
    assert loopback.get("/api/health").status_code == 200
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    for host in ("127.0.0.1", "::1", "localhost", "LOCALHOST"):
        monkeypatch.setattr(main, "bind_host", host)
        response = loopback.get("/api/health")
        assert response.status_code == 200, host
        assert "json" in response.headers["content-type"]


def test_non_loopback_without_a_long_token_refuses_to_start(monkeypatch, capsys):
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    for host in ("127.0.0.1", "::1", "localhost", "LocalHost"):
        main.ensure_access_token(host)

    for host in ("0.0.0.0", "::", "192.168.1.9", "127.0.0.2"):
        with pytest.raises(SystemExit) as exc:
            main.ensure_access_token(host)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "APP_ACCESS_TOKEN" in err
        assert 'python3 -c "import secrets;print(secrets.token_urlsafe(24))"' in err
        assert host in err

    monkeypatch.setenv("APP_ACCESS_TOKEN", SHORT_TOKEN)
    assert len(SHORT_TOKEN) < 16
    with pytest.raises(SystemExit) as exc:
        main.ensure_access_token("10.1.2.3")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert SHORT_TOKEN not in err
    assert 'python3 -c "import secrets;print(secrets.token_urlsafe(24))"' in err

    monkeypatch.setenv("APP_ACCESS_TOKEN", "   ")
    with pytest.raises(SystemExit):
        main.ensure_access_token("0.0.0.0")

    monkeypatch.setenv("APP_ACCESS_TOKEN", f"  {'c' * 16}  ")
    main.ensure_access_token("0.0.0.0")


def test_main_refuses_lan_before_the_port_check(monkeypatch, capsys):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--host", "0.0.0.0"])
    called = {"port": False, "serve": False}
    monkeypatch.setattr(main, "ensure_port_available", lambda host, port: called.__setitem__("port", True))

    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: called.__setitem__("serve", True))

    with pytest.raises(SystemExit):
        main.main()
    err = capsys.readouterr().err
    assert "listening beyond loopback" not in err
    assert 'python3 -c "import secrets;print(secrets.token_urlsafe(24))"' in err
    assert called == {"port": False, "serve": False}


def test_lan_token_gates_api_and_leaves_static_open(lan):
    missing = lan.get("/api/health")
    wrong = lan.get("/api/health", headers={"X-Access-Token": "not-the-right-token"})
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json() == wrong.json() == {"detail": "Unauthorized"}
    assert missing.text == wrong.text
    leaked = missing.text.lower()
    assert TOKEN.lower() not in leaked
    assert "app_access_token" not in leaked
    assert "missing" not in leaked
    assert "invalid" not in leaked
    assert "json" in missing.headers["content-type"]

    ok = lan.get("/api/health", headers={"X-Access-Token": TOKEN})
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"

    for path in (
        "/",
        "/analyze",
        "/manifest.webmanifest",
        "/sw.js",
        "/static/api.js",
        "/static/style.css",
        "/static/icons/icon-192.png",
        "/static/icons/icon-maskable-512.png",
    ):
        opened = lan.get(path)
        assert opened.status_code == 200, path


def test_token_in_the_query_string_is_not_accepted(lan):
    for query in (
        {"X-Access-Token": TOKEN},
        {"x-access-token": TOKEN},
        {"access_token": TOKEN},
        {"token": TOKEN},
    ):
        response = lan.get("/api/health", params=query)
        assert response.status_code == 401, query
    response = lan.get(
        "/api/health",
        params={"access_token": "nope", "X-Access-Token": "also-nope"},
        headers={"X-Access-Token": TOKEN},
    )
    assert response.status_code == 200
    bearer = lan.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert bearer.status_code == 401


def test_header_case_and_duplicate_token_headers(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    assert _guard("GET", "/api/health", [
        (b"host", b"127.0.0.1:8000"),
        (b"X-Access-Token", TOKEN.encode()),
    ]) is None
    blocked = _guard("GET", "/api/health", [
        (b"host", b"127.0.0.1:8000"),
        (b"x-access-token", b"wrong-token-value!!"),
        (b"X-Access-Token", TOKEN.encode()),
    ])
    assert blocked is not None and blocked.status_code == 401
    query = b"X-Access-Token=" + TOKEN.encode() + b"&access_token=" + TOKEN.encode()
    blocked = _guard("GET", "/api/health", [(b"host", b"127.0.0.1:8000")], query=query)
    assert blocked is not None and blocked.status_code == 401
    almost = _guard("GET", "/api/health", [
        (b"host", b"127.0.0.1:8000"),
        (b"x-access-token", (TOKEN + " ").encode()),
    ])
    assert almost is not None and almost.status_code == 401


def test_cross_origin_post_is_forbidden_and_same_origin_is_accepted(loopback, monkeypatch):
    monkeypatch.setattr(main.audit, "log_event", lambda *args, **kwargs: {})
    evil = loopback.post("/api/avatar/override", json=OVERRIDE, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    assert evil.json()["detail"] == "Forbidden"

    null_origin = loopback.post("/api/avatar/override", json=OVERRIDE, headers={"Origin": "null"})
    assert null_origin.status_code == 403

    other_port = loopback.post(
        "/api/avatar/override",
        json=OVERRIDE,
        headers={"Origin": "http://127.0.0.1:9999"},
    )
    assert other_port.status_code == 403

    suffix = loopback.post(
        "/api/avatar/override",
        json=OVERRIDE,
        headers={"Origin": "http://testserver.evil.example"},
    )
    assert suffix.status_code == 403

    same = loopback.post("/api/avatar/override", json=OVERRIDE, headers={"Origin": "http://127.0.0.1:8000"})
    assert same.status_code == 200
    assert same.json()["ok"] is True

    missing = loopback.post("/api/avatar/override", json=OVERRIDE)
    assert missing.status_code == 200

    cased = loopback.post("/api/avatar/override", json=OVERRIDE, headers={"Origin": "HTTP://127.0.0.1:8000"})
    assert cased.status_code == 200


def test_origin_header_case_and_content_type_case(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    evil = _guard("POST", "/api/avatar/override", [
        (b"Host", b"127.0.0.1:8000"),
        (b"Origin", b"http://evil.example"),
        (b"Content-Type", b"application/json"),
    ])
    assert evil is not None and evil.status_code == 403

    mixed = _guard("DELETE", "/api/profile/delegates/taxes", [
        (b"host", b"127.0.0.1:8000"),
        (b"origin", b"http://127.0.0.1:8000"),
        (b"ORIGIN", b"http://evil.example:8000"),
    ])
    assert mixed is not None and mixed.status_code == 403

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        blocked = _guard(method, "/api/profile", [
            (b"host", b"127.0.0.1:8000"),
            (b"ORIGIN", b"https://evil.example:8000"),
            (b"content-type", b"application/json"),
        ])
        assert blocked is not None and blocked.status_code == 403, method

    get_ok = _guard("GET", "/api/health", [
        (b"host", b"127.0.0.1:8000"),
        (b"Origin", b"http://evil.example"),
    ])
    assert get_ok is None

    userinfo = _guard("POST", "/api/avatar/override", [
        (b"host", b"127.0.0.1:8000"),
        (b"origin", b"http://user:pass@127.0.0.1:8000"),
        (b"content-type", b"application/json"),
    ])
    assert userinfo is not None and userinfo.status_code == 403

    both_types = _guard("POST", "/api/avatar/override", [
        (b"host", b"127.0.0.1:8000"),
        (b"content-type", b"text/plain"),
        (b"Content-Type", b"application/json"),
    ])
    assert both_types is not None and both_types.status_code == 415

    prefix = _guard("POST", "/api/avatar/override", [
        (b"host", b"127.0.0.1:8000"),
        (b"content-type", b"application/jsonx"),
    ])
    assert prefix is not None and prefix.status_code == 415

    accepted = _guard("POST", "/api/avatar/override", [
        (b"Host", b"127.0.0.1:8000"),
        (b"Content-Type", b"Application/JSON; charset=UTF-8"),
    ])
    assert accepted is None


def test_wrong_content_type_is_415_and_the_ui_types_pass(loopback, monkeypatch):
    monkeypatch.setattr(main.audit, "log_event", lambda *args, **kwargs: {})
    plain = loopback.post(
        "/api/avatar/override",
        content=json.dumps(OVERRIDE),
        headers={"Content-Type": "text/plain", "Origin": "http://127.0.0.1:8000"},
    )
    assert plain.status_code == 415
    assert plain.json()["detail"] == "Unsupported Media Type"

    urlencoded = loopback.post("/api/analyze", data={"text": "hello"})
    assert urlencoded.status_code == 415

    json_upload = loopback.post(
        "/api/analyze",
        content=json.dumps({"text": "hello"}),
        headers={"Content-Type": "application/json"},
    )
    assert json_upload.status_code == 415

    missing = loopback.post("/api/avatar/override", content=json.dumps(OVERRIDE))
    assert missing.status_code == 415

    charset = loopback.post(
        "/api/avatar/override",
        content=json.dumps(OVERRIDE),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert charset.status_code == 200

    upper = loopback.post(
        "/api/avatar/override",
        content=json.dumps(OVERRIDE),
        headers={"Content-Type": "Application/JSON"},
    )
    assert upper.status_code == 200

    form = loopback.post("/api/analyze", files={"text": (None, "too short")})
    assert form.status_code == 400


def test_lan_banner_names_the_address_and_hides_secrets(monkeypatch, capsys):
    token = "banner-token-DO-NOT-PRINT"
    anthropic = "sk-ant-DO-NOT-PRINT"
    openai_key = "sk-openai-DO-NOT-PRINT"
    monkeypatch.setenv("APP_ACCESS_TOKEN", token)
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setattr(sys, "argv", ["main.py", "--host", "0.0.0.0"])
    monkeypatch.setattr(main, "ensure_port_available", lambda host, port: None)

    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    text = main.format_lan_banner("0.0.0.0", 8123, ["http://192.168.1.20:8123"])
    for piece in (token, anthropic, openai_key, "DO-NOT-PRINT"):
        assert piece not in text
    assert "0.0.0.0:8123" in text
    assert "http://192.168.1.20:8123" in text
    assert "trusted private network" in text
    assert "public Wi-Fi" in text
    assert "configured API keys" in text
    assert "Ctrl+C" in text
    assert text.startswith("+") and text.endswith("+")

    main.main()
    captured = capsys.readouterr()
    assert "0.0.0.0:8123" in captured.err
    assert "listening beyond loopback" in captured.err
    assert "Open from a phone" in captured.err
    for piece in (token, anthropic, openai_key, "DO-NOT-PRINT"):
        assert piece not in captured.err
        assert piece not in captured.out
    assert main.bind_host == "0.0.0.0"
    client = TestClient(main.app, base_url="http://127.0.0.1:8123")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Access-Token": token}).status_code == 200


def test_loopback_main_does_not_print_the_lan_banner(monkeypatch, capsys):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(main, "ensure_port_available", lambda host, port: None)
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    main.main()
    captured = capsys.readouterr()
    assert "listening beyond loopback" not in captured.err
    assert "WARNING" not in captured.err
    assert "127.0.0.1" in captured.out


def test_rate_limit_on_analyse_and_vote_in_lan_mode(lan, monkeypatch):
    monkeypatch.setattr(main.profile_mod, "load_profile", lambda: None)
    clock = {"now": 1_000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])

    for _ in range(main.RATE_LIMIT + 5):
        denied = lan.post("/api/analyze", files={"text": (None, "hi")})
        assert denied.status_code == 401

    statuses = []
    for _ in range(main.RATE_LIMIT + 1):
        response = lan.post(
            "/api/analyze",
            files={"text": (None, "hi")},
            headers={"X-Access-Token": TOKEN},
        )
        statuses.append(response.status_code)
    assert statuses[:-1] == [400] * main.RATE_LIMIT
    assert statuses[-1] == 429
    assert response.json()["detail"] == "Too Many Requests"
    assert response.headers["retry-after"] == "60"

    vote = lan.post(
        "/api/avatar/vote",
        json={"analysis": {}},
        headers={"X-Access-Token": TOKEN},
    )
    assert vote.status_code == 429

    clock["now"] += main.RATE_WINDOW_SECONDS + 1
    again = lan.post(
        "/api/analyze",
        files={"text": (None, "hi")},
        headers={"X-Access-Token": TOKEN},
    )
    assert again.status_code == 400


def test_loopback_is_not_rate_limited(loopback):
    for _ in range(main.RATE_LIMIT + 1):
        response = loopback.post("/api/analyze", files={"text": (None, "too short")})
        assert response.status_code == 400


def test_rate_limit_is_per_ip(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "192.168.1.20")
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    main.reset_rate_limits()
    headers = [
        (b"host", b"127.0.0.1:8000"),
        (b"x-access-token", TOKEN.encode()),
        (b"content-type", b"multipart/form-data; boundary=abc"),
    ]
    for _ in range(main.RATE_LIMIT):
        assert _guard("POST", "/api/analyze", headers, client=("203.0.113.5", 1)) is None
    blocked = _guard("POST", "/api/analyze", headers, client=("203.0.113.5", 1))
    assert blocked is not None and blocked.status_code == 429
    other = _guard("POST", "/api/analyze", headers, client=("203.0.113.6", 1))
    assert other is None


def test_service_worker_waits_for_the_page_to_reload():
    js = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert MAIN_CACHE_VERSION not in js
    assert 'CACHE_VERSION = "avatar-democracy-shell-v5"' in js
    assert "decodeURIComponent" in js
    assert "canonicalPath" in js
    install = _listener(js, "install")
    assert "skipWaiting" not in install
    assert js.count("self.skipWaiting()") == 1
    message = _listener(js, "message")
    assert "SKIP_WAITING" in message
    assert "skipWaiting" in message
    assert 'pathname === "/api" || pathname.startsWith("/api/")' in js
    assert "/static/api.js" in js

    page = (FRONTEND / "pwa.js").read_text(encoding="utf-8")
    assert "Update available, reload" in page
    assert "SKIP_WAITING" in page
    assert "controllerchange" in page
    assert "skipWaiting()" not in page

    helper = (FRONTEND / "api.js").read_text(encoding="utf-8")
    assert "Enter access token" in helper
    assert "X-Access-Token" in helper
    assert "localStorage" in helper
    assert "apiFetch" in helper
    for name in ("app.js", "avatar.js", "vote.js", "bills.js", "chamber.js", "history.js"):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        assert "apiFetch(" in text
        assert "fetch(" not in text
        html_name = "analyze.html" if name == "app.js" else name.replace(".js", ".html")
        html = (FRONTEND / html_name).read_text(encoding="utf-8")
        assert html.index("/static/api.js") < html.index(f"/static/{name}")


def test_maskable_icon_keeps_the_mark_inside_the_safe_zone():
    data = (FRONTEND / "icons" / "icon-maskable-512.png").read_bytes()
    width, height, channels, rows = _png_rows(data)
    assert (width, height, channels) == (512, 512, 3)
    margin = int(512 * 0.10)
    center = (512 - 1) / 2
    radius = 0.4 * 512
    saw_mark = False
    for y, row in enumerate(rows):
        for x in range(width):
            pixel = (row[x * 3], row[x * 3 + 1], row[x * 3 + 2])
            outside_square = x < margin or y < margin or x >= width - margin or y >= height - margin
            if pixel == MASKABLE_BG:
                continue
            saw_mark = True
            assert not outside_square
            distance = (x - center) ** 2 + (y - center) ** 2
            assert distance <= radius ** 2
    assert saw_mark
    for x, y in ((0, 0), (0, 511), (511, 0), (511, 511), (10, 10)):
        row = rows[y]
        assert (row[x * 3], row[x * 3 + 1], row[x * 3 + 2]) == MASKABLE_BG


def test_content_security_policy_blocks_inline_scripts(loopback):
    response = loopback.get("/")
    policy = response.headers["content-security-policy"]
    for part in (
        "default-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ):
        assert part in policy
    assert "unsafe-eval" not in policy
    # Inline scripts stay blocked. Style attributes on the compass and chamber
    # need style-src 'unsafe-inline'; script-src does not include it.
    assert "script-src 'self'" in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy


def test_rebinding_host_is_rejected_and_allowed_host_works(loopback, monkeypatch):
    monkeypatch.setattr(main.audit, "log_event", lambda *args, **kwargs: {})
    spent = {"analyze": 0, "vote": 0}

    def analyze(text):
        spent["analyze"] += 1
        return {"title": "nope"}

    def vote(analysis, profile):
        spent["vote"] += 1
        raise AssertionError("vote should not run")

    monkeypatch.setattr(main.comparison, "analyze_for_api", analyze)
    monkeypatch.setattr(main.avatar, "cast_vote", vote)
    evil = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
    analyze_response = loopback.post(
        "/api/analyze",
        files={"text": (None, "Section 1. " * 20)},
        headers=evil,
    )
    vote_response = loopback.post("/api/avatar/vote", json={"analysis": {"sections": []}}, headers=evil)
    audit_response = loopback.get("/api/audit", headers={"Host": "evil.example:8000"})
    page = loopback.get("/", headers={"Host": "evil.example"})
    assert analyze_response.status_code == 421
    assert vote_response.status_code == 421
    assert audit_response.status_code == 421
    assert page.status_code == 421
    assert analyze_response.json()["detail"] == "Misdirected Request"
    assert spent == {"analyze": 0, "vote": 0}

    allowed = loopback.post(
        "/api/avatar/override",
        json=OVERRIDE,
        headers={"Host": "localhost:8000", "Origin": "http://localhost:8000"},
    )
    assert allowed.status_code == 200
    https_origin = loopback.post(
        "/api/avatar/override",
        json=OVERRIDE,
        headers={"Host": "127.0.0.1:8000", "Origin": "https://127.0.0.1:8000"},
    )
    assert https_origin.status_code == 200


def test_host_allowlist_rejects_rebinding_tricks(loopback, monkeypatch):
    def status(host, **extra):
        headers = {"Host": host, **extra}
        return loopback.get("/api/health", headers=headers).status_code

    assert status("127.0.0.1:8000") == 200
    assert status("127.0.0.1") == 200
    assert status("LOCALHOST:8000") == 200
    assert status("localhost.") == 200
    assert status("[::1]:8000") == 200
    assert status("[::1]") == 200
    assert status("127.0.0.1:9999") == 421
    assert status("evil.example.") == 421
    assert status("evil.example:8000", **{"X-Forwarded-Host": "127.0.0.1:8000"}) == 421
    assert status("notlocalhost") == 421

    duplicate = _guard("GET", "/api/health", [
        (b"host", b"127.0.0.1:8000"),
        (b"Host", b"evil.example:8000"),
    ])
    assert duplicate is not None and duplicate.status_code == 421
    same_twice = _guard("GET", "/", [
        (b"host", b"127.0.0.1:8000"),
        (b"host", b"127.0.0.1:8000"),
    ])
    assert same_twice is not None and same_twice.status_code == 421

    monkeypatch.setenv("ALLOWED_HOSTS", "phone.local, Extra.Example")
    assert status("phone.local:8000") == 200
    assert status("extra.example") == 200
    assert status("other.example") == 421

    mismatched_port = loopback.post(
        "/api/avatar/override",
        json=OVERRIDE,
        headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:9999"},
    )
    assert mismatched_port.status_code == 403


def _origin_allowed(origins: list[bytes], host: bytes = b"127.0.0.1:8080") -> bool:
    headers = [(b"host", host), *((b"origin", value) for value in origins)]
    return main._origin_allowed(Request(_scope("POST", "/api/profile", headers)))


def test_origin_accepts_scheme_default_or_bind_port(monkeypatch):
    """A browser Origin on 443 must pass when the process listens on 8080.

    Railway terminates TLS and forwards to $PORT. The Host allowlist already
    accepts a host with no port. The Origin check must accept that same host
    when the port is the scheme default or the bound port, and nothing else.
    """
    monkeypatch.setattr(main, "bind_port", 8080)
    monkeypatch.setenv("ALLOWED_HOSTS", "allowed-host")

    assert _origin_allowed([b"https://allowed-host"]) is True
    assert _origin_allowed([b"http://allowed-host:8080"]) is True
    assert _origin_allowed([b"https://evil.example"]) is False
    assert _origin_allowed([b"https://evil.example:443"]) is False
    assert _origin_allowed([b"http://evil.example"]) is False
    assert _origin_allowed([b"http://evil.example:80"]) is False
    assert _origin_allowed([b"http://evil.example:8080"]) is False
    assert _origin_allowed([b"https://allowed-host:4443"]) is False
    assert _origin_allowed([b"null"]) is False
    assert _origin_allowed([b"NULL"]) is False
    assert _origin_allowed([b" null "]) is False
    assert _origin_allowed([
        b"https://allowed-host",
        b"https://allowed-host",
    ]) is False
    assert _origin_allowed([
        b"https://allowed-host",
        b"https://evil.example",
    ]) is False
    assert _origin_allowed([b"https://allowed-host, https://allowed-host"]) is False

    # Host headers stay "bound port or no port". A scheme-default port is not
    # a Host match unless it is also the port this process bound.
    assert main.host_header_allowed(["allowed-host"]) is True
    assert main.host_header_allowed(["allowed-host:8080"]) is True
    assert main.host_header_allowed(["allowed-host:443"]) is False
    assert main.host_header_allowed(["allowed-host:80"]) is False
    assert main.host_header_allowed(["evil.example"]) is False


def test_origin_scheme_default_rejects_lookalike_forms(monkeypatch):
    monkeypatch.setattr(main, "bind_port", 8080)
    monkeypatch.setenv("ALLOWED_HOSTS", "allowed-host, [2001:db8::10]")

    assert _origin_allowed([b"https://[::1]"]) is True
    assert _origin_allowed([b"https://[::1]:443"]) is True
    assert _origin_allowed([b"https://[::1]:8080"]) is True
    assert _origin_allowed([b"http://[::1]:80"]) is True
    assert _origin_allowed([b"https://[2001:DB8::10]"]) is True
    assert _origin_allowed([b"https://[2001:db8::1]"]) is False
    assert _origin_allowed([b"https://[::1%25eth0]"]) is False
    assert _origin_allowed([b"https://[::ffff:127.0.0.1]"]) is False
    assert _origin_allowed([b"https://[::1]:4443"]) is False

    assert _origin_allowed([b"https://ALLOWED-HOST"]) is True
    assert _origin_allowed([b"HTTPS://Allowed-Host"]) is True
    assert _origin_allowed([b"https://allowed-host."]) is True
    assert _origin_allowed([b"https://allowed-host.:443"]) is True
    assert _origin_allowed([b"https://127.0.0.1."]) is True
    assert _origin_allowed([b"https://allowed-host:443"]) is True
    assert _origin_allowed([b"http://allowed-host"]) is True
    assert _origin_allowed([b"http://allowed-host:80"]) is True
    assert _origin_allowed([b"https://allowed-host:8080"]) is True
    assert _origin_allowed([b"http://localhost:8080/"]) is True

    assert _origin_allowed([b"https://user:pass@allowed-host"]) is False
    assert _origin_allowed([b"https://user@allowed-host"]) is False
    assert _origin_allowed([b"https://@allowed-host"]) is False
    assert _origin_allowed([b"http://user:pass@allowed-host:8080"]) is False
    assert _origin_allowed([b"https://user:pass@[::1]:443"]) is False
    assert _origin_allowed([b"https://allowed-host:80"]) is False
    assert _origin_allowed([b"http://allowed-host:443"]) is False
    assert _origin_allowed([b"https://allowed-host.evil.example"]) is False
    assert _origin_allowed([b"https://127.0.0.1.allowed-host"]) is False
    assert _origin_allowed([b"https://allowed-host/extra"]) is False
    assert _origin_allowed([b"https://allowed-host#fragment"]) is False
    assert _origin_allowed([]) is True


def test_profile_post_accepts_implicit_https_origin(monkeypatch, tmp_path):
    """POST /api/profile with an invite cookie, the LAN token, and Origin on 443."""
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("ALLOWED_HOSTS", "demo.avatardemocracy.org")
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setattr(main, "bind_port", 8080)
    monkeypatch.setattr(main.audit, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(main.profile_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main.profile_mod, "PROFILE_FILE", tmp_path / "profile.json")
    main.reset_rate_limits()
    invites.close_store()
    try:
        issued = invites.create_invite(label="ada")
        client = TestClient(main.app, base_url="http://127.0.0.1:8080")
        closed = client.post(
            "/api/profile",
            json={"values": "before the cookie"},
            headers={
                "X-Access-Token": TOKEN,
                "Origin": "https://demo.avatardemocracy.org",
            },
        )
        assert closed.status_code == 403
        assert closed.json()["detail"] == "Forbidden"

        assert client.get("/", params={"token": issued.token}, follow_redirects=False).status_code == 303

        no_token = client.post(
            "/api/profile",
            json={"values": "missing token"},
            headers={"Origin": "https://demo.avatardemocracy.org"},
        )
        assert no_token.status_code == 401
        assert no_token.json()["detail"] == "Unauthorized"

        bad_origin = client.post(
            "/api/profile",
            json={"values": "wrong origin"},
            headers={
                "X-Access-Token": TOKEN,
                "Origin": "https://evil.example",
            },
        )
        assert bad_origin.status_code == 403
        assert bad_origin.json()["detail"] == "Forbidden"

        saved = client.post(
            "/api/profile",
            json={"questionnaire_answers": {}, "values": "careful"},
            headers={
                "X-Access-Token": TOKEN,
                "Origin": "https://demo.avatardemocracy.org",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["profile"]["values"] == "careful"
    finally:
        invites.close_store()


def test_lan_bind_allows_its_address_and_rejects_others(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "192.168.1.20")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(main, "machine_host_names", lambda: {"demo-host"})
    client = TestClient(main.app, base_url="http://192.168.1.20:8000")
    assert client.get("/api/health", headers={"X-Access-Token": TOKEN}).status_code == 200
    assert client.get(
        "/api/health",
        headers={"Host": "demo-host:8000", "X-Access-Token": TOKEN},
    ).status_code == 200
    assert client.get(
        "/api/health",
        headers={"Host": "10.1.1.1:8000", "X-Access-Token": TOKEN},
    ).status_code == 421
    assert client.get("/", headers={"Host": "10.1.1.1"}).status_code == 421

    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setattr(main, "lan_interface_ips", lambda: ["203.0.113.10"])
    assert client.get(
        "/api/health",
        headers={"Host": "203.0.113.10:8000", "X-Access-Token": TOKEN},
    ).status_code == 200
    assert client.get(
        "/api/health",
        headers={"Host": "198.51.100.8:8000", "X-Access-Token": TOKEN},
    ).status_code == 421


def test_spending_routes_share_one_bucket(lan, monkeypatch):
    monkeypatch.setattr(main.profile_mod, "load_profile", lambda: None)
    assert main.SPENDING_ROUTES == (
        "POST /api/analyze",
        "POST /api/avatar/vote",
        "POST /api/bills/{bill_id}/vote",
        "POST /api/bills/{bill_id}/endorse",
        "POST /api/bills/{bill_id}/simulate-endorsements",
    )
    calls = [
        lambda: lan.post("/api/analyze", files={"text": (None, "hi")}, headers={"X-Access-Token": TOKEN}),
        lambda: lan.post("/api/avatar/vote", json={"analysis": {}}, headers={"X-Access-Token": TOKEN}),
        lambda: lan.post("/api/bills/missing/vote", headers={"X-Access-Token": TOKEN}),
        lambda: lan.post("/api/bills/missing/endorse", headers={"X-Access-Token": TOKEN}),
        lambda: lan.post(
            "/api/bills/missing/simulate-endorsements",
            json={"count": 1},
            headers={"X-Access-Token": TOKEN},
        ),
    ]
    for call in calls:
        main.reset_rate_limits()
        for _ in range(main.RATE_LIMIT):
            assert call().status_code != 429
        assert call().status_code == 429
    main.reset_rate_limits()
    for _ in range(main.RATE_LIMIT):
        assert lan.post(
            "/api/analyze",
            files={"text": (None, "hi")},
            headers={"X-Access-Token": TOKEN},
        ).status_code != 429
    assert lan.post("/api/bills/missing/endorse", headers={"X-Access-Token": TOKEN}).status_code == 429
    assert lan.post(
        "/api/bills/missing/simulate-endorsements",
        json={"count": 1},
        headers={"X-Access-Token": TOKEN},
    ).status_code == 429
    assert lan.post("/api/bills/missing/vote", headers={"X-Access-Token": TOKEN}).status_code == 429


def test_rate_limit_ignores_forwarded_for_and_caps_ips(lan, monkeypatch):
    headers = {"X-Access-Token": TOKEN}
    for i in range(main.RATE_LIMIT):
        response = lan.post(
            "/api/analyze",
            files={"text": (None, "hi")},
            headers={**headers, "X-Forwarded-For": f"198.51.100.{i}", "X-Real-IP": "203.0.113.9"},
        )
        assert response.status_code == 400
    blocked = lan.post(
        "/api/analyze",
        files={"text": (None, "hi")},
        headers={**headers, "X-Forwarded-For": "203.0.113.50"},
    )
    assert blocked.status_code == 429

    clock = {"now": 10.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(main, "RATE_LIMIT_MAX_IPS", 3)
    main.reset_rate_limits()

    def limited(ip: str) -> bool:
        request = Request(_scope(
            "POST",
            "/api/analyze",
            [(b"host", b"127.0.0.1:8000")],
            client=(ip, 1),
        ))
        return main._rate_limited(request)

    assert limited("203.0.113.1") is False
    assert limited("203.0.113.2") is False
    clock["now"] += main.RATE_WINDOW_SECONDS + 1
    assert limited("203.0.113.3") is False
    assert set(main._rate_hits) == {"203.0.113.3"}
    for i in range(5):
        limited(f"198.51.100.{i}")
    assert len(main._rate_hits) <= 3


_CLASS_HELPERS = (
    "allowClass(",
    "claimMark(",
    "voteOutcomeClass(",
    "passedClass(",
    "currentClass(",
)
_ATTR_HELPERS = _CLASS_HELPERS + (
    "voteDomId(",
    "domIndex(",
    "cssPercent(",
    "cssColor(",
    "escHtml(",
    "htmlNumber(",
)
_ATTR_RE = re.compile(
    r'''(?P<name>class|id|style|title|value|data-[\w-]+)\s*=\s*"(?P<val>[^"]*)"'''
)


def test_templates_do_not_interpolate_raw_values_into_attributes():
    for name in ("app.js", "bills.js", "vote.js"):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        for raw in ("sig-${", "flag-${", "state-${", "vote-${pos}", "pos-${"):
            assert raw not in text, name
        for match in _ATTR_RE.finditer(text):
            allowed = _CLASS_HELPERS if match.group("name") == "class" else _ATTR_HELPERS
            for expr in re.findall(r"\$\{([^}]+)\}", match.group("val")):
                expr = expr.strip()
                assert any(expr.startswith(prefix) for prefix in allowed), (name, match.group(0), expr)
