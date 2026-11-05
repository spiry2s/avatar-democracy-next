"""Phone shell: manifest, icons, viewport, service worker, and bind host.

No network. The default bind address stays on localhost. The service worker
may cache the static shell only; /api is excluded before any cache write.
"""

from __future__ import annotations

import struct
from pathlib import Path

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
CLIENT = TestClient(main.app)
PAGES = sorted(FRONTEND.glob("*.html"))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _route(path: Path) -> str:
    if path.name == "index.html":
        return "/"
    return "/" + path.stem


def _png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(PNG_MAGIC)
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_manifest_is_json_and_has_required_fields():
    response = CLIENT.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    body = response.json()
    assert body["name"] == "Avatar Democracy"
    assert body["short_name"]
    assert body["start_url"] == "/"
    assert body["display"] == "standalone"
    assert body["theme_color"] == "#f3f1ec"
    assert body["background_color"] == "#f3f1ec"
    icons = body["icons"]
    assert isinstance(icons, list) and icons
    sizes = {icon["sizes"] for icon in icons}
    assert "192x192" in sizes
    assert "512x512" in sizes
    assert any("maskable" in (icon.get("purpose") or "") for icon in icons)
    for icon in icons:
        assert icon["src"].startswith("/static/icons/")
        assert icon["type"] == "image/png"


def test_manifest_icons_exist_and_are_served():
    icons = CLIENT.get("/manifest.webmanifest").json()["icons"]
    seen: set[str] = set()
    for icon in icons:
        src = icon["src"]
        seen.add(src)
        file_path = FRONTEND / src.removeprefix("/static/")
        assert file_path.is_file(), src
        response = CLIENT.get(src)
        assert response.status_code == 200, src
        assert response.headers["content-type"].startswith("image/png"), src
        width, height = _png_size(response.content)
        assert f"{width}x{height}" == icon["sizes"], src
        assert response.content == file_path.read_bytes()
    assert "/static/icons/icon-192.png" in seen
    assert "/static/icons/icon-512.png" in seen
    assert any(src.endswith("icon-maskable-512.png") for src in seen)


def test_every_html_page_has_viewport_and_manifest():
    assert PAGES, "frontend HTML pages disappeared"
    for path in PAGES:
        text = path.read_text(encoding="utf-8")
        response = CLIENT.get(_route(path))
        assert response.status_code == 200, path.name
        assert "html" in response.headers["content-type"]
        for source in (text, response.text):
            assert 'name="viewport"' in source, path.name
            assert "width=device-width" in source, path.name
            assert 'rel="manifest"' in source, path.name
            assert "/manifest.webmanifest" in source, path.name
            assert 'name="theme-color"' in source, path.name
            assert 'name="apple-mobile-web-app-capable"' in source, path.name
            assert 'name="apple-mobile-web-app-status-bar-style"' in source, path.name
            assert 'rel="apple-touch-icon"' in source, path.name


def test_service_worker_excludes_api_from_caching():
    response = CLIENT.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    js = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert response.text == js
    assert "CACHE_VERSION" in js
    assert 'pathname === "/api" || pathname.startsWith("/api/")' in js

    fetch_at = js.index('addEventListener("fetch"')
    fetch_body = js[fetch_at:]
    guard_at = fetch_body.index("isApiPath")
    respond_at = fetch_body.index("respondWith")
    assert guard_at < respond_at
    assert "cache.put" not in fetch_body[:respond_at]

    network_first = js[js.index("async function networkFirst"):]
    assert network_first.index("fetch(request)") < network_first.index("cache.put")
    assert "caches.delete" in js
    assert "key !== CACHE_VERSION" in js

    for line in js.splitlines():
        stripped = line.strip().strip('",')
        assert not stripped.startswith('"/api'), line


def test_default_bind_host_is_localhost(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    assert main.resolve_bind_host([]) == "127.0.0.1"
    assert main.resolve_bind_host(["-q"]) == "127.0.0.1"
    assert main.resolve_bind_host([]) != "0.0.0.0"


def test_host_flag_and_env_are_opt_in(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    assert main.resolve_bind_host(["--host", "0.0.0.0"]) == "0.0.0.0"
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert main.resolve_bind_host([]) == "0.0.0.0"
    assert main.resolve_bind_host(["--host", "127.0.0.1"]) == "127.0.0.1"
