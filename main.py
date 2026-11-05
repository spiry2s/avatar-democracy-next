"""
FastAPI server for the Bill Summarizer.

Serves:
- GET /            - Web UI (frontend/index.html)
- POST /api/analyze - Analyze a bill (text, an official bill URL, or an uploaded PDF)
- GET /api/health  - Health check

Run with:
    python main.py

Or with uvicorn directly, on loopback:
    uvicorn main:app --host 127.0.0.1 --port 8000

A bind address other than 127.0.0.1, ::1, or localhost requires
APP_ACCESS_TOKEN (at least 16 characters). Loopback does not.
Use `python main.py` for that check. Uvicorn's own --host is not visible here.
"""

from __future__ import annotations

import hmac
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import (
    audit,
    avatar,
    bills as bills_mod,
    chamber,
    config,
    comparison,
    conflict,
    extractors,
    llm,
    population,
    profile as profile_mod,
    questionnaire,
)
from backend.bill_fetch import BillSourceError

# Resolve frontend directory relative to project root
PROJECT_ROOT = Path(__file__).parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

# ── LAN access token, browser guards, startup banner ─────────────────────────
#
# Loopback (127.0.0.1, ::1, localhost) does not need a token. Host and Origin
# are still checked against an allowlist, so a DNS-rebind to some other name
# is rejected. Anyone else on this machine can still open the loopback address.
# Any other bind address refuses to start until APP_ACCESS_TOKEN is at least
# 16 characters, and then every /api request must send that token.

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
MIN_ACCESS_TOKEN_CHARS = 16
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_IPS = 10_000
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)
# HTTP routes that spend a model call. One shared per-IP bucket covers all of them.
# POST /api/analyze spends a call for pasted text, an uploaded PDF, and the
# official bill-link field (the URL is fetched, then analyzed like a file).
# Backend callers behind those routes:
#   comparison.analyze_for_api -> summarizer (llm.get_provider / llm.resolve)
#   avatar.cast_vote -> llm.resolve for the primary vote, the checker pass, and drift
#   chamber.tag_sections -> llm.resolve
#   conflict.detect_conflicts -> llm.resolve
#   _freeze_and_analyze calls analyze_for_api and, when configured, detect_conflicts
SPENDING_ROUTES = (
    "POST /api/analyze",
    "POST /api/avatar/vote",
    "POST /api/bills/{bill_id}/vote",
    "POST /api/bills/{bill_id}/endorse",
    "POST /api/bills/{bill_id}/simulate-endorsements",
)
_JSON_MEDIA = "application/json"
_UPLOAD_MEDIA = "multipart/form-data"
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_JSON_EXACT = frozenset({
    "/api/profile",
    "/api/profile/delegates",
    "/api/avatar/vote",
    "/api/avatar/override",
    "/api/bills",
    "/api/population/regenerate",
})

# Address and port `python main.py` resolved. Loopback port 8000 until main()
# sets them, so the default process does not require a token.
bind_host = "127.0.0.1"
bind_port = 8000

_rate_lock = threading.Lock()
_rate_hits: OrderedDict[str, list[float]] = OrderedDict()


def is_loopback_host(host: str) -> bool:
    """True only for 127.0.0.1, ::1, and localhost. Comparison is case-insensitive."""
    return host.strip().lower() in LOOPBACK_HOSTS


def configured_access_token() -> str:
    """The shared LAN token, with surrounding whitespace removed. May be empty."""
    return os.environ.get("APP_ACCESS_TOKEN", "").strip()


def access_token_required(host: str | None = None) -> bool:
    """A non-loopback bind requires the token. Loopback never does."""
    chosen = bind_host if host is None else host
    return not is_loopback_host(chosen)


def access_token_startup_error(host: str) -> str:
    """Tell the operator how to set a token. Never includes the token or a key."""
    return (
        f"Refusing to start: {host} is not a loopback address.\n"
        "Loopback is only 127.0.0.1, ::1, or localhost. It does not need a token;\n"
        "Host and Origin checks still apply. Anyone else on this machine can reach it.\n"
        "Set APP_ACCESS_TOKEN to a random value of at least 16 characters, then start again.\n"
        "Generate one with:\n"
        '  python3 -c "import secrets;print(secrets.token_urlsafe(24))"\n'
        "Example:\n"
        f"  APP_ACCESS_TOKEN='<paste the token>' python main.py --host {host}"
    )


