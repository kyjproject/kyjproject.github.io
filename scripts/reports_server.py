"""Tiny local HTTP server backing two local-only review tools:

  - tools/review_dashboard.html — manual QA pass over the ENTIRE question
    bank, grouped by category/difficulty, tile-grid style (like the site's
    own Analysis tab). Each question gets marked approved (green), problem
    (red), or editing (yellow) as you go through it.
  - tools/review_reports.html — the narrower queue of questions students
    flagged as broken via the in-app "Report a problem" button.

Both edit db/categories/*.json directly. No third-party deps, plain fetch()
from the browser, localhost only — same approach as scripts/staging_server.py.

Usage:
    python3 scripts/reports_server.py
    -> open http://localhost:8766/ (the dashboard) or
       http://localhost:8766/tools/review_reports.html (student reports)

After editing anything, rebuild the live site:
    python3 scripts/build_db.py
    python3 scripts/build_site_data.py
    python3 scripts/build_site.py
    python3 scripts/build_github_pages.py
"""
import base64
import glob
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ThreadingHTTPServer handles each request on its own thread, and every write
# below is a read-modify-write of a whole JSON file (load, mutate, save). Two
# POSTs landing close together (e.g. approving two different questions within
# the same second) could otherwise both read the pre-change file and each
# write back just their own edit — the second write silently clobbers the
# first. One lock around every mutating request serializes them so nothing
# gets lost.
WRITE_LOCK = threading.Lock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_PATH = os.path.join(ROOT, "db", "staging", "reported_questions.json")
RESOLVED_PATH = os.path.join(ROOT, "db", "staging", "resolved_reports.json")
REVIEW_STATUS_PATH = os.path.join(ROOT, "db", "staging", "review_status.json")
CATEGORIES_DIR = os.path.join(ROOT, "db", "categories")
PORT = 8766

# category -> target category-file, kept in sync with tools/add_question.html's
# CATEGORY_META and scripts/review_staged_questions.py's — needed so that if an
# edit changes a question's category, it gets moved to the right file.
CATEGORY_FILE = {
    "Grammar": "grammar.json",
    "Vocab": "vocab.json",
    "Transitions": "transitions.json",
    "Notes": "notes.json",
    "Functions": "functions.json",
    "Main Idea": "main_idea.json",
    "Inferences": "inferences.json",
    "Cross-Text": "cross-text.json",
    "Textual Evidence": "command_of_evidence.json",
    "Quantitative Evidence": "command_of_evidence.json",
    "Algebra": "algebra.json",
    "Advanced Math": "advanced_math.json",
    "Problem-Solving and Data Analysis": "problem_solving_and_data_analysis.json",
    "Geometry and Trigonometry": "geometry_and_trigonometry.json",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".css": "text/css",
}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        text = f.read()
    return json.loads(text) if text.strip() else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def all_category_paths():
    return sorted(glob.glob(os.path.join(CATEGORIES_DIR, "*.json")))


def load_all_questions():
    """[(filepath, record), ...] across every db/categories/*.json file."""
    out = []
    for path in all_category_paths():
        for r in load_json(path, []):
            out.append((path, r))
    return out


