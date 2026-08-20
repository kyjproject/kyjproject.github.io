"""Parse Command of Evidence.pdf: same text pipeline as parse_category.py,
plus chart/table image extraction for questions that have one.

Roughly half of these questions reference a graph or data table rendered as
vector graphics on the page (not a raster image, so page.get_images() finds
nothing — that's why this category needed its own pass). This script:

  1. Parses question text/choices/rationale/domain/skill/difficulty exactly
     like parse_category.py (same footer-stripping, same regex pipeline).
  2. For each question's first page, filters out the template "chrome"
     (background frames, the ID/Answer navy badges, the top header row) from
     page.get_drawings(), and classifies the page as chart/table-bearing if
     enough real drawing elements remain, spanning enough vertical space.
  3. For chart/table questions, crops + renders that region to a PNG under
     images/command_of_evidence/, and strips the leading run of short
     "chart-label" lines (axis numbers, legend entries) out of the stem —
     the image is the reliable source for that content now.
"""
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(__file__))
from parse_category import (  # noqa: E402
    parse_pdf, normalize, annotate_underlines, fix_full_prompts, _is_underline_stroke,
    DOMAIN_CODES, SKILL_CODES, DIFF_MAP,
)

PDF_PATH = "/Users/kyj/Downloads/sat-database/RW/Command of Evidence.pdf"
CATEGORY = "Command of Evidence"
IMAGES_DIR = "/Users/kyj/Downloads/sat-database/images/command_of_evidence"
OUT_PATH = "/Users/kyj/Downloads/sat-database/db/categories/command_of_evidence.json"

NAVY = (0.10196078568696976, 0.14508278667926788, 0.3843137323856354)

# Manually reviewed and removed (bad/unhelpful crops, judged case by case —
# not something the chart_region() heuristic can detect on its own). Kept
# here rather than only deleting the PNG so a future re-run of this script
# doesn't just regenerate them from the PDF and silently undo the removal.
MANUAL_NO_IMAGE_IDS = {
    "7e1dd168",  # Evidence #5
    "b74860b2",  # Evidence #53
    "2ee45938",  # Evidence #62
    "1dc74ae7",  # Evidence #114
    "b864fb8e",  # Evidence #119
    "ca62f45d",  # Evidence #122
    "26a81ff6",  # Evidence #190
    "c7b268c2",  # Evidence #198
    "518cb779",  # Evidence #258
}


def is_navy(fill, tol=0.08):
    if not fill:
        return False
    return all(abs(fill[i] - NAVY[i]) < tol for i in range(3))


def filtered_drawings(page):
    page_area = page.rect.width * page.rect.height
    pw = page.rect.width
    keep = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width * r.height > 0.5 * page_area:
            continue  # full-page background frame
        if r.width > 0.8 * pw:
            continue  # full-width header divider/rule
        if r.y1 < 175:
            continue  # top header band / ID badge
        if is_navy(d.get("fill")):
            # "ID: ..." / "ID: ... Answer" navy badge — this exact navy fill
            # is never used for real chart/table content anywhere in the
            # PDF, so exclude it regardless of where it lands on the page.
            # (The old check only excluded it below y=650, which missed it
            # on pages where a table pushes the badge higher up — that badge
            # would then get treated as "real" chart content and blow the
            # crop's bounding box out to include everything below it.)
            continue
        if _is_underline_stroke(d):
            # Underlined passage/prompt text (added after this function was
            # first written) is a thin stroke just like real chart gridlines
            # — without this, an underline anywhere below the chart (very
            # common: these questions often ask about "the underlined
            # claim") drags the crop's bounding box down through the rest of
            # the passage, the choices, and the footer.
            continue
        if r.width < 3 and r.height < 3:
            # Degenerate point-sized artifact (a corner cap, effectively
            # 0x0) — real gridlines/borders/bars always have substantial
            # extent in at least one dimension. One of these sitting well
            # outside a chart's actual bounds (e.g. a stray dot below a
            # legend box) can single-handedly stretch the crop's bounding
            # box past the real content despite being visually meaningless.
            continue
        keep.append(r)
    return keep


