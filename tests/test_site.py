"""Checks for the static project page in docs/site/.

No network. The page must not keep a [PLACEHOLDER marker, must not load a
third-party resource, and every relative link must name a file in docs/site/.
GitHub links must point at this repository, and blob links must name a file
that exists here. CI already runs this file with the rest of the suite.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
INDEX = SITE / "index.html"
GITHUB = "https://github.com/spiry2s/avatar-democracy"
PAGE_LOAD = {"link", "script", "img", "source", "iframe", "video", "audio", "object", "embed"}

KEPT = (
    "needs your own API key. The second-vendor check is off unless you switch it on.",
    "Language models can still misread legal text. A quote shows you where to look; "
    "it doesn't prove the summary is right. Always read the bill itself. This isn't legal advice.",
    'The Avatar, bill-lifecycle and "chamber" pages are a demo. '
    "The chamber is a synthetic population, not real voters.",
    "The white paper is a working document, open to critique, with its open problems listed in the paper itself.",
    "Avatar Democracy isn't affiliated with, funded by, or run for any political party, "
    "candidate or campaign. The analyzer's prompts tell it to avoid partisan framing and "
    "are published for anyone to review. Any party may use the code under the MIT license, "
    "but none speaks for the project.",
)


class Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._take(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._take(tag, attrs)

    def _take(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {key: value or "" for key, value in attrs}
        if found.get("id"):
            self.ids.add(found["id"])
        for key in ("href", "src", "data", "poster"):
            if key in found and found[key] != "":
                self.refs.append((tag.lower(), key, found[key].strip()))


def _local_file(rel: str) -> None:
    assert rel and "\\" not in rel and not rel.startswith("/")
    target = (SITE / rel).resolve()
    assert target.is_relative_to(SITE.resolve()), rel
    assert target.is_file(), rel


def _github(url: str) -> None:
    bare, _fragment = urldefrag(url)
    bare = bare.split("?", 1)[0]
    assert bare.startswith(GITHUB), bare
    if bare.rstrip("/") == GITHUB:
        return
    if bare.rstrip("/") == GITHUB + "/issues":
        return
    prefix = GITHUB + "/blob/main/"
    assert bare.startswith(prefix), bare
    rel = unquote(bare[len(prefix):])
    assert rel and not rel.endswith("/") and "\\" not in rel
    target = (ROOT / rel).resolve()
    assert target.is_relative_to(ROOT.resolve()), rel
    assert target.is_file(), rel


def _check_ref(tag: str, value: str, ids: set[str]) -> None:
    lowered = value.lower()
    assert not lowered.startswith(("javascript:", "data:", "vbscript:", "mailto:", "//"))
    if value.startswith("#"):
        assert value[1:] in ids, value
        return
    if lowered.startswith(("http://", "https://")):
        assert tag not in PAGE_LOAD, value
        assert lowered.startswith("https://"), value
        _github(value)
        return
    path, fragment = (value.split("#", 1) + [""])[:2]
    path = unquote(path.split("?", 1)[0])
    _local_file(path)
    if fragment:
        assert fragment in ids, fragment


def test_no_placeholder_text():
    assert "[PLACEHOLDER" not in INDEX.read_text(encoding="utf-8").upper()


def test_no_signup_form_or_script():
    raw = INDEX.read_text(encoding="utf-8").lower()
    for tag in ("<script", "<form", "<input", "<iframe", "<object", "<embed"):
        assert tag not in raw
    assert "subscribe" not in raw
    assert "newsletter" not in raw


def test_required_links_and_kept_caveats():
    text = INDEX.read_text(encoding="utf-8")
    assert 'href="https://github.com/spiry2s/avatar-democracy"' in text
    assert "https://github.com/spiry2s/avatar-democracy/blob/main/ai_avatar_democracy_v1.1.md" in text
    assert "https://github.com/spiry2s/avatar-democracy/issues" in text
    assert "python main.py" in text
    assert "help citizens read the bills that affect them. The Avatar demo explores taking part" in text
    assert "The white paper is a working document, open to critique." in text
    assert "Who can see that feedback depends on who can see the repository: it is public if the repository is public, and while the repository is private only collaborators can see it." in text
    for sentence in KEPT:
        assert sentence in text


def test_abstention_is_not_an_analyzer_feature():
    raw = INDEX.read_text(encoding="utf-8")
    start = raw.index('id="analyzer"')
    body = raw[start:raw.index("</section>", start)].lower()
    assert "abstain" not in body
    caution = raw.lower().split('id="caution"', 1)[1].split("</section>", 1)[0]
    assert "avatar demo" in caution
    assert "bill analyzer" in caution
    assert "abstain" in caution


def test_internal_and_github_links_resolve():
    raw = INDEX.read_text(encoding="utf-8")
    parser = Refs()
    parser.feed(raw)
    parser.close()
    assert parser.refs, "page has no links"
    for tag, _key, value in parser.refs:
        _check_ref(tag, value, parser.ids)

    css_path = SITE / "style.css"
    assert css_path.is_file()
    css = css_path.read_text(encoding="utf-8").lower()
    assert "http://" not in css and "https://" not in css and "//" not in css
    assert "@import" not in css
    assert "url(" not in css