def ensure_access_token(host: str) -> None:
    """Exit before the process listens when a non-loopback bind has no usable token."""
    if is_loopback_host(host):
        return
    if len(configured_access_token()) >= MIN_ACCESS_TOKEN_CHARS:
        return
    print(access_token_startup_error(host), file=sys.stderr, flush=True)
    raise SystemExit(1)


def lan_ipv4_addresses() -> list[str]:
    """IPv4 addresses a phone on this LAN can open. Empty when none are found."""
    import socket

    found: list[str] = []

    def add(ip: str) -> None:
        if not ip or ip.startswith("127.") or ip == "0.0.0.0" or ip in found:
            return
        found.append(ip)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # UDP connect picks the default route address and does not send a packet.
            probe.connect(("192.0.2.1", 9))
            add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        infos = []
    for info in infos:
        try:
            add(info[4][0])
        except (IndexError, TypeError):
            continue
    return found


def phone_urls(host: str, port: int) -> list[str]:
    """URLs to open from a phone. A wildcard bind is expanded to LAN addresses."""
    cleaned = host.strip()
    if cleaned.lower() in {"0.0.0.0", "::", "[::]"}:
        addresses = lan_ipv4_addresses()
        if not addresses:
            return [f"http://<this-computer-lan-ip>:{port}"]
        return [f"http://{ip}:{port}" for ip in addresses]
    shown = cleaned
    if ":" in cleaned and not cleaned.startswith("["):
        shown = f"[{cleaned}]"
    return [f"http://{shown}:{port}"]


def format_lan_banner(host: str, port: int, urls: list[str] | None = None) -> str:
    """Boxed warning for stderr. Names the bind address and phone URLs.

    Does not read or include APP_ACCESS_TOKEN or any API key.
    """
    chosen = phone_urls(host, port) if urls is None else list(urls)
    lines = [
        "WARNING: Avatar Democracy is listening beyond loopback.",
        f"Bind address: {host}:{port}",
        "Open from a phone on this network:",
        *[f"  {url}" for url in chosen],
        "Anyone on this network who has the token can run analyses that spend the configured API keys.",
        "Use this only on a trusted private network.",
        "Never use it on public Wi-Fi.",
        "Press Ctrl+C to stop.",
    ]
    width = max(len(line) for line in lines)
    border = "+" + ("-" * (width + 2)) + "+"
    body = [f"| {line.ljust(width)} |" for line in lines]
    return "\n".join([border, *body, border])


def machine_host_names() -> set[str]:
    """This machine's hostname and FQDN, lowercased, without a trailing dot."""
    import socket

    names: set[str] = set()
    for lookup in (socket.gethostname, socket.getfqdn):
        try:
            value = lookup()
        except OSError:
            continue
        if value:
            names.add(value.strip().lower().rstrip("."))
    return names


def lan_interface_ips() -> list[str]:
    """IPv4 and IPv6 addresses of this machine, excluding loopback."""
    found = list(lan_ipv4_addresses())
    import socket

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
    except OSError:
        infos = []
    for info in infos:
        try:
            ip = str(info[4][0]).split("%", 1)[0]
        except (IndexError, TypeError, AttributeError):
            continue
        if ip and ip != "::1" and ip not in found:
            found.append(ip)
    return found


def extra_allowed_hosts() -> set[str]:
    """Hostnames from ALLOWED_HOSTS. Commas separate them. `*` is not a wildcard."""
    names: set[str] = set()
    for part in os.environ.get("ALLOWED_HOSTS", "").split(","):
        item = part.strip().lower().rstrip(".")
        if item.startswith("[") and item.endswith("]"):
            item = item[1:-1]
        if item:
            names.add(item)
    return names


def allowed_host_names() -> set[str]:
    """Names the browser may put in Host and Origin for this bind address.

    Loopback always allows 127.0.0.1, localhost, and ::1. A LAN bind also
    allows the bound address, each interface address when the bind is
    0.0.0.0 or ::, the machine hostname, and ALLOWED_HOSTS.
    """
    names = {"127.0.0.1", "localhost", "::1"}
    names.update(extra_allowed_hosts())
    if is_loopback_host(bind_host):
        return names
    cleaned = bind_host.strip().lower().strip("[]").rstrip(".")
    if cleaned in {"0.0.0.0", "::"}:
        for ip in lan_interface_ips():
            names.add(ip.strip().lower().rstrip("."))
    elif cleaned:
        names.add(cleaned)
    names.update(machine_host_names())
    return names