def chart_region(page, min_elements=5, min_height=50, max_raw_height=380,
                  pad_top=110, pad_bottom=15, pad_x=100):
    # Whole-page bbox of every kept drawing (correctly bridges a chart's own
    # internal parts, e.g. axis+bars to a legend box ~80pt below — real
    # multi-part charts stay under ~300pt raw height). max_raw_height is a
    # backstop: on the rare page where a stray far-off element manages to
    # survive filtering, it caps how far the crop can stretch instead of
    # pulling in the rest of the page.
    #
    # min_elements/min_height were recalibrated after filtered_drawings()
    # started excluding underline strokes and the navy answer badge
    # (previously counted as "real" chart content, which is also what let a
    # stray underline blow the crop out — see filtered_drawings above).
    # Across all 258 questions this cleanly separates true chart/table pages
    # (7-17 elements, 71-159pt tall) from pages with no chart at all
    # (0 elements, 0pt) — these thresholds sit with margin on both sides of
    # that gap.
    #
    # pad_bottom is intentionally small: a legend box's own border is a real
    # drawing, so it's already part of `keep`'s bbox, not something padding
    # needs to reach for — and on a table page the passage often resumes
    # just a few points below the last row, so a generous pad_bottom mainly
    # ends up grabbing a few lines of unrelated passage text instead.
    #
    # pad_top/pad_x are the opposite: generous on purpose, because the only
    # thing above/beside a chart is its own title and axis-title text (a
    # multi-line title or a long rotated axis label can sit 100pt+ from the
    # nearest drawn element) or blank margin/the "ID: ..." badge — never
    # another question's content — so erring large there costs nothing but
    # a little empty space, unlike pad_bottom.
    keep = filtered_drawings(page)
    if len(keep) < min_elements:
        return None
    x0 = min(r.x0 for r in keep)
    y0 = min(r.y0 for r in keep)
    x1 = max(r.x1 for r in keep)
    y1 = max(r.y1 for r in keep)
    if (y1 - y0) < min_height:
        return None
    y1 = min(y1, y0 + max_raw_height)
    # A generous pad_top can otherwise reach up past the "ID: ..." badge
    # into the Domain/Skill/Difficulty header strip above it — real content
    # (the page's own header, not the question's), so clamp there instead
    # of letting every wide-titled chart drag it into frame too.
    HEADER_CLAMP_Y = 118
    return fitz.Rect(
        max(0, x0 - pad_x), max(HEADER_CLAMP_Y, y0 - pad_top),
        min(page.rect.width, x1 + pad_x), min(page.rect.height, y1 + pad_bottom),
    )


