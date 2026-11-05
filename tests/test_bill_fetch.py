"""Official bill links: allowlist, SSRF checks, extraction. No live network.

The fetcher is mocked at the DNS and socket boundary. Nothing in this file
opens a real connection or calls a model.
"""

from __future__ import annotations

import io
import json
import socket
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from backend import bill_fetch, extractors

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_PDF = FIXTURES / "bill_link_sample.pdf"
EMPTY_PDF = FIXTURES / "bill_link_empty.pdf"
TOKEN = "lan-access-token-value"
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2001:4860:4860::8888"
BILL_TEXT = (
    "Section 1. The agency shall publish legislative records for the public. "
    "Section 2. The duty covers bills, amendments, and the vote tally."
)

EXPECTED_HOSTS = frozenset({
    "sejm.gov.pl",
    "api.sejm.gov.pl",
    "www.sejm.gov.pl",
    "orka.sejm.gov.pl",
    "isap.sejm.gov.pl",
    "stortinget.no",
    "www.stortinget.no",
    "data.stortinget.no",
    "lovdata.no",
    "www.lovdata.no",
    "congress.gov",
    "www.congress.gov",
    "api.congress.gov",
    "govinfo.gov",
    "www.govinfo.gov",
})

ACCEPTED = [
    ("https://api.sejm.gov.pl/eli/acts/DU/2024/1823/text.html", "api.sejm.gov.pl", "/eli/acts/DU/2024/1823/text.html"),
    ("https://www.sejm.gov.pl/sejm10.nsf/agent.xsp", "www.sejm.gov.pl", "/sejm10.nsf/agent.xsp"),
    ("https://orka.sejm.gov.pl/proc10.nsf", "orka.sejm.gov.pl", "/proc10.nsf"),
    ("https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20240001823", "isap.sejm.gov.pl", "/isap.nsf/DocDetails.xsp?id=WDU20240001823"),
    ("https://www.stortinget.no/no/Saker-og-publikasjoner", "www.stortinget.no", "/no/Saker-og-publikasjoner"),
    ("https://data.stortinget.no/eksport/saker", "data.stortinget.no", "/eksport/saker"),
    ("https://lovdata.no/dokument/NL/lov/1995-02-24-12", "lovdata.no", "/dokument/NL/lov/1995-02-24-12"),
    ("https://www.congress.gov/bill/118th-congress/house-bill/7521/text", "www.congress.gov", "/bill/118th-congress/house-bill/7521/text"),
    ("https://api.congress.gov/v3/bill/118/hr/7521", "api.congress.gov", "/v3/bill/118/hr/7521"),
    ("https://govinfo.gov/link/bills/118/hr/7521?link-type=pdf", "govinfo.gov", "/link/bills/118/hr/7521?link-type=pdf"),
    (
        "https://www.govinfo.gov/content/pkg/BILLS-118hr7521ih/pdf/BILLS-118hr7521ih.pdf",
        "www.govinfo.gov",
        "/content/pkg/BILLS-118hr7521ih/pdf/BILLS-118hr7521ih.pdf",
    ),
    ("HTTPS://WWW.CONGRESS.GOV/bill/118", "www.congress.gov", "/bill/118"),
    ("https://www.congress.gov:443/bill/118", "www.congress.gov", "/bill/118"),
    ("https://www.congress.gov/bill#@evil.example", "www.congress.gov", "/bill"),
    ("https://www.congress.gov/search?q=a@b", "www.congress.gov", "/search?q=a@b"),
    ("https://sejm.gov.pl/eli", "sejm.gov.pl", "/eli"),
    ("https://stortinget.no/no", "stortinget.no", "/no"),
    ("https://www.lovdata.no/dokument", "www.lovdata.no", "/dokument"),
    ("https://congress.gov/bill", "congress.gov", "/bill"),
    ("https://www.congress.gov./bill", "www.congress.gov", "/bill"),
    ("https://WWW.CONGRESS.GOV./bill", "www.congress.gov", "/bill"),
    ("www.congress.gov/bill/118", "www.congress.gov", "/bill/118"),
    ("  congress.gov/bill ", "congress.gov", "/bill"),
    ("  https://www.congress.gov/bill  ", "www.congress.gov", "/bill"),
    ("//www.congress.gov/bill", "www.congress.gov", "/bill"),
    ("https://sejm.gov.pl/posłowie?q=łódź", "sejm.gov.pl", "/pos%C5%82owie?q=%C5%82%C3%B3d%C5%BA"),
    ("https://lovdata.no/sok/ønske?år=2024", "lovdata.no", "/sok/%C3%B8nske?%C3%A5r=2024"),
    ("https://www.congress.gov/bill#łódź", "www.congress.gov", "/bill"),
    ("https://" + "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in "www.congress.gov") + "/bill", "www.congress.gov", "/bill"),
    ("https://www\u3002congress\u3002gov/bill", "www.congress.gov", "/bill"),
]

# Each of these must fail before DNS. They are the allowlist bypasses.
REJECTED = [
    "https://example.com/bill",
    "evil.example/bill",
    "https://evil.www.congress.gov/bill",
    "https://www.congress.gov.evil.example/bill",
    "https://api.sejm.gov.pl.evil.example/eli",
    "http://www.congress.gov/bill",
    "https://www.congress.gov:8443/bill",
    "https://www.congress.gov:80/bill",
    "https://www.congress.gov:4430/bill",
    "https://www.congress.gov:0443/bill",
    "https://user:pass@www.congress.gov/bill",
    "https://evil.example@www.congress.gov/bill",
    "https://www.congress.gov@evil.example/bill",
    "https://@www.congress.gov/bill",
    "https://www.congress.gov\\@evil.example/bill",
    "https://evil.example\\@www.congress.gov/bill",
    "https://www.congress.gov\\evil.example/bill",
    "https:\\\\www.congress.gov\\bill",
    "https://www.congress.gov../bill",
    "https://evil.example./",
    "https://www.congress.gov%2eevil.example/",
    "https://www.congress.gov%00.evil.example/",
    "https://www.congress.gov%2f.evil.example/",
    "https://www.congress.gov%40evil.example/",
    "https://%77ww.congress.gov/bill",
    "https://www.xn--cngress-8of.gov/bill",
    "https://xn--sejm-xxx.gov.pl/",
    "https://www.c\u03bfngress.gov/bill",
    "https://www.\u0441\u043engress.gov/bill",
    "https://www.congress.gov\u200b/bill",
    "https://[::1]/",
    "https://[::1]:443/",
    "https://[::ffff:127.0.0.1]/",
    "https://[0:0:0:0:0:ffff:127.0.0.1]/",
    "https://[2001:db8::1]/",
    "https://2130706433/",
    "https://0177.0.0.1/",
    "https://017700000001/",
    "https://0x7f000001/",
    "https://0x7f.0x0.0x0.0x1/",
    "https://127.1/",
    "https://127.0.0.1/",
    "https://10.0.0.1/",
    "https://169.254.169.254/",
    "https://www.congress.gov/bill\r\nHost: evil.example",
    "https://www.congress.gov\t.evil.example/bill",
    "https://www.congress.gov/bill\nX: y",
    "HTTPS://EVIL.EXAMPLE/www.congress.gov",
    "file:///etc/passwd",
    "https://evil.example#www.congress.gov",
]


@pytest.fixture(autouse=True)
def _no_congress_key(monkeypatch):
    """Mapping tests opt in. A developer key must not add a live API call."""
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)


