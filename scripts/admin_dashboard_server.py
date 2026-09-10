"""Tiny read-only local HTTP server for tools/admin_dashboard.html — an
admin-only view over every user's raw progress data (db/cloud_save/progress/
*.json), for eyeballing activity, scores, and weak spots across the whole
group. Same no-deps pattern as scripts/reports_server.py / titles_server.py.

This is intentionally read-only (GET only, no POST/write endpoints) since
the dashboard only displays data — it never edits a user's progress file.

Usage:
    python3 scripts/admin_dashboard_server.py
    -> open http://localhost:8769/tools/admin_dashboard.html in any browser

Never publish this page or its /api/* responses anywhere public — it
exposes every user's full raw progress (notes, mock answers, etc.).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRESS_DIR = os.path.join(ROOT, "db", "cloud_save", "progress")
ALL_QUESTIONS_PATH = os.path.join(ROOT, "db", "all_questions.json")
PORT = 8769

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".json": "application/json",
    ".css": "text/css",
}

# Only the fields the dashboard's scoring/breakdown logic actually needs —
# trimmed down from all_questions.json's full records (which carry stems,
# choices, rationales, image paths, ...) so the response stays small.
QUESTION_FIELDS = ("id", "category", "domain", "skill", "skill_code",
                    "difficulty_label", "test", "correct_answer")


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
            path = "/tools/admin_dashboard.html"

        if path == "/api/users":
            users = []
            if os.path.isdir(PROGRESS_DIR):
                for fname in sorted(os.listdir(PROGRESS_DIR)):
                    if fname.endswith(".json"):
                        users.append(fname[:-5])
            self._send_json({"ok": True, "users": users})
            return

        if path.startswith("/api/progress/"):
            username = path[len("/api/progress/"):]
            if "/" in username or ".." in username:
                self._send_json({"ok": False, "error": "invalid username"}, 400)
                return
            fpath = os.path.join(PROGRESS_DIR, f"{username}.json")
            if not os.path.isfile(fpath):
                self._send_json({"ok": False, "error": "no such user"}, 404)
                return
            with open(fpath) as f:
                data = json.load(f)
            self._send_json({"ok": True, "data": data})
            return

        if path == "/api/questions":
            questions = []
            if os.path.isfile(ALL_QUESTIONS_PATH):
                with open(ALL_QUESTIONS_PATH) as f:
                    full = json.load(f)
                questions = [{k: q.get(k) for k in QUESTION_FIELDS} for q in full]
            self._send_json({"ok": True, "questions": questions})
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

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {ROOT}")
    print(f"Open http://localhost:{PORT}/tools/admin_dashboard.html in any browser.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
