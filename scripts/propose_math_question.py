"""Propose Math questions from the command line, into the exact same review
queue the site's own Propose tab writes to.

Both intake paths — a student using the "Propose" tab in the browser, and
this script — append to the same db/staging/proposed_questions.json, which
scripts/review_staged_questions.py reviews and promotes from. That shared
file (and CATEGORY_META now living there, which this script imports rather
than redefining) is what keeps the two "synchronized": nothing here reaches
the live database on its own, same guarantee the web Propose flow has.

Two ways to use it:

  Interactive, one question at a time:
      python3 scripts/propose_math_question.py

  Batch, from a JSON file (a single question object, or a list of them) —
  handy if you already generate math questions with your own scripts (OCR
  extraction, parsing, etc.) and just want to stage a bunch at once:
      python3 scripts/propose_math_question.py --from-json my_questions.json

  Batch input records only need: category, skill, difficulty_label, stem,
  prompt, is_mc, correct_answer, and (for is_mc) choices + rationale_by_choice
  (for free-response, rationale_full instead). id/domain/domain_code/
  skill_code/test/assessment/source_pdf are filled in automatically from
  category+skill — same convention as the web Propose tab. Any of those you
  do supply are left alone (and still validated).

After staging anything, review it the normal way:
    python3 scripts/review_staged_questions.py
"""
import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_staged_questions as rsq  # noqa: E402 — shares CATEGORY_META/validate with the reviewer so the two never drift apart

ROOT = rsq.ROOT
STAGING_PATH = rsq.STAGING_PATH
IMAGES_DIR = os.path.join(ROOT, "images")


def load_staging():
    return rsq.load_json(STAGING_PATH, [])


