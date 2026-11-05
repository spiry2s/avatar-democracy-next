"""Fetch a bill from an official https URL.

The only hosts that may be fetched are ``OFFICIAL_BILL_HOSTS``. Matching is
exact: no suffix rule, no subdomain wildcard, and no environment variable
that widens the set. To add a source, add its hostname here and cover it in
``tests/test_bill_fetch.py``. The README section "Paste a bill link" is the
operator-facing version of that.

SSRF controls, in order, for the original URL and for every redirect:

- https only, no userinfo, no port other than 443
- hostname is ASCII letters, digits, and hyphens; a trailing dot, a
  percent-encoded host, an IP literal, or a non-ASCII name is rejected
- the hostname is compared with ``OFFICIAL_BILL_HOSTS`` and must also be
  the literal start of the URL, so an ``@`` or a parser quirk cannot swap
  the authority
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
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from collections.abc import Iterable

import http.client


class BillSourceError(Exception):
    """A bill link was rejected or could not be read. ``detail`` is safe to show."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# Official bill sources. The host must match one of these strings exactly
# after the normalisations in parse_official_url (lowercase, no port, no
# trailing dot). A name that is not listed here is not fetched, even when
# it is a subdomain or a look-alike of a listed name.
#
#   Sejm        api.sejm.gov.pl, www.sejm.gov.pl, orka.sejm.gov.pl
#   ISAP        isap.sejm.gov.pl
#   Stortinget  www.stortinget.no, data.stortinget.no
#   Lovdata     lovdata.no
#   Congress    www.congress.gov, api.congress.gov
#
# To add a source: put the exact https hostname in this set. Do not add a
# pattern, a suffix, or an IP address. Then add that hostname to the
# acceptance test. There is intentionally no ALLOWED_BILL_HOSTS env var.
OFFICIAL_BILL_HOSTS: frozenset[str] = frozenset({
    "api.sejm.gov.pl",
    "www.sejm.gov.pl",
    "orka.sejm.gov.pl",
    "isap.sejm.gov.pl",
    "www.stortinget.no",
    "data.stortinget.no",
    "lovdata.no",
    "www.congress.gov",
    "api.congress.gov",
})

TOTAL_TIMEOUT_SECONDS = 10.0
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_URL_CHARS = 2048
_READ_CHUNK = 64 * 1024

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
    "Accept-Encoding": "identity",
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
MSG_TOO_MANY = (
    "That link redirected too many times. "
    "Download the file and upload it instead."
)
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

    The check is exact. A trailing dot, a different port, userinfo, a
    look-alike name, an IP literal, or any non-https scheme fails closed.
    """
    if not isinstance(url, str):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    stripped = url.strip()
    if not stripped or len(stripped) > MAX_URL_CHARS:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if any(ch not in _URL_CHARS for ch in stripped):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    try:
        parts = urlsplit(stripped)
    except ValueError as exc:
        raise BillSourceError(MSG_UNSUPPORTED, 400) from exc
    if parts.scheme != "https":
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if parts.username is not None or parts.password is not None:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    host, port_token = _split_authority(parts.netloc)
    if port_token not in (None, "443"):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    try:
        parsed_port = parts.port
    except ValueError as exc:
        raise BillSourceError(MSG_UNSUPPORTED, 400) from exc
    if parsed_port not in (None, 443):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    parsed_host = parts.hostname
    if parsed_host is None or parsed_host != host:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if not _is_plain_hostname(host) or _is_ip_literal(host):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    if host not in OFFICIAL_BILL_HOSTS:
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    _require_literal_authority(stripped, host)
    path = parts.path or "/"
    if not path.startswith("/") or any(ch in path for ch in "\\\r\n "):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    query = parts.query
    if any(ch in query for ch in "\\\r\n "):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    target = path if not query else f"{path}?{query}"
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

    Redirects are manual. Each hop is parsed, allowlisted, and resolved
    again. The fourth redirect is an error and is not followed.
    """
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    current = url
    redirects = 0
    while True:
        parsed = parse_official_url(current)
        _check_deadline(deadline)
        ip = resolve_pinned_ip(parsed.host, deadline)
        _check_deadline(deadline)
        hop = open_exchange(parsed.host, ip, parsed.target, deadline)
        if hop.status in _REDIRECTS:
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise BillSourceError(MSG_TOO_MANY, 400)
            if not hop.location:
                raise BillSourceError(MSG_UNSUPPORTED, 400)
            current = _next_url(parsed, hop.location)
            continue
        if hop.status != 200 or not hop.kind:
            raise BillSourceError(MSG_UPSTREAM, 502)
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


