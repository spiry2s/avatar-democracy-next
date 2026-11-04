"""
Text extraction from bill sources: raw text, URLs, and uploaded PDFs.

URL fetching is a server-side request made on a client's behalf, so it is
locked down: http/https only, every hop (including redirects) must resolve to a
public address, and the body is size-capped. This stops the analyzer from being
used to probe the host's LAN (routers, admin panels, cloud metadata endpoints).
"""

from __future__ import annotations

import io
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

from backend import config

MAX_REDIRECTS = 5


class SourceError(ValueError):
    """The source could not be fetched or read."""


def extract_text(source: str | bytes, source_type: str) -> str:
    """
    Extract plain text from a bill source.

    Args:
        source: The bill source — str for text/url, bytes for pdf.
        source_type: One of "text", "url", "pdf".

    Raises:
        ValueError: If extraction fails or source_type is unknown.
    """
    if source_type == "text":
        return source if isinstance(source, str) else source.decode("utf-8", errors="replace")

    if source_type == "url":
        return _extract_from_url(str(source))

    if source_type == "pdf":
        content = source if isinstance(source, bytes) else source.encode("latin-1")
        return _extract_from_pdf(content)

    raise ValueError(f"Unknown source_type: {source_type!r}. Expected 'text', 'url', or 'pdf'.")


def _check_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SourceError("Only http:// and https:// URLs are supported.")
    host = parts.hostname
    if not host:
        raise SourceError("URL has no host.")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SourceError(f"Could not resolve host {host!r}.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise SourceError(
                f"Refusing to fetch {host!r}: it resolves to a private or reserved address ({ip})."
            )


def _extract_from_url(url: str) -> str:
    """Fetch a URL and extract text. Handles HTML and PDF responses."""
    import httpx

    with httpx.Client(follow_redirects=False, timeout=30) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public_url(url)
            with client.stream("GET", url, headers={"User-Agent": "AvatarDemocracy-BillAnalyzer/0.2"}) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise SourceError("Redirect without a Location header.")
                    url = urljoin(url, location)
                    continue
                r.raise_for_status()
                body = bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) > config.MAX_SOURCE_BYTES:
                        raise SourceError(
                            f"Document is larger than {config.MAX_SOURCE_BYTES // (1024 * 1024)} MB."
                        )
                content_type = r.headers.get("content-type", "").lower()
                encoding = r.encoding or "utf-8"
                break
        else:
            raise SourceError(f"More than {MAX_REDIRECTS} redirects.")

    if "pdf" in content_type or bytes(body[:5]) == b"%PDF-":
        return _extract_from_pdf(bytes(body))
    return _extract_from_html(bytes(body).decode(encoding, errors="replace"))


def _extract_from_html(html: str) -> str:
    """Strip HTML tags and return readable text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z]+;", " ", text)

    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes."""
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = [t.strip() for t in (p.extract_text() for p in reader.pages) if t]
    except pypdf.errors.PdfReadError as e:
        raise SourceError(f"Not a readable PDF: {e}") from e

    if not pages:
        raise SourceError("Could not extract any text from the PDF. It may be a scanned image.")
    return "\n\n".join(pages)
