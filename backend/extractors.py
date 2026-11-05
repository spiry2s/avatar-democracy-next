"""
Text extraction from bill sources: raw text, official URLs, and uploaded PDFs.

URL fetches go through ``backend.bill_fetch``. Only the hosts in
``OFFICIAL_BILL_HOSTS`` are retrieved, and the TCP connection is pinned to
a public address that this process resolved itself.
"""

from __future__ import annotations

import io
import re

from backend.bill_fetch import MSG_TYPE, MSG_UNSUPPORTED, BillSourceError, fetch_official_document

# The analyzer refuses anything shorter than this. A PDF under the floor is
# treated as a scan (no text layer) rather than as a short bill.
MIN_BILL_CHARS = 100
PDF_MAX_PAGES = 300
PDF_MAX_CHARS = 1_500_000

MSG_NO_TEXT_LAYER = (
    "This PDF has no text layer. It looks like a scan. "
    "Upload a text file instead."
)
MSG_PDF_UNREADABLE = (
    "Could not read that PDF. If it is a scan, upload a text file instead."
)

_DROP_BLOCKS = re.compile(
    r"(?is)<(script|style|nav|noscript|template|header|footer)\b[^>]*>.*?</\1>"
)
_TAGS = re.compile(r"(?is)<[^>]+>")


def extract_text(source: str | bytes, source_type: str) -> str:
    """
    Extract plain text from a bill source.

    Args:
        source: The bill source — str for text/url, bytes for pdf.
        source_type: One of "text", "url", "pdf".

    Returns:
        Extracted plain text.

    Raises:
        ValueError: If extraction fails or source_type is unknown.
    """
    if source_type == "text":
        return source if isinstance(source, str) else source.decode("utf-8", errors="replace")

    if source_type == "url":
        if not isinstance(source, str):
            raise BillSourceError(MSG_UNSUPPORTED, 400)
        return _extract_from_url(source)

    if source_type == "pdf":
        content = source if isinstance(source, bytes) else source.encode("latin-1")
        return _extract_from_pdf(content)

    raise ValueError(f"Unknown source_type: {source_type!r}. Expected 'text', 'url', or 'pdf'.")


def _extract_from_url(url: str) -> str:
    """Fetch an official URL and return readable text. HTML is not returned raw."""
    document = fetch_official_document(url)
    if document.kind == "pdf":
        return _extract_from_pdf(document.body)
    if document.kind == "html":
        return _extract_from_html(document.body.decode("utf-8-sig", errors="replace"))
    if document.kind == "text":
        return document.body.decode("utf-8-sig", errors="replace")
    raise BillSourceError(MSG_TYPE, 400)


def _extract_from_html(html: str) -> str:
    """Strip scripts, styles, and navigation and return readable text."""
    try:
        text = _html_with_beautifulsoup(html)
    except ImportError:
        text = _html_fallback(html)
    except Exception:
        text = _html_fallback(html)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _html_with_beautifulsoup(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _html_fallback(html: str) -> str:
    text = _DROP_BLOCKS.sub(" ", html)
    text = _TAGS.sub(" ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return text


def _extract_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes, capped by page and character count.

    An empty or very short result is the scanned-PDF case: the file has no
    usable text layer, so the caller is asked for a text upload.
    """
    try:
        import pypdf
    except ImportError as e:
        raise ImportError("pypdf is required for PDF extraction: pip install pypdf") from e

    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        page_count = len(reader.pages)
        pieces: list[str] = []
        total = 0
        for index in range(min(page_count, PDF_MAX_PAGES)):
            if total >= PDF_MAX_CHARS:
                break
            extracted = reader.pages[index].extract_text() or ""
            piece = extracted.strip()
            if not piece:
                continue
            room = PDF_MAX_CHARS - total
            if len(piece) > room:
                piece = piece[:room]
            pieces.append(piece)
            total += len(piece)
    except BillSourceError:
        raise
    except Exception as exc:
        raise BillSourceError(MSG_PDF_UNREADABLE, 400) from exc

    text = "\n\n".join(pieces).strip()
    if len(text) < MIN_BILL_CHARS:
        raise BillSourceError(MSG_NO_TEXT_LAYER, 400)
    return text
