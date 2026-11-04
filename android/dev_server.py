"""
Desktop test harness for the STANDALONE Android bundle.

Serves the same thing MainActivity serves on the phone — frontend/ assets plus
the injected app/*.js standalone scripts — so the in-app JS backend can be
exercised in a desktop browser. No FastAPI, no Python backend: every /api/*
call is handled by the injected JavaScript, exactly as in the APK.

Usage:  py -3 android/dev_server.py   ->  http://127.0.0.1:8123
"""

from __future__ import annotations

import http.server
import mimetypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
APP_ASSETS = ROOT / "android" / "app" / "src" / "main" / "assets" / "app"

# Keep in sync with MainActivity.INJECT_TAGS
INJECT_TAGS = (
    '<script src="/app/store.js"></script>'
    '<script src="/app/prompts.js"></script>'
    '<script src="/app/llm.js"></script>'
    '<script src="/app/localapi.js"></script>'
    '<script src="/app/shim.js"></script>'
)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path in ("", "/"):
            path = "/index.html"

        if path.startswith("/static/"):
            file = FRONTEND / path[len("/static/"):]
        elif path.startswith("/app/"):
            file = APP_ASSETS / path[len("/app/"):]
        else:
            name = path.lstrip("/")
            if "." not in name:
                name += ".html"
            file = FRONTEND / name

        if not file.is_file():
            self.send_error(404, f"Not found: {path}")
            return

        data = file.read_bytes()
        mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        if file.suffix == ".html":
            html = data.decode("utf-8")
            html = html.replace("<head>", "<head>" + INJECT_TAGS, 1)
            data = html.encode("utf-8")
            mime = "text/html"
        elif file.suffix == ".js":
            mime = "text/javascript"

        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep test output quiet


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8123), Handler)
    print("Standalone bundle test server: http://127.0.0.1:8123")
    server.serve_forever()
