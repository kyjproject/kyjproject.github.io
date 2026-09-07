"""Parse a College Board Math question-bank PDF (Algebra, Advanced Math,
Geometry and Trigonometry, Problem-Solving and Data Analysis).

Math PDFs render every number/variable/expression as vector paths (little
drawn glyphs), not as extractable text and not as raster images — page.
get_text() returns a blank wherever a number or algebraic expression should
be (page.get_images() finds nothing either). This is the same "chart is
vector graphics, not a picture" situation parse_command_of_evidence.py
already deals with for R&W's Quantitative Evidence charts, just far more
pervasive here: nearly every question has several of these gaps, inline
within otherwise-ordinary sentences, in the answer choices, and in the
rationale.

Two-pass approach:

  1. Plain-text structural pass (parse_question_block_math, closely mirrors
     parse_category.parse_question_block): splits the PDF's plain
     page.get_text() on "Question ID <id>" boundaries and regexes out
     id/domain/skill/difficulty/correct_answer/rationale exactly like the
     R&W parser — none of that is math, so plain text extraction is exact.
     Also detects multiple-choice vs. student-produced-response (SPR/
     grid-in: no A-D choices, a typed numeric answer) questions.

  2. Geometric image pass (build_region / extract_question_images): revisits
     the question's own page(s) via page.get_text("rawdict") to find the
     same structural anchors (the "A. "/"B. "/"C. "/"D. " choice-letter
     lines, the "ID: <id> Answer" line, "Rationale") by their pixel
     position this time, which turns each field (stem / each choice /
     rationale) into a bounded rectangle on the page. Within that rectangle:
       a. Inline gaps *within* one text line (a missing variable between two
          words, e.g. "of <blank> and") are found via a purely positional
          signal: PyMuPDF lays consecutive real glyphs edge-to-edge with
          ~0pt between them, so any pair of consecutive characters on the
          same line with a real gap between them (`next.x0 - prev.x1 >
          GAP_EPS`) is a hole — regardless of font, justification, or
          subject, since it needs no font-specific space-width guess.
       b. Whatever vector drawings remain in the rectangle after (a) has
          claimed its slice (a whole answer choice that's pure math with no
          surrounding words, a freestanding graph/table/figure) are
          clustered by proximity into one or more standalone images.
     Each hole/cluster is cropped straight to its own small PNG (named
     "<question id>_<n>.png", since at this point the record's id is
     already known — no separate rename pass needed) and spliced into that
     field's text as a "⟦IMG:relative/path.png⟧" token, which
     build_site_data.py/the site turn into an inline <img>.

  Bounding every image search to its own field's rectangle (not just
  "cluster whatever's near each other on the page") is what keeps 4
  side-by-side answer-choice tables from merging into one crop — they can
  sit as little as ~8pt apart, well inside any clustering distance that
  still needs to bridge a single table's own internal gridlines.
"""
import bisect
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(__file__))
from parse_category import normalize, collapse_ws  # noqa: E402

REPO_ROOT = "/Users/kyj/Documents/kyj-sat"

DOMAIN_CODES = {
    "Algebra": "ALG",
    "Advanced Math": "ADVM",
    "Geometry and Trigonometry": "GEOT",
    "Problem-Solving and Data Analysis": "PSDA",
}

SKILL_CODES = {
    "Linear equations in one variable": "LEQ1",
    "Linear equations in two variables": "LEQ2",
    "Linear functions": "LFUNC",
    "Linear inequalities in one or two variables": "LINEQ",
    "Systems of two linear equations in two variables": "SYS2",
    "Equivalent expressions": "EQUIV",
    "Nonlinear equations in one variable and systems of equations in two variables": "NLEQ",
    "Nonlinear functions": "NLFUNC",
    "Area and volume": "AREAVOL",
    "Circles": "CIRC",
    "Lines, angles, and triangles": "LAT",
    "Right triangles and trigonometry": "RTRIG",
    "Evaluating statistical claims: Observational studies and experiments": "STATCLAIM",
    "Inference from sample statistics and margin of error": "INFERSTAT",
    "One-variable data: Distributions and measures of center and spread": "ONEVAR",
    "Percentages": "PCT",
    "Probability and conditional probability": "PROB",
    "Ratios, rates, proportional relationships, and units": "RATES",
    "Two-variable data: Models and scatterplots": "TWOVAR",
}

