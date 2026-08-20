"""Screenshots/PDF of a real exam module -> OCR -> fuzzy-match against the
existing question bank -> a review file you check before anything gets
written to db/module_composition.json.

Why fuzzy-match instead of classifying the OCR text directly: Bluebook
recycles questions across practice-test forms, so most questions you
screenshot from a real attempt already exist verbatim in db/all_questions.json
with a known category (and, for Command of Evidence, a known qualitative/
quantitative split via `evidence_type`). Matching just needs to find *which*
known question this is; the category comes along for free. Only genuinely
new questions (not in the bank) need a human to tag them by hand.

Usage:
    python3 scripts/ocr_extract_exam.py <images_dir_or_pdf> -o review.json
    python3 scripts/ocr_extract_exam.py <images_dir_or_pdf> -o review.json --threshold 82

Input:
    - A directory of image files (png/jpg/jpeg/webp), one screenshot per
      question, named so they sort in question order (q01.png, q02.png, ...
      or 1.png, 2.png, ... — natural-sorted either way).
    - OR a single PDF, one question per page.

Output:
    A JSON review file (see ApplyModuleReview's docstring for the format) —
    nothing is written to db/module_composition.json by this script. Review
    it (print output already flags anything below --threshold), hand-correct
    or null out any wrong "matched_category" entries, then run
    scripts/apply_module_review.py against it.
"""
import argparse
import json
import os
import re
import sys

import pymupdf
import pytesseract
from PIL import Image, ImageOps
from rapidfuzz import fuzz, process

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_QUESTIONS_PATH = os.path.join(ROOT, "db", "all_questions.json")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

# db category/skill -> this repo's module_composition.json category keys.
# Command of Evidence needs evidence_type (Textual/Quantitative) to split.
CATEGORY_MAP = {
    "Vocab": "vocab",
    "Functions": "functions",
    "Cross-Text": "cross_text",
    "Main Idea": "main_idea",
    "Inferences": "inferences",
    "Grammar": "grammar",
    "Transitions": "transitions",
    "Notes": "notes",
}


def natural_sort_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def normalize_text(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_corpus():
    with open(ALL_QUESTIONS_PATH) as f:
        questions = json.load(f)
    corpus = []
    for q in questions:
        choices_text = " ".join(q.get("choices", {}).values())
        match_text = normalize_text(f"{q['stem']} {q.get('prompt', '')} {choices_text}")
        mapped_category = CATEGORY_MAP.get(q["category"])
        if q["category"] == "Command of Evidence":
            mapped_category = (
                "command_of_evidence_quantitative"
                if q.get("evidence_type") == "Quantitative"
                else "command_of_evidence_qualitative"
            )
        corpus.append({
            "id": q["id"],
            "category": q["category"],
            "skill": q["skill"],
            "mapped_category": mapped_category,
            "match_text": match_text,
            "stem_snippet": q["stem"][:120].replace("\n", " "),
        })
    return corpus


def ocr_image(img: Image.Image) -> str:
    # Light preprocessing: grayscale + autocontrast. Screenshots from a
    # digital test are usually clean, high-contrast text — this is enough
    # for Tesseract without pulling in a full binarization pipeline.
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img)


def gather_inputs(path):
    """Returns a list of (label, PIL.Image) in question order."""
    if os.path.isdir(path):
        files = [f for f in os.listdir(path) if os.path.splitext(f)[1].lower() in IMAGE_EXTS]
        files.sort(key=natural_sort_key)
        if not files:
            sys.exit(f"No image files found in {path}")
        return [(f, Image.open(os.path.join(path, f))) for f in files]
    elif path.lower().endswith(".pdf"):
        doc = pymupdf.open(path)
        out = []
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append((f"page-{i:02d}", img))
        return out
    else:
        sys.exit(f"{path} is neither a directory of images nor a .pdf file")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Directory of question screenshots, or a single PDF (one question per page)")
    ap.add_argument("-o", "--output", default="review.json", help="Where to write the review file (default: review.json)")
    ap.add_argument("--threshold", type=int, default=80, help="Similarity score (0-100) below which a match is flagged low-confidence (default: 80)")
    args = ap.parse_args()

    print(f"Loading question bank ({ALL_QUESTIONS_PATH})...")
    corpus = load_corpus()
    corpus_texts = [c["match_text"] for c in corpus]

    print(f"Reading input from {args.input}...")
    inputs = gather_inputs(args.input)
    print(f"Found {len(inputs)} question image(s)/page(s). Running OCR + matching...\n")

    review = []
    for idx, (label, img) in enumerate(inputs, start=1):
        raw_text = ocr_image(img)
        norm_text = normalize_text(raw_text)

        if not norm_text:
            print(f"  [{idx:>2}] {label:<20} EMPTY OCR RESULT — skipped, needs manual review")
            review.append({
                "index": idx, "source": label, "ocr_text_snippet": "",
                "matched_id": None, "matched_category": None, "matched_skill": None,
                "similarity": 0, "status": "empty",
            })
            continue

        result = process.extractOne(norm_text, corpus_texts, scorer=fuzz.token_sort_ratio)
        _, score, best_idx = result
        best = corpus[best_idx]
        status = "matched" if score >= args.threshold else "low_confidence"

        flag = "" if status == "matched" else "  <-- LOW CONFIDENCE, check manually"
        print(f"  [{idx:>2}] {label:<20} {score:>5.1f}%  {best['category']:<20} {best['skill']:<32}{flag}")

        review.append({
            "index": idx,
            "source": label,
            "ocr_text_snippet": raw_text.strip()[:200].replace("\n", " "),
            "matched_id": best["id"],
            "matched_category": best["mapped_category"],
            "matched_category_label": best["category"],
            "matched_skill": best["skill"],
            "matched_stem_snippet": best["stem_snippet"],
            "similarity": round(score, 1),
            "status": status,
        })

    with open(args.output, "w") as f:
        json.dump(review, f, indent=2, ensure_ascii=False)

    n_matched = sum(1 for r in review if r["status"] == "matched")
    n_low = sum(1 for r in review if r["status"] == "low_confidence")
    n_empty = sum(1 for r in review if r["status"] == "empty")
    print(f"\n{n_matched} matched confidently, {n_low} low-confidence, {n_empty} empty — out of {len(review)} total.")
    print(f"Wrote {args.output}. Review it — especially the low-confidence/empty rows — before running apply_module_review.py.")


if __name__ == "__main__":
    main()