def find_question(qid):
    """Return (filepath, index, record) for the live question with this id, or None."""
    for path in all_category_paths():
        records = load_json(path, [])
        for i, r in enumerate(records):
            if r.get("id") == qid:
                return path, i, r
    return None


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            path = "/tools/review_dashboard.html"

        if path == "/api/reports":
            reports = load_json(REPORTS_PATH, [])
            joined = []
            for r in reports:
                found = find_question(r.get("qid"))
                joined.append({**r, "question": found[2] if found else None})
            resolved = load_json(RESOLVED_PATH, [])
            self._send_json({"ok": True, "reports": joined, "resolved": resolved})
            return

        if path == "/api/questions":
            questions = [r for _path, r in load_all_questions()]
            review_status = load_json(REVIEW_STATUS_PATH, {})
            reported_qids = sorted({r.get("qid") for r in load_json(REPORTS_PATH, [])})
            self._send_json({"ok": True, "questions": questions, "review_status": review_status,
                              "reported_qids": reported_qids})
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
        # This is a local dev tool that gets edited while it's running — never
        # let the browser serve a stale cached copy of the HTML/JS after a
        # file changes on disk.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        with WRITE_LOCK:
            self._do_POST_locked()

    def _do_POST_locked(self):
        route = urlparse(self.path).path
        body = self._read_body()

        if route == "/api/resolve":
            report = body.get("report")
            if not report or not report.get("qid") or not report.get("reported_at"):
                self._send_json({"ok": False, "error": "missing report"}, 400)
                return
            reports = load_json(REPORTS_PATH, [])
            remaining = [
                r for r in reports
                if not (r.get("qid") == report["qid"] and r.get("reported_at") == report["reported_at"])
            ]
            if len(remaining) == len(reports):
                self._send_json({"ok": False, "error": "report not found (already resolved?)"}, 404)
                return
            save_json(REPORTS_PATH, remaining)
            resolved = load_json(RESOLVED_PATH, [])
            resolved.append({**report, "resolved_at": body.get("resolved_at")})
            save_json(RESOLVED_PATH, resolved)
            # Resolving a student report means someone looked at (and fixed,
            # or judged fine) that question — that's exactly what "approved"
            # means on the review dashboard, so keep the two in sync instead
            # of leaving the dashboard with no record it was ever handled.
            review_status = load_json(REVIEW_STATUS_PATH, {})
            review_status[report["qid"]] = {
                "status": "approved",
                "note": f"resolved report: {report.get('reason', '')}".strip(": "),
                "updated_at": body.get("resolved_at"),
            }
            save_json(REVIEW_STATUS_PATH, review_status)
            self._send_json({"ok": True, "remaining": len(remaining)})
            return

        if route == "/api/set_status":
            qid = body.get("qid")
            status = body.get("status")
            if not qid or status not in ("approved", "problem", "editing", None):
                self._send_json({"ok": False, "error": "missing/invalid qid or status"}, 400)
                return
            review_status = load_json(REVIEW_STATUS_PATH, {})
            if status is None:
                review_status.pop(qid, None)
            else:
                review_status[qid] = {
                    "status": status,
                    "note": body.get("note", ""),
                    "updated_at": body.get("updated_at"),
                }
            save_json(REVIEW_STATUS_PATH, review_status)
            self._send_json({"ok": True})
            return

        if route == "/api/update_question":
            record = body.get("record")
            if not record or not record.get("id"):
                self._send_json({"ok": False, "error": "missing record"}, 400)
                return
            found = find_question(record["id"])
            if not found:
                self._send_json({"ok": False, "error": f"question {record['id']} not found in db/categories"}, 404)
                return
            old_path, idx, _old = found
            target_file = CATEGORY_FILE.get(record.get("category"))
            target_path = os.path.join(CATEGORIES_DIR, target_file) if target_file else old_path

            old_records = load_json(old_path, [])
            if target_path == old_path:
                old_records[idx] = record
                save_json(old_path, old_records)
            else:
                # category changed — move the record to its new file
                del old_records[idx]
                save_json(old_path, old_records)
                target_records = load_json(target_path, [])
                target_records.append(record)
                save_json(target_path, target_records)

            status = body.get("status")
            if status:
                review_status = load_json(REVIEW_STATUS_PATH, {})
                review_status[record["id"]] = {
                    "status": status,
                    "note": body.get("note", ""),
                    "updated_at": body.get("updated_at"),
                }
                save_json(REVIEW_STATUS_PATH, review_status)

            self._send_json({"ok": True, "path": os.path.relpath(target_path, ROOT)})
            return

        if route == "/api/write_image":
            image_b64 = body.get("image_base64")
            image_rel_path = body.get("image_rel_path")
            if not image_b64 or not image_rel_path:
                self._send_json({"ok": False, "error": "missing image_base64/image_rel_path"}, 400)
                return
            full_img_path = os.path.normpath(os.path.join(ROOT, image_rel_path))
            if not full_img_path.startswith(ROOT):
                self._send_json({"ok": False, "error": "invalid image path"}, 400)
                return
            os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
            with open(full_img_path, "wb") as f:
                f.write(base64.b64decode(image_b64))
            self._send_json({"ok": True})
            return

        self._send_json({"ok": False, "error": "not found"}, 404)

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {ROOT}")
    print(f"Open http://localhost:{PORT}/ for the full review dashboard,")
    print(f"or http://localhost:{PORT}/tools/review_reports.html for just student reports.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