DIFF_MAP = {"Easy": 1, "Medium": 2, "Hard": 3}

FOOTER_RE = re.compile(
    r"Assessment\nSAT\nTest\nMath\nDomain\n(.*?)\nSkill\n(.*?)\nDifficulty\n(?:\d{1,4}\n)*",
    re.S,
)
QDIFF_RE = re.compile(r"Question Difficulty:\s*(\w+)\n?")

NAVY = (0.10196078568696976, 0.14508278667926788, 0.3843137323856354)
GAP_EPS = 0.9          # pt; real inter-glyph gaps are ~0, math holes are several pt+
HEADER_CLAMP_Y = 118   # the per-page Domain/Skill/Difficulty info box sits above this
CLUSTER_MARGIN = 5     # pt; must bridge one figure's own internal gaps but never the
                        # ~8pt gap between two side-by-side answer-choice tables
CONTENT_X0, CONTENT_X1 = 8, 604


# --- Pass 1: plain-text structure (id/domain/skill/difficulty/answer) ------

def parse_question_block_math(block, category, source_pdf):
    errors = []
    m_id = re.match(r"Question ID (\S+)\nID: (\S+)\n", block)
    if not m_id:
        errors.append("no id header")
        return None, errors
    qid = m_id.group(2)
    rest = block[m_id.end():]

    found = {"domain": None, "skill": None}

    def footer_sub(m):
        if found["domain"] is None:
            found["domain"] = collapse_ws(m.group(1))
            found["skill"] = collapse_ws(m.group(2))
        return ""

    rest = FOOTER_RE.sub(footer_sub, rest)

    diff = {"label": None}

    def qdiff_sub(m):
        if diff["label"] is None:
            diff["label"] = m.group(1)
        return ""

    rest = QDIFF_RE.sub(qdiff_sub, rest)

    ans_hdr_m = re.search(r"\nID: " + re.escape(qid) + r" Answer\n", rest)
    if not ans_hdr_m:
        errors.append("no answer header")
        return None, errors
    pre_answer = rest[: ans_hdr_m.start()]
    after_ans_hdr = rest[ans_hdr_m.end():]

    choice_matches = list(re.finditer(r"(?m)^([A-D])\.[ \t]*", pre_answer))
    is_mc = len(choice_matches) > 0
    choices = {}
    stem_raw = pre_answer
    if is_mc:
        stem_raw = pre_answer[: choice_matches[0].start()]
        for idx, cm in enumerate(choice_matches):
            letter = cm.group(1)
            start = cm.end()
            end = choice_matches[idx + 1].start() if idx + 1 < len(choice_matches) else len(pre_answer)
            choices[letter] = collapse_ws(pre_answer[start:end])
        if len(choices) != 4:
            errors.append(f"expected 4 choices got {len(choices)}")

    if is_mc:
        correct_m = re.search(r"Correct Answer:\s*([A-D])\n", after_ans_hdr)
    else:
        correct_m = re.search(r"Correct Answer:\s*(.+?)\n", after_ans_hdr)
    if not correct_m:
        errors.append("no correct answer")
        return None, errors
    correct = correct_m.group(1).strip()
    after_correct = after_ans_hdr[correct_m.end():]

    rationale_m = re.search(r"Rationale\n(.*)$", after_correct, re.S)
    if not rationale_m:
        errors.append("no rationale")
        return None, errors
    rationale_raw = rationale_m.group(1)
    rationale_raw = FOOTER_RE.sub(footer_sub, rationale_raw)
    rationale_raw = QDIFF_RE.sub(qdiff_sub, rationale_raw)

    if found["domain"] is None or found["skill"] is None:
        errors.append("no domain/skill footer")
    if diff["label"] is None:
        errors.append("no difficulty")

    record = {
        "id": qid,
        "category": category,
        "source_pdf": source_pdf,
        "assessment": "SAT",
        "test": "Math",
        "domain": found["domain"] or "",
        "domain_code": DOMAIN_CODES.get(found["domain"] or "", ""),
        "skill": found["skill"] or "",
        "skill_code": SKILL_CODES.get(found["skill"] or "", ""),
        "difficulty_label": diff["label"] or "",
        "difficulty": DIFF_MAP.get(diff["label"]),
        "tags": [],
        "stem_plain": normalize(collapse_ws(stem_raw)),
        "prompt": "",
        "is_mc": is_mc,
        "choices_plain": choices,
        "correct_answer": correct,
        "rationale_plain": normalize(collapse_ws(rationale_raw)),
        "has_image": False,
        "image_paths": [],
    }
    return record, errors