def _normalize_hostname(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _parse_host_header(value: str) -> tuple[str, int | None] | None:
    """Split a Host header into (hostname, port or None). None if it is not one host."""
    text = value.strip()
    if not text or any(ch in text for ch in " \t\r\n,\\@"):
        return None
    if text.startswith("["):
        end = text.find("]")
        if end <= 1:
            return None
        host = text[1:end]
        rest = text[end + 1:]
        if rest == "":
            port: int | None = None
        elif rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        else:
            return None
    elif text.count(":") > 1:
        return None
    elif ":" in text:
        host, port_text = text.rsplit(":", 1)
        if not port_text.isdigit():
            return None
        port = int(port_text)
    else:
        host = text
        port = None
    if not host or "/" in host:
        return None
    if port is not None and not 0 < port <= 65535:
        return None
    return _normalize_hostname(host), port


def host_header_allowed(values: list[str]) -> bool:
    """One Host header, on the allowlist, with the bound port or no port.

    A second Host header is rejected. X-Forwarded-Host is not read.
    """
    if len(values) != 1:
        return False
    parsed = _parse_host_header(values[0])
    if parsed is None:
        return False
    host, port = parsed
    if port is not None and port != bind_port:
        return False
    return host in allowed_host_names()


def _raw_header_values(request: Request, name: str) -> list[str]:
    """Every value of a header, matched case-insensitively on the raw ASGI name.

    Header names are compared after lowercasing so `Origin` and `origin` are
    the same header. A second copy is not dropped.
    """
    wanted = name.lower().encode("latin-1")
    values: list[str] = []
    for key, value in request.scope.get("headers") or ():
        if not isinstance(key, (bytes, bytearray)):
            continue
        if bytes(key).lower() != wanted:
            continue
        if isinstance(value, (bytes, bytearray)):
            values.append(bytes(value).decode("latin-1"))
        else:
            values.append(str(value))
    return values


def _split_origin(value: str) -> tuple[str, str, int] | None:
    """Parse an Origin or a scheme://host[:port]. None when it is not one origin."""
    text = value.strip()
    if not text or text.lower() == "null":
        return None
    if any(ch in text for ch in " \t\r\n,"):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.query or parts.fragment:
        return None
    if parts.path not in {"", "/"}:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host.lower(), port


def _origin_allowed(request: Request) -> bool:
    """Missing Origin is allowed. A present Origin must name an allowed host.

    The comparison is the allowlist plus http or https plus the bound port.
    It is not "Origin equals this request's Host". `null`, a different port,
    a different host, or more than one Origin value is rejected.
    """
    origins = _raw_header_values(request, "origin")
    if not origins:
        return True
    if len(origins) != 1:
        return False
    got = _split_origin(origins[0])
    if got is None:
        return False
    scheme, host, port = got
    if scheme not in {"http", "https"}:
        return False
    if port != bind_port:
        return False
    return _normalize_hostname(host) in allowed_host_names()


def _media_types(request: Request) -> list[str]:
    types: list[str] = []
    for value in _raw_header_values(request, "content-type"):
        types.append(value.split(";", 1)[0].strip().lower())
    return types


def _is_json_path(path: str) -> bool:
    if path in _JSON_EXACT:
        return True
    parts = [part for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[0] == "api"
        and parts[1] == "profile"
        and parts[2] == "delegates"
        and parts[4] == "sources"
    ):
        return True
    if (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "bills"
        and parts[3] in {"versions", "simulate-endorsements"}
    ):
        return True
    return False


def _expected_media_type(method: str, path: str) -> str | None:
    """JSON endpoints require application/json. The upload endpoint requires multipart."""
    if method not in {"POST", "PUT", "PATCH"}:
        return None
    if path == "/api/analyze":
        return _UPLOAD_MEDIA
    if _is_json_path(path):
        return _JSON_MEDIA
    return None


def is_spending_request(method: str, path: str) -> bool:
    """True for every POST that can call a model. They share one rate-limit bucket."""
    if method != "POST":
        return False
    if path in {"/api/analyze", "/api/avatar/vote"}:
        return True
    parts = [part for part in path.split("/") if part]
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "bills"
        and parts[3] in {"vote", "endorse", "simulate-endorsements"}
    )


def reset_rate_limits() -> None:
    """Drop the in-memory counters. Tests use this so cases stay independent."""
    with _rate_lock:
        _rate_hits.clear()


def _client_ip(request: Request) -> str:
    """The socket peer. X-Forwarded-For and X-Real-IP are not consulted."""
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _prune_rate_limits(now: float) -> None:
    cutoff = now - RATE_WINDOW_SECONDS
    expired = []
    for ip, hits in _rate_hits.items():
        hits[:] = [stamp for stamp in hits if stamp > cutoff]
        if not hits:
            expired.append(ip)
    for ip in expired:
        _rate_hits.pop(ip, None)