def save_staging(records):
    os.makedirs(os.path.dirname(STAGING_PATH), exist_ok=True)
    with open(STAGING_PATH, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def all_known_ids(staged):
    ids = rsq.existing_ids()
    ids.update(r["id"] for r in staged if r.get("id"))
    return ids


def random_id(known_ids):
    import secrets
    while True:
        qid = secrets.token_hex(4)
        if qid not in known_ids:
            return qid


def image_dir_for(slug):
    # Math images live one directory deeper than R&W's — see
    # images/math/{algebra,advanced_math,...} on disk, and proposeImageDir()
    # in site/index.template.html, which this mirrors for the web flow.
    return os.path.join("images", "math", slug)


def prompt(label, default=None, required=True):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if not val and required:
            print("  (required)")
            continue
        return val


def prompt_multiline(label):
    print(f"{label} (end with a blank line):")
    lines = []
    while True:
        line = input()
        if line == "" and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def prompt_choice(label, options, default=None):
    opt_str = "/".join(options)
    while True:
        val = (input(f"{label} ({opt_str})" + (f" [{default}]" if default else "") + ": ").strip() or default)
        if val in options:
            return val
        print(f"  Pick one of: {opt_str}")


def interactive_one():
    staged = load_staging()
    known_ids = all_known_ids(staged)

    categories = list(rsq.MATH_CATEGORY_META.keys())
    print("Categories: " + ", ".join(f"{i+1}) {c}" for i, c in enumerate(categories)))
    cat_idx = prompt_choice("Category #", [str(i + 1) for i in range(len(categories))])
    category = categories[int(cat_idx) - 1]
    file, domain, domain_code, skills = rsq.MATH_CATEGORY_META[category]
    skill_names = list(skills.keys())
    print("Skills: " + ", ".join(f"{i+1}) {s}" for i, s in enumerate(skill_names)))
    skill_idx = prompt_choice("Skill #", [str(i + 1) for i in range(len(skill_names))])
    skill = skill_names[int(skill_idx) - 1]

    difficulty_label = prompt_choice("Difficulty", ["Easy", "Medium", "Hard"], default="Medium")
    title = prompt("Title (for your own reference, not shown to students)")
    stem = prompt_multiline("Stem (word problem text)")
    q_prompt = prompt("Prompt (must end in '?')")
    while not q_prompt.strip().endswith("?"):
        print("  Prompt must end in '?'.")
        q_prompt = prompt("Prompt (must end in '?')")

    is_mc = prompt_choice("Multiple choice or free response", ["mc", "free"], default="mc") == "mc"

    choices = {}
    correct_answer = ""
    rationale_by_choice = {}
    rationale_full = ""
    if is_mc:
        for letter in "ABCD":
            choices[letter] = prompt(f"Choice {letter}")
        correct_answer = prompt_choice("Correct choice", ["A", "B", "C", "D"])
        for letter in "ABCD":
            rationale_by_choice[letter] = prompt(f"Rationale for {letter}")
        rationale_full = " ".join(rationale_by_choice[l] for l in "ABCD")
    else:
        correct_answer = prompt("Correct answer(s) (comma-separated if multiple accepted forms)")
        rationale_full = prompt_multiline("Rationale")

    tags_raw = prompt("Tags (comma-separated, optional)", default="", required=False)
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    has_image_raw = prompt_choice("Attach an image (diagram/graph)?", ["y", "n"], default="n")
    has_image = has_image_raw == "y"
    image_src = None
    if has_image:
        image_src = prompt("Path to local image file (PNG or SVG)")
        if not os.path.exists(image_src):
            print(f"  WARNING: {image_src} doesn't exist — staging without the image; fix image_paths later.")
            has_image = False
            image_src = None

    qid = random_id(known_ids)
    slug = file.replace(".json", "")
    record = {
        "id": qid, "title": title, "category": category, "source_pdf": "manual", "page": None,
        "assessment": "SAT", "test": "Math",
        "domain": domain, "domain_code": domain_code, "skill": skill, "skill_code": skills[skill],
        "difficulty_label": difficulty_label, "difficulty": rsq.DIFF_MAP[difficulty_label],
        "stem": stem, "prompt": q_prompt, "is_mc": is_mc,
        "choices": choices, "correct_answer": correct_answer,
        "rationale_full": rationale_full, "rationale_by_choice": rationale_by_choice,
        "tags": tags, "has_image": has_image, "image_paths": [],
        "proposed_by": os.environ.get("USER", "cli"), "proposed_at": datetime.now(timezone.utc).isoformat(),
    }

    if has_image and image_src:
        ext = "svg" if image_src.lower().endswith(".svg") else "png"
        rel_path = os.path.join(image_dir_for(slug), f"{qid}.{ext}")
        abs_path = os.path.join(ROOT, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        shutil.copyfile(image_src, abs_path)
        record["image_paths"] = [{"path": rel_path, "position": "stem"}]
        print(f"Copied image -> {rel_path}")

    errors = rsq.validate(record, rsq.existing_ids(), {r["id"]: 1 for r in staged})
    if errors:
        print("\nVALIDATION ERRORS — not staged:")
        for e in errors:
            print(f"  - {e}")
        return

    staged.append(record)
    save_staging(staged)
    print(f"\nStaged {qid!r} ({len(staged)} question(s) now pending in {STAGING_PATH}).")
    print("Review with: python3 scripts/review_staged_questions.py")


def fill_defaults(record):
    category = record.get("category")
    meta = rsq.MATH_CATEGORY_META.get(category)
    if meta:
        file, domain, domain_code, skills = meta
        record.setdefault("domain", domain)
        record.setdefault("domain_code", domain_code)
        skill = record.get("skill")
        if skill in skills:
            record.setdefault("skill_code", skills[skill])
    record.setdefault("test", "Math")
    record.setdefault("assessment", "SAT")
    record.setdefault("source_pdf", "manual")
    record.setdefault("page", None)
    record.setdefault("title", record.get("id") or "")
    record.setdefault("tags", [])
    record.setdefault("has_image", bool(record.get("image_paths")))
    record.setdefault("image_paths", [])
    record.setdefault("is_mc", bool(record.get("choices")))
    if record.get("is_mc"):
        record.setdefault("rationale_by_choice", {})
    else:
        record["choices"] = {}
        record.setdefault("rationale_by_choice", {})
    if "difficulty" not in record and record.get("difficulty_label") in rsq.DIFF_MAP:
        record["difficulty"] = rsq.DIFF_MAP[record["difficulty_label"]]
    record.setdefault("proposed_by", os.environ.get("USER", "cli"))
    record.setdefault("proposed_at", datetime.now(timezone.utc).isoformat())
    return record


def batch_from_json(path):
    with open(path) as f:
        data = json.load(f)
    records = data if isinstance(data, list) else [data]

    staged = load_staging()
    known_ids = all_known_ids(staged)
    staged_id_counts = {}
    added = 0
    for i, record in enumerate(records, 1):
        record = fill_defaults(dict(record))
        if not record.get("id"):
            record["id"] = random_id(known_ids)
        known_ids.add(record["id"])
        staged_id_counts[record["id"]] = staged_id_counts.get(record["id"], 0) + 1

        errors = rsq.validate(record, rsq.existing_ids(), staged_id_counts)
        if errors:
            print(f"[{i}/{len(records)}] SKIPPED {record.get('id')!r} — validation errors:")
            for e in errors:
                print(f"  - {e}")
            continue
        staged.append(record)
        added += 1
        print(f"[{i}/{len(records)}] staged {record['id']!r} ({record.get('category')})")

    if added:
        save_staging(staged)
    print(f"\nStaged {added}/{len(records)} question(s). {len(staged)} now pending in {STAGING_PATH}.")
    if added:
        print("Review with: python3 scripts/review_staged_questions.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", metavar="PATH", help="Stage one question (object) or many (list) from a JSON file instead of prompting interactively")
    args = ap.parse_args()

    if args.from_json:
        batch_from_json(args.from_json)
    else:
        interactive_one()


if __name__ == "__main__":
    main()