def parse_pdf_structure(pdf_path, category):
    doc = fitz.open(pdf_path)
    page_texts = [normalize(page.get_text()) for page in doc]
    full_text = "".join(page_texts)

    page_offsets = [0]
    for t in page_texts:
        page_offsets.append(page_offsets[-1] + len(t))

    def page_for_offset(off):
        return bisect.bisect_right(page_offsets, off) - 1 + 1  # 1-indexed

    starts = [m.start() for m in re.finditer(r"Question ID \S+\nID: \S+\n", full_text)]
    starts.append(len(full_text))
    blocks = [full_text[starts[i]: starts[i + 1]] for i in range(len(starts) - 1)]
    block_start_pages = [page_for_offset(starts[i]) for i in range(len(starts) - 1)]
    block_end_pages = [page_for_offset(max(starts[i + 1] - 1, starts[i])) for i in range(len(starts) - 1)]

    source_pdf = os.path.basename(pdf_path)
    records, failures = [], []
    for b, p0, p1 in zip(blocks, block_start_pages, block_end_pages):
        rec, errors = parse_question_block_math(b, category, source_pdf)
        if rec is None:
            failures.append((errors, b[:200]))
            continue
        rec["page"] = p0
        rec["end_page"] = p1
        if errors:
            rec["_warnings"] = errors
        records.append(rec)
    return records, failures, len(doc)


# --- Pass 2: geometric image extraction ------------------------------------

def is_navy(fill, tol=0.08):
    if not fill:
        return False
    return all(abs(fill[i] - NAVY[i]) < tol for i in range(3))


def filtered_drawings(page):
    pw, ph = page.rect.width, page.rect.height
    keep = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width * r.height > 0.3 * pw * ph:
            continue  # background fill
        if r.width > 0.8 * pw:
            continue  # full-width header/divider rule
        if r.y1 < HEADER_CLAMP_Y:
            continue  # per-page Domain/Skill/Difficulty info box
        if is_navy(d.get("fill")):
            continue  # "ID: ..." / "ID: ... Answer" navy badge
        if r.width < 1 and r.height < 1:
            continue  # degenerate point artifact
        keep.append(r)
    return keep


_page_line_cache = {}


def get_page_lines(page):
    key = page.number
    if key in _page_line_cache:
        return _page_line_cache[key]
    rd = page.get_text("rawdict")
    raw_lines = []
    for b in rd["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            chars = []
            for s in l["spans"]:
                for c in s["chars"]:
                    chars.append((c["c"], c["bbox"]))
            if not chars:
                continue
            if chars[-1][1][3] < HEADER_CLAMP_Y:
                continue
            raw_lines.append(chars)

    # A math gap wide enough can make PyMuPDF split one visual text row into
    # several separate "line" dicts at the same y (rather than one line with
    # a gap between spans, as usually happens) — e.g. "for up to <blank>
    # days is <blank> for the first day..." comes back as 4 distinct lines
    # all sharing the same top y. Merge any such same-row entries back into
    # one line (by rounded y0) before gap detection, otherwise the "hole
    # between two chars on one line" signal never fires and a plain inline
    # number gets mis-treated as a freestanding figure instead.
    groups = {}
    for idx, chars in enumerate(raw_lines):
        text = "".join(c for c, _ in chars)
        if text.startswith("ID:") or text.startswith("Question ID"):
            # Structural anchor lines are always complete on their own — on
            # a very short/compact question, an "ID: <id>" line and the far
            # later "ID: <id> Answer" line can coincidentally land at the
            # very same y0 (e.g. a one-line "What is X% of Y?" question);
            # merging them would glue two distinct anchors into one string
            # that matches neither regex correctly. Key by index instead of
            # y0 so they're never merged into anything else.
            groups[f"anchor{idx}"] = list(chars)
            continue
        y0 = round(chars[0][1][1], 0)
        groups.setdefault(y0, []).extend(chars)

    lines = []
    for y0, chars in groups.items():
        chars.sort(key=lambda c: c[1][0])
        top = min(c[1][1] for c in chars)
        bottom = max(c[1][3] for c in chars)
        left = min(c[1][0] for c in chars)
        right = max(c[1][2] for c in chars)
        lines.append({"bbox": (left, top, right, bottom), "chars": chars})
    lines.sort(key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
    _page_line_cache[key] = lines
    return lines


def line_text(line):
    return "".join(c for c, _ in line["chars"])


def find_anchor_y(lines, predicate, default=None):
    for l in lines:
        if predicate(l):
            return l["bbox"][1]
    return default


def rects_touch(a, b, margin):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 + margin < bx0 or bx1 + margin < ax0 or ay1 + margin < by0 or by1 + margin < ay0)


def cluster_rects(rects, margin):
    items = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out, used = [], [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            cur = items[i][:]
            used[i] = True
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                if rects_touch(cur, items[j], margin):
                    b = items[j]
                    cur = [min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])]
                    used[j] = True
                    changed = True
            out.append(cur)
        items = out
    return items