def _rate_limited(request: Request) -> bool:
    """Shared LAN limit: RATE_LIMIT spending posts per minute for one client IP.

    The map keeps at most RATE_LIMIT_MAX_IPS addresses and drops the least
    recently seen one past that. Expired timestamps are removed first.
    """
    ip = _client_ip(request)
    now = time.monotonic()
    with _rate_lock:
        _prune_rate_limits(now)
        hits = _rate_hits.get(ip)
        if hits is None:
            while len(_rate_hits) >= RATE_LIMIT_MAX_IPS:
                _rate_hits.popitem(last=False)
            hits = []
            _rate_hits[ip] = hits
        else:
            _rate_hits.move_to_end(ip)
        if len(hits) >= RATE_LIMIT:
            return True
        hits.append(now)
        return False


def _presented_token_ok(request: Request) -> bool:
    """True only when the single X-Access-Token header matches, via compare_digest.

    The query string is ignored. Zero headers, or more than one, does not match.
    A token shorter than 16 characters is never accepted.
    """
    expected = configured_access_token()
    if len(expected) < MIN_ACCESS_TOKEN_CHARS:
        return False
    presented = _raw_header_values(request, "x-access-token")
    if len(presented) != 1:
        return False
    try:
        got = presented[0].encode("utf-8")
        want = expected.encode("utf-8")
    except UnicodeError:
        return False
    return hmac.compare_digest(got, want)


