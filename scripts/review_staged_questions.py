"""Review questions staged by tools/add_question.html one at a time and
promote approved ones into db/categories/<category>.json.

This is the "strict approval" gate for manually-added questions: nothing
staged in db/staging/proposed_questions.json reaches the real database until
it passes validation here AND a human explicitly approves it. Rejecting a
question removes it (and, if you choose, its staged image) permanently;
skipping leaves it in staging for next time.

Usage:
    python3 scripts/review_staged_questions.py
    python3 scripts/review_staged_questions.py --dry-run   # validate only, write nothing

After approving anything, rebuild the live database:
    python3 scripts/build_db.py
    python3 scripts/build_site_data.py
    python3 scripts/build_site.py
    python3 scripts/build_github_pages.py
"""
import argparse
import glob
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING_PATH = os.path.join(ROOT, "db", "staging", "proposed_questions.json")
CATEGORIES_DIR = os.path.join(ROOT, "db", "categories")

# category -> (target file, expected domain, domain_code, expected skill, skill_code)
CATEGORY_META = {
    "Grammar": ("grammar.json", "Standard English Conventions", "SEC", {
        "Boundaries": "BOUND",
        "Form, Structure, and Sense": "FSS",
    }),
    "Vocab": ("vocab.json", "Craft and Structure", "CAS", {"Words in Context": "WIC"}),
    "Transitions": ("transitions.json", "Expression of Ideas", "EOI", {"Transitions": "TRANS"}),
    "Notes": ("notes.json", "Expression of Ideas", "EOI", {"Rhetorical Synthesis": "RSYN"}),
    "Functions": ("functions.json", "Craft and Structure", "CAS", {"Text Structure and Purpose": "TSP"}),
    "Main Idea": ("main_idea.json", "Information and Ideas", "INI", {"Central Ideas and Details": "CID"}),
    "Inferences": ("inferences.json", "Information and Ideas", "INI", {"Inferences": "INFER"}),
    "Cross-Text": ("cross-text.json", "Craft and Structure", "CAS", {"Cross-Text Connections": "XTEXT"}),
    "Textual Evidence": ("command_of_evidence.json", "Information and Ideas", "INI",
                         {"Command of Evidence (Textual)": "COE"}),
    "Quantitative Evidence": ("command_of_evidence.json", "Information and Ideas", "INI",
                              {"Command of Evidence (Quantitative)": "COE"}),
}

DIFF_MAP = {"Easy": 1, "Medium": 2, "Hard": 3}
REQUIRED_STR_FIELDS = ["id", "category", "stem", "prompt", "correct_answer", "rationale_full",
                       "domain", "domain_code", "skill", "skill_code", "difficulty_label"]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def existing_ids():
    ids = set()
    for path in glob.glob(os.path.join(CATEGORIES_DIR, "*.json")):
        for r in load_json(path, []):
            ids.add(r["id"])
    return ids


def validate(record, known_ids, staging_id_counts):
    errors = []
    for field in REQUIRED_STR_FIELDS:
        value = record.get(field)
        empty = value.strip() == "" if isinstance(value, str) else not value
        if empty:
            errors.append(f"missing/empty required field: {field}")

    category = record.get("category")
    if category not in CATEGORY_META:
        errors.append(f"unknown category: {category!r} (must be one of {sorted(CATEGORY_META)})")
    else:
        _, domain, domain_code, skills = CATEGORY_META[category]
        if record.get("domain") != domain:
            errors.append(f"domain {record.get('domain')!r} doesn't match expected {domain!r} for category {category!r}")
        if record.get("domain_code") != domain_code:
            errors.append(f"domain_code {record.get('domain_code')!r} doesn't match expected {domain_code!r}")
        skill = record.get("skill")
        if skill not in skills:
            errors.append(f"skill {skill!r} isn't valid for category {category!r} (expected one of {sorted(skills)})")
        elif record.get("skill_code") != skills[skill]:
            errors.append(f"skill_code {record.get('skill_code')!r} doesn't match expected {skills[skill]!r} for skill {skill!r}")

    diff_label = record.get("difficulty_label")
    if diff_label not in DIFF_MAP:
        errors.append(f"difficulty_label must be Easy/Medium/Hard, got {diff_label!r}")
    elif record.get("difficulty") != DIFF_MAP[diff_label]:
        errors.append(f"difficulty {record.get('difficulty')!r} doesn't match difficulty_label {diff_label!r} (expected {DIFF_MAP[diff_label]})")

    choices = record.get("choices") or {}
    if set(choices.keys()) != {"A", "B", "C", "D"}:
        errors.append(f"choices must have exactly keys A-D, got {sorted(choices.keys())}")
    else:
        texts = [v.strip() for v in choices.values()]
        if any(not t for t in texts):
            errors.append("a choice is empty")
        if len(set(texts)) != len(texts):
            errors.append("two or more choices have identical text")

    correct = record.get("correct_answer")
    if correct not in {"A", "B", "C", "D"}:
        errors.append(f"correct_answer must be A-D, got {correct!r}")

    rbc = record.get("rationale_by_choice") or {}
    for letter in "ABCD":
        if not str(rbc.get(letter, "")).strip():
            errors.append(f"rationale_by_choice is missing/empty for choice {letter}")

    if not record.get("prompt", "").strip().endswith("?"):
        errors.append("prompt doesn't end in '?'")

    qid = record.get("id")
    if not qid:
        pass  # already reported above
    elif qid in known_ids:
        errors.append(f"id {qid!r} already exists in the live database")
    elif staging_id_counts.get(qid, 0) > 1:
        errors.append(f"id {qid!r} is duplicated elsewhere in staging")

    if record.get("has_image"):
        for img in record.get("image_paths", []):
            path = img.get("path") if isinstance(img, dict) else img
            if not path or not os.path.exists(os.path.join(ROOT, path)):
                errors.append(f"has_image is true but image file is missing: {path}")
    return errors


