"""Static-host checks for docs/site.

Parses index.html and reads vercel.json. No network. The page is HTML and
CSS only, so a script element or an inline handler would need a looser
Content-Security-Policy than the one this file requires.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
INDEX = SITE / "index.html"

URL_ATTRS = {"href", "src", "action", "poster", "data", "cite", "formaction"}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'none'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.handlers: list[str] = []
        self.inline_styles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._take(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._take(tag, attrs)

    def _take(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        found = {key.lower(): value or "" for key, value in attrs}
        if name == "script":
            self.scripts.append(found.get("src", ""))
        if name == "style":
            self.inline_styles.append("style")
        if "style" in found:
            self.inline_styles.append(found["style"])
        for key, value in found.items():
            if key.startswith("on") and len(key) > 2:
                self.handlers.append(key)
            if key in URL_ATTRS and value.strip():
                self.urls.append(value.strip())


def _page() -> Page:
    parser = Page()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    parser.close()
    return parser


def test_no_inline_scripts():
    page = _page()
    raw = INDEX.read_text(encoding="utf-8").lower()
    assert page.scripts == []
    assert page.handlers == []
    assert "<script" not in raw
    assert "javascript:" not in raw
    # style-src is 'self', so a <style> block or a style attribute would be blocked.
    assert page.inline_styles == []
    assert "<style" not in raw


def test_no_http_or_localhost_urls_and_local_assets_exist():
    text = INDEX.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "http://" not in lowered
    assert "localhost" not in lowered
    assert "127.0.0.1" not in lowered

    page = _page()
    assert page.urls, "page has no links"
    for url in page.urls:
        lowered_url = url.lower()
        assert not lowered_url.startswith("http://"), url
        assert "localhost" not in lowered_url, url
        assert "127.0.0.1" not in lowered_url, url
        assert not lowered_url.startswith(("javascript:", "data:", "vbscript:", "//")), url
        if url.startswith("#"):
            continue
        if lowered_url.startswith("https://"):
            continue
        path = unquote(url.split("#", 1)[0].split("?", 1)[0])
        assert path and not path.startswith(("/", "\\")) and "\\" not in path, url
        target = (SITE / path).resolve()
        assert target.is_relative_to(SITE.resolve()), url
        assert target.is_file(), url


def test_vercel_json_has_csp():
    config = json.loads((SITE / "vercel.json").read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    # null would auto-detect a build. An empty command is the skip.
    assert config["buildCommand"] == ""
    assert config["framework"] is None
    assert config["cleanUrls"] is True

    found: list[str] = []
    policies: dict[str, str] = {}
    assert config["headers"], "vercel.json has no headers"
    for block in config["headers"]:
        assert block["source"] == "/(.*)"
        for header in block["headers"]:
            key = header["key"].lower()
            policies[key] = header["value"]
            if key == "content-security-policy":
                found.append(header["value"])
    assert found == [CSP]
    assert "unsafe-inline" not in CSP and "unsafe-eval" not in CSP
    assert policies["x-content-type-options"] == "nosniff"
    assert policies["referrer-policy"] == "strict-origin-when-cross-origin"
    assert policies["permissions-policy"] == "camera=(), microphone=(), geolocation=()"

    ignored = (SITE / ".vercelignore").read_text(encoding="utf-8")
    assert "README.md" in ignored.splitlines()
