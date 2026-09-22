"""One local HTTP server backing tools/dashboard.html — a single hub that
replaces running staging_server.py / titles_server.py / reports_server.py /
admin_dashboard_server.py separately. It serves the exact same routes those
four scripts already exposed (so the existing tools/*.html pages they backed
— review_dashboard.html, review_reports.html, review_site_feedback.html,
manage_titles.html, admin_dashboard.html — keep working unchanged, now
embedded as tabs), plus new routes for:

  - reviewing student-proposed questions (db/staging/proposed_questions.json)
    from a browser instead of the CLI-only review_staged_questions.py
  - resetting a user's password / listing usernames, by relaying to the
    Apps Script web app's admin_reset_password / admin_list_users actions
    (see scripts/apps-script/Code.gs) — this script never touches
    passwords itself, it just forwards your admin secret server-side so it
    never has to live in browser JS
  - "nudges": how many new reports / feedback items / proposed questions
    have shown up since you last opened each tab

Usage:
    python3 scripts/dashboard_server.py
    -> open http://localhost:8770/tools/dashboard.html in any browser

No third-party dependencies — stdlib http.server only. Binds to localhost
only; there's no auth because nothing outside your own machine can reach it.

Password reset / user listing need a local, untracked
db/staging/dashboard_config.json (never committed — see .gitignore):
    {"cloud_save_url": "https://script.google.com/macros/s/.../exec",
     "admin_secret": "same value as the ADMIN_SECRET Script Property"}
Every other tab works without this file.
"""
import base64
import glob
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_staged_questions as rsq  # noqa: E402  (needs sys.path tweak above)

# Same read-modify-write race guard as reports_server.py — every write below
# is load-mutate-save on a whole JSON file.
WRITE_LOCK = threading.Lock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8770

TITLES_PATH = os.path.join(ROOT, "db", "user_titles.json")
JUMPSCARE_PATH = os.path.join(ROOT, "db", "jumpscare_config.json")

REPORTS_PATH = os.path.join(ROOT, "db", "staging", "reported_questions.json")
RESOLVED_PATH = os.path.join(ROOT, "db", "staging", "resolved_reports.json")
REVIEW_STATUS_PATH = os.path.join(ROOT, "db", "staging", "review_status.json")
SITE_FEEDBACK_PATH = os.path.join(ROOT, "db", "staging", "site_feedback.json")
RESOLVED_SITE_FEEDBACK_PATH = os.path.join(ROOT, "db", "staging", "resolved_site_feedback.json")
STAGING_PATH = os.path.join(ROOT, "db", "staging", "proposed_questions.json")
CATEGORIES_DIR = os.path.join(ROOT, "db", "categories")

PROGRESS_DIR = os.path.join(ROOT, "db", "cloud_save", "progress")
ALL_QUESTIONS_PATH = os.path.join(ROOT, "db", "all_questions.json")

DASHBOARD_CONFIG_PATH = os.path.join(ROOT, "db", "staging", "dashboard_config.json")
DASHBOARD_SEEN_PATH = os.path.join(ROOT, "db", "staging", "dashboard_seen.json")

# category -> target category-file — same table as reports_server.py, needed
# so that if a report/proposed-question edit changes a question's category,
# it gets moved to the right file.
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

# Trimmed fields for /api/admin_questions — see admin_dashboard_server.py's
# original QUESTION_FIELDS comment for why (keeps the response small; the
# progress dashboard's scoring logic never needs stems/choices/rationales).
ADMIN_QUESTION_FIELDS = ("id", "category", "domain", "skill", "skill_code",
                          "difficulty_label", "test", "correct_answer")

