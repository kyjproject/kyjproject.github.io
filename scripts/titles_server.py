"""Tiny local HTTP server so tools/manage_titles.html can edit
db/user_titles.json from a form instead of hand-editing JSON — same pattern
as scripts/staging_server.py + tools/add_question.html.

Usage:
    python3 scripts/titles_server.py
    -> open http://localhost:8766/tools/manage_titles.html in any browser

No third-party dependencies — stdlib http.server only. Binds to localhost
only; there's no auth because nothing outside your own machine can reach it.

Writing here only changes your local db/user_titles.json — Code.gs reads
this file from GitHub, not this machine, so commit + push it for a change
to reach the live site. And right now LEADERBOARD_BADGES_ENABLED_
(Code.gs) / LB_BADGES_ENABLED (site/index.template.html) are both off, so
no badge shows anywhere regardless, until those two flags flip back on.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLES_PATH = os.path.join(ROOT, "db", "user_titles.json")
PORT = 8766

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".json": "application/json",
    ".css": "text/css",
}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            path = "/tools/manage_titles.html"
        if path == "/api/titles":
            titles = {}
            if os.path.exists(TITLES_PATH):
                with open(TITLES_PATH) as f:
                    titles = json.load(f)
            self._send_json({"ok": True, "titles": titles})
            return
        full = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not full.startswith(ROOT) or not os.path.isfile(full):
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        ctype = CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlparse(self.path).path != "/api/titles":
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        # Same "client sends the whole desired state" pattern as
        # staging_server.py's /api/stage — covers creating/editing a title,
        # adding/removing one of its users, and deleting a title outright,
        # since tools/manage_titles.html always sends the full titles
        # registry ({title_id: {text, emoji, color, reason, users: [...]}})
        # it wants written.
        titles = body.get("titles")
        if not isinstance(titles, dict):
            self._send_json({"ok": False, "error": "missing/invalid 'titles' object"}, 400)
            return
        os.makedirs(os.path.dirname(TITLES_PATH), exist_ok=True)
        with open(TITLES_PATH, "w") as f:
            json.dump(titles, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
        self._send_json({"ok": True, "count": len(titles)})

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {ROOT}")
    print(f"Open http://localhost:{PORT}/tools/manage_titles.html in any browser.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