def find_passage_anchor(page, prompt, min_width=400, min_y=150, anchor_words=7):
    """Find where the real prose passage starts on a chart/table page, using
    the PDF's own text block layout rather than guessing from word counts.

    Every chart-internal text element (axis numbers, axis titles, legend
    entries, rotated category labels) is a narrow block, because it's sized
    to its own label, not wrapped to the page margins. The passage paragraph
    is the only thing that wraps at close to the full page text width
    (~530-580pt here) — so a sufficiently wide block is a candidate start of
    the passage, regardless of chart type (bar chart, table, scatter...).

    One wrinkle: a *table's own title* can also be wide (e.g. "Simulated
    Change in Annual Aquifer Input and Irrigation Output if Precipitation
    Concentration Increases..."), so width alone isn't quite enough there —
    but a title like that is always followed immediately by the table's
    (narrow) column headers, whereas a real passage's second wrapped line is
    also wide. So: prefer a candidate whose *next* block is wide too; only
    fall back to a lone wide block if no such pair exists.

    Returns a short anchor string to search for in the already-extracted
    stem text, or None if no candidate was found.
    """
    all_blocks = sorted(
        (b for b in page.get_text("dict")["blocks"] if b.get("lines")),
        key=lambda b: b["bbox"][1],
    )

    def block_text(b):
        # Join lines with a space (matching how `stem` itself was built via
        # clean_paragraphs) — joining with "" instead would glue words
        # across a line break and make the anchor fail to exact-match stem.
        line_texts = ["".join(sp["text"] for sp in ln["spans"]) for ln in b["lines"]]
        return normalize(" ".join(t for t in line_texts if t).strip())

    # Only search blocks above the prompt (not every question phrases it as
    # "Which choice...?" — some are "According to the graph, ...?") —
    # otherwise a candidate can come from the answer choices or rationale
    # further down the page (also wide, punctuated prose), producing an
    # anchor that will never be found in `stem` (which only covers the
    # passage/prompt region) and silently falling back to no cleaning at all.
    prompt_start = normalize(prompt)[:20]
    prompt_idx = next(
        (i for i, b in enumerate(all_blocks) if block_text(b).startswith(prompt_start)),
        len(all_blocks),
    )
    blocks = all_blocks[:prompt_idx]

    def is_wide(b):
        x0, _, x1, _ = b["bbox"]
        return (x1 - x0) >= min_width

    def looks_like_prose(text):
        # A table row or header reads as a run of short, unpunctuated
        # noun/number tokens ("Little red tree frog 1% yes no") even once
        # properly space-joined — real prose almost always hits a comma or
        # period within a ~10+ word span, and that holds regardless of
        # whether a table cell's digits happen to sit flush against a label
        # (a plain digit-letter adjacency test caught "TRAPPIST-1e",
        # "1930s" and other legitimate content as false positives — this is
        # the more robust single signal). Strip numeric punctuation first
        # (thousands separators, decimal points — "5,815.51") so a row of
        # numbers doesn't masquerade as a punctuated sentence.
        text = re.sub(r"(?<=\d)[,.]\s*(?=\d)", "", text)
        return bool(re.search(r"[,.;:]", text))

    def starts_like_sentence(text):
        # A block that begins mid-sentence — typically the text right after
        # an italicized term (a species name, a title) breaks it into its
        # own block — starts with a lowercase word ("testudinum from sites
        # on...", continuing "...the seagrass Thalassia testudinum..."). A
        # genuine sentence start is always capitalized. Find the first
        # letter (skipping a leading quote mark, dash, parenthesis, etc.)
        # and check its case.
        m = re.search(r"[A-Za-z]", text)
        return bool(m) and text[m.start()].isupper()

    def has_prose_signal(i, b):
        # A candidate qualifies if it has its own punctuation, OR — a
        # genuine sentence can be split across several PDF blocks by italic
        # terms/em dashes, with the period landing several blocks later
        # ("In a study of the evolution of DptA and DptB—...and foster" /
        # "beneficial microbes in fruit flies (Drosophila)—researchers
        # assessed..." / "by Providencia rettgeri and Acetobacter sicerae,
        # bacteria common..." — three blocks before the first comma) — walk
        # forward through consecutive same-sentence continuations (each
        # starting lowercase, confirming it's not an unrelated new block
        # like another table row's acronym or a fresh passage) and check the
        # accumulated text for punctuation. Stops as soon as a block starts
        # a new capitalized sentence: that boundary is what keeps a table's
        # last row (e.g. "VISIR (...) 2006") from borrowing the *unrelated*
        # real passage's punctuation right after it.
        text = block_text(b)
        if looks_like_prose(text):
            return True
        j = i + 1
        while j < len(blocks):
            next_text = block_text(blocks[j])
            if starts_like_sentence(next_text):
                return False
            text += " " + next_text
            if looks_like_prose(text):
                return True
            j += 1
        return False

    candidates = [
        (i, b) for i, b in enumerate(blocks)
        if b["bbox"][1] >= min_y and is_wide(b) and len(block_text(b).split()) >= 4
        and starts_like_sentence(block_text(b)) and has_prose_signal(i, b)
    ]

    # Prefer the last candidate as the fallback (not the first): a table's
    # own title/header is always the earliest wide block on the page, while
    # the real passage — when present — comes after the table. If nothing
    # pairs cleanly, the latest candidate is the better guess of the two.
    fallback = None
    for i, b in candidates:
        fallback = b
        next_b = blocks[i + 1] if i + 1 < len(blocks) else None
        if next_b is not None and is_wide(next_b) and looks_like_prose(block_text(next_b)):
            return " ".join(block_text(b).split()[:anchor_words])

    if fallback is not None:
        return " ".join(block_text(fallback).split()[:anchor_words])
    return None


def strip_chart_label_noise(stem, page, prompt, max_words=6, max_lines=25):
    """Drop the leading run of chart-junk text (axis numbers, legend entries,
    rotated category labels, wrapped chart titles) from a stem, preferring
    the page-layout-based anchor and falling back to a word-count heuristic
    only if that anchor can't be found or located in the text."""
    anchor = find_passage_anchor(page, prompt)
    if anchor:
        idx = stem.find(anchor)
        if idx > 0:
            return stem[idx:].strip()

    lines = stem.split("\n")
    i = 0
    while i < min(len(lines), max_lines):
        words = lines[i].strip().split()
        if len(words) > max_words:
            break
        i += 1
    if i == 0 or i >= len(lines):
        return stem  # nothing stripped, or stripped everything (don't do that)
    cleaned = "\n".join(lines[i:]).strip()
    return cleaned if cleaned else stem