class ImageCounter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def build_region_multi(doc, spans, qid, img_dir, img_rel_prefix, counter, x0=CONTENT_X0, x1=CONTENT_X1):
    """Like build_region, but over a list of (page_num_1indexed, y0, y1)
    spans possibly crossing several physical pages (e.g. 4 answer-choice
    graphs too tall to all fit on one page). Items from later spans simply
    follow items from earlier ones — no cross-page y-sort needed since the
    spans are already given in reading order."""
    items, had_any = [], False
    for page_num, y0, y1 in spans:
        page = doc[page_num - 1]
        page_items, had_img = build_region(page, y0, y1, qid, img_dir, img_rel_prefix, counter, x0, x1)
        items.extend(page_items)
        had_any = had_any or had_img
    return items, had_any


def build_region(page, y0, y1, qid, img_dir, img_rel_prefix, counter, x0=CONTENT_X0, x1=CONTENT_X1):
    """Extract the text (with inline "⟦IMG:...⟧" tokens) inside the
    rectangle [x0,y0,x1,y1] on `page`, cropping any math content found into
    its own PNG under img_dir. Returns (items, region_had_drawings) where
    items is a list of {"type": "text"/"img", "text"/"path": ..., "y": ...}
    in top-to-bottom order.
    """
    all_lines = [l for l in get_page_lines(page) if y0 - 0.5 <= l["bbox"][1] < y1]
    drawings = [
        d for d in filtered_drawings(page)
        if d.y0 >= y0 - 1 and d.y1 <= y1 + 1 and d.x0 >= x0 - 1 and d.x1 <= x1 + 1
    ]
    consumed = []
    items = []
    trailing_leading_found = [False]

    for line in all_lines:
        chars = line["chars"]
        ly0, ly1 = line["bbox"][1], line["bbox"][3]
        pieces = []
        cur = ""
        for i, (ch, (ax0, ay0, ax1, ay1)) in enumerate(chars):
            cur += ch
            if i + 1 < len(chars):
                nx0 = chars[i + 1][1][0]
                gap = nx0 - ax1
                if gap > GAP_EPS:
                    pieces.append(("text", cur))
                    cur = ""
                    pad_x, pad_y = 1.0, 3.0
                    clip = fitz.Rect(ax1 - pad_x, ly0 - pad_y, nx0 + pad_x, ly1 + pad_y)
                    consumed.append([clip.x0, clip.y0, clip.x1, clip.y1])
                    n = counter.next()
                    fname = f"{qid}_{n}.png"
                    pix = page.get_pixmap(clip=clip, dpi=400)
                    pix.save(os.path.join(img_dir, fname))
                    pieces.append(("img", f"{img_rel_prefix}/{fname}"))
        if cur:
            pieces.append(("text", cur))
        items.append({"type": "line", "y": ly0, "x0": chars[0][1][0], "pieces": pieces, "chars": chars})

    def is_consumed(r):
        for c in consumed:
            if r.x0 >= c[0] - 1 and r.y0 >= c[1] - 1 and r.x1 <= c[2] + 1 and r.y1 <= c[3] + 1:
                return True
        return False

    MAX_INLINE_W, MAX_INLINE_H = 80, 40
    # Precompute region-wide clusters of everything not yet consumed, so a
    # trailing/leading candidate that's actually just one small-looking edge
    # of a much bigger figure (e.g. a table's "x"/"y" header cell, sitting
    # right next to the "A. " marker while the rest of the table extends far
    # below it) can be recognized as part of that bigger cluster and left
    # alone here — otherwise it gets cropped twice: once as a tiny "trailing"
    # fragment, and again as part of the full table via leftover clustering
    # below.
    big_cluster_boxes = [
        c for c in cluster_rects([[d.x0, d.y0, d.x1, d.y1] for d in drawings if not is_consumed(d)], CLUSTER_MARGIN)
        if (c[2] - c[0]) > MAX_INLINE_W or (c[3] - c[1]) > MAX_INLINE_H
    ]

    def in_big_cluster(c, pad=1):
        return any(
            c[0] >= g[0] - pad and c[1] >= g[1] - pad and c[2] <= g[2] + pad and c[3] <= g[3] + pad
            for g in big_cluster_boxes
        )

    # A hole at the very start/end of a wrapped line (e.g. "...ends in a
    # draw and <blank>" where the sentence wraps right after the missing
    # value) has no *next*/*previous* character on the same line to pair
    # against, so the loop above never finds it — it only catches gaps
    # *between* two characters. Its drawing still sits within this line's
    # own vertical band, just past the last (or before the first) char
    # horizontally, so a second, targeted pass catches it here — before
    # generic leftover clustering below would otherwise strand it as its
    # own out-of-place paragraph in the middle of a sentence.
    for it in items:
        chars = it.pop("chars")
        if not chars:
            continue
        ly0, ly1 = min(c[1][1] for c in chars), max(c[1][3] for c in chars)
        first_x0, last_x1 = chars[0][1][0], chars[-1][1][2]
        band_h = ly1 - ly0

        def overlaps_band(d, ly0=ly0, ly1=ly1, band_h=band_h):
            ov = min(d.y1, ly1) - max(d.y0, ly0)
            return ov > 0.5 * min(band_h, d.y1 - d.y0)

        # Cluster candidates *before* cropping — a multi-part glyph (a digit
        # made of separate strokes, a whole equation) must stay one crop.
        # Only a small resulting cluster (a single trailing/leading value
        # completing the sentence, e.g. the wrap-boundary "...and <blank>
        # points for...") is treated as inline; a large one (an entire
        # answer choice that's pure math, with only a bare "A. " marker as
        # its "line") is left for the generic leftover-clustering pass below
        # instead, since it's a standalone figure, not a sentence fragment.
        def small_clusters(cands):
            boxes = [[d.x0, d.y0, d.x1, d.y1] for d in cands]
            out = []
            for c in cluster_rects(boxes, CLUSTER_MARGIN):
                if (c[2] - c[0]) <= MAX_INLINE_W and (c[3] - c[1]) <= MAX_INLINE_H and not in_big_cluster(c):
                    out.append(c)
            return out

        trailing_cands = [d for d in drawings if not is_consumed(d) and d.x0 >= last_x1 - 1 and overlaps_band(d)]
        leading_cands = [d for d in drawings if not is_consumed(d) and d.x1 <= first_x0 + 1 and overlaps_band(d)]
        for c in sorted(small_clusters(leading_cands), key=lambda c: c[0]):
            pad = 2
            clip = fitz.Rect(c[0] - pad, ly0 - pad, c[2] + pad, ly1 + pad)
            consumed.append([clip.x0, clip.y0, clip.x1, clip.y1])
            n = counter.next()
            fname = f"{qid}_{n}.png"
            page.get_pixmap(clip=clip, dpi=400).save(os.path.join(img_dir, fname))
            it["pieces"].insert(0, ("img", f"{img_rel_prefix}/{fname}"))
            trailing_leading_found[0] = True
        for c in sorted(small_clusters(trailing_cands), key=lambda c: c[0]):
            pad = 2
            clip = fitz.Rect(c[0] - pad, ly0 - pad, c[2] + pad, ly1 + pad)
            consumed.append([clip.x0, clip.y0, clip.x1, clip.y1])
            n = counter.next()
            fname = f"{qid}_{n}.png"
            page.get_pixmap(clip=clip, dpi=400).save(os.path.join(img_dir, fname))
            it["pieces"].append(("img", f"{img_rel_prefix}/{fname}"))
            trailing_leading_found[0] = True

    def covered(r):
        for c in consumed:
            if r.x0 >= c[0] - 1 and r.y0 >= c[1] - 1 and r.x1 <= c[2] + 1 and r.y1 <= c[3] + 1:
                return True
        return False

    leftover = [[d.x0, d.y0, d.x1, d.y1] for d in drawings if not covered(d)]
    clusters = cluster_rects(leftover, CLUSTER_MARGIN)
    had_inline = any(kind == "img" for it in items if it["type"] == "line" for kind, _ in it["pieces"])
    had_drawings = bool(clusters) or trailing_leading_found[0] or had_inline
    for c in clusters:
        pad = 4
        clip = fitz.Rect(max(x0, c[0] - pad), max(y0, c[1] - pad), min(x1, c[2] + pad), min(y1, c[3] + pad))
        if clip.width < 2 or clip.height < 2:
            continue
        n = counter.next()
        fname = f"{qid}_{n}.png"
        pix = page.get_pixmap(clip=clip, dpi=400)
        pix.save(os.path.join(img_dir, fname))
        items.append({"type": "img", "y": c[1], "path": f"{img_rel_prefix}/{fname}"})

    items.sort(key=lambda it: it["y"])
    return items, had_drawings


