"""Hotkey-driven capture assistant for logging a real practice-test module as
you take it.

This does NOT touch Bluebook in any way — no simulated clicks, no reading its
window contents, no auto-advancing. You take the test entirely normally, at
your own pace, answering every question yourself. The only thing this adds is
a global hotkey: press it (default F9) whenever you're ready to log the
question currently on screen, and it silently screenshots your screen (or a
region you specify), OCRs it, and matches it against the question bank —
right then, so you get instant feedback instead of a batch job afterward.
Press the stop hotkey (default F10) or Ctrl+C when you've finished the
module.

Output is the same review.json format scripts/ocr_extract_exam.py produces,
so scripts/apply_module_review.py works unchanged on it afterward.

One-time setup on macOS: whatever app runs this (Terminal/iTerm/etc.) needs
"Screen Recording" permission (for the screenshot) and "Accessibility"
permission (for the global hotkey to be seen at all, even while Bluebook has
focus) — System Settings -> Privacy & Security. You'll get a permission
prompt the first time either is needed if you haven't granted it yet.

Usage:
    python3 scripts/live_capture_session.py --test 9 --module module_1
    python3 scripts/live_capture_session.py --test 9 --module module_1 --region 100,100,1200,800
    python3 scripts/live_capture_session.py --test 9 --module module_1 --hotkey f8 --stop-hotkey f12

Tip: --region crops the screenshot to just the Bluebook content area (find
the numbers with any screenshot tool's crosshair/selection readout) — tighter
crops mean less irrelevant on-screen text for OCR to wade through, which
usually improves match accuracy. Without it, the whole primary display is
captured.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import mss
from PIL import Image
from pynput import keyboard

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_extract_exam import load_corpus, ocr_image, normalize_text  # noqa: E402
from rapidfuzz import fuzz, process  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_key(name):
    name = name.strip().lower()
    attr = f"{name}" if len(name) > 1 else name
    key = getattr(keyboard.Key, attr, None)
    if key is not None:
        return key
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise SystemExit(f"Don't recognize key name {name!r} — try something like f9, esc, space, or a single letter.")


def parse_region(s):
    if not s:
        return None
    parts = [int(p) for p in s.split(",")]
    if len(parts) != 4:
        raise SystemExit("--region must be x,y,w,h (four integers)")
    x, y, w, h = parts
    return {"left": x, "top": y, "width": w, "height": h}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", required=True, help="Practice test number, e.g. 9 (used only to name the capture folder)")
    ap.add_argument("--module", required=True, choices=["module_1", "module_2_harder", "module_2_easier"])
    ap.add_argument("--region", help="x,y,w,h screen region to capture (default: whole primary display)")
    ap.add_argument("--hotkey", default="f9", help="Key that captures the current question (default: f9)")
    ap.add_argument("--stop-hotkey", default="f10", help="Key that ends the session (default: f10)")
    ap.add_argument("--out-dir", help="Where to save screenshots + review.json (default: captures/test<N>_<module>_<timestamp>/)")
    ap.add_argument("--threshold", type=int, default=80, help="Similarity score below which a match is flagged low-confidence")
    args = ap.parse_args()

    capture_key = parse_key(args.hotkey)
    stop_key = parse_key(args.stop_hotkey)
    region = parse_region(args.region)

    out_dir = args.out_dir or os.path.join(
        ROOT, "captures", f"test{args.test}_{args.module}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    os.makedirs(out_dir, exist_ok=True)
    review_path = os.path.join(out_dir, "review.json")

    print("Loading question bank...")
    corpus = load_corpus()
    corpus_texts = [c["match_text"] for c in corpus]

    print(f"\nCapture folder: {out_dir}")
    print(f"Take Test {args.test}, {args.module} in Bluebook normally, at your own pace.")
    print(f"  Press [{args.hotkey.upper()}] after reading each question to log it.")
    print(f"  Press [{args.stop_hotkey.upper()}] (or Ctrl+C) when the module is done.\n")

    review = []
    state = {"running": True}

    def capture_one():
        idx = len(review) + 1
        with mss.mss() as sct:
            shot = sct.grab(region or sct.monitors[1])
            img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)
        img_path = os.path.join(out_dir, f"q{idx:02d}.png")
        img.save(img_path)

        raw_text = ocr_image(img)
        norm_text = normalize_text(raw_text)

        if not norm_text:
            print(f"  [{idx:>2}] captured — EMPTY OCR RESULT, will need manual review")
            row = {"index": idx, "source": f"q{idx:02d}.png", "ocr_text_snippet": "",
                   "matched_id": None, "matched_category": None, "matched_skill": None,
                   "similarity": 0, "status": "empty"}
        else:
            _, score, best_idx = process.extractOne(norm_text, corpus_texts, scorer=fuzz.token_sort_ratio)
            best = corpus[best_idx]
            status = "matched" if score >= args.threshold else "low_confidence"
            flag = "" if status == "matched" else "  <-- LOW CONFIDENCE, check manually"
            print(f"  [{idx:>2}] captured — {score:>5.1f}%  {best['category']:<20} {best['skill']:<32}{flag}")
            row = {
                "index": idx, "source": f"q{idx:02d}.png",
                "ocr_text_snippet": raw_text.strip()[:200].replace("\n", " "),
                "matched_id": best["id"], "matched_category": best["mapped_category"],
                "matched_category_label": best["category"], "matched_skill": best["skill"],
                "matched_stem_snippet": best["stem_snippet"], "similarity": round(score, 1),
                "status": status,
            }

        review.append(row)
        with open(review_path, "w") as f:
            json.dump(review, f, indent=2, ensure_ascii=False)

    def on_press(key):
        try:
            if key == capture_key:
                capture_one()
            elif key == stop_key:
                state["running"] = False
                return False  # stop the listener
        except Exception as e:
            print(f"  Error on capture: {e}", file=sys.stderr)

    with keyboard.Listener(on_press=on_press) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            pass

    n_matched = sum(1 for r in review if r["status"] == "matched")
    n_low = sum(1 for r in review if r["status"] == "low_confidence")
    n_empty = sum(1 for r in review if r["status"] == "empty")
    print(f"\nSession ended: {len(review)} question(s) captured "
          f"({n_matched} matched, {n_low} low-confidence, {n_empty} empty).")
    print(f"Saved to {review_path}")
    print("Review it, fix anything wrong, then run:")
    print(f"  python3 scripts/apply_module_review.py {review_path} --test {args.test} --module {args.module}")


if __name__ == "__main__":
    main()