@pytest.fixture
def loopback(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    main.reset_rate_limits()
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def lan(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "0.0.0.0")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    main.reset_rate_limits()
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


class FakeResponse:
    def __init__(self, status: int, headers: list[tuple[str, str]], chunks: list[bytes]):
        self.status = status
        self._headers = list(headers)
        self._chunks = list(chunks)
        self.reads = 0
        self.closed = False

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def read(self, _n: int | None = None) -> bytes:
        self.reads += 1
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeConn:
    def __init__(self, host: str, ip: str, timeout: float, response: FakeResponse):
        self.host = host
        self._ip = ip
        self.timeout = timeout
        self.response = response
        self.requested = None
        self.closed = False
        self.sock = None

    def connect(self) -> None:
        return None

    def request(self, method: str, target: str, body: bytes | None = None, headers: dict | None = None) -> None:
        self.requested = (method, target, body, headers)

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class Scripted:
    def __init__(self, rounds: list[tuple[int, list[tuple[str, str]], list[bytes]]]):
        self.rounds = list(rounds)
        self.calls: list[tuple[str, str, float]] = []
        self.conns: list[FakeConn] = []

    def __call__(self, host: str, ip: str, timeout: float) -> FakeConn:
        self.calls.append((host, ip, timeout))
        if not self.rounds:
            raise AssertionError(f"unexpected connection to {host} via {ip}")
        status, headers, chunks = self.rounds.pop(0)
        conn = FakeConn(host, ip, timeout, FakeResponse(status, headers, chunks))
        self.conns.append(conn)
        return conn


def _forbid_dns(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("DNS was called")

    monkeypatch.setattr(bill_fetch.socket, "getaddrinfo", explode)


def _forbid_httpx(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("httpx was used")

    monkeypatch.setattr(httpx, "Client", explode)


def _dns(monkeypatch, mapping: dict[str, list[str]] | None = None, default: str = PUBLIC_V4) -> list[str]:
    seen: list[str] = []

    def getaddrinfo(host: str, _port: int, *_args, **_kwargs):
        seen.append(host)
        ips = mapping[host] if mapping is not None and host in mapping else [default]
        infos = []
        for ip in ips:
            if ":" in ip:
                infos.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0)))
            else:
                infos.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)))
        return infos

    monkeypatch.setattr(bill_fetch.socket, "getaddrinfo", getaddrinfo)
    return seen


def _plain(body: bytes = BILL_TEXT.encode(), content_type: str = "text/plain; charset=utf-8") -> tuple:
    return (
        200,
        [("Content-Type", content_type), ("Content-Length", str(len(body)))],
        [body],
    )


def _redirect(location: str) -> tuple:
    return (302, [("Location", location)], [b""])