def open_exchange(host: str, ip: str, target: str, deadline: float) -> _Hop:
    """One GET to ``ip``, presented as ``host``. No proxy, no auto-redirect."""
    remaining = _check_deadline(deadline)
    conn = _new_connection(host, ip, remaining)
    response = None
    try:
        conn.connect()
        conn.timeout = _check_deadline(deadline)
        conn.request("GET", target, headers=dict(_REQUEST_HEADERS))
        response = conn.getresponse()
        return _consume(response, conn, deadline)
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


def _consume(response: http.client.HTTPResponse, conn: http.client.HTTPSConnection, deadline: float) -> _Hop:
    status = int(response.status)
    if status in _REDIRECTS:
        locations = _header_values(response, "location")
        if len(locations) != 1:
            raise BillSourceError(MSG_UNSUPPORTED, 400)
        return _Hop(status=status, location=locations[0], kind=None, body=b"")
    if status != 200:
        raise BillSourceError(MSG_UPSTREAM, 502)
    kinds = [_KINDS.get(_media_type(value)) for value in _header_values(response, "content-type")]
    if len(kinds) != 1 or kinds[0] is None:
        raise BillSourceError(MSG_TYPE, 400)
    _reject_huge_content_length(response)
    body = _read_limited(response, conn, deadline)
    body = _decode_body(body, _header_values(response, "content-encoding"))
    return _Hop(status=200, location=None, kind=kinds[0], body=body)


def _read_limited(response: http.client.HTTPResponse, conn: http.client.HTTPSConnection, deadline: float) -> bytes:
    """Read the body in chunks and stop once the cap is crossed.

    Content-Length is not the only limit. A missing length, a short length,
    or a chunked body still stops at ``MAX_DOWNLOAD_BYTES``.
    """
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
        if len(out) + len(chunk) > MAX_DOWNLOAD_BYTES:
            raise BillSourceError(MSG_TOO_BIG, 413)
        out += chunk


def _reject_huge_content_length(response: http.client.HTTPResponse) -> None:
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
    if declared > MAX_DOWNLOAD_BYTES:
        raise BillSourceError(MSG_TOO_BIG, 413)


def _decode_body(body: bytes, encodings: Iterable[str]) -> bytes:
    for value in encodings:
        token = value.strip().lower()
        if token in {"", "identity"}:
            continue
        if token == "gzip":
            body = _decompress_limited(zlib.decompressobj(16 + zlib.MAX_WBITS), body)
            continue
        if token == "deflate":
            body = _inflate_limited(body)
            continue
        raise BillSourceError(MSG_TYPE, 400)
    return body


def _inflate_limited(data: bytes) -> bytes:
    errors: list[Exception] = []
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return _decompress_limited(zlib.decompressobj(wbits), data)
        except zlib.error as exc:
            errors.append(exc)
    raise BillSourceError(MSG_UNSAFE, 400) from errors[-1]


def _decompress_limited(decoder: zlib.Decompress, data: bytes) -> bytes:
    out = bytearray()
    view = memoryview(data)
    for start in range(0, len(view), _READ_CHUNK):
        piece = decoder.decompress(view[start:start + _READ_CHUNK])
        if len(out) + len(piece) > MAX_DOWNLOAD_BYTES:
            raise BillSourceError(MSG_TOO_BIG, 413)
        out += piece
    tail = decoder.flush()
    if len(out) + len(tail) > MAX_DOWNLOAD_BYTES:
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
    """The allowlisted host must be the authority written in the URL."""
    lowered = url.strip().lower()
    prefix = "https://" + host
    if not lowered.startswith(prefix):
        raise BillSourceError(MSG_UNSUPPORTED, 400)
    rest = lowered[len(prefix):]
    if not rest:
        return
    if rest[0] in "/?#":
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
    if (
        not location
        or len(location) > MAX_URL_CHARS
        or any(ord(ch) < 32 or ord(ch) == 127 or ord(ch) > 126 or ch == "\\" for ch in location)
    ):
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