def print_record(record, idx, total):
    print("\n" + "=" * 78)
    title = record.get("title") or "(untitled)"
    print(f"[{idx}/{total}] \"{title}\"  id={record.get('id')}  category={record.get('category')}")
    print(f"domain={record.get('domain')} ({record.get('domain_code')})  "
          f"skill={record.get('skill')} ({record.get('skill_code')})  "
          f"difficulty={record.get('difficulty_label')} ({record.get('difficulty')})")
    if record.get("tags"):
        print("tags=" + ", ".join(record["tags"]))
    print("-" * 78)
    print("STEM:\n" + str(record.get("stem", "")))
    print("\nPROMPT: " + str(record.get("prompt", "")))
    choices = record.get("choices") or {}
    correct = record.get("correct_answer")
    rationale_by_choice = record.get("rationale_by_choice") or {}
    print()
    for letter in "ABCD":
        mark = " <-- correct" if letter == correct else ""
        print(f"  {letter}. {choices.get(letter, '')}{mark}")
        rationale = rationale_by_choice.get(letter, "")
        if rationale:
            print(f"      {rationale}")
    if record.get("has_image"):
        print("\nIMAGE(S): " + ", ".join(
            (img.get("path") if isinstance(img, dict) else img) for img in record.get("image_paths", [])
        ))
    print("=" * 78)


def append_to_category(record):
    category = record["category"]
    filename, *_ = CATEGORY_META[category]
    path = os.path.join(CATEGORIES_DIR, filename)
    records = load_json(path, [])
    clean = {k: v for k, v in record.items() if not k.startswith("staged_")}
    records.append(clean)
    with open(path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Validate and print every staged question, write nothing")
    args = ap.parse_args()

    staged = load_json(STAGING_PATH, [])
    if not staged:
        print(f"No staged questions in {STAGING_PATH}.")
        return

    known_ids = existing_ids()

    approved = rejected = skipped = 0
    remaining = list(staged)
    i = 0
    total = len(staged)
    seen_count = 0
    while i < len(remaining):
        record = remaining[i]
        seen_count += 1
        print_record(record, seen_count, total)
        staging_id_counts = Counter(r["id"] for r in remaining if r.get("id"))
        errors = validate(record, known_ids, staging_id_counts)
        if errors:
            print("VALIDATION ERRORS (cannot approve until fixed):")
            for e in errors:
                print(f"  - {e}")

        if args.dry_run:
            i += 1
            continue

        prompt = "Approve (a) / Reject (r) / Skip (s) / Quit (q) [s]: "
        if errors:
            prompt = "Reject (r) / Skip (s) / Quit (q) [s]: "
        choice = input(prompt).strip().lower() or "s"

        if choice == "q":
            break
        elif choice == "a" and not errors:
            path = append_to_category(record)
            print(f"Approved -> {path}")
            known_ids.add(record["id"])
            del remaining[i]
            approved += 1
            with open(STAGING_PATH, "w") as f:
                json.dump(remaining, f, indent=2, ensure_ascii=False)
        elif choice == "r":
            if record.get("has_image"):
                del_img = input("Also delete the staged image file(s)? (y/n) [n]: ").strip().lower() == "y"
                if del_img:
                    for img in record.get("image_paths", []):
                        p = img.get("path") if isinstance(img, dict) else img
                        full = os.path.join(ROOT, p) if p else None
                        if full and os.path.exists(full):
                            os.remove(full)
                            print(f"Deleted {full}")
            print("Rejected — removed from staging.")
            del remaining[i]
            rejected += 1
            with open(STAGING_PATH, "w") as f:
                json.dump(remaining, f, indent=2, ensure_ascii=False)
        else:
            print("Skipped — left in staging.")
            skipped += 1
            i += 1

    print(f"\nDone. approved={approved} rejected={rejected} skipped={skipped} "
          f"remaining_in_staging={len(remaining)}")
    if approved and not args.dry_run:
        print("\nRebuild the live database with:")
        print("  python3 scripts/build_db.py")
        print("  python3 scripts/build_site_data.py")
        print("  python3 scripts/build_site.py")
        print("  python3 scripts/build_github_pages.py")


if __name__ == "__main__":
    main()
