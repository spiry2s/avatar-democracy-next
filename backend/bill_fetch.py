"""Fetch a bill from an official https URL.

Recognised Sejm print links are rewritten to the print PDF on
``api.sejm.gov.pl``. congress.gov bill links are rewritten to a GovInfo PDF.
Those hops use the checks below. A small JSON body is read only for the Sejm
print record and, when ``CONGRESS_API_KEY`` is set, the Congress text list.
That JSON is not returned as the bill.

The only hosts that may be fetched are ``OFFICIAL_BILL_HOSTS``. Matching is
exact: no suffix rule, no subdomain wildcard, and no environment variable
that widens the set. To add a source, add its hostname here and cover it in
``tests/test_bill_fetch.py``. The README section "Paste a bill link" is the
operator-facing version of that.

SSRF controls, in order, for the original URL and for every redirect:

- https only (a missing scheme is read as https), no userinfo, no port
  other than 443
- the host is converted with IDNA and one trailing dot is removed, then
  compared with ``OFFICIAL_BILL_HOSTS``. A homograph encodes to a different
  name and does not match. A percent-encoded host and an IP literal do not
  match either
- non-ASCII characters in the path and query are percent-encoded. The
  fragment is not sent
- the authority written in an all-ASCII URL must still be that host, so an
  ``@`` or a parser quirk cannot swap it
- DNS is resolved by this process; private, loopback, link-local,
  multicast, reserved, and IPv4-mapped IPv6 addresses are discarded
- the TCP connection goes to that validated IP (the TLS name and the Host
  header stay the official hostname), so a DNS rebind between the check
  and the connect cannot change the target
- redirects are followed by this code, at most three, and each hop repeats
  the same checks; environment proxies are never consulted
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import unicodedata
import zlib
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urljoin, urlsplit
from collections.abc import Iterable

import http.client


class BillSourceError(Exception):
    """A bill link was rejected or could not be read. ``detail`` is safe to show.

    ``code`` is a short stable token for the UI. It is not an upstream status,
    a header name, or a challenge page.
    """

    def __init__(self, detail: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code or _CODE_OF.get(detail, "bill_source")


# Official bill sources. The host must match one of these ASCII strings
# exactly after parse_official_url normalises it: trim, default scheme,
# lowercase, IDNA, and one trailing dot removed. A subdomain or a
# look-alike that encodes to a different name is not fetched.
#
#   Sejm        sejm.gov.pl, api.sejm.gov.pl, www.sejm.gov.pl, orka.sejm.gov.pl
#   ISAP        isap.sejm.gov.pl
#   Stortinget  stortinget.no, www.stortinget.no, data.stortinget.no
#   Lovdata     lovdata.no, www.lovdata.no
#   Congress    congress.gov, www.congress.gov, api.congress.gov
#   GovInfo     govinfo.gov, www.govinfo.gov
#
# To add a source: put the exact https hostname in this set. Do not add a
# pattern, a suffix, or an IP address. Then add that hostname to the
# acceptance test. There is intentionally no ALLOWED_BILL_HOSTS env var.
# CONGRESS_API_KEY does not add a host.
OFFICIAL_BILL_HOSTS: frozenset[str] = frozenset({
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

TOTAL_TIMEOUT_SECONDS = 10.0
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
# Sejm and Congress metadata only. A pasted link cannot opt into this.
METADATA_MAX_BYTES = 256 * 1024
MAX_REDIRECTS = 3
MAX_URL_CHARS = 2048
_READ_CHUNK = 64 * 1024
CHALLENGE_SCAN_BYTES = 64 * 1024

_URL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "-._~:/?#[]@!$&'()*+,;=%"
)
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_KINDS = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/pdf": "pdf",
    "text/plain": "text",
}
_REQUEST_HEADERS = {
    "User-Agent": "AvatarDemocracyBillFetch/0.1",
    "Accept": "text/html, application/xhtml+xml, application/pdf, text/plain;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, identity",
    "Connection": "close",
}

# Addresses that must never be connected to, including ranges Python's
# ipaddress flags have not always called "private" (CGNAT, benchmarking).
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fec0::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("::ffff:0:0/96"),
)

MSG_UNSUPPORTED = (
    "That link is not from a supported official source. "
    "Download the file and upload it instead."
)
MSG_UNSAFE = (
    "That link could not be fetched safely. "
    "Download the file and upload it instead."
)
# The analyzer UI is English (html lang="en" on every page, no locale
# catalog). This is the string the error area shows.
MSG_BLOCKED = (
    "That site blocked automatic fetching. "
    "Paste a PDF link instead "
    "(for a Sejm print, https://api.sejm.gov.pl/sejm/term10/prints/3305/3305.pdf) "
    "or paste the bill text."
)
# The redirect cap is the same tester-facing failure. ``code`` tells them apart.
MSG_TOO_MANY = MSG_BLOCKED
MSG_TOO_BIG = (
    "That document is larger than 15 MB. "
    "Download the file and upload it instead."
)
MSG_TIMEOUT = (
    "The official source did not respond in time. "
    "Download the file and upload it instead."
)
MSG_TYPE = (
    "That link is not an HTML page, a PDF, or a plain-text file. "
    "Download the file and upload it instead."
)
MSG_UPSTREAM = (
    "The official source did not return the document. "
    "Download the file and upload it instead."
)

_CODE_OF = {
    MSG_UNSUPPORTED: "unsupported",
    MSG_UNSAFE: "unsafe",
    MSG_TOO_BIG: "too_big",
    MSG_TIMEOUT: "timeout",
    MSG_TYPE: "unsupported_type",
    MSG_UPSTREAM: "upstream",
    MSG_BLOCKED: "source_blocked",
}

_WAF_STATUSES = frozenset({403, 429, 503})
_JSON_TYPES = frozenset({"application/json", "application/hal+json"})
_CHALLENGE_BODY = (
    "just a moment",
    "cf-browser-verification",
    "challenge-platform",
    "/cdn-cgi/challenge",
    "cf-challenge",
    "attention required",
    "enable javascript and cookies",
    "checking your browser",
    "_incapsula_resource",
    "incapsula incident",
    "request unsuccessful. incapsula",
    "powered by imperva",
)
_SEJM_DRUK = re.compile(r"(?i)^/sejm([1-9]\d{0,2})\.nsf/druk\.xsp$")
_SEJM_PRINT = re.compile(
    r"(?i)^/sejm/term([1-9]\d{0,2})/prints/([0-9]+(?:-[0-9A-Za-z]+)?)(?:/)?$"
)
_ORKA_OPISY = re.compile(
    r"(?i)^/proc([1-9]\d{0,2})\.nsf/opisy/([0-9]+(?:-[0-9A-Za-z]+)?)\.html?$"
)
_PRINT_NUMBER = re.compile(r"[0-9]+(?:-[0-9A-Za-z]+)?")
_PDF_NAME = re.compile(r"(?i)[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf")
_CONGRESS_BILL = re.compile(
    r"(?i)^/bill/([1-9]\d{0,2})(?:st|nd|rd|th)-congress/"
    r"(house-bill|senate-bill|house-joint-resolution|senate-joint-resolution|"
    r"house-concurrent-resolution|senate-concurrent-resolution|"
    r"house-resolution|senate-resolution)/"
    r"([1-9]\d{0,5})(?:/text)?/?$"
)
_CONGRESS_CODES = {
    "house-bill": "hr",
    "senate-bill": "s",
    "house-joint-resolution": "hjres",
    "senate-joint-resolution": "sjres",
    "house-concurrent-resolution": "hconres",
    "senate-concurrent-resolution": "sconres",
    "house-resolution": "hres",
    "senate-resolution": "sres",
}
_BILLS_PKG = re.compile(r"(?i)(?:^|/)(BILLS-\d+[a-z]+\d+[a-z0-9]*)\.pdf$")
_API_KEY = re.compile(r"[A-Za-z0-9._-]{16,128}")
_GOVINFO_HOSTS = frozenset({"govinfo.gov", "www.govinfo.gov"})
_CONGRESS_HOSTS = frozenset({"congress.gov", "www.congress.gov", "api.congress.gov"})


@dataclass(frozen=True)
class OfficialUrl:
    """An allowlisted https URL reduced to the host we will connect as."""

    host: str
    target: str


@dataclass(frozen=True)
class OfficialDocument:
    """Bytes fetched from an official host, already limited and decoded."""

    kind: str
    body: bytes
    host: str


@dataclass(frozen=True)
class _Hop:
    status: int
    location: str | None
    kind: str | None
    body: bytes


def parse_official_url(url: str) -> OfficialUrl:
    """Return the official host and request-target, or raise BillSourceError.

    Surrounding whitespace is removed. A missing scheme is read as https.
    The host is IDNA-encoded and one trailing dot is removed, then matched
    exactly. Non-ASCII in the path and query is percent-encoded. The
    fragment is not fetched. A different port, userinfo, a look-alike host,
    or an IP literal still fails closed.
    """
    if not isinstance(url, str):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    prepared = _prepare_url(url)
    if not _url_text_ok(prepared):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    try:
        parts = urlsplit(prepared)
    except ValueError as exc:
        raise BillSourceError(MSG_UNSUPPORTED, 400) from exc
    if parts.scheme != "https":
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if parts.username is not None or parts.password is not None:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    raw_host, port_token = _split_authority(parts.netloc)
    if port_token not in (None, "443"):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    try:
        parsed_port = parts.port
    except ValueError as exc:
        raise BillSourceError(MSG_UNSUPPORTED, 400) from exc
    if parsed_port not in (None, 443):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if parts.hostname is None or parts.hostname != raw_host:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    host = _normalize_host(raw_host)
    if not _is_plain_hostname(host) or _is_ip_literal(host):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if host not in OFFICIAL_BILL_HOSTS:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    _require_literal_authority(prepared, host)
    # Encode the fragment as well, then drop it. It is not part of the request.
    _percent_encode_non_ascii(parts.fragment)
    target = _request_target(parts.path, parts.query)
    return OfficialUrl(host=host, target=target)


def ip_is_blocked(raw: str) -> bool:
    """True for any address this fetcher must not open a socket to.

    Unparseable values are blocked. Every IPv4-mapped IPv6 address is
    blocked, including one whose embedded IPv4 address is public.
    """
    text = str(raw).split("%", 1)[0].strip()
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return True
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    for network in _BLOCKED_NETWORKS:
        if network.version == ip.version and ip in network:
            return True
    return False


def resolve_pinned_ip(host: str, deadline: float) -> str:
    """Resolve ``host`` and return one public IP, or raise.

    The returned address is the only address the caller may connect to.
    A private address in the result list is skipped. If none are public,
    the fetch stops.
    """
    for ip in _resolve_ips(host, deadline):
        if not ip_is_blocked(ip):
            return ip
    raise BillSourceError(MSG_UNSAFE, 400)


def fetch_official_document(url: str) -> OfficialDocument:
    """GET an official bill URL and return its limited body.

    Sejm print pages and congress.gov bill pages are rewritten to a PDF
    on an allowlisted host before the document is fetched. Redirects are
    manual. Each hop is parsed, allowlisted, and resolved again. A hop
    back to a URL already requested is a block. The fourth redirect is
    not followed.
    """
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    resolved = _resolve_source(url, deadline)
    if resolved.document is not None:
        return resolved.document
    return _fetch_direct(resolved.url, deadline)


def _fetch_direct(
    url: str,
    deadline: float,
    *,
    accept_json: bool = False,
    max_bytes: int | None = None,
    api_key: str | None = None,
) -> OfficialDocument:
    """GET ``url`` with the shared deadline. No host rewrite.

    ``accept_json`` and ``max_bytes`` are the metadata step only. The key
    is sent only to api.congress.gov, and never placed in the URL.
    """
    current = url
    redirects = 0
    seen: set[tuple[str, str]] = set()
    while True:
        parsed = parse_official_url(current)
        identity = (parsed.host, parsed.target)
        if identity in seen:
            raise BillSourceError(MSG_BLOCKED, 409, "redirect_loop")
        seen.add(identity)
        _check_deadline(deadline)
        ip = resolve_pinned_ip(parsed.host, deadline)
        _check_deadline(deadline)
        hop = open_exchange(
            parsed.host,
            ip,
            parsed.target,
            deadline,
            headers=_request_headers(parsed.host, accept_json, api_key),
            accept_json=accept_json,
            max_bytes=max_bytes,
        )
        if hop.status in _REDIRECTS:
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise BillSourceError(MSG_TOO_MANY, 400, "too_many_redirects")
            if not hop.location:
                raise BillSourceError(MSG_UNSUPPORTED, 400)
            current = _next_url(parsed, hop.location)
            continue
        if hop.status != 200 or not hop.kind:
            raise BillSourceError(MSG_UPSTREAM, 502)
        if hop.kind == "json" and not accept_json:
            raise BillSourceError(MSG_TYPE, 400)
        return OfficialDocument(kind=hop.kind, body=hop.body, host=parsed.host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a pinned IP and names the official host.

    ``http.client`` does not read HTTP(S)_PROXY. The Host header and the TLS
    server name are ``host``. The TCP peer is ``_pinned_ip``.
    """

    def __init__(self, host: str, ip: str, timeout: float) -> None:
        super().__init__(host=host, port=443, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        raw.settimeout(self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(raw, server_hostname=self.host)


def _new_connection(host: str, ip: str, timeout: float) -> http.client.HTTPSConnection:
    return _PinnedHTTPSConnection(host, ip, timeout)


def open_exchange(
    host: str,
    ip: str,
    target: str,
    deadline: float,
    headers: dict[str, str] | None = None,
    *,
    accept_json: bool = False,
    max_bytes: int | None = None,
) -> _Hop:
    """One GET to ``ip``, presented as ``host``. No proxy, no auto-redirect."""
    remaining = _check_deadline(deadline)
    conn = _new_connection(host, ip, remaining)
    response = None
    try:
        conn.connect()
        conn.timeout = _check_deadline(deadline)
        conn.request("GET", target, headers=dict(headers or _REQUEST_HEADERS))
        response = conn.getresponse()
        return _consume(response, conn, deadline, accept_json=accept_json, max_bytes=max_bytes)
    except BillSourceError:
        raise
    except TimeoutError as exc:
        raise BillSourceError(MSG_TIMEOUT, 504) from exc
    except (OSError, http.client.HTTPException, zlib.error) as exc:
        raise BillSourceError(MSG_UNSAFE, 400) from exc
    finally:
        if response is not None:
            response.close()
        conn.close()


def _consume(
    response: http.client.HTTPResponse,
    conn: http.client.HTTPSConnection,
    deadline: float,
    *,
    accept_json: bool = False,
    max_bytes: int | None = None,
) -> _Hop:
    status = int(response.status)
    if status in _REDIRECTS:
        locations = _header_values(response, "location")
        if len(locations) != 1:
            raise BillSourceError(MSG_UNSUPPORTED, 400)
        return _Hop(status=status, location=locations[0], kind=None, body=b"")
    if status in _WAF_STATUSES and _is_waf_challenge(response, conn, deadline):
        raise BillSourceError(MSG_BLOCKED, 422, "source_blocked")
    if status != 200:
        raise BillSourceError(MSG_UPSTREAM, 502)
    allowed = dict(_KINDS)
    if accept_json:
        for media in _JSON_TYPES:
            allowed[media] = "json"
    kinds = [allowed.get(_media_type(value)) for value in _header_values(response, "content-type")]
    if len(kinds) != 1 or kinds[0] is None:
        raise BillSourceError(MSG_TYPE, 400)
    _reject_huge_content_length(response, max_bytes)
    body = _read_limited(response, conn, deadline, max_bytes)
    body = _decode_body(body, _header_values(response, "content-encoding"), max_bytes)
    return _Hop(status=200, location=None, kind=kinds[0], body=body)


def _byte_cap(max_bytes: int | None) -> int:
    """The download cap for this hop. None keeps the document cap."""
    return MAX_DOWNLOAD_BYTES if max_bytes is None else max_bytes


def _read_limited(
    response: http.client.HTTPResponse,
    conn: http.client.HTTPSConnection,
    deadline: float,
    max_bytes: int | None = None,
) -> bytes:
    """Read the body in chunks and stop once the cap is crossed.

    Content-Length is not the only limit. A missing length, a short length,
    or a chunked body still stops at the cap.
    """
    cap = _byte_cap(max_bytes)
    out = bytearray()
    while True:
        remaining = _check_deadline(deadline)
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.settimeout(remaining)
            except OSError:
                pass
        try:
            chunk = response.read(_READ_CHUNK)
        except TimeoutError as exc:
            raise BillSourceError(MSG_TIMEOUT, 504) from exc
        if not chunk:
            return bytes(out)
        if len(out) + len(chunk) > cap:
            raise BillSourceError(MSG_TOO_BIG, 413)
        out += chunk


def _reject_huge_content_length(response: http.client.HTTPResponse, max_bytes: int | None = None) -> None:
    values = _header_values(response, "content-length")
    if not values:
        return
    if len(values) != 1:
        raise BillSourceError(MSG_UNSAFE, 400)
    try:
        declared = int(values[0].strip())
    except ValueError as exc:
        raise BillSourceError(MSG_UNSAFE, 400) from exc
    if declared < 0:
        raise BillSourceError(MSG_UNSAFE, 400)
    if declared > _byte_cap(max_bytes):
        raise BillSourceError(MSG_TOO_BIG, 413)


def _decode_body(body: bytes, encodings: Iterable[str], max_bytes: int | None = None) -> bytes:
    for value in encodings:
        token = value.strip().lower()
        if token in {"", "identity"}:
            continue
        if token == "gzip":
            body = _decompress_limited(zlib.decompressobj(16 + zlib.MAX_WBITS), body, max_bytes)
            continue
        if token == "deflate":
            body = _inflate_limited(body, max_bytes)
            continue
        if token == "br":
            body = _brotli_limited(body, max_bytes)
            continue
        raise BillSourceError(MSG_TYPE, 400)
    return body


def _inflate_limited(data: bytes, max_bytes: int | None = None) -> bytes:
    errors: list[Exception] = []
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return _decompress_limited(zlib.decompressobj(wbits), data, max_bytes)
        except zlib.error as exc:
            errors.append(exc)
    raise BillSourceError(MSG_UNSAFE, 400) from errors[-1]


def _decompress_limited(decoder: zlib.Decompress, data: bytes, max_bytes: int | None = None) -> bytes:
    cap = _byte_cap(max_bytes)
    out = bytearray()
    view = memoryview(data)
    for start in range(0, len(view), _READ_CHUNK):
        piece = decoder.decompress(view[start:start + _READ_CHUNK])
        if len(out) + len(piece) > cap:
            raise BillSourceError(MSG_TOO_BIG, 413)
        out += piece
    tail = decoder.flush()
    if len(out) + len(tail) > cap:
        raise BillSourceError(MSG_TOO_BIG, 413)
    out += tail
    return bytes(out)


def _resolve_ips(host: str, deadline: float) -> list[str]:
    remaining = _check_deadline(deadline)
    box: dict[str, object] = {}

    def work() -> None:
        try:
            box["infos"] = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except Exception as exc:  # noqa: BLE001 — fail closed on any resolver error
            box["error"] = exc

    thread = threading.Thread(target=work, name="bill-fetch-dns", daemon=True)
    thread.start()
    thread.join(remaining)
    if thread.is_alive():
        raise BillSourceError(MSG_TIMEOUT, 504)
    error = box.get("error")
    if error is not None:
        raise BillSourceError(MSG_UNSAFE, 400) from error if isinstance(error, Exception) else None
    infos = box.get("infos")
    ips: list[str] = []
    if isinstance(infos, list):
        for info in infos:
            try:
                raw = str(info[4][0]).split("%", 1)[0]
            except (IndexError, TypeError, AttributeError):
                continue
            if raw and raw not in ips:
                ips.append(raw)
    if not ips:
        raise BillSourceError(MSG_UNSAFE, 400)
    return ips


def _prepare_url(url: str) -> str:
    """Trim the paste and supply https when the scheme was left off."""
    text = url.strip()
    if not text or len(text) > MAX_URL_CHARS:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if text.startswith("//"):
        text = "https:" + text
    elif "://" not in text:
        text = "https://" + text
    if len(text) > MAX_URL_CHARS:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    return text


def _url_text_ok(text: str) -> bool:
    """Allow URL characters and letters such as ł, ó, ø, and å.

    ASCII controls, spaces, backslashes, and Unicode control or separator
    characters are rejected. A format character such as U+200B must not be
    stripped into an allowlisted name by IDNA.
    """
    for ch in text:
        if ch in _URL_CHARS:
            continue
        if ord(ch) < 128:
            return False
        if unicodedata.category(ch)[0] in {"C", "Z"}:
            return False
    return True


def _normalize_host(raw: str) -> str:
    """IDNA-encode the host, then remove one trailing dot."""
    try:
        encoded = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BillSourceError(MSG_UNSUPPORTED, 400) from exc
    if encoded.endswith("."):
        encoded = encoded[:-1]
    return encoded


def _percent_encode_non_ascii(text: str) -> str:
    """UTF-8 percent-encode characters outside ASCII. Leave the rest as written."""
    if text.isascii():
        return text
    return "".join(ch if ord(ch) < 128 else quote(ch, safe="") for ch in text)


def _request_target(path: str, query: str) -> str:
    path = _percent_encode_non_ascii(path or "/")
    query = _percent_encode_non_ascii(query)
    if not path.startswith("/") or any(ch in path for ch in "\\\r\n "):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if any(ch in query for ch in "\\\r\n "):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    return path if not query else f"{path}?{query}"


def _brotli_limited(data: bytes, max_bytes: int | None = None) -> bytes:
    """Decode brotli and stop if the decoded bytes pass the download cap."""
    try:
        import brotli
    except ImportError as exc:
        raise BillSourceError(MSG_TYPE, 400) from exc
    cap = _byte_cap(max_bytes)
    decoder = brotli.Decompressor()
    out = bytearray()
    view = memoryview(data)
    try:
        spans = range(0, len(view), _READ_CHUNK) if len(view) else (0,)
        for start in spans:
            chunk = view[start:start + _READ_CHUNK] if len(view) else b""
            piece = decoder.process(chunk)
            if len(out) + len(piece) > cap:
                raise BillSourceError(MSG_TOO_BIG, 413)
            out += piece
        if not decoder.is_finished():
            piece = decoder.process(b"")
            if len(out) + len(piece) > cap:
                raise BillSourceError(MSG_TOO_BIG, 413)
            out += piece
    except brotli.error as exc:
        raise BillSourceError(MSG_UNSAFE, 400) from exc
    return bytes(out)


def _split_authority(netloc: str) -> tuple[str, str | None]:
    if not netloc or any(ch in netloc for ch in "@\\\t\r\n "):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    lowered = netloc.lower()
    if lowered.startswith("[") or lowered.count(":") > 1:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if ":" not in lowered:
        return lowered, None
    host, port = lowered.rsplit(":", 1)
    if port == "":
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    return host, port


def _is_plain_hostname(host: str) -> bool:
    if not host or len(host) > 253 or host.endswith(".") or host.startswith(".") or ".." in host:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in label):
            return False
    if not labels[-1].isalpha():
        return False
    return True


def _is_ip_literal(host: str) -> bool:
    if ":" in host:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    if labels and all(_is_numeric_label(label) for label in labels):
        return True
    return False


def _is_numeric_label(label: str) -> bool:
    if label.startswith(("0x", "0X")) and len(label) > 2:
        return all(ch in "0123456789abcdefABCDEF" for ch in label[2:])
    return label.isdigit()


def _require_literal_authority(url: str, host: str) -> None:
    """An all-ASCII URL must literally name the allowlisted host.

    A non-ASCII host is checked after IDNA instead, because the raw text
    will not contain the ASCII name. One trailing dot is allowed here; the
    matcher has already removed it.
    """
    if any(ord(ch) > 127 for ch in url):
        return
    lowered = url.strip().lower()
    prefix = "https://" + host
    if not lowered.startswith(prefix):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    rest = lowered[len(prefix):]
    if not rest or rest[0] in "/?#":
        return
    if rest[0] == ".":
        rest = rest[1:]
        if not rest or rest[0] in "/?#":
            return
    if rest.startswith(":443"):
        after = rest[4:]
        if not after or after[0] in "/?#":
            return
    raise BillSourceError(MSG_UNSUPPORTED, 400)


def _next_url(current: OfficialUrl, location: str) -> str:
    if not isinstance(location, str):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    location = location.strip()
    if not location or len(location) > MAX_URL_CHARS or not _url_text_ok(location):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    base = f"https://{current.host}{current.target}"
    return urljoin(base, location)


def _header_values(response: http.client.HTTPResponse, name: str) -> list[str]:
    wanted = name.lower()
    values: list[str] = []
    for key, value in response.getheaders():
        if str(key).lower() == wanted:
            values.append(str(value))
    return values


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _check_deadline(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BillSourceError(MSG_TIMEOUT, 504)
    return remaining


@dataclass(frozen=True)
class _Resolved:
    url: str
    document: OfficialDocument | None = None


def _resolve_source(url: str, deadline: float) -> _Resolved:
    """Rewrite a recognised Sejm or Congress bill URL to a PDF URL.

    Anything else is returned unchanged. The original URL is allowlisted
    before any metadata request.
    """
    parsed = parse_official_url(url)
    sejm = _sejm_print_ref(parsed)
    if sejm is not None:
        return _resolve_sejm(sejm[0], sejm[1], deadline)
    congress = _congress_bill_ref(parsed)
    if congress is not None:
        return _resolve_congress(congress[0], congress[1], congress[2], deadline)
    return _Resolved(url=url)


def _sejm_print_ref(parsed: OfficialUrl) -> tuple[str, str] | None:
    """Term and print number from a recognised Sejm URL, or None."""
    path, query = _split_target(parsed.target)
    if parsed.host in {"sejm.gov.pl", "www.sejm.gov.pl"}:
        match = _SEJM_DRUK.fullmatch(path)
        if match is None:
            return None
        number = _query_value(query, "nr")
        if number is None or _PRINT_NUMBER.fullmatch(number) is None:
            return None
        return match.group(1), number
    if parsed.host == "orka.sejm.gov.pl":
        match = _ORKA_OPISY.fullmatch(path)
        if match is None:
            return None
        return match.group(1), match.group(2)
    if parsed.host == "api.sejm.gov.pl":
        match = _SEJM_PRINT.fullmatch(path)
        if match is None:
            return None
        return match.group(1), match.group(2)
    return None


def _resolve_sejm(term: str, number: str, deadline: float) -> _Resolved:
    """PDF for a Sejm print. Metadata JSON is internal; the PDF is the document."""
    meta = f"https://api.sejm.gov.pl/sejm/term{term}/prints/{number}"
    fallback = f"{meta}/{number}.pdf"
    try:
        document = _fetch_direct(
            meta,
            deadline,
            accept_json=True,
            max_bytes=METADATA_MAX_BYTES,
        )
    except BillSourceError as exc:
        if exc.code == "timeout":
            raise
        return _Resolved(url=fallback)
    if document.kind == "pdf":
        return _Resolved(url=fallback, document=document)
    name = _pdf_attachment_name(document.body, number) if document.kind == "json" else None
    if not name:
        return _Resolved(url=fallback)
    return _Resolved(url=f"{meta}/{name}")


def _pdf_attachment_name(body: bytes, number: str) -> str | None:
    """An attachment whose name ends in .pdf, preferring ``{number}.pdf``."""
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    attachments = data.get("attachments")
    if not isinstance(attachments, list):
        return None
    names: list[str] = []
    for item in attachments:
        if isinstance(item, str) and _safe_pdf_name(item):
            names.append(item)
    if not names:
        return None
    preferred = f"{number}.pdf".lower()
    for name in names:
        if name.lower() == preferred:
            return name
    return names[0]


def _safe_pdf_name(name: str) -> bool:
    if not name.lower().endswith(".pdf"):
        return False
    if ".." in name or "/" in name or "\\" in name or "%" in name:
        return False
    return _PDF_NAME.fullmatch(name) is not None


def _congress_bill_ref(parsed: OfficialUrl) -> tuple[str, str, str] | None:
    """Congress number, govinfo bill type, and bill number."""
    if parsed.host not in {"congress.gov", "www.congress.gov"}:
        return None
    path, _query = _split_target(parsed.target)
    match = _CONGRESS_BILL.fullmatch(path)
    if match is None:
        return None
    code = _CONGRESS_CODES.get(match.group(2).lower())
    if code is None:
        return None
    return match.group(1), code, match.group(3)


def _resolve_congress(congress: str, code: str, number: str, deadline: float) -> _Resolved:
    """GovInfo PDF for a congress.gov bill page.

    A key, when set, is read from the environment at call time and sent
    only as a header to api.congress.gov. Without one, the keyless GovInfo
    link service names the most recent PDF.
    """
    link = _govinfo_link(congress, code, number)
    key = congress_api_key()
    if key is None:
        return _Resolved(url=link)
    meta = f"https://api.congress.gov/v3/bill/{congress}/{code}/{number}/text?format=json"
    try:
        document = _fetch_direct(
            meta,
            deadline,
            accept_json=True,
            max_bytes=METADATA_MAX_BYTES,
            api_key=key,
        )
    except BillSourceError as exc:
        if exc.code == "timeout":
            raise
        return _Resolved(url=link)
    if document.kind == "pdf":
        return _Resolved(url=link, document=document)
    if document.kind != "json":
        return _Resolved(url=link)
    found = _govinfo_pdf_from_congress_json(document.body)
    return _Resolved(url=found or link)


def _govinfo_link(congress: str, code: str, number: str) -> str:
    return f"https://www.govinfo.gov/link/bills/{congress}/{code}/{number}?link-type=pdf"


def congress_api_key() -> str | None:
    """The Congress API key, or None when it is unset or not a single token.

    A value with whitespace or a character that could break a header is
    ignored. Nothing here invents a key.
    """
    raw = os.environ.get("CONGRESS_API_KEY")
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if _API_KEY.fullmatch(key) is None:
        return None
    return key


def _govinfo_pdf_from_congress_json(body: bytes) -> str | None:
    """First PDF format in the Congress text-versions payload, as a GovInfo URL."""
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    versions = data.get("textVersions")
    if isinstance(versions, dict):
        nested = versions.get("textVersions")
        if not isinstance(nested, list):
            nested = versions.get("items")
        versions = nested
    if not isinstance(versions, list):
        return None
    for version in versions:
        if not isinstance(version, dict):
            continue
        formats = version.get("formats")
        if not isinstance(formats, list):
            continue
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            if str(fmt.get("type", "")).strip().lower() != "pdf":
                continue
            url = fmt.get("url")
            if not isinstance(url, str):
                continue
            rewritten = _congress_pdf_to_govinfo(url)
            if rewritten:
                return rewritten
    return None


def _congress_pdf_to_govinfo(url: str) -> str | None:
    """Package PDF on govinfo. A congress.gov PDF is not fetched."""
    try:
        parsed = parse_official_url(url)
    except BillSourceError:
        return None
    if parsed.host not in _GOVINFO_HOSTS and parsed.host not in _CONGRESS_HOSTS:
        return None
    path = parsed.target.split("?", 1)[0]
    match = _BILLS_PKG.search(path)
    if match is None:
        if parsed.host in _GOVINFO_HOSTS and path.lower().endswith(".pdf"):
            return f"https://{parsed.host}{parsed.target}"
        return None
    pkg = match.group(1)
    if ".." in pkg or "/" in pkg:
        return None
    return f"https://www.govinfo.gov/content/pkg/{pkg}/pdf/{pkg}.pdf"


def _split_target(target: str) -> tuple[str, str]:
    path, _, query = target.partition("?")
    return path, query


def _query_value(query: str, name: str) -> str | None:
    wanted = name.lower()
    try:
        pairs = parse_qsl(query, keep_blank_values=False, errors="replace")
    except ValueError:
        return None
    for key, value in pairs:
        if key.lower() == wanted and value:
            return value
    return None


def _request_headers(host: str, accept_json: bool, api_key: str | None) -> dict[str, str]:
    headers = dict(_REQUEST_HEADERS)
    if accept_json:
        headers["Accept"] = "application/json"
    if api_key and host == "api.congress.gov":
        headers["X-Api-Key"] = api_key
    return headers


def _is_waf_challenge(
    response: http.client.HTTPResponse,
    conn: http.client.HTTPSConnection,
    deadline: float,
) -> bool:
    """True when a 403, 429, or 503 looks like a Cloudflare or Imperva wall.

    The body is scanned only when the headers do not already say so, and
    only up to ``CHALLENGE_SCAN_BYTES``. Nothing from the page is returned.
    """
    if _challenge_headers(response):
        return True
    raw = _read_prefix(response, conn, deadline, CHALLENGE_SCAN_BYTES)
    try:
        decoded = _decode_body(raw, _header_values(response, "content-encoding"), CHALLENGE_SCAN_BYTES)
    except BillSourceError:
        decoded = raw
    text = decoded[:CHALLENGE_SCAN_BYTES].decode("utf-8", errors="replace").lower()
    return any(marker in text for marker in _CHALLENGE_BODY)


def _challenge_headers(response: http.client.HTTPResponse) -> bool:
    for key, value in response.getheaders():
        name = str(key).lower().strip()
        val = str(value).lower()
        if name == "cf-mitigated":
            return True
        if name == "x-iinfo":
            return True
        if name == "x-cdn" and "imperva" in val:
            return True
        if name == "set-cookie" and ("incap_ses" in val or "visid_incap" in val):
            return True
    return False


def _read_prefix(
    response: http.client.HTTPResponse,
    conn: http.client.HTTPSConnection,
    deadline: float,
    limit: int,
) -> bytes:
    """Read at most ``limit`` body bytes. A short read is not an error."""
    out = bytearray()
    while len(out) < limit:
        remaining = _check_deadline(deadline)
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.settimeout(remaining)
            except OSError:
                pass
        try:
            chunk = response.read(min(_READ_CHUNK, limit - len(out)))
        except TimeoutError as exc:
            raise BillSourceError(MSG_TIMEOUT, 504) from exc
        if not chunk:
            break
        need = limit - len(out)
        if len(chunk) > need:
            chunk = chunk[:need]
        out += chunk
    return bytes(out)