def render_items_inline(items):
    """Join into a single collapsed-whitespace string (for choices)."""
    out = []
    for it in items:
        if it["type"] == "line":
            for kind, val in it["pieces"]:
                out.append(val if kind == "text" else f"⟦IMG:{val}⟧")
        else:
            out.append(f"⟦IMG:{it['path']}⟧")
    return collapse_ws("".join(out))


def render_items_block(items):
    """Join into paragraph(s) (for stem/rationale): a standalone image item
    gets its own paragraph; consecutive text lines join into one paragraph."""
    paragraphs = []
    cur = []
    for it in items:
        if it["type"] == "img":
            if cur:
                paragraphs.append(collapse_ws("".join(cur)))
                cur = []
            paragraphs.append(f"⟦IMG:{it['path']}⟧")
        else:
            for kind, val in it["pieces"]:
                cur.append(val if kind == "text" else f"⟦IMG:{val}⟧")
            cur.append(" ")
    if cur:
        paragraphs.append(collapse_ws("".join(cur)))
    return "\n\n".join(p for p in paragraphs if p)


def page_lines_tagged(doc, page_range):
    """[(page_num, line), ...] across 1-indexed pages in page_range, in
    reading order — used to find anchors that might land on any of a
    question's pages (a fat 4-graph answer set can push choices C/D onto
    the next physical page)."""
    out = []
    for pn in page_range:
        for l in get_page_lines(doc[pn - 1]):
            out.append((pn, l))
    return out