def _deny(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def guard_api_request(request: Request) -> JSONResponse | None:
    """Block a bad request. None means the route may run.

    Host is checked on every route, including pages, before Origin and before
    the access token. Static pages, the manifest, icons, and sw.js still do
    not need the token.
    """
    if not host_header_allowed(_raw_header_values(request, "host")):
        return _deny(421, "Misdirected Request")
    path = request.url.path
    if path != "/api" and not path.startswith("/api/"):
        return None
    if access_token_required() and not _presented_token_ok(request):
        # Same body for a missing header and a wrong header.
        return _deny(401, "Unauthorized")
    if request.method in _STATE_CHANGING and not _origin_allowed(request):
        return _deny(403, "Forbidden")
    expected = _expected_media_type(request.method, path)
    if expected is not None:
        types = _media_types(request)
        if not types or any(media != expected for media in types):
            return _deny(415, "Unsupported Media Type")
    if access_token_required() and is_spending_request(request.method, path):
        if _rate_limited(request):
            return _deny(429, "Too Many Requests", {"Retry-After": str(int(RATE_WINDOW_SECONDS))})
    return None


def _with_security_headers(response: JSONResponse):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


app = FastAPI(
    title="Bill Summarizer",
    description="Plain-language summaries and red-flag detection for legislative bills.",
    version="0.1.0",
)

# Serve frontend static files (CSS, JS) at /static/*
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.middleware("http")
async def reject_untrusted_api_requests(request: Request, call_next):
    """Host allowlist, then token, origin, content type, and the LAN rate limit."""
    blocked = guard_api_request(request)
    if blocked is not None:
        return _with_security_headers(blocked)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


class AnalyzeTextRequest(BaseModel):
    """Request body for analyzing pasted text."""

    text: str
    source_url: Optional[str] = None


def _is_model_text_error(exc: ValueError) -> bool:
    """True when the model text failed JSON parsing or the analysis schema.

    A provider ValueError, such as the Anthropic SDK requiring streaming, is
    not model text and must not be reported as unparseable output.
    """
    text = str(exc)
    return text.startswith("Could not parse") or text.startswith("Analysis JSON failed schema")


def _output_status(exc: ValueError) -> int:
    return 502 if _is_model_text_error(exc) else 500


def _output_detail(exc: ValueError) -> str:
    if _is_model_text_error(exc):
        return f"Analysis produced unparseable output: {exc}"
    return f"Analysis failed: {exc}"


def _serve(filename: str) -> FileResponse:
    page = FRONTEND_DIR / filename
    if not page.exists():
        raise HTTPException(status_code=500, detail=f"Frontend file missing: {filename}")
    return FileResponse(page)


@app.get("/")
async def root() -> FileResponse:
    """Serve the landing page."""
    return _serve("index.html")


@app.get("/analyze")
async def analyze_page() -> FileResponse:
    """Serve the bill analyzer UI."""
    return _serve("analyze.html")


@app.get("/avatar")
async def avatar_page() -> FileResponse:
    """Serve the Avatar setup UI."""
    return _serve("avatar.html")


@app.get("/vote")
async def vote_page() -> FileResponse:
    """Serve the voting UI."""
    return _serve("vote.html")


@app.get("/history")
async def history_page() -> FileResponse:
    """Serve the audit-log UI."""
    return _serve("history.html")


@app.get("/bills")
async def bills_page() -> FileResponse:
    """Serve the bill-lifecycle UI."""
    return _serve("bills.html")


@app.get("/chamber")
async def chamber_page() -> FileResponse:
    """Serve the Proxy Chamber population dashboard."""
    return _serve("chamber.html")


@app.get("/manifest.webmanifest")
async def web_manifest() -> FileResponse:
    """Web app manifest so a phone can add the UI to its home screen."""
    path = FRONTEND_DIR / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Frontend file missing: manifest.webmanifest")
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    """Static-shell service worker. It does not cache /api."""
    path = FRONTEND_DIR / "sw.js"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Frontend file missing: sw.js")
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/health")
async def health() -> dict:
    """Health check. Reports whether the configured LLM provider has a credential.

    With the default Anthropic provider this is whether ANTHROPIC_API_KEY is set.
    Comparison fields name the configured provider and model only. The response
    never includes an API key or any other secret.
    """
    configured = comparison.comparison_configured()
    provider = (config.COMPARISON_PROVIDER or "").strip().lower() if configured else ""
    model = (config.COMPARISON_MODEL or "").strip() if configured else ""
    return {
        "status": "ok",
        "api_key_configured": llm.credentials_configured(),
        "comparison_configured": configured,
        "comparison_provider": provider,
        "comparison_model": model,
        "comparison_key_configured": bool(provider) and llm.credentials_configured(provider),
    }


@app.post("/api/analyze")
async def analyze(
    text: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> JSONResponse:
    """
    Analyze a bill from text, an official bill link, or an uploaded PDF.

    Provide exactly one of:
    - text: Pasted bill text
    - url: https URL on the official-source allowlist (HTML, PDF, or plain text)
    - file: Uploaded PDF

    The url field is fetched by backend.bill_fetch and then analyzed through
    the same pipeline as an uploaded file. Any other host is a 400.

    Returns the structured analysis as JSON.
    """
    # Count provided inputs
    sources_provided = sum(x is not None and x != "" for x in [text, url, file])

    if sources_provided == 0:
        raise HTTPException(
            status_code=400,
            detail="Provide one of: text, url, or file",
        )
    if sources_provided > 1:
        raise HTTPException(
            status_code=400,
            detail="Provide only one of: text, url, or file (not multiple)",
        )

    # Extract text from whichever source was provided
    try:
        if text:
            bill_text = extractors.extract_text(text, source_type="text")
        elif url:
            bill_text = extractors.extract_text(url, source_type="url")
        elif file:
            content = await file.read()
            bill_text = extractors.extract_text(content, source_type="pdf")
        else:
            # Should never reach here due to checks above
            raise HTTPException(status_code=400, detail="No source provided")
    except BillSourceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract bill text: {str(e)}",
        ) from e

    if not bill_text or len(bill_text.strip()) < extractors.MIN_BILL_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Extracted text is too short ({len(bill_text)} chars). "
            "The source may not contain a bill or extraction failed.",
        )

    # Run the analysis
    try:
        analysis = comparison.analyze_for_api(bill_text)
    except llm.LLMTruncatedError as e:
        # A cut-off reply is not unparseable JSON. Say so.
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        # Configuration errors (missing API key)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=_output_status(e), detail=_output_detail(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}",
        ) from e

    body: dict = {
        "success": True,
        "analysis": analysis,
        "source_chars": len(bill_text),
    }
    # Same object as analysis["model_comparison"] when a second family ran.
    # Omitted entirely when comparison is not configured.
    if isinstance(analysis, dict) and "model_comparison" in analysis:
        body["model_comparison"] = analysis["model_comparison"]
    return JSONResponse(content=body)


# ── Avatar: profile configuration ────────────────────────────────────────────


class ProfileRequest(BaseModel):
    """Create or update the citizen's compass + values."""

    questionnaire_answers: dict[str, int] = {}
    values: str = ""


class DelegateRequest(BaseModel):
    issue_area: str
    name: str
    note: str = ""


@app.get("/api/questionnaire")
async def get_questionnaire() -> dict:
    """The seeding questionnaire (statements + scale + axis labels)."""
    return questionnaire.public_questionnaire()


