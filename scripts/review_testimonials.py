"""Review Home-tab testimonials submitted via the site (db/staging/
testimonials.json, written by handleSubmitTestimonial_ in
scripts/apps-script/Code.gs) one at a time, approving into db/testimonials.json
(publicly readable live via handleListTestimonials_ — no site rebuild needed)
or rejecting for good.

Usage:
    python3 scripts/review_testimonials.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING_PATH = os.path.join(ROOT, "db", "staging", "testimonials.json")
LIVE_PATH = os.path.join(ROOT, "db", "testimonials.json")


def load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        text = f.read()
    return json.loads(text) if text.strip() else []


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    staged = load(STAGING_PATH)
    if not staged:
        print("Nothing staged.")
        return
    live = load(LIVE_PATH)
    remaining = []
    for i, t in enumerate(staged):
        print(f"\n--- {i + 1}/{len(staged)} ---")
        print(f"Rating: {'*' * int(t.get('rating', 5))} ({t.get('rating')})")
        print(f"Author: {t.get('author')}  (submitted_by: {t.get('submitted_by')}, {t.get('submitted_at')})")
        print(f"Quote: {t.get('quote')}")
        choice = input("[a]pprove / [r]eject / [s]kip / [e]dit quote / [q]uit? ").strip().lower()
        if choice == "q":
            remaining.append(t)
            remaining.extend(staged[i + 1:])
            break
        if choice == "e":
            new_quote = input("New quote text: ").strip()
            if new_quote:
                t["quote"] = new_quote
            choice = input("[a]pprove / [r]eject / [s]kip? ").strip().lower()
        if choice == "a":
            live.append({k: t[k] for k in ("rating", "quote", "author") if k in t})
            print("Approved.")
        elif choice == "r":
            print("Rejected.")
        else:
            remaining.append(t)
            print("Skipped.")

    save(STAGING_PATH, remaining)
    save(LIVE_PATH, live)
    print(f"\n{len(live)} live testimonial(s), {len(remaining)} still staged.")


if __name__ == "__main__":
    main()
