"""Take a review.json produced (and hand-checked) after
scripts/ocr_extract_exam.py, tally its matched categories, and write the
result into db/module_composition.json for one test/module.

This is the second, explicit "I've checked this" step — ocr_extract_exam.py
never touches the database itself. Before running this, open review.json and
fix anything wrong: change a bad `matched_category` to the correct key (see
db/module_composition.json's `categories` list), or set it to null to leave
that question out of the tally entirely (e.g. if OCR/matching failed and you
don't want to hand-classify it right now).

Usage:
    python3 scripts/apply_module_review.py review.json --test 9 --module module_1
    python3 scripts/apply_module_review.py review.json --test 9 --module module_1 --verified

By default the written entry is confidence="reconstructed" with a note that
it came from OCR + your review — matching an existing question in the bank
is about as reliable as re-reading it yourself, but it's still a machine step
you didn't personally re-verify line by line. Pass --verified once you've
actually eyeballed every row against what you saw on screen.
"""
import argparse
import json
import os
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSITION_PATH = os.path.join(ROOT, "db", "module_composition.json")

VALID_MODULES = {"module_1", "module_2_harder", "module_2_easier"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_file", help="Path to a review.json from ocr_extract_exam.py")
    ap.add_argument("--test", required=True, help="Practice test number, e.g. 9")
    ap.add_argument("--module", required=True, choices=sorted(VALID_MODULES))
    ap.add_argument("--verified", action="store_true", help="Mark the resulting entry confidence=verified instead of reconstructed")
    ap.add_argument("--dry-run", action="store_true", help="Print the resulting tally but don't write the file")
    args = ap.parse_args()

    with open(args.review_file) as f:
        review = json.load(f)

    with open(COMPOSITION_PATH) as f:
        db = json.load(f)

    categories = db["categories"]
    counts = Counter()
    unresolved = []
    for row in review:
        cat = row.get("matched_category")
        if cat is None:
            unresolved.append(row)
            continue
        if cat not in categories:
            raise SystemExit(f"Row {row['index']} ({row['source']}) has an unknown matched_category: {cat!r}. "
                              f"Must be one of {categories} or null.")
        counts[cat] += 1

    total = sum(counts.values())
    print(f"Tally from {len(review)} rows ({len(unresolved)} excluded — null matched_category):")
    for cat in categories:
        print(f"  {cat:<36} {counts.get(cat, 0)}")
    print(f"  {'TOTAL':<36} {total}")

    if unresolved:
        print(f"\n{len(unresolved)} row(s) excluded from the tally (null matched_category) — these need manual")
        print("classification if you want a complete module composition:")
        for row in unresolved:
            print(f"  [{row['index']:>2}] {row['source']}: \"{row.get('ocr_text_snippet', '')[:80]}\"")

    if total != 27:
        print(f"\nWARNING: total is {total}, not 27. Either some rows are still unresolved (see above), "
              "or a matched_category is being double-counted / missing. Fix review.json before applying, "
              "or proceed anyway if you know why (e.g. you're only importing a partial module).")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    confidence = "verified" if args.verified else "reconstructed"
    entry = {
        "confidence": confidence,
        "source": "OCR + fuzzy-match against local question bank (scripts/ocr_extract_exam.py), "
                   + ("manually reviewed row-by-row" if args.verified else "reviewed at the review.json stage"),
        "source_url": None,
        "counts": {cat: counts.get(cat, 0) for cat in categories},
        "total": total,
        "flags": [] if not unresolved else [
            f"{len(unresolved)} question(s) had no confident match in the bank and were left out of this "
            f"tally (row indices: {[r['index'] for r in unresolved]}) — total is {total}, not 27, until "
            f"those are classified by hand and re-applied."
        ],
    }

    if args.test not in db["tests"]:
        db["tests"][args.test] = {m: None for m in VALID_MODULES}
    db["tests"][args.test][args.module] = entry

    with open(COMPOSITION_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nWrote Test {args.test} {args.module} to {COMPOSITION_PATH} "
          f"(confidence={confidence}) — {date.today().isoformat()}.")


if __name__ == "__main__":
    main()