def _pdf(texts: list[str]) -> bytes:
    """One Helvetica page per string. An empty string draws no text."""
    next_id = 3
    page_ids = []
    content_blobs = []
    for text in texts:
        page_ids.append(next_id)
        next_id += 2
        if text:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        else:
            stream = b""
        content_blobs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    font_id = next_id
    page_blobs = []
    for content_id, _text in zip(page_ids, texts):
        # content object is the page id + 1
        page_blobs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    ordered = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {len(texts)} /Kids [{kids}] >>".encode(),
    ]
    for page_blob, content_blob in zip(page_blobs, content_blobs):
        ordered.append(page_blob)
        ordered.append(content_blob)
    ordered.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(ordered, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(ordered) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(ordered) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _html() -> bytes:
    paragraph = BILL_TEXT
    page = (
        "<!DOCTYPE html><html><head><style>.secret{display:none}</style>"
        "<script>alert(1)</script></head><body>"
        "<nav>Skip navigation</nav><header>Site header</header>"
        f"<main><p>{paragraph}</p></main></body></html>"
    )
    return page.encode()


# ── allowlist ─────────────────────────────────────────────────────────────────


def test_allowlist_is_the_documented_set():
    assert bill_fetch.OFFICIAL_BILL_HOSTS == EXPECTED_HOSTS
    source = (ROOT / "backend" / "bill_fetch.py").read_text(encoding="utf-8")
    assert "OFFICIAL_BILL_HOSTS" in source
    assert "no environment variable" in source.lower() or "no ALLOWED_BILL_HOSTS" in source
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for host in EXPECTED_HOSTS:
        assert host in readme
    assert "Paste a bill link" in readme
    assert "OFFICIAL_BILL_HOSTS" in readme


@pytest.mark.parametrize("url,host,target", ACCEPTED)
def test_allowlist_accepts_the_official_url(url, host, target):
    parsed = bill_fetch.parse_official_url(url)
    assert parsed.host == host
    assert parsed.target == target


@pytest.mark.parametrize("url", REJECTED)
def test_bypass_is_rejected_before_dns(monkeypatch, url):
    _forbid_dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document(url)
    assert exc.value.status_code == 400
    assert exc.value.detail == bill_fetch.MSG_UNSUPPORTED


def test_userinfo_and_non_default_port_are_rejected(monkeypatch):
    _forbid_dns(monkeypatch)
    for url in (
        "https://user:pass@www.congress.gov/bill",
        "https://token@api.sejm.gov.pl/eli",
        "https://www.congress.gov:8443/bill",
        "https://lovdata.no:444/dokument",
    ):
        with pytest.raises(bill_fetch.BillSourceError) as exc:
            bill_fetch.fetch_official_document(url)
        assert exc.value.detail == bill_fetch.MSG_UNSUPPORTED


def test_uppercase_official_host_is_fetched_in_lowercase(monkeypatch):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    script = Scripted([_plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document("HTTPS://WWW.Congress.GOV/bill/118")
    assert script.calls[0][0] == "www.congress.gov"
    assert script.calls[0][1] == PUBLIC_V4
    assert script.conns[0].requested[1] == "/bill/118"
    assert document.body == BILL_TEXT.encode()
    assert document.kind == "text"


# ── addresses ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ip", [
    "127.0.0.1",
    "10.1.2.3",
    "169.254.169.254",
    "::1",
    "::ffff:127.0.0.1",
    "::ffff:8.8.8.8",
    "0.0.0.0",
    "100.64.0.1",
    "192.168.1.20",
    "224.0.0.1",
    "fc00::1",
    "fe80::1",
    "198.18.0.1",
    "198.19.255.254",
])
def test_blocked_address_is_not_connected(monkeypatch, ip):
    assert bill_fetch.ip_is_blocked(ip) is True

    def getaddrinfo(_host, _port, *_args, **_kwargs):
        if ":" in ip:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    monkeypatch.setattr(bill_fetch.socket, "getaddrinfo", getaddrinfo)

    def connect(host, resolved, timeout):
        raise AssertionError(f"connected to {resolved}")

    monkeypatch.setattr(bill_fetch, "_new_connection", connect)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.congress.gov/bill")
    assert exc.value.status_code == 400
    assert exc.value.detail == bill_fetch.MSG_UNSAFE
    assert ip not in exc.value.detail


def test_public_address_is_not_blocked_and_a_private_answer_is_skipped(monkeypatch):
    assert bill_fetch.ip_is_blocked(PUBLIC_V4) is False
    assert bill_fetch.ip_is_blocked("8.8.8.8") is False
    assert bill_fetch.ip_is_blocked(PUBLIC_V6) is False
    _dns(monkeypatch, {"www.congress.gov": ["127.0.0.1", "10.2.3.4", PUBLIC_V4]})
    script = Scripted([_plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://www.congress.gov/bill")
    assert script.calls == [("www.congress.gov", PUBLIC_V4, script.calls[0][2])]
    assert all(ip != "127.0.0.1" for _host, ip, _timeout in script.calls)


def test_connection_is_pinned_and_ignores_proxy_env(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:8888")
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    body = BILL_TEXT.encode()
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n" + body
    )
    recorded: dict = {"sent": bytearray()}

    class Raw:
        def settimeout(self, _timeout):
            return None

        def close(self):
            return None

    class MemorySock:
        def settimeout(self, _timeout):
            return None

        def sendall(self, data):
            recorded["sent"] += bytes(data)

        def makefile(self, *_args, **_kwargs):
            return io.BytesIO(raw)

        def close(self):
            return None

        def setsockopt(self, *_args, **_kwargs):
            return None

    def create_connection(address, timeout=None, source_address=None):
        recorded["address"] = address
        recorded["timeout"] = timeout
        recorded["source"] = source_address
        return Raw()

    class Context:
        def wrap_socket(self, sock, server_hostname=None):
            recorded["sni"] = server_hostname
            return MemorySock()

    monkeypatch.setattr(bill_fetch.socket, "create_connection", create_connection)
    monkeypatch.setattr(bill_fetch.ssl, "create_default_context", lambda: Context())
    document = bill_fetch.fetch_official_document("https://www.congress.gov/bill/118")
    assert recorded["address"] == (PUBLIC_V4, 443)
    assert recorded["address"][0] != "127.0.0.1"
    assert 0 < recorded["timeout"] <= bill_fetch.TOTAL_TIMEOUT_SECONDS
    assert recorded["sni"] == "www.congress.gov"
    sent = bytes(recorded["sent"])
    assert b"GET /bill/118 HTTP/1.1\r\n" in sent
    assert b"Host: www.congress.gov\r\n" in sent
    assert b"127.0.0.1" not in sent
    assert b"Proxy-Authorization" not in sent
    assert b"Authorization:" not in sent
    assert b"Cookie:" not in sent
    assert document.body == body
    assert document.host == "www.congress.gov"


def test_percent_encoded_crlf_stays_in_the_path(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([_plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://www.congress.gov/bill%0d%0aX-Injected:%20yes")
    target = script.conns[0].requested[1]
    assert target == "/bill%0d%0aX-Injected:%20yes"
    assert "\r" not in target and "\n" not in target
    headers = script.conns[0].requested[3]
    assert "X-Injected" not in headers
    assert "Authorization" not in headers
    assert "Cookie" not in headers


# ── redirects ─────────────────────────────────────────────────────────────────


def test_redirect_off_the_allowlist_is_not_followed(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([_redirect("https://evil.example/secret")])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.congress.gov/start")
    assert exc.value.status_code == 400
    assert exc.value.detail == bill_fetch.MSG_UNSUPPORTED
    assert [call[0] for call in script.calls] == ["www.congress.gov"]


@pytest.mark.parametrize("location", [
    "http://www.congress.gov/next",
    "https://user:pass@www.congress.gov/next",
    "https://www.congress.gov:444/next",
    "https://www.congress.gov.evil.example/next",
    "https://127.0.0.1/next",
    "https://169.254.169.254/latest",
    "//evil.example/next",
    "https://www.congress.gov@evil.example/secret",
    "file:///etc/passwd",
    "https://www.congress.gov%2eevil.example/next",
])
def test_redirect_hop_is_revalidated(monkeypatch, location):
    _dns(monkeypatch)
    script = Scripted([_redirect(location)])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://api.congress.gov/start")
    assert exc.value.detail == bill_fetch.MSG_UNSUPPORTED
    assert len(script.calls) == 1


def test_redirect_to_a_private_address_is_blocked(monkeypatch):
    _dns(monkeypatch, {
        "www.congress.gov": [PUBLIC_V4],
        "api.congress.gov": ["169.254.169.254"],
    })
    script = Scripted([_redirect("https://api.congress.gov/secret")])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.congress.gov/start")
    assert exc.value.detail == bill_fetch.MSG_UNSAFE
    assert [call[0] for call in script.calls] == ["www.congress.gov"]


def test_three_redirects_are_allowed_and_a_fourth_is_not(monkeypatch):
    _dns(monkeypatch)
    ok = Scripted([
        _redirect("https://api.congress.gov/a"),
        _redirect("https://www.congress.gov/b"),
        _redirect("https://api.congress.gov/c"),
        _plain(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", ok)
    document = bill_fetch.fetch_official_document("https://www.congress.gov/start")
    assert document.body == BILL_TEXT.encode()
    assert len(ok.calls) == 4

    blocked = Scripted([
        _redirect("https://api.congress.gov/a"),
        _redirect("https://www.congress.gov/b"),
        _redirect("https://api.congress.gov/c"),
        _redirect("https://www.congress.gov/d"),
        _plain(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", blocked)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.congress.gov/start")
    assert exc.value.status_code == 400
    assert exc.value.detail == bill_fetch.MSG_TOO_MANY
    assert exc.value.code == "too_many_redirects"
    assert len(blocked.calls) == 4


def test_relative_redirect_stays_on_the_official_host(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([_redirect("/next-bill"), _plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://lovdata.no/dokument/a")
    assert [call[0] for call in script.calls] == ["lovdata.no", "lovdata.no"]
    assert script.conns[1].requested[1] == "/next-bill"


def test_redirect_hop_uses_the_same_normalisation(monkeypatch):
    _dns(monkeypatch)
    dotted = Scripted([_redirect("https://www.congress.gov./next"), _plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", dotted)
    bill_fetch.fetch_official_document("https://congress.gov/start")
    assert [call[0] for call in dotted.calls] == ["congress.gov", "www.congress.gov"]
    assert dotted.conns[1].requested[1] == "/next"

    letters = Scripted([_redirect("https://sejm.gov.pl/posłowie?q=łódź"), _plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", letters)
    bill_fetch.fetch_official_document("https://www.sejm.gov.pl/start")
    assert letters.calls[1][0] == "sejm.gov.pl"
    assert letters.conns[1].requested[1] == "/pos%C5%82owie?q=%C5%82%C3%B3d%C5%BA"


# ── limits ────────────────────────────────────────────────────────────────────


def test_oversize_stream_is_aborted_even_without_content_length(monkeypatch):
    monkeypatch.setattr(bill_fetch, "MAX_DOWNLOAD_BYTES", 1000)
    _dns(monkeypatch)
    chunks = [b"a" * 400 for _ in range(8)]
    response_headers = [("Content-Type", "text/plain")]
    script = Scripted([(200, response_headers, chunks)])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.congress.gov/big")
    assert exc.value.status_code == 413
    assert exc.value.detail == bill_fetch.MSG_TOO_BIG
    response = script.conns[0].response
    assert response.closed is True
    assert response.reads < 8
    assert script.conns[0].closed is True


def test_content_length_over_the_cap_does_not_download_the_body(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([(
        200,
        [("Content-Type", "text/plain"), ("Content-Length", str(bill_fetch.MAX_DOWNLOAD_BYTES + 1))],
        [b"a" * 100],
    )])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.stortinget.no/big")
    assert exc.value.status_code == 413
    assert script.conns[0].response.reads == 0
    assert bill_fetch.MAX_DOWNLOAD_BYTES == 15 * 1024 * 1024


def test_a_short_content_length_does_not_disable_the_stream_cap(monkeypatch):
    monkeypatch.setattr(bill_fetch, "MAX_DOWNLOAD_BYTES", 500)
    _dns(monkeypatch)
    chunks = [b"b" * 200 for _ in range(6)]
    script = Scripted([(200, [("Content-Type", "text/plain"), ("Content-Length", "10")], chunks)])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://data.stortinget.no/big")
    assert exc.value.status_code == 413
    assert script.conns[0].response.reads < 6


def test_timeout_is_a_handled_error(monkeypatch):
    _dns(monkeypatch)

    class Slow(FakeResponse):
        def read(self, _n=None):
            self.reads += 1
            raise socket.timeout("timed out")

    def factory(host, ip, timeout):
        return FakeConn(host, ip, timeout, Slow(200, [("Content-Type", "text/plain")], []))

    monkeypatch.setattr(bill_fetch, "_new_connection", factory)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://isap.sejm.gov.pl/slow")
    assert exc.value.status_code == 504
    assert exc.value.detail == bill_fetch.MSG_TIMEOUT


def test_dns_timeout_does_not_connect(monkeypatch):
    monkeypatch.setattr(bill_fetch, "TOTAL_TIMEOUT_SECONDS", 0.2)
    started = threading.Event()
    release = threading.Event()
    holder: dict = {}

    def hang(*_args, **_kwargs):
        holder["thread"] = threading.current_thread()
        started.set()
        release.wait(2)
        return []

    monkeypatch.setattr(bill_fetch.socket, "getaddrinfo", hang)

    def connect(*_args, **_kwargs):
        raise AssertionError("connected")

    monkeypatch.setattr(bill_fetch, "_new_connection", connect)
    try:
        with pytest.raises(bill_fetch.BillSourceError) as exc:
            bill_fetch.fetch_official_document("https://orka.sejm.gov.pl/slow")
        assert exc.value.status_code == 504
        assert exc.value.detail == bill_fetch.MSG_TIMEOUT
        assert started.wait(1)
    finally:
        release.set()
        thread = holder.get("thread")
        if thread is not None:
            thread.join(2)


def test_wrong_content_type_is_rejected_without_reading_the_body(monkeypatch):
    _dns(monkeypatch)
    for media in ("image/png", "application/octet-stream", "application/json", "text/xml"):
        script = Scripted([(200, [("Content-Type", media), ("Content-Length", "8")], [b"not-text"])])
        monkeypatch.setattr(bill_fetch, "_new_connection", script)
        with pytest.raises(bill_fetch.BillSourceError) as exc:
            bill_fetch.fetch_official_document("https://www.congress.gov/file")
        assert exc.value.status_code == 400
        assert exc.value.detail == bill_fetch.MSG_TYPE
        assert script.conns[0].response.reads == 0


def test_missing_content_type_is_rejected(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([(200, [("Content-Length", "4")], [b"text"])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://lovdata.no/x")
    assert exc.value.detail == bill_fetch.MSG_TYPE


def test_gzip_is_decoded_and_a_bomb_is_capped(monkeypatch):
    import gzip

    monkeypatch.setattr(bill_fetch, "MAX_DOWNLOAD_BYTES", 1000)
    _dns(monkeypatch)
    plain = BILL_TEXT.encode()
    assert len(plain) < 1000
    script = Scripted([(
        200,
        [("Content-Type", "text/plain"), ("Content-Encoding", "gzip")],
        [gzip.compress(plain)],
    )])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document("https://api.sejm.gov.pl/eli/acts")
    assert document.body == plain

    bomb = gzip.compress(b"A" * 5000)
    script = Scripted([(
        200,
        [("Content-Type", "text/plain"), ("Content-Encoding", "gzip"), ("Content-Length", str(len(bomb)))],
        [bomb],
    )])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://api.sejm.gov.pl/eli/acts")
    assert exc.value.status_code == 413


def test_brotli_is_decoded_and_a_bomb_is_capped(monkeypatch):
    import brotli

    monkeypatch.setattr(bill_fetch, "MAX_DOWNLOAD_BYTES", 1000)
    _dns(monkeypatch)
    plain = BILL_TEXT.encode()
    assert len(plain) < 1000
    encoded = brotli.compress(plain)
    script = Scripted([(
        200,
        [("Content-Type", "text/plain"), ("Content-Encoding", "br")],
        [encoded],
    )])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document("https://www.lovdata.no/dokument")
    assert document.body == plain

    bomb = brotli.compress(b"A" * 5000)
    script = Scripted([(
        200,
        [("Content-Type", "text/plain"), ("Content-Encoding", "br"), ("Content-Length", str(len(bomb)))],
        [bomb],
    )])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.lovdata.no/dokument")
    assert exc.value.status_code == 413


# ── extraction ────────────────────────────────────────────────────────────────


def test_html_strips_script_style_and_nav(monkeypatch):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    body = _html()
    script = Scripted([(200, [("Content-Type", "text/html; charset=utf-8")], [body])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    text = extractors.extract_text("https://www.sejm.gov.pl/bill", "url")
    assert "alert(1)" not in text
    assert "<script" not in text.lower()
    assert "Skip navigation" not in text
    assert ".secret" not in text
    assert "Site header" not in text
    assert "shall publish legislative records" in text


def test_fixture_pdf_extracts_its_text_layer():
    data = SAMPLE_PDF.read_bytes()
    assert data.startswith(b"%PDF")
    text = extractors.extract_text(data, "pdf")
    assert "public records" in text
    assert "duty to publish legislative text" in text
    assert "<" not in text


def test_empty_pdf_asks_for_a_text_file():
    text_error = None
    try:
        extractors.extract_text(EMPTY_PDF.read_bytes(), "pdf")
    except bill_fetch.BillSourceError as exc:
        text_error = exc
    assert text_error is not None
    assert text_error.status_code == 400
    assert text_error.detail == extractors.MSG_NO_TEXT_LAYER
    assert "no text layer" in text_error.detail
    assert "text file" in text_error.detail

    short = _pdf(["scan"])
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        extractors.extract_text(short, "pdf")
    assert exc.value.detail == extractors.MSG_NO_TEXT_LAYER


def test_pdf_page_and_character_caps(monkeypatch):
    long_page = "A" * 80 + "KEEP" + "B" * 80 + "DROP-TAIL"
    data = _pdf([long_page, "PAGE-TWO-MARKER " + ("C" * 120)])
    monkeypatch.setattr(extractors, "PDF_MAX_PAGES", 1)
    text = extractors.extract_text(data, "pdf")
    assert "PAGE-TWO-MARKER" not in text
    assert "KEEP" in text

    monkeypatch.setattr(extractors, "PDF_MAX_PAGES", 10)
    monkeypatch.setattr(extractors, "PDF_MAX_CHARS", 120)
    capped = extractors.extract_text(data, "pdf")
    assert len(capped) <= 120
    assert len(capped) >= extractors.MIN_BILL_CHARS
    assert "DROP-TAIL" not in capped
    assert "PAGE-TWO-MARKER" not in capped


def test_url_pdf_uses_the_same_extractor_as_an_upload(monkeypatch):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    data = SAMPLE_PDF.read_bytes()
    script = Scripted([(200, [("Content-Type", "application/pdf")], [data])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    from_url = extractors.extract_text("https://api.congress.gov/v3/bill/118/hr/1/text?format=pdf", "url")
    from_file = extractors.extract_text(data, "pdf")
    assert from_url == from_file
    assert "public records" in from_url


# ── route ─────────────────────────────────────────────────────────────────────


def test_analyze_url_feeds_the_existing_pipeline(loopback, monkeypatch):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    data = SAMPLE_PDF.read_bytes()
    script = Scripted([(200, [("Content-Type", "application/pdf")], [data])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    seen = {}

    def analyze(text):
        seen["text"] = text
        return {"title": "Fixture", "plain_summary": "ok"}

    monkeypatch.setattr(main.comparison, "analyze_for_api", analyze)
    response = loopback.post(
        "/api/analyze",
        files={"url": (None, "https://www.congress.gov/bill/118th-congress/house-bill/1/text")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["source_chars"] == len(seen["text"])
    assert seen["text"] == extractors.extract_text(data, "pdf")
    assert body["analysis"]["title"] == "Fixture"


def test_analyze_html_url_passes_plain_text_not_markup(loopback, monkeypatch):
    _dns(monkeypatch)
    script = Scripted([(200, [("Content-Type", "text/html")], [_html()])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    seen = {}
    monkeypatch.setattr(main.comparison, "analyze_for_api", lambda text: seen.setdefault("text", text) or {"title": "T"})
    response = loopback.post(
        "/api/analyze",
        files={"url": (None, "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=1")},
    )
    assert response.status_code == 200
    assert "alert(1)" not in seen["text"]
    assert "<script" not in seen["text"].lower()
    assert "shall publish" in seen["text"]
    assert response.headers["content-type"].startswith("application/json")


def test_analyze_rejects_an_unsupported_url_without_calling_the_model(loopback, monkeypatch):
    _forbid_dns(monkeypatch)

    def analyze(_text):
        raise AssertionError("model was called")

    monkeypatch.setattr(main.comparison, "analyze_for_api", analyze)
    response = loopback.post("/api/analyze", files={"url": (None, "https://example.com/bill.pdf")})
    assert response.status_code == 400
    assert response.json()["detail"] == bill_fetch.MSG_UNSUPPORTED
    assert response.json()["code"] == "unsupported"


def test_analyze_reports_an_empty_pdf_and_a_timeout_and_a_bad_type(loopback, monkeypatch):
    _dns(monkeypatch)

    def analyze(_text):
        raise AssertionError("model was called")

    monkeypatch.setattr(main.comparison, "analyze_for_api", analyze)

    empty = Scripted([(200, [("Content-Type", "application/pdf")], [EMPTY_PDF.read_bytes()])])
    monkeypatch.setattr(bill_fetch, "_new_connection", empty)
    response = loopback.post("/api/analyze", files={"url": (None, "https://www.congress.gov/scan.pdf")})
    assert response.status_code == 400
    assert response.json()["detail"] == extractors.MSG_NO_TEXT_LAYER

    class Slow(FakeResponse):
        def read(self, _n=None):
            raise socket.timeout("timed out")

    def slow_factory(host, ip, timeout):
        conn = FakeConn(host, ip, timeout, Slow(200, [("Content-Type", "application/pdf")], []))
        return conn

    monkeypatch.setattr(bill_fetch, "_new_connection", slow_factory)
    timed = loopback.post("/api/analyze", files={"url": (None, "https://lovdata.no/dokument/slow")})
    assert timed.status_code == 504
    assert timed.json()["detail"] == bill_fetch.MSG_TIMEOUT

    png = Scripted([(200, [("Content-Type", "image/png")], [b"\x89PNG"])])
    monkeypatch.setattr(bill_fetch, "_new_connection", png)
    rejected = loopback.post("/api/analyze", files={"url": (None, "https://data.stortinget.no/file")})
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == bill_fetch.MSG_TYPE


def test_analyze_aborts_an_oversize_url(loopback, monkeypatch):
    monkeypatch.setattr(bill_fetch, "MAX_DOWNLOAD_BYTES", 300)
    _dns(monkeypatch)
    monkeypatch.setattr(main.comparison, "analyze_for_api", lambda _text: (_ for _ in ()).throw(AssertionError("model")))
    script = Scripted([(200, [("Content-Type", "text/plain")], [b"a" * 200, b"b" * 200, b"c" * 200])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    response = loopback.post("/api/analyze", files={"url": (None, "https://www.stortinget.no/big")})
    assert response.status_code == 413
    assert response.json()["detail"] == bill_fetch.MSG_TOO_BIG
    assert script.conns[0].response.closed is True


def test_bill_link_requires_the_token_and_the_shared_rate_limit_on_lan(lan, monkeypatch):
    def no_resolve(*_args, **_kwargs):
        raise AssertionError("DNS was called")

    monkeypatch.setattr(bill_fetch, "resolve_pinned_ip", no_resolve)

    def explode(*_args, **_kwargs):
        raise AssertionError("fetcher ran")

    original_fetch = bill_fetch.fetch_official_document
    monkeypatch.setattr(bill_fetch, "fetch_official_document", explode)
    denied = lan.post("/api/analyze", files={"url": (None, "https://www.congress.gov/bill")})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "Unauthorized"

    origin = lan.post(
        "/api/analyze",
        files={"url": (None, "https://www.congress.gov/bill")},
        headers={"X-Access-Token": TOKEN, "Origin": "https://evil.example"},
    )
    assert origin.status_code == 403

    typed = lan.post(
        "/api/analyze",
        data={"url": "https://www.congress.gov/bill"},
        headers={"X-Access-Token": TOKEN},
    )
    assert typed.status_code == 415

    host = main.guard_api_request(main.Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/analyze",
        "raw_path": b"/api/analyze",
        "query_string": b"",
        "headers": [
            (b"host", b"evil.example"),
            (b"x-access-token", TOKEN.encode()),
            (b"content-type", b"multipart/form-data; boundary=abc"),
        ],
        "client": ("203.0.113.9", 40000),
        "server": ("testserver", 80),
    }))
    assert host is not None and host.status_code == 421

    # Allowlist rejection happens inside the real fetcher, before DNS.
    monkeypatch.setattr(bill_fetch, "fetch_official_document", original_fetch)
    statuses = []
    for _ in range(main.RATE_LIMIT):
        response = lan.post(
            "/api/analyze",
            files={"url": (None, "https://example.com/bill")},
            headers={"X-Access-Token": TOKEN},
        )
        statuses.append(response.status_code)
    assert statuses == [400] * main.RATE_LIMIT
    blocked = lan.post(
        "/api/analyze",
        files={"url": (None, "https://www.congress.gov/bill")},
        headers={"X-Access-Token": TOKEN},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too Many Requests"
    assert blocked.headers["retry-after"] == "60"


def test_loopback_bill_link_does_not_require_a_token(loopback, monkeypatch):
    _forbid_dns(monkeypatch)
    response = loopback.post("/api/analyze", files={"url": (None, "https://example.com/bill")})
    assert response.status_code == 400
    assert response.json()["detail"] == bill_fetch.MSG_UNSUPPORTED


# ── page ──────────────────────────────────────────────────────────────────────


def test_analyze_page_offers_a_bill_link_without_inline_script():
    html = (ROOT / "frontend" / "analyze.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert 'id="bill-url"' in html
    assert "Paste a bill link" in html
    assert "Supported sources" in html
    assert "download the file and upload it" in html
    assert "Drop a PDF here, or click to select" in html
    assert "Paste bill text here..." in html
    assert "<script>" not in html.lower()
    assert "onclick=" not in html.lower()
    assert "formData.append('url', url)" in js
    assert "Fetching the bill" in js
    assert "errorText.textContent = msg" in js
    assert "matchMedia" not in js
    assert "apiFetch(" in js
    assert "fetch(" not in js
    assert "@media (prefers-color-scheme: dark)" in css
    panel = css.split(".link-panel {", 1)[1].split("}", 1)[0]
    assert "#" not in panel
    assert "var(--" in panel


# ── Sejm and Congress PDF mapping, and bot walls ──────────────────────────────

CONGRESS_KEY = "congress-test-key-0123456789"
_LEAKS = ("cloudflare", "imperva", "incapsula", "cf-ray", "cf-mitigated", "traceback", "198.18")


def _json_body(payload: dict) -> tuple:
    body = json.dumps(payload).encode()
    return (
        200,
        [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))],
        [body],
    )


def _pdf_ok() -> tuple:
    body = b"%PDF-1.4\n" + BILL_TEXT.encode()
    return (200, [("Content-Type", "application/pdf"), ("Content-Length", str(len(body)))], [body])


def _assert_blocked(err: bill_fetch.BillSourceError, code: str, status: int) -> None:
    assert err.detail == bill_fetch.MSG_BLOCKED
    assert err.code == code
    assert err.status_code == status
    lowered = err.detail.lower()
    for leak in _LEAKS:
        assert leak not in lowered
    assert "https://api.sejm.gov.pl/sejm/term10/prints/3305/3305.pdf" in err.detail
    assert "paste the bill text" in lowered
    assert code in {"source_blocked", "redirect_loop", "too_many_redirects"}


def test_fetch_limits_are_unchanged():
    assert bill_fetch.MAX_REDIRECTS == 3
    assert bill_fetch.TOTAL_TIMEOUT_SECONDS == 10.0
    assert bill_fetch.MAX_DOWNLOAD_BYTES == 15 * 1024 * 1024
    assert bill_fetch.METADATA_MAX_BYTES == 256 * 1024
    assert bill_fetch.METADATA_MAX_BYTES < bill_fetch.MAX_DOWNLOAD_BYTES
    assert bill_fetch.ip_is_blocked("198.18.0.1") is True
    assert bill_fetch.ip_is_blocked("198.19.255.254") is True
    assert bill_fetch.ip_is_blocked("198.20.0.1") is False
    assert "198.18.0.0/15" in (ROOT / "backend" / "bill_fetch.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("url", [
    "https://govinfo.gov.evil.example/pkg",
    "https://www.govinfo.gov.evil.example/content/pkg/x",
    "https://evil.govinfo.gov/pkg",
    "https://notgovinfo.gov/pkg",
    "https://govinfo.com/pkg",
    "https://govinfo.gov.pl/pkg",
    "https://api.govinfo.gov/packages/x",
    "https://www.govinfo.gov.attacker.test/link/bills/118/hr/1",
])
def test_govinfo_lookalikes_are_rejected_before_dns(monkeypatch, url):
    _forbid_dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document(url)
    assert exc.value.detail == bill_fetch.MSG_UNSUPPORTED
    assert exc.value.code == "unsupported"


def test_govinfo_matches_the_two_hosts_exactly():
    bare = bill_fetch.parse_official_url("https://govinfo.gov/link/bills/118/hr/1?link-type=pdf")
    www = bill_fetch.parse_official_url(
        "https://WWW.GOVINFO.GOV./content/pkg/BILLS-118hr1ih/pdf/BILLS-118hr1ih.pdf"
    )
    assert bare.host == "govinfo.gov"
    assert www.host == "www.govinfo.gov"
    assert bare.host in bill_fetch.OFFICIAL_BILL_HOSTS
    assert www.host in bill_fetch.OFFICIAL_BILL_HOSTS
    assert "api.govinfo.gov" not in bill_fetch.OFFICIAL_BILL_HOSTS
    assert "govinfo.gov.evil.example" not in bill_fetch.OFFICIAL_BILL_HOSTS


@pytest.mark.parametrize("url,term,number", [
    ("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3305", "10", "3305"),
    ("https://sejm.gov.pl/sejm9.nsf/druk.xsp?NR=19-001&foo=1", "9", "19-001"),
    ("https://WWW.SEJM.GOV.PL/Sejm10.nsf/druk.xsp?nr=3305", "10", "3305"),
    ("https://api.sejm.gov.pl/sejm/term10/prints/3305", "10", "3305"),
    ("https://api.sejm.gov.pl/sejm/term10/prints/3305/", "10", "3305"),
    ("https://orka.sejm.gov.pl/proc10.nsf/opisy/3305.htm", "10", "3305"),
    ("https://orka.sejm.gov.pl/proc6.nsf/opisy/2668.html", "6", "2668"),
])
def test_sejm_urls_are_fetched_as_the_print_pdf(monkeypatch, url, term, number):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    payload = {
        "number": number,
        "term": int(term),
        "attachments": [f"{number}-uzas.docx", f"{number}-uzas.pdf", f"{number}.pdf"],
    }
    script = Scripted([_json_body(payload), _pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document(url)
    assert [call[0] for call in script.calls] == ["api.sejm.gov.pl", "api.sejm.gov.pl"]
    assert all(call[1] == PUBLIC_V4 for call in script.calls)
    assert script.conns[0].requested[1] == f"/sejm/term{term}/prints/{number}"
    assert script.conns[0].requested[3]["Accept"] == "application/json"
    assert "X-Api-Key" not in script.conns[0].requested[3]
    assert script.conns[1].requested[1] == f"/sejm/term{term}/prints/{number}/{number}.pdf"
    assert script.conns[1].requested[3]["Accept"] == bill_fetch._REQUEST_HEADERS["Accept"]
    assert document.kind == "pdf"
    assert document.host == "api.sejm.gov.pl"


def test_sejm_uses_the_only_pdf_attachment_and_rejects_an_unsafe_name(monkeypatch):
    _dns(monkeypatch)
    only = Scripted([
        _json_body({"attachments": ["3305-uzas.docx", "3305-uzas.PDF"]}),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", only)
    bill_fetch.fetch_official_document("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3305")
    assert only.conns[1].requested[1] == "/sejm/term10/prints/3305/3305-uzas.PDF"

    unsafe = Scripted([
        _json_body({"attachments": [
            "https://evil.example/a.pdf",
            "../3305.pdf",
            "3305.pdf/../../evil.pdf",
            "note.docx",
        ]}),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", unsafe)
    bill_fetch.fetch_official_document("https://api.sejm.gov.pl/sejm/term10/prints/3305")
    assert unsafe.conns[1].requested[1] == "/sejm/term10/prints/3305/3305.pdf"
    assert all(call[0] == "api.sejm.gov.pl" for call in unsafe.calls)


def test_sejm_metadata_failure_falls_back_to_the_predictable_pdf(monkeypatch):
    _dns(monkeypatch)
    failed = Scripted([
        (502, [("Content-Type", "text/plain")], [b"upstream"]),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", failed)
    document = bill_fetch.fetch_official_document("https://orka.sejm.gov.pl/proc10.nsf/opisy/3305.htm")
    assert document.kind == "pdf"
    assert failed.conns[1].requested[1] == "/sejm/term10/prints/3305/3305.pdf"

    huge = str(bill_fetch.METADATA_MAX_BYTES + 1)
    capped = Scripted([
        (200, [("Content-Type", "application/json"), ("Content-Length", huge)], [b"{" * 100]),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", capped)
    bill_fetch.fetch_official_document("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3305")
    assert capped.conns[0].response.reads == 0
    assert capped.conns[1].requested[1] == "/sejm/term10/prints/3305/3305.pdf"


def test_sejm_metadata_does_not_connect_to_a_benchmarking_address(monkeypatch):
    _dns(monkeypatch, {"api.sejm.gov.pl": ["198.18.0.1"]})

    def connect(*_args, **_kwargs):
        raise AssertionError("connected")

    monkeypatch.setattr(bill_fetch, "_new_connection", connect)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3305")
    assert exc.value.detail == bill_fetch.MSG_UNSAFE
    assert "198.18" not in exc.value.detail


def test_an_existing_sejm_pdf_and_an_opaque_orka_link_are_not_rewritten(monkeypatch):
    _dns(monkeypatch)
    direct = Scripted([_pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", direct)
    bill_fetch.fetch_official_document("https://api.sejm.gov.pl/sejm/term10/prints/3305/3305.pdf")
    assert direct.calls[0][0] == "api.sejm.gov.pl"
    assert direct.conns[0].requested[1] == "/sejm/term10/prints/3305/3305.pdf"

    orka = Scripted([_pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", orka)
    file_url = "https://orka.sejm.gov.pl/Druki10ka.nsf/0/ABC/$file/3305.pdf"
    bill_fetch.fetch_official_document(file_url)
    assert orka.calls[0][0] == "orka.sejm.gov.pl"
    assert orka.conns[0].requested[1] == "/Druki10ka.nsf/0/ABC/$file/3305.pdf"

    opaque = Scripted([_plain()])
    monkeypatch.setattr(bill_fetch, "_new_connection", opaque)
    page = "https://orka.sejm.gov.pl/Home.nsf/druk.xsp?documentId=E52B3D8D11B543D9C1258DB80062C91C"
    bill_fetch.fetch_official_document(page)
    assert opaque.calls[0][0] == "orka.sejm.gov.pl"
    assert "api.sejm.gov.pl" not in [call[0] for call in opaque.calls]
    assert opaque.conns[0].requested[1].startswith("/Home.nsf/druk.xsp?documentId=")


@pytest.mark.parametrize("slug,code,number", [
    ("house-bill", "hr", "7521"),
    ("senate-bill", "s", "1"),
    ("house-joint-resolution", "hjres", "12"),
    ("senate-joint-resolution", "sjres", "3"),
    ("house-concurrent-resolution", "hconres", "4"),
    ("senate-concurrent-resolution", "sconres", "5"),
    ("house-resolution", "hres", "6"),
    ("senate-resolution", "sres", "7"),
])
def test_congress_bill_without_a_key_uses_the_govinfo_link(monkeypatch, slug, code, number):
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    script = Scripted([
        _redirect(f"https://www.govinfo.gov/content/pkg/BILLS-118{code}{number}ih/pdf/BILLS-118{code}{number}ih.pdf"),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    url = f"https://www.congress.gov/bill/118th-congress/{slug}/{number}/text"
    document = bill_fetch.fetch_official_document(url)
    assert [call[0] for call in script.calls] == ["www.govinfo.gov", "www.govinfo.gov"]
    assert script.conns[0].requested[1] == f"/link/bills/118/{code}/{number}?link-type=pdf"
    assert "api.congress.gov" not in [call[0] for call in script.calls]
    assert "X-Api-Key" not in script.conns[0].requested[3]
    assert document.kind == "pdf"
    assert document.host == "www.govinfo.gov"


def test_congress_bill_without_text_suffix_and_without_www_uses_the_same_link(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([_pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://congress.gov/bill/119th-congress/senate-bill/2")
    assert script.calls[0][0] == "www.govinfo.gov"
    assert script.conns[0].requested[1] == "/link/bills/119/s/2?link-type=pdf"


def test_congress_rewrite_refuses_a_benchmarking_address(monkeypatch):
    _dns(monkeypatch, {"www.govinfo.gov": ["198.18.0.1"]})

    def connect(*_args, **_kwargs):
        raise AssertionError("connected")

    monkeypatch.setattr(bill_fetch, "_new_connection", connect)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document(
            "https://www.congress.gov/bill/118th-congress/house-bill/1"
        )
    assert exc.value.detail == bill_fetch.MSG_UNSAFE
    assert "198.18" not in exc.value.detail


def test_congress_with_a_key_uses_the_first_pdf_on_govinfo(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", CONGRESS_KEY)
    _dns(monkeypatch)
    _forbid_httpx(monkeypatch)
    payload = {
        "textVersions": [
            {
                "type": "Engrossed in House",
                "formats": [
                    {"type": "Formatted Text", "url": "https://www.congress.gov/118/bills/hr7521/BILLS-118hr7521eh.htm"},
                    {"type": "PDF", "url": "https://www.congress.gov/118/bills/hr7521/BILLS-118hr7521eh.pdf"},
                ],
            },
            {
                "type": "Introduced in House",
                "formats": [
                    {"type": "PDF", "url": "https://www.congress.gov/118/bills/hr7521/BILLS-118hr7521ih.pdf"},
                ],
            },
        ]
    }
    script = Scripted([_json_body(payload), _pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document(
        "https://www.congress.gov/bill/118th-congress/house-bill/7521/text"
    )
    assert script.calls[0][0] == "api.congress.gov"
    assert script.calls[0][1] == PUBLIC_V4
    assert script.conns[0].requested[1] == "/v3/bill/118/hr/7521/text?format=json"
    assert CONGRESS_KEY not in script.conns[0].requested[1]
    assert script.conns[0].requested[3]["X-Api-Key"] == CONGRESS_KEY
    assert script.conns[0].requested[3]["Accept"] == "application/json"
    assert script.calls[1][0] == "www.govinfo.gov"
    assert script.conns[1].requested[1] == "/content/pkg/BILLS-118hr7521eh/pdf/BILLS-118hr7521eh.pdf"
    assert "X-Api-Key" not in script.conns[1].requested[3]
    assert "www.congress.gov" not in [call[0] for call in script.calls]
    assert document.kind == "pdf"


def test_congress_key_skips_a_version_without_a_pdf_and_an_off_list_url(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", CONGRESS_KEY)
    _dns(monkeypatch)
    payload = {
        "textVersions": [
            {"type": "Reported", "formats": [{"type": "Formatted Text", "url": "https://www.congress.gov/118/bills/hr1/BILLS-118hr1rh.htm"}]},
            {"type": "Introduced", "formats": [
                {"type": "PDF", "url": "https://evil.example/BILLS-118hr1ih.pdf"},
                {"type": "PDF", "url": "https://www.congress.gov/118/bills/hr1/BILLS-118hr1ih.pdf"},
            ]},
        ]
    }
    script = Scripted([_json_body(payload), _pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://www.congress.gov/bill/118th-congress/house-bill/1")
    assert [call[0] for call in script.calls] == ["api.congress.gov", "www.govinfo.gov"]
    assert script.conns[1].requested[1] == "/content/pkg/BILLS-118hr1ih/pdf/BILLS-118hr1ih.pdf"


def test_congress_metadata_failure_falls_back_to_the_keyless_link(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", CONGRESS_KEY)
    _dns(monkeypatch)
    script = Scripted([
        (403, [("Content-Type", "text/html"), ("cf-mitigated", "challenge")], [b"<html>Just a moment</html>"]),
        _pdf_ok(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document(
        "https://www.congress.gov/bill/118th-congress/senate-bill/10/text"
    )
    assert script.conns[0].response.reads == 0
    assert script.conns[0].requested[3]["X-Api-Key"] == CONGRESS_KEY
    assert CONGRESS_KEY not in script.conns[0].requested[1]
    assert script.calls[1][0] == "www.govinfo.gov"
    assert script.conns[1].requested[1] == "/link/bills/118/s/10?link-type=pdf"
    assert "X-Api-Key" not in script.conns[1].requested[3]
    assert document.kind == "pdf"
    assert CONGRESS_KEY not in document.body.decode()


def test_a_congress_key_with_header_characters_is_not_sent(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", CONGRESS_KEY + "\r\nX-Injected: yes")
    _dns(monkeypatch)
    script = Scripted([_pdf_ok()])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    bill_fetch.fetch_official_document("https://www.congress.gov/bill/118th-congress/house-bill/7521")
    assert script.calls[0][0] == "www.govinfo.gov"
    headers = script.conns[0].requested[3]
    assert "X-Api-Key" not in headers
    assert "X-Injected" not in headers
    joined = " ".join(headers.values())
    assert CONGRESS_KEY not in joined
    assert "\r" not in joined and "\n" not in joined


def test_json_is_still_rejected_outside_the_metadata_step(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([(200, [("Content-Type", "application/json"), ("Content-Length", "2")], [b"{}"])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.govinfo.gov/bulkdata")
    assert exc.value.detail == bill_fetch.MSG_TYPE
    assert exc.value.code == "unsupported_type"
    assert script.conns[0].response.reads == 0


def test_a_challenge_header_is_a_blocked_fetch_and_the_body_is_not_read(monkeypatch):
    _dns(monkeypatch)
    cases = [
        (403, [("Content-Type", "text/html"), ("cf-mitigated", "challenge"), ("Server", "cloudflare")]),
        (503, [("Content-Type", "text/html"), ("X-Iinfo", "1-1-1")]),
        (429, [("Content-Type", "text/html"), ("X-CDN", "Imperva"), ("Set-Cookie", "incap_ses_1=1")]),
    ]
    for status, headers in cases:
        script = Scripted([(status, headers, [b"<html>Just a moment</html>"])])
        monkeypatch.setattr(bill_fetch, "_new_connection", script)
        with pytest.raises(bill_fetch.BillSourceError) as exc:
            bill_fetch.fetch_official_document("https://www.congress.gov/search")
        _assert_blocked(exc.value, "source_blocked", 422)
        assert script.conns[0].response.reads == 0
        assert exc.value.code != "upstream"


def test_a_challenge_body_is_a_blocked_fetch_without_leaking_the_page(monkeypatch):
    _dns(monkeypatch)
    page = b"<html><title>Just a moment...</title><p>Checking your browser before congress.gov</p></html>"
    script = Scripted([(403, [("Content-Type", "text/html; charset=utf-8"), ("Server", "cloudflare")], [page])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://lovdata.no/dokument/x")
    _assert_blocked(exc.value, "source_blocked", 422)
    assert "Just a moment" not in exc.value.detail
    assert "congress.gov" not in exc.value.detail
    assert script.conns[0].response.reads >= 1

    import gzip

    zipped = gzip.compress(b"<html>Request unsuccessful. Incapsula incident 123</html>")
    imperva = Scripted([(429, [
        ("Content-Type", "text/html"),
        ("Content-Encoding", "gzip"),
    ], [zipped])])
    monkeypatch.setattr(bill_fetch, "_new_connection", imperva)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.sejm.gov.pl/start")
    _assert_blocked(exc.value, "source_blocked", 422)
    assert "Incapsula" not in exc.value.detail
    assert "123" not in exc.value.detail


def test_a_plain_403_stays_an_upstream_error(monkeypatch):
    _dns(monkeypatch)
    script = Scripted([(403, [("Content-Type", "text/plain")], [b"Forbidden"])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document("https://www.stortinget.no/no")
    assert exc.value.detail == bill_fetch.MSG_UPSTREAM
    assert exc.value.code == "upstream"
    assert exc.value.status_code == 502
    assert exc.value.detail != bill_fetch.MSG_BLOCKED


def test_a_normal_page_that_mentions_a_challenge_is_not_a_block(monkeypatch):
    _dns(monkeypatch)
    body = b"<html><p>Just a moment while the bill is printed. " + BILL_TEXT.encode() + b"</p></html>"
    script = Scripted([(200, [("Content-Type", "text/html")], [body])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    document = bill_fetch.fetch_official_document("https://www.stortinget.no/no/sak")
    assert document.kind == "html"
    assert document.body == body


def test_a_redirect_to_the_same_url_is_a_loop_and_is_not_followed(monkeypatch):
    _dns(monkeypatch)
    url = "https://www.sejm.gov.pl/Sejm10.nsf/PrzebiegProc.xsp?nr=3305"
    script = Scripted([
        (307, [("Location", url), ("X-CDN", "Imperva")], [b""]),
        _plain(),
    ])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    with pytest.raises(bill_fetch.BillSourceError) as exc:
        bill_fetch.fetch_official_document(url)
    _assert_blocked(exc.value, "redirect_loop", 409)
    assert len(script.calls) == 1
    assert script.calls[0][0] == "www.sejm.gov.pl"
    assert "PrzebiegProc.xsp" in script.conns[0].requested[1]
    assert "api.sejm.gov.pl" not in [call[0] for call in script.calls]
    assert exc.value.code != "too_many_redirects"
    assert exc.value.code != "source_blocked"


def test_blocked_codes_stay_distinct():
    codes = {
        bill_fetch.BillSourceError(bill_fetch.MSG_BLOCKED, 422, "source_blocked").code,
        bill_fetch.BillSourceError(bill_fetch.MSG_BLOCKED, 409, "redirect_loop").code,
        bill_fetch.BillSourceError(bill_fetch.MSG_TOO_MANY, 400, "too_many_redirects").code,
        bill_fetch.BillSourceError(bill_fetch.MSG_UPSTREAM, 502).code,
        bill_fetch.BillSourceError(bill_fetch.MSG_UNSUPPORTED, 400).code,
    }
    assert codes == {
        "source_blocked",
        "redirect_loop",
        "too_many_redirects",
        "upstream",
        "unsupported",
    }
    assert len(codes) == 5


def test_analyze_shows_the_blocked_message_and_not_the_challenge(loopback, monkeypatch):
    _dns(monkeypatch)

    def analyze(_text):
        raise AssertionError("model was called")

    monkeypatch.setattr(main.comparison, "analyze_for_api", analyze)
    page = b"<html>Just a moment... Enable JavaScript and cookies</html>"
    script = Scripted([(403, [("Content-Type", "text/html"), ("Server", "cloudflare")], [page])])
    monkeypatch.setattr(bill_fetch, "_new_connection", script)
    response = loopback.post(
        "/api/analyze",
        files={"url": (None, "https://www.congress.gov/search?q=bill")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == bill_fetch.MSG_BLOCKED
    assert body["code"] == "source_blocked"
    assert "Just a moment" not in body["detail"]
    assert "cloudflare" not in json.dumps(body).lower()
    assert response.headers["content-type"].startswith("application/json")