# Nudge feeds: local staging file -> the timestamp field each of its records
# carries. Kept in one place since /api/nudges and /api/mark_seen both walk it.
NUDGE_FEEDS = {
    "reports": (REPORTS_PATH, "reported_at"),
    "site_feedback": (SITE_FEEDBACK_PATH, "submitted_at"),
    "proposed": (STAGING_PATH, "proposed_at"),
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def all_category_paths():
    return sorted(glob.glob(os.path.join(CATEGORIES_DIR, "*.json")))


def load_all_questions():
    out = []
    for path in all_category_paths():
        for r in load_json(path, []):
            out.append((path, r))
    return out


def find_question(qid):
    for path in all_category_paths():
        records = load_json(path, [])
        for i, r in enumerate(records):
            if r.get("id") == qid:
                return path, i, r
    return None


# Same 4 steps as `kyj-sat rebuild` (scripts/kyj-sat) — everything
# downstream of db/categories/*.json: the SQLite db, the aggregated
# db/all_questions.json + site/data.js, and both site builds (site/dist for
# the Claude Artifact, site/pages for GitHub Pages / the local static
# preview). Run from the dashboard so an edit shows up on a locally-served
# site without switching to a terminal.
REBUILD_SCRIPTS = ["build_db.py", "build_site_data.py", "build_site.py", "build_github_pages.py"]


def run_rebuild():
    logs = []
    for name in REBUILD_SCRIPTS:
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", name)],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
        logs.append(f"$ python3 scripts/{name}\n{proc.stdout}{proc.stderr}".rstrip())
        if proc.returncode != 0:
            return False, "\n\n".join(logs)
    return True, "\n\n".join(logs)


def load_dashboard_config():
    cfg = load_json(DASHBOARD_CONFIG_PATH, {})
    return cfg.get("cloud_save_url") or "", cfg.get("admin_secret") or ""


def call_apps_script(action, extra):
    url, secret = load_dashboard_config()
    if not url or not secret:
        return {"ok": False, "error": (
            "Not configured — create db/staging/dashboard_config.json with "
            '{"cloud_save_url": "<your Apps Script /exec URL>", '
            '"admin_secret": "<your ADMIN_SECRET Script Property>"}. '
            "This file is untracked (see .gitignore) — it's never committed."
        )}
    payload = {"action": action, "adminSecret": secret, **extra}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "text/plain;charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Couldn't reach Apps Script: {e}"}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Apps Script returned a non-JSON response"}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET ----------------

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            path = "/tools/dashboard.html"

        if path == "/api/titles":
            self._send_json({"ok": True, "titles": load_json(TITLES_PATH, {})})
            return

        if path == "/api/jumpscare":
            self._send_json({"ok": True, "config": load_json(JUMPSCARE_PATH, {"enabled": True, "users": []})})
            return

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

        if path == "/api/admin_questions":
            questions = []
            if os.path.isfile(ALL_QUESTIONS_PATH):
                with open(ALL_QUESTIONS_PATH) as f:
                    full = json.load(f)
                questions = [{k: q.get(k) for k in ADMIN_QUESTION_FIELDS} for q in full]
            self._send_json({"ok": True, "questions": questions})
            return

        if path == "/api/questions":
            questions = [r for _path, r in load_all_questions()]
            review_status = load_json(REVIEW_STATUS_PATH, {})
            reported_qids = sorted({r.get("qid") for r in load_json(REPORTS_PATH, [])})
            self._send_json({"ok": True, "questions": questions, "review_status": review_status,
                              "reported_qids": reported_qids})
            return

        if path == "/api/reports":
            def join_question(r):
                found = find_question(r.get("qid"))
                return {**r, "question": found[2] if found else None}
            reports = [join_question(r) for r in load_json(REPORTS_PATH, [])]
            # Resolved reports need the same join — otherwise every resolved
            # report shows "question not found" in tools/review_reports.html
            # regardless of whether it's actually still in db/categories,
            # since that page renders straight off r.question.
            resolved = [join_question(r) for r in load_json(RESOLVED_PATH, [])]
            self._send_json({"ok": True, "reports": reports, "resolved": resolved})
            return

        if path == "/api/site_feedback":
            self._send_json({
                "ok": True,
                "feedback": load_json(SITE_FEEDBACK_PATH, []),
                "resolved": load_json(RESOLVED_SITE_FEEDBACK_PATH, []),
            })
            return

        if path == "/api/proposed":
            staged = load_json(STAGING_PATH, [])
            known_ids = rsq.existing_ids()
            staging_id_counts = Counter(r.get("id") for r in staged if r.get("id"))
            out = [{"record": r, "errors": rsq.validate(r, known_ids, staging_id_counts)} for r in staged]
            self._send_json({"ok": True, "proposed": out})
            return

        if path == "/api/nudges":
            seen = load_json(DASHBOARD_SEEN_PATH, {})
            counts = {}
            for section, (fpath, time_field) in NUDGE_FEEDS.items():
                items = load_json(fpath, [])
                last_seen = seen.get(section, "")
                counts[section] = {
                    "new": sum(1 for it in items if str(it.get(time_field) or "") > last_seen),
                    "open": len(items),
                }
            counts["total_new"] = sum(c["new"] for c in counts.values() if isinstance(c, dict))
            self._send_json({"ok": True, "nudges": counts})
            return

        if path == "/api/dashboard_config_status":
            url, secret = load_dashboard_config()
            self._send_json({"ok": True, "configured": bool(url and secret)})
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
        # These pages get edited while the server is running — never serve a
        # stale cached copy after a file changes on disk.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------- POST ----------------

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        with WRITE_LOCK:
            self._do_POST_locked()

    def _do_POST_locked(self):
        route = urlparse(self.path).path
        body = self._read_body()

        if route == "/api/titles":
            titles = body.get("titles")
            if not isinstance(titles, dict):
                self._send_json({"ok": False, "error": "missing/invalid 'titles' object"}, 400)
                return
            save_json(TITLES_PATH, titles)
            self._send_json({"ok": True, "count": len(titles)})
            return

        if route == "/api/jumpscare":
            config = body.get("config")
            if not isinstance(config, dict) or not isinstance(config.get("users"), list):
                self._send_json({"ok": False, "error": "missing/invalid 'config' object (needs a 'users' list)"}, 400)
                return
            clean = {
                "enabled": bool(config.get("enabled", True)),
                "users": sorted({str(u).strip().lower() for u in config["users"] if str(u).strip()}),
            }
            save_json(JUMPSCARE_PATH, clean)
            self._send_json({"ok": True, "config": clean})
            return

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

        if route == "/api/resolve_site_feedback":
            item = body.get("item")
            if not item or not item.get("submitted_at"):
                self._send_json({"ok": False, "error": "missing item"}, 400)
                return
            feedback = load_json(SITE_FEEDBACK_PATH, [])
            remaining = [f for f in feedback if f.get("submitted_at") != item["submitted_at"]]
            if len(remaining) == len(feedback):
                self._send_json({"ok": False, "error": "item not found (already handled?)"}, 404)
                return
            save_json(SITE_FEEDBACK_PATH, remaining)
            resolved = load_json(RESOLVED_SITE_FEEDBACK_PATH, [])
            resolved.append({**item, "resolved_at": body.get("resolved_at")})
            save_json(RESOLVED_SITE_FEEDBACK_PATH, resolved)
            self._send_json({"ok": True, "remaining": len(remaining)})
            return

        if route == "/api/dismiss_site_feedback":
            item = body.get("item")
            if not item or not item.get("submitted_at"):
                self._send_json({"ok": False, "error": "missing item"}, 400)
                return
            feedback = load_json(SITE_FEEDBACK_PATH, [])
            remaining = [f for f in feedback if f.get("submitted_at") != item["submitted_at"]]
            if len(remaining) == len(feedback):
                self._send_json({"ok": False, "error": "item not found (already handled?)"}, 404)
                return
            save_json(SITE_FEEDBACK_PATH, remaining)
            self._send_json({"ok": True, "remaining": len(remaining)})
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

        if route == "/api/proposed/approve":
            result = self._approve_proposed(body)
            self._send_json(result, 200 if result.get("ok") else 400)
            return

        if route == "/api/proposed/reject":
            result = self._reject_proposed(body)
            self._send_json(result, 200 if result.get("ok") else 404)
            return

        if route == "/api/mark_seen":
            section = body.get("section")
            sections = list(NUDGE_FEEDS) if section in (None, "all") else [section]
            if any(s not in NUDGE_FEEDS for s in sections):
                self._send_json({"ok": False, "error": "unknown section"}, 400)
                return
            seen = load_json(DASHBOARD_SEEN_PATH, {})
            stamp = now_iso()
            for s in sections:
                seen[s] = stamp
            save_json(DASHBOARD_SEEN_PATH, seen)
            self._send_json({"ok": True, "seen": seen})
            return

        if route == "/api/reset_password":
            username = body.get("username")
            new_password = body.get("new_password")
            if not username or not new_password:
                self._send_json({"ok": False, "error": "missing username/new_password"}, 400)
                return
            result = call_apps_script("admin_reset_password", {
                "username": username, "newPassword": new_password,
            })
            self._send_json(result, 200 if result.get("ok") else 400)
            return

        if route == "/api/admin_users":
            result = call_apps_script("admin_list_users", {})
            self._send_json(result, 200 if result.get("ok") else 400)
            return

        if route == "/api/rebuild":
            try:
                ok, log = run_rebuild()
            except subprocess.TimeoutExpired as e:
                ok, log = False, f"Timed out after {e.timeout}s running {e.cmd}"
            self._send_json({"ok": ok, "log": log}, 200 if ok else 500)
            return

        self._send_json({"ok": False, "error": "not found"}, 404)

    # ---------------- proposed-question review helpers ----------------

    def _find_proposed(self, staged, qid, proposed_at):
        for i, r in enumerate(staged):
            if r.get("id") == qid and r.get("proposed_at") == proposed_at:
                return i
        return -1

    def _approve_proposed(self, body):
        qid, proposed_at = body.get("id"), body.get("proposed_at")
        if not qid or not proposed_at:
            return {"ok": False, "error": "missing id/proposed_at"}
        staged = load_json(STAGING_PATH, [])
        idx = self._find_proposed(staged, qid, proposed_at)
        if idx == -1:
            return {"ok": False, "error": "not found (already handled?)"}
        record = staged[idx]

        known_ids = rsq.existing_ids()
        staging_id_counts = Counter(r.get("id") for r in staged if r.get("id"))
        errors = rsq.validate(record, known_ids, staging_id_counts)
        if errors:
            return {"ok": False, "error": "Cannot approve — fix these first: " + "; ".join(errors)}

        clean = {k: v for k, v in record.items()
                 if not k.startswith("staged_") and k not in ("proposed_by", "proposed_at")}
        path = rsq.append_to_category(clean)

        del staged[idx]
        save_json(STAGING_PATH, staged)
        return {"ok": True, "path": os.path.relpath(path, ROOT)}

    def _reject_proposed(self, body):
        qid, proposed_at = body.get("id"), body.get("proposed_at")
        if not qid or not proposed_at:
            return {"ok": False, "error": "missing id/proposed_at"}
        staged = load_json(STAGING_PATH, [])
        idx = self._find_proposed(staged, qid, proposed_at)
        if idx == -1:
            return {"ok": False, "error": "not found (already handled?)"}
        record = staged[idx]

        if body.get("delete_image") and record.get("has_image"):
            for img in record.get("image_paths", []):
                p = img.get("path") if isinstance(img, dict) else img
                full = os.path.join(ROOT, p) if p else None
                if full and os.path.isfile(full):
                    os.remove(full)

        del staged[idx]
        save_json(STAGING_PATH, staged)
        return {"ok": True}

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {ROOT}")
    print(f"Open http://localhost:{PORT}/tools/dashboard.html in any browser.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