@app.get("/api/profile")
async def get_profile() -> JSONResponse:
    """Return the stored profile, or null if none configured yet."""
    p = profile_mod.load_profile()
    return JSONResponse(content={"profile": p.to_dict() if p else None})


@app.post("/api/profile")
async def put_profile(req: ProfileRequest) -> JSONResponse:
    """
    Create or update the citizen's compass and values. Delegates are managed
    separately (they have asymmetric rate-limits). Preserves existing delegates.
    """
    existing = profile_mod.load_profile()
    is_new = existing is None
    p = existing or profile_mod.CitizenProfile()

    p.questionnaire_answers = req.questionnaire_answers or p.questionnaire_answers
    p.compass = questionnaire.score(p.questionnaire_answers)
    p.values = req.values

    profile_mod.save_profile(p)
    audit.log_event(
        "profile_created" if is_new else "profile_updated",
        {"compass": p.compass, "values_len": len(p.values)},
    )
    return JSONResponse(content={"profile": p.to_dict()})


@app.post("/api/profile/delegates")
async def add_delegate(req: DelegateRequest) -> JSONResponse:
    """Add/replace a per-issue delegate. Enters cooling-off before it's followed."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="Configure your Avatar before adding delegates.")
    try:
        delegate = p.add_delegate(req.issue_area, req.name, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    profile_mod.save_profile(p)
    audit.log_event("delegate_added", {
        "issue_area": delegate.issue_area,
        "name": delegate.name,
        "active_at": delegate.active_at,
        "cooling_off_seconds": profile_mod.cooling_off_seconds(),
    })
    return JSONResponse(content={"profile": p.to_dict(), "added": delegate.to_dict()})


@app.delete("/api/profile/delegates/{issue_area}")
async def revoke_delegate(issue_area: str) -> JSONResponse:
    """Revoke a per-issue delegate immediately."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    removed = p.revoke_delegate(issue_area)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No delegate for issue area '{issue_area}'.")

    profile_mod.save_profile(p)
    audit.log_event("delegate_revoked", {"issue_area": issue_area.strip().lower()})
    return JSONResponse(content={"profile": p.to_dict()})


class SourceRequest(BaseModel):
    title: str = ""
    text: str


