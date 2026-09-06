"""Tiny local HTTP server so tools/add_question.html can save staged
questions without the File System Access API (showDirectoryPicker), which
only Chromium browsers implement — Firefox and Safari have no equivalent.
This sidesteps that entirely: the page does a plain fetch() POST, which
every browser supports, and this script does the actual file write.

Usage:
    python3 scripts/staging_server.py
    -> open http://localhost:8765/tools/add_question.html in any browser

No third-party dependencies — stdlib http.server only. Binds to localhost
only; there's no auth because nothing outside your own machine can reach it.
"""
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING_PATH = os.path.join(ROOT, "db", "staging", "proposed_questions.json")
PORT = 8765

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
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
            path = "/tools/add_question.html"
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
        if urlparse(self.path).path != "/api/stage":
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        record = body["record"]

        staged = []
        if os.path.exists(STAGING_PATH):
            with open(STAGING_PATH) as f:
                staged = json.load(f)
        staged.append(record)
        os.makedirs(os.path.dirname(STAGING_PATH), exist_ok=True)
        with open(STAGING_PATH, "w") as f:
            json.dump(staged, f, indent=2, ensure_ascii=False)

        image_b64 = body.get("image_base64")
        image_rel_path = body.get("image_rel_path")
        if image_b64 and image_rel_path:
            full_img_path = os.path.normpath(os.path.join(ROOT, image_rel_path))
            if not full_img_path.startswith(ROOT):
                self._send_json({"ok": False, "error": "invalid image path"}, 400)
                return
            os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
            with open(full_img_path, "wb") as f:
                f.write(base64.b64decode(image_b64))

        self._send_json({"ok": True, "count": len(staged)})

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {ROOT}")
    print(f"Open http://localhost:{PORT}/tools/add_question.html in any browser (Firefox included).")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