def main():
    os.makedirs(IMAGES_DIR, exist_ok=True)
    doc = fitz.open(PDF_PATH)

    records, failures, npages = parse_pdf(PDF_PATH, CATEGORY)
    print(f"{CATEGORY}: {npages} pages -> {len(records)} questions parsed, {len(failures)} failed")
    for errors, snippet in failures[:10]:
        print(f"  FAIL {errors}: {snippet!r}")

    n_images = 0
    for rec in records:
        manual_removed = rec["id"] in MANUAL_NO_IMAGE_IDS
        if manual_removed:
            stale = os.path.join(IMAGES_DIR, f"{rec['id']}.png")
            if os.path.exists(stale):
                os.remove(stale)

        page_idx = rec["page"] - 1
        page = doc[page_idx]
        clip = chart_region(page)
        if clip is None:
            continue

        if not manual_removed:
            n_images += 1
            img_path = os.path.join(IMAGES_DIR, f"{rec['id']}.png")
            pix = page.get_pixmap(clip=clip, dpi=200)
            pix.save(img_path)

            rec["has_image"] = True
            rec["image_paths"] = [{
                "path": f"images/command_of_evidence/{rec['id']}.png",
                "position": "stem",
            }]

        # Raw text-extracted stem for a chart page is jumbled (axis numbers,
        # legend labels, wrapped title all run together before the real
        # passage prose) — strip that leading run now that the image covers
        # it (still true even for a manually-removed image — the chart/table
        # is still on the page, just not worth cropping, so the passage
        # still needs separating from that surrounding chart-label text).
        cleaned = strip_chart_label_noise(rec["stem"], page, rec["prompt"])
        rec["stem_raw"] = rec["stem"]
        rec["stem"] = cleaned

    print(f"  {n_images} questions have a chart/table image")

    # Split into two separate browsing/practice/test categories rather than
    # a sub-filter within one "Command of Evidence" bucket — matching every
    # other category in this repo (one bucket per distinct question type,
    # not per source PDF). category gets the short display name; skill keeps
    # the "Command of Evidence (...)" form, matching the naming already used
    # for this exact split in db/module_composition.json's per-module skill
    # breakdowns. Driven by has_image, set above only for real crops — the 9
    # MANUAL_NO_IMAGE_IDS pages were false-positive chart detections that are
    # actually ordinary "textual" questions, so has_image is already correct
    # for those too. Verified this matches a prompt-keyword classifier
    # (graph/data/table) on all 258 questions with zero mismatches, so
    # has_image alone is a reliable signal — no separate heuristic needed.
    # `domain` (the official College Board domain, "Information and Ideas")
    # is untouched, and stays shared by both.
    for rec in records:
        quantitative = bool(rec.get("has_image"))
        rec["category"] = "Quantitative Evidence" if quantitative else "Textual Evidence"
        rec["skill"] = f"{CATEGORY} (Quantitative)" if quantitative else f"{CATEGORY} (Textual)"
    n_quant = sum(1 for r in records if r["category"] == "Quantitative Evidence")
    print(f"  {n_quant} quantitative / {len(records) - n_quant} textual")

    # Run after chart-label stripping (not inside parse_pdf) so the anchor
    # search there operates on plain text — inserting <u> tags first could
    # shift or hide the anchor phrase it's searching for. fix_full_prompts
    # trims the opposite (trailing) end of stem, so order between it and the
    # chart-label stripping above doesn't matter; it must still run before
    # annotate_underlines since it can shift where stem ends.
    fix_full_prompts(records, doc)
    annotate_underlines(records, doc)
    n_underlined = sum(1 for r in records if "<u>" in r["stem"])
    print(f"  {n_underlined} questions have underlined passage text")

    warned = [r for r in records if "_warnings" in r]
    if warned:
        print(f"  {len(warned)} parsed with warnings:")
        for r in warned[:10]:
            print(f"    {r['id']}: {r['_warnings']}")

    ids = [r["id"] for r in records]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        print("  DUPLICATE IDS:", dupes)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    import json
    with open(OUT_PATH, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"  wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