def find_anchor(tagged_lines, predicate, default=None):
    for pn, l in tagged_lines:
        if predicate(l):
            return (pn, l["bbox"][1])
    return default


def find_anchor_bottom(tagged_lines, predicate, default=None):
    for pn, l in tagged_lines:
        if predicate(l):
            return (pn, l["bbox"][3])
    return default


def spans_between(doc, start, end):
    """(page_num, y) start (inclusive) to (page_num, y) end (exclusive) ->
    list of (page_num, y0, y1) spans, one per physical page crossed."""
    p0, y0 = start
    p1, y1 = end
    if p0 == p1:
        return [(p0, y0, y1)] if y1 > y0 else []
    spans = [(p0, y0, doc[p0 - 1].rect.height)]
    for pn in range(p0 + 1, p1):
        spans.append((pn, HEADER_CLAMP_Y, doc[pn - 1].rect.height))
    spans.append((p1, HEADER_CLAMP_Y, y1))
    return spans


def extract_question_images(doc, rec, img_dir, img_rel_prefix, counter):
    page_range = list(range(rec["page"], rec.get("end_page", rec["page"]) + 1))
    tagged = page_lines_tagged(doc, page_range)

    stem_start = find_anchor_bottom(tagged, lambda l: line_text(l).startswith(f"ID: {rec['id']}"))
    if stem_start is None:
        rec.setdefault("_warnings", []).append("image pass: no id line found")
        return

    answer_hdr = find_anchor(
        tagged, lambda l: line_text(l).startswith("ID:") and line_text(l).strip().endswith("Answer")
    )
    if answer_hdr is None:
        rec.setdefault("_warnings", []).append("image pass: no answer header found")
        return

    rationale_hdr = find_anchor(tagged, lambda l: line_text(l).strip() == "Rationale")
    if rationale_hdr is not None and rationale_hdr < answer_hdr:
        # Sane layouts always put "Rationale" below the answer header. On
        # rare, extremely short questions ("What is 40% of 25?") the PDF's
        # layout compresses so much that badge/label positions collide or
        # invert — a "Rationale" label geometrically *above* its own answer
        # header means this page's y-coordinates aren't trustworthy for
        # splitting fields at all here. Bail out entirely and keep the
        # plain-text (image-free) fields from the structural pass instead of
        # risking choice/rationale content bleeding into each other.
        rec.setdefault("_warnings", []).append("image pass: rationale anchor above answer header, page layout unreliable")
        return

    # Pull the *entire* stem+choices span (right after "ID: <qid>" through
    # the answer header) as one region, then split it into stem/A/B/C/D by
    # walking its items in reading order and switching "bucket" whenever a
    # bare "A. "/"B. "/"C. "/"D. " marker line is hit — rather than trying to
    # pre-compute each choice's y-range from the markers' own positions.
    # That geometric approach breaks whenever a marker isn't top-aligned
    # with its content: a tall graph's "A. " label sits vertically *centered*
    # next to the graph, not above it, so cutting at the marker's y would
    # slice the graph itself in half between the stem and choice A, or
    # between two choices. A marker is always still a real, self-contained
    # text line in reading order right before its content, so switching on
    # it as it's encountered is exact regardless of where it sits visually.
    is_mc = rec["is_mc"]
    marker_re = re.compile(r"^([A-D])\.\s*")

    full_spans = spans_between(doc, stem_start, answer_hdr)
    all_items, had_any_img = build_region_multi(doc, full_spans, rec["id"], img_dir, img_rel_prefix, counter)

    buckets = {"stem": [], "A": [], "B": [], "C": [], "D": []}
    current = "stem"
    found_markers = set()
    for it in all_items:
        if is_mc and it["type"] == "line" and it.get("x0", 999) < 25 and it["pieces"]:
            kind0, val0 = it["pieces"][0]
            m = marker_re.match(val0) if kind0 == "text" else None
            if m:
                current = m.group(1)
                found_markers.add(current)
                rest = val0[m.end():]
                new_pieces = ([("text", rest)] if rest else []) + it["pieces"][1:]
                if new_pieces:
                    buckets[current].append({**it, "pieces": new_pieces})
                continue
        buckets[current].append(it)

    if is_mc and len(found_markers) != 4:
        # A handful of extremely short questions ("What is 40% of 25?") get
        # a page layout so compressed that the answer header lands *before*
        # the choice markers geometrically (a two-column layout, rather than
        # the usual single flow) — stem_start..answer_hdr then misses the
        # choices entirely, and whatever it does contain would be wrong.
        # Bail out completely rather than publish a part-scrambled record;
        # the plain-text (image-free) fields from the structural pass are
        # still correct, just missing the numbers.
        rec.setdefault("_warnings", []).append(
            f"image pass: found {len(found_markers)}/4 choice markers, page layout unreliable"
        )
        return

    stem_text = render_items_block(buckets["stem"])
    if stem_text:
        rec["stem"] = stem_text
    stem_had_img = any(
        it["type"] == "img" or (it["type"] == "line" and any(k == "img" for k, _ in it["pieces"]))
        for it in buckets["stem"]
    )
    rec["has_image"] = rec.get("has_image", False) or stem_had_img
    if stem_had_img:
        rec["image_paths"].append({"path": None, "position": "stem"})  # path(s) embedded inline in stem instead

    if is_mc:
        new_choices = {}
        for letter in "ABCD":
            text = render_items_inline(buckets[letter])
            new_choices[letter] = text if text else rec["choices_plain"].get(letter, "")
            if text:
                rec["has_image"] = rec["has_image"] or any(
                    it["type"] == "img" or (it["type"] == "line" and any(k == "img" for k, _ in it["pieces"]))
                    for it in buckets[letter]
                )
        rec["choices"] = new_choices

    # rationale: "Rationale" anchor to Question Difficulty/Assessment/next
    # question ID, possibly spanning onto following pages.
    end_page = rec.get("end_page", rec["page"])
    rationale_paragraphs = []
    for pnum in range(rec["page"], end_page + 1):
        pg = doc[pnum - 1]
        plines = get_page_lines(pg)
        if pnum == rec["page"]:
            ry0 = find_anchor_y(plines, lambda l: line_text(l).strip() == "Rationale")
            if ry0 is None:
                continue
            ry0 += 1
        else:
            ry0 = HEADER_CLAMP_Y
        qdiff_y = find_anchor_y(
            plines,
            lambda l: line_text(l).replace("ﬃ", "ffi").startswith("Question Diffi")
            or line_text(l).startswith("Question Di"),
        )
        next_qid_y = find_anchor_y(
            plines, lambda l: re.match(r"^Question ID \S", line_text(l)) is not None
        )
        candidates = [y for y in (qdiff_y, next_qid_y) if y is not None]
        ry1 = min(candidates) if candidates else pg.rect.height
        if ry1 <= ry0:
            continue
        items, had_img = build_region(pg, ry0, ry1, rec["id"], img_dir, img_rel_prefix, counter)
        text = render_items_block(items)
        if text:
            rationale_paragraphs.append(text)
        if had_img:
            rec["has_image"] = True
    if rationale_paragraphs:
        rec["rationale_full"] = "\n\n".join(rationale_paragraphs)