@app.post("/api/profile/delegates/{issue_area}/sources")
async def add_delegate_source(issue_area: str, req: SourceRequest) -> JSONResponse:
    """Attach a recorded position to a delegate (grounds the Avatar in evidence)."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    try:
        src = p.add_source(issue_area, req.title, req.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    profile_mod.save_profile(p)
    audit.log_event("delegate_source_added", {
        "issue_area": issue_area.strip().lower(), "title": src["title"],
    })
    return JSONResponse(content={"profile": p.to_dict(), "added": src})


@app.delete("/api/profile/delegates/{issue_area}/sources/{source_id}")
async def remove_delegate_source(issue_area: str, source_id: str) -> JSONResponse:
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    if not p.remove_source(issue_area, source_id):
        raise HTTPException(status_code=404, detail="Source not found.")
    profile_mod.save_profile(p)
    return JSONResponse(content={"profile": p.to_dict()})


# ── Avatar: voting ────────────────────────────────────────────────────────────


class VoteRequest(BaseModel):
    """A bill analysis (as produced by /api/analyze) for the Avatar to vote on."""

    analysis: dict


class OverrideRequest(BaseModel):
    bill_title: str
    section_id: str
    position: str
    previous_position: str = ""


@app.post("/api/avatar/vote")
async def avatar_vote(req: VoteRequest) -> JSONResponse:
    """Cast the citizen's Avatar vote on a bill analysis."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(
            status_code=400,
            detail="Configure your Avatar first (visit /avatar).",
        )

    try:
        vote = avatar.cast_vote(req.analysis, p)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        if _is_model_text_error(e):
            detail = f"Avatar produced unparseable output: {e}"
            status = 502
        else:
            detail = f"Vote failed: {e}"
            status = 500
        raise HTTPException(status_code=status, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vote failed: {e}") from e

    audit.log_event("vote_cast", {
        "bill_title": vote.bill_title,
        "sections": len(vote.section_votes),
        "yes": sum(1 for v in vote.section_votes if v.position == "yes" and not v.needs_human_review),
        "no": sum(1 for v in vote.section_votes if v.position == "no" and not v.needs_human_review),
        "abstain": sum(1 for v in vote.section_votes if v.position == "abstain"),
        "needs_human_review": sum(
            1 for v in vote.section_votes
            if v.needs_human_review and v.position in ("yes", "no")
        ),
    })
    return JSONResponse(content={"vote": vote.to_dict()})


@app.post("/api/avatar/override")
async def avatar_override(req: OverrideRequest) -> JSONResponse:
    """Record a citizen override of an Avatar section vote."""
    audit.log_event("vote_overridden", {
        "bill_title": req.bill_title,
        "section_id": req.section_id,
        "from": req.previous_position,
        "to": req.position,
    })
    return JSONResponse(content={"ok": True})


@app.get("/api/audit")
async def get_audit(limit: int = 200) -> JSONResponse:
    """Return the audit log, newest first."""
    return JSONResponse(content={"events": audit.read_events(limit=limit)})


# ── Bill lifecycle ────────────────────────────────────────────────────────────


class ProposeRequest(BaseModel):
    title: str
    text: str
    scope: str = "ordinary"
    emergency: bool = False


class VersionRequest(BaseModel):
    text: str
    note: str = ""


class SimulateRequest(BaseModel):
    count: int = 1


def _persist_refresh(bill: bills_mod.Bill) -> bills_mod.Bill:
    """Advance the lazy clock on read and persist if it changed state."""
    if bills_mod.refresh_state(bill):
        bills_mod.save_bill(bill)
    return bill


@app.get("/api/bills")
async def list_bills() -> JSONResponse:
    out = [_persist_refresh(b).to_dict() for b in bills_mod.list_bills()]
    return JSONResponse(content={"bills": out})


@app.get("/api/bills/{bill_id}")
async def get_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return JSONResponse(content={"bill": _persist_refresh(bill).to_dict()})


@app.post("/api/bills")
async def propose_bill(req: ProposeRequest) -> JSONResponse:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Bill text is required")
    bill = bills_mod.propose(req.title, req.text, req.scope, emergency=req.emergency)
    bills_mod.save_bill(bill)
    audit.log_event("bill_proposed", {
        "id": bill.id, "title": bill.title, "scope": bill.scope, "emergency": bill.emergency,
    })
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/versions")
async def add_bill_version(bill_id: str, req: VersionRequest) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.add_version(bill, req.text, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/open")
async def open_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.open_for_endorsement(bill)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    bills_mod.save_bill(bill)
    audit.log_event("bill_opened", {"id": bill.id, "title": bill.title})
    return JSONResponse(content={"bill": bill.to_dict()})


def _freeze_and_analyze(bill: bills_mod.Bill) -> None:
    """If the bill just crossed its endorsement threshold, freeze it and run the
    neutral analysis once on the frozen text so it's ready when voting opens."""
    if bills_mod.freeze_if_threshold_met(bill):
        audit.log_event("bill_frozen", {
            "id": bill.id, "frozen_version": bill.frozen_version,
            "cooling_off_until": bill.cooling_off_until, "emergency": bill.emergency,
        })
        try:
            bill.analysis = comparison.analyze_for_api(bills_mod.frozen_text(bill))
        except Exception:
            bill.analysis = None  # analysis will be retried lazily at vote time

        # Cross-bill conflict: compare against already-passed bills.
        if config.CROSS_BILL_CONFLICT_CHECK and bill.analysis:
            try:
                passed = [
                    {"id": b.id, "title": b.title,
                     "summary": (b.analysis or {}).get("plain_summary", "") or b.title}
                    for b in bills_mod.list_bills()
                    if b.state == bills_mod.BillState.PASSED and b.id != bill.id
                ]
                bill.conflicts = conflict.detect_conflicts(bill.analysis, passed)
                if bill.conflicts:
                    audit.log_event("bill_conflict_flagged", {"id": bill.id, "count": len(bill.conflicts)})
            except Exception:
                bill.conflicts = []


@app.post("/api/bills/{bill_id}/endorse")
async def endorse_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.endorse(bill, "local")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit.log_event("bill_endorsed", {"id": bill.id, "total": bills_mod.total_endorsements(bill)})
    _freeze_and_analyze(bill)
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.delete("/api/bills/{bill_id}/endorse")
async def revoke_bill_endorsement(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    if not bills_mod.revoke_endorsement(bill, "local"):
        raise HTTPException(status_code=400, detail="No revocable endorsement (not endorsed, or the 24h window has passed).")
    bills_mod.save_bill(bill)
    audit.log_event("endorsement_revoked", {"id": bill.id})
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/simulate-endorsements")
async def simulate_endorsements(bill_id: str, req: SimulateRequest) -> JSONResponse:
    """Demo scaffold: add N simulated endorsements so thresholds are reachable
    with one human. The Proxy Chamber phase replaces this with real Avatars."""
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.state != bills_mod.BillState.ENDORSING:
        raise HTTPException(status_code=400, detail="Bill is not open for endorsement")
    bill.simulated_endorsements += max(0, req.count)
    _freeze_and_analyze(bill)
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/vote")
async def vote_on_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    _persist_refresh(bill)
    if bill.state != bills_mod.BillState.VOTING:
        raise HTTPException(status_code=400, detail=f"Bill is not open for voting (state: {bill.state}).")

    profile = profile_mod.load_profile()
    if profile is None:
        raise HTTPException(status_code=400, detail="Configure your Avatar first (visit /avatar).")

    try:
        if bill.analysis is None:
            bill.analysis = comparison.analyze_for_api(bills_mod.frozen_text(bill))
        # The operator's own Avatar votes through the full engine (values + delegates).
        av = avatar.cast_vote(bill.analysis, profile)
        bill.vote = av.to_dict()
        # The Proxy Chamber: one axis-tagging call, then the whole population votes
        # deterministically. Operator's position is folded into the tally.
        pop = population.load_population()
        tags = chamber.tag_sections(bill.analysis)
        tallies = chamber.aggregate(bill.analysis, bill.vote, pop, tags)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        if _is_model_text_error(e):
            detail = f"Produced unparseable output: {e}"
            status = 502
        else:
            detail = f"Vote failed: {e}"
            status = 500
        raise HTTPException(status_code=status, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vote failed: {e}") from e

    bill.result = bills_mod.compute_result(tallies, config.SECTION_PASSAGE_RATIO)
    bill.result["population_size"] = len(pop) + 1  # + the operator
    bill.result["section_tags"] = tags
    bill.state = bills_mod.BillState.PASSED if bill.result["passed"] else bills_mod.BillState.FAILED
    bills_mod.save_bill(bill)

    audit.log_event("bill_voted", {
        "id": bill.id, "title": bill.title,
        "population": bill.result["population_size"],
        "sections_passed": bill.result["sections_passed"],
        "sections_total": bill.result["sections_total"],
    })
    audit.log_event("bill_passed" if bill.result["passed"] else "bill_failed",
                    {"id": bill.id, "title": bill.title})
    return JSONResponse(content={"bill": bill.to_dict()})


# ── Proxy Chamber: population ─────────────────────────────────────────────────


class RegeneratePopulationRequest(BaseModel):
    size: Optional[int] = None


@app.get("/api/population")
async def get_population() -> JSONResponse:
    """Synthetic population: size, archetypes, per-axis means, and compasses."""
    return JSONResponse(content=population.distribution())


@app.post("/api/population/regenerate")
async def regenerate_population(req: RegeneratePopulationRequest) -> JSONResponse:
    citizens = population.generate_population(req.size)
    audit.log_event("population_generated", {"size": len(citizens)})
    return JSONResponse(content=population.distribution(citizens))


def ensure_port_available(host: str, port: int) -> None:
    """Exit before the startup banner when the bind port is already taken."""
    import errno
    import socket
    import sys

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Port {port} on {host} is already in use. "
                "Set PORT to a free port and start again.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
    finally:
        sock.close()


def resolve_bind_host(argv: list[str] | None = None) -> str:
    """Address ``python main.py`` binds.

    ``--host`` wins when it is passed. Otherwise the HOST environment variable
    is used. When neither is set, the default stays ``127.0.0.1`` (localhost).
    ``0.0.0.0`` is opt-in and exposes the app to the local network.
    """
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default=None)
    args, _unknown = parser.parse_known_args([] if argv is None else argv)
    if args.host:
        return args.host
    return os.environ.get("HOST", "127.0.0.1")


def main() -> None:
    """Entry point for `python main.py`."""
    import uvicorn

    global bind_host, bind_port

    port = int(os.environ.get("PORT", "8000"))
    host = resolve_bind_host(sys.argv[1:])
    # Token first, then the port, then the banner. A refused start prints neither
    # the banner nor the listen line. The port check still exits before the banner.
    ensure_access_token(host)
    ensure_port_available(host, port)
    bind_host = host
    bind_port = port

    if not is_loopback_host(host):
        print(format_lan_banner(host, port), file=sys.stderr, flush=True)

    print(f"Starting Bill Summarizer on http://{host}:{port}")
    print(f"LLM provider: {config.LLM_PROVIDER} (primary model {config.MODEL})")
    print(f"API key configured: {llm.credentials_configured()}")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