def main():
    if len(sys.argv) != 3:
        print("usage: parse_math_category.py <pdf_path> <category_name>")
        sys.exit(1)
    pdf_path, category = sys.argv[1], sys.argv[2]
    slug = category.lower().replace(" ", "_").replace("-", "_")

    records, failures, npages = parse_pdf_structure(pdf_path, category)
    print(f"{category}: {npages} pages -> {len(records)} questions parsed, {len(failures)} failed")
    for errors, snippet in failures[:10]:
        print(f"  FAIL {errors}: {snippet!r}")

    img_dir = os.path.join(REPO_ROOT, "images", "math", slug)
    os.makedirs(img_dir, exist_ok=True)
    img_rel_prefix = f"images/math/{slug}"

    doc = fitz.open(pdf_path)
    counter = ImageCounter()
    for rec in records:
        rec["stem"] = normalize(rec["stem_plain"])
        try:
            extract_question_images(doc, rec, img_dir, img_rel_prefix, counter)
        except Exception as e:  # noqa: BLE001
            rec.setdefault("_warnings", []).append(f"image pass exception: {e!r}")
        if not rec.get("rationale_full"):
            rec["rationale_full"] = normalize(rec.get("rationale_plain") or "")
        if not rec.get("choices") and rec["is_mc"]:
            rec["choices"] = rec.get("choices_plain", {})
        rec.pop("stem_plain", None)
        rec.pop("choices_plain", None)
        rec.pop("rationale_plain", None)
        rec.pop("end_page", None)
        rec.setdefault("choices", rec.get("choices", {}))

        # Same split R&W uses (site shows this per-letter under each choice
        # on "show answer") — MC rationales consistently open each
        # paragraph with "Choice X is correct."/"Choice X is incorrect.",
        # unaffected by embedded "⟦IMG:...⟧" tokens since that phrasing is
        # always real text. SPR (grid-in) rationales never use this
        # phrasing ("The correct answer is ...") and have no A-D choices to
        # attach a breakdown to, so they simply get none.
        rationale_by_choice = {}
        choice_expl_matches = list(re.finditer(r"Choice ([A-D]) is (?:correct|incorrect)\.?", rec["rationale_full"]))
        for idx, cem in enumerate(choice_expl_matches):
            letter = cem.group(1)
            start = cem.start()
            end = choice_expl_matches[idx + 1].start() if idx + 1 < len(choice_expl_matches) else len(rec["rationale_full"])
            rationale_by_choice[letter] = rec["rationale_full"][start:end].strip()
        rec["rationale_by_choice"] = rationale_by_choice
        if "image_paths" in rec:
            rec["image_paths"] = []  # images are inline tokens now; kept only for schema compat

    n_img_q = sum(1 for r in records if r["has_image"])
    print(f"  {n_img_q} questions have >=1 embedded math image, {counter.n} images total")

    warned = [r for r in records if "_warnings" in r]
    if warned:
        print(f"  {len(warned)} parsed with warnings:")
        for r in warned[:15]:
            print(f"    {r['id']}: {r['_warnings']}")

    ids = [r["id"] for r in records]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        print("  DUPLICATE IDS (source PDF repeats these verbatim; keeping first occurrence only):", dupes)
        seen = set()
        deduped = []
        for r in records:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            deduped.append(r)
        records = deduped

    out_path = os.path.join(REPO_ROOT, "db", "categories", f"{slug}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
