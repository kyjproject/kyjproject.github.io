"""Generic parser for the College Board Reading & Writing question-bank PDFs.

Unlike parse_grammar.py (one question per physical page), this version
concatenates the whole PDF's text first and splits on "Question ID <id>"
boundaries, because some categories (e.g. Cross-Text, Functions) have
questions with long rationales that spill onto a second physical page,
with the Domain/Skill footer landing mid-rationale rather than at the end.
"""
import fitz
import re
import json
import os
import sys

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}

DOMAIN_CODES = {
    "Standard English Conventions": "SEC",
    "Information and Ideas": "INI",
    "Craft and Structure": "CAS",
    "Expression of Ideas": "EOI",
}

SKILL_CODES = {
    "Boundaries": "BOUND",
    "Form, Structure, and Sense": "FSS",
    "Central Ideas and Details": "CID",
    "Inferences": "INFER",
    "Command of Evidence": "COE",
    "Cross-Text Connections": "XTEXT",
    "Text Structure and Purpose": "TSP",
    "Words in Context": "WIC",
    "Rhetorical Synthesis": "RSYN",
    "Transitions": "TRANS",
}

DIFF_MAP = {"Easy": 1, "Medium": 2, "Hard": 3}

FOOTER_RE = re.compile(
    r"Assessment\nSAT\nTest\nReading and Writing\nDomain\n(.*?)\nSkill\n(.*?)\nDifficulty\n(?:\d{1,4}\n)*",
    re.S,
)
QDIFF_RE = re.compile(r"Question Difficulty:\s*(\w+)\n?")


def normalize(text):
    for lig, rep in LIGATURES.items():
        text = text.replace(lig, rep)
    text = text.replace("\xa0", " ")
    return text


def clean_paragraphs(s):
    # "Text 1"/"Text 2" (Cross-Text) always sit on their own physical line in
    # the source, but on pages where the PDF has no blank-line gap anywhere
    # in the passage block, the label's *preceding* line has nothing marking
    # it as a paragraph break either — so the label silently gets folded into
    # the same paragraph as the text right before it (e.g. "...anything but.
    # Text 2" as one run-on sentence). Force a blank line in front of the
    # label line itself, whether or not one was already there, before the
    # existing "^(Text \d)\n(?=\S)" pass (below) handles the *following* side.
    lines0 = s.split("\n")
    fixed = []
    for line in lines0:
        if re.match(r"^Text \d$", line.strip()) and fixed and fixed[-1].strip() != "":
            fixed.append("")
        fixed.append(line)
    s = "\n".join(fixed)

    s = re.sub(r"(?m)^(Text \d)\n(?=\S)", r"\1\n\n", s)
    lines = s.split("\n")
    paragraphs, current = [], []
    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append(_join_paragraph_lines(current))
                current = []
        else:
            current.append(line.strip())
    if current:
        paragraphs.append(_join_paragraph_lines(current))
    return "\n\n".join(paragraphs)


# A block's physical PDF lines normally wrap at the page's text width (~90+
# chars in these PDFs — see the rationale/prose blocks) and should be
# rejoined with spaces to undo that incidental wrap. Verse quoted directly in
# a passage (poems) instead breaks every line *deliberately*, well short of
# that width, and joining those with spaces destroys the poem's line/stanza
# structure — the "weird stanza differentiation" a poem stem otherwise ends
# up with. Heuristic: a block of 3+ lines that are all short is almost
# certainly verse, not a wrapped sentence, so its line breaks are preserved
# (site CSS renders them via white-space: pre-line) instead of collapsed.
_VERSE_LINE_MAX = 70


def _join_paragraph_lines(lines):
    if len(lines) >= 3 and all(len(l) <= _VERSE_LINE_MAX for l in lines):
        return "\n".join(lines)
    return " ".join(lines)


# Many literary-excerpt stems open with an italicized attribution sentence —
# "The following text is [adapted ]from <Author>'s <year> <novel/poem/...>
# <Title>." — sometimes with a second short scene-setting sentence right
# after it, then the actual passage. On source pages with no blank-line gap
# at all, clean_paragraphs has nothing to split on, so the citation runs
# straight into the passage as one paragraph (a title glued directly onto a
# poem's first line, etc). This finds the first genuine sentence boundary
# (skipping likely abbreviations/initials, e.g. "A. E. Henderson" or "et
# al.") and forces a paragraph break there, so the citation reads as its own
# line like it does in the real test. Best-effort: a citation with an
# unusually long translator/editor credit clause may still be one sentence
# short of ideal, but it's always strictly better than zero separation.
INTRO_ATTRIBUTION_RE = re.compile(r"^The following texts? (is|are)\b")
SENTENCE_BOUNDARY_RE = re.compile(r"([.!?])([\"'’”)]*)\s+(?=[A-Z])")
ABBREV_SKIP = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "al", "mme", "mmes",
    "messrs", "ph", "no", "vol", "ed", "eds", "trans", "rev", "gen", "lt", "col",
    "capt", "sgt", "prof",
}


def separate_intro_attribution(stem):
    if not INTRO_ATTRIBUTION_RE.match(stem):
        return stem
    # Only the *first* existing paragraph matters here: a poem's stanza
    # breaks further down the stem also produce "\n\n", but those don't mean
    # the citation itself is already split from what follows it — checking
    # for "\n\n" anywhere in the first 400 chars (the old heuristic) bailed
    # out on exactly that case, leaving the citation glued to the poem's
    # first line. Only skip when the first paragraph is already short
    # enough to plausibly be just the citation.
    first_para_end = stem.find("\n\n")
    first_para = stem if first_para_end == -1 else stem[:first_para_end]
    if len(first_para) < 300:
        return stem
    for m in SENTENCE_BOUNDARY_RE.finditer(first_para):
        pre = first_para[: m.start(1)]
        word_m = re.search(r"([A-Za-z]+)$", pre)
        if word_m and (len(word_m.group(1)) == 1 or word_m.group(1).lower() in ABBREV_SKIP):
            continue  # likely an initial/abbreviation, not a real sentence end
        end = m.end(2)
        return stem[:end] + "\n\n" + stem[m.end():]
    return stem


def collapse_ws(s):
    return re.sub(r"\s*\n\s*", " ", s).strip()


# Notes/Rhetorical-Synthesis questions present a short list of bulleted facts
# ("a student has taken the following notes:" / a bare list of sentences)
# before asking which choice best uses them. clean_paragraphs only starts a
# new paragraph at a *blank* line, and these bullets have no blank line
# between them in the source PDF — so the whole list collapses into one
# run-on paragraph, losing the list structure entirely. Two source formats
# show up: some pages already carry a literal "• " glyph per line; others
# have no glyph at all, just one plain sentence per line after an intro
# phrase. Handled by turning each bullet into its own "\n\n"-separated
# paragraph (glyph added ourselves in the no-glyph case) before
# clean_paragraphs runs, so each renders as its own line/paragraph like any
# other paragraph break in the site.
BULLET_PREFIX = "• "
NOTES_INTRO_RE = re.compile(r"(taken the following notes:)[ \t]*\n")


def listify_bullets(stem_raw):
    lines = stem_raw.split("\n")

    if any(l.startswith(BULLET_PREFIX) for l in lines):
        out, cur = [], None
        for l in lines:
            if l.startswith(BULLET_PREFIX):
                if cur is not None:
                    out.append(cur)
                cur = l
            elif cur is not None:
                cur += " " + l  # wrapped continuation of the current bullet
            else:
                out.append(l)  # content before the first bullet, if any
        if cur is not None:
            out.append(cur)
        return "\n\n".join(out)

    m = NOTES_INTRO_RE.search(stem_raw)
    if not m:
        return stem_raw
    before = stem_raw[: m.end()]
    after_lines = stem_raw[m.end():].split("\n")
    notes, rest, in_notes = [], [], True
    for l in after_lines:
        if in_notes and l.strip() and not l.startswith("The student wants"):
            notes.append(BULLET_PREFIX + l.strip())
        else:
            in_notes = False
            rest.append(l)
    if not notes:
        return stem_raw
    return before + "\n\n".join(notes) + "\n\n" + "\n".join(rest)


# --- Underline detection -----------------------------------------------
#
# SAT Functions/Command of Evidence/Cross-Text questions frequently refer to
# "the underlined sentence"/"the underlined portion" within the passage, but
# plain text extraction (page.get_text()) discards all formatting, so that
# reference silently pointed at nothing. The underline itself isn't a text
# attribute in these PDFs — it's a separate thin horizontal vector stroke
# drawn just under the line, discoverable via page.get_drawings(). It has a
# very consistent, distinctive color/width that never collides with chart or
# table chrome elements (those are all *filled* rects in different colors;
# this is a *stroked* line), which makes it a safe, precise signal to key off.
UNDERLINE_COLOR = (0.11764705926179886, 0.11764705926179886, 0.11764705926179886)
UNDERLINE_WIDTH = 0.4801118075847626


def _is_underline_stroke(d):
    if d["type"] != "s":
        return False
    r = d["rect"]
    if r.height > 3 or r.width < 3:
        return False
    color = d.get("color")
    width = d.get("width") or 0
    if not color or any(abs(color[i] - UNDERLINE_COLOR[i]) > 0.05 for i in range(3)):
        return False
    if abs(width - UNDERLINE_WIDTH) > 0.3:
        return False
    return True


def _merge_segments(segs, gap=10):
    segs = sorted(segs)
    merged = []
    for x0, x1 in segs:
        if merged and x0 - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))
    return merged


def extract_underline_runs(page, y_tol=3, word_overlap_thresh=0.5):
    """Return the underlined text run(s) on a page, in reading order.

    A run is built by walking the page's text lines top to bottom, matching
    each line's y-position against a merged underline-stroke group directly
    beneath it (strokes come in several short segments per line — split by
    the PDF renderer around glyph descenders — so segments at the same y are
    merged into continuous x-ranges first). A word counts as underlined if
    its own x-range is majority-covered by that merged range. A run carries
    over onto the next line only when the underline reaches the last word of
    the current line AND starts at the first word of the next one — i.e. an
    actual sentence wrap, not two coincidentally-underlined fragments on
    adjacent lines.
    """
    strokes = [d for d in page.get_drawings() if _is_underline_stroke(d)]
    if not strokes:
        return []
    by_y = {}
    for d in strokes:
        r = d["rect"]
        by_y.setdefault(round(r.y0, 1), []).append((r.x0, r.x1))
    y_groups = {y: _merge_segments(segs) for y, segs in by_y.items()}

    words = page.get_text("words")
    lines = {}
    for w in words:
        key = (w[5], w[6])
        lines.setdefault(key, []).append(w)
    line_keys_sorted = sorted(
        lines.keys(), key=lambda k: (min(w[1] for w in lines[k]), min(w[0] for w in lines[k]))
    )

    runs, current, carry = [], [], False
    for key in line_keys_sorted:
        line_words = sorted(lines[key], key=lambda w: w[0])
        y1 = max(w[3] for w in line_words)
        matched_segs = next((segs for y, segs in y_groups.items() if abs(y - y1) <= y_tol), None)
        if not matched_segs:
            if current:
                runs.append(current)
                current = []
            carry = False
            continue
        idxs = []
        for i, w in enumerate(line_words):
            x0, x1 = w[0], w[2]
            covered = sum(
                max(0, min(x1, sx1) - max(x0, sx0)) for sx0, sx1 in matched_segs
            )
            if covered / (x1 - x0) >= word_overlap_thresh:
                idxs.append(i)
        if not idxs:
            if current:
                runs.append(current)
                current = []
            carry = False
            continue
        starts_at_start = idxs[0] == 0
        ends_at_end = idxs[-1] == len(line_words) - 1
        piece = " ".join(w[4] for w in line_words[idxs[0]: idxs[-1] + 1])
        if carry and starts_at_start:
            current.append(piece)
        else:
            if current:
                runs.append(current)
            current = [piece]
        carry = ends_at_end
    if current:
        runs.append(current)
    return [" ".join(r) for r in runs]


def fix_full_prompts(records, doc):
    """Recover the complete prompt sentence when it wrapped across 2+
    physical PDF lines.

    The prompt-boundary regex in parse_question_block only captures the
    single line immediately before the answer choices ("...?" up against
    "\\nA. "), so a prompt that wraps loses its earlier line(s) — e.g. only
    "explanation?" gets captured instead of "Which response from a survey
    ...best supports the researchers' explanation?", with the missing prefix
    left stranded at the end of `stem` instead.

    PyMuPDF's own block layout already solves most of this: the prompt
    sentence is normally its own text block, separate from the passage block
    before it and the "A. ..." choice block after it. But a mid-sentence
    formatting change (e.g. underlined text right before "...the underlined
    sentence?") can itself split that one sentence across two *separate*
    blocks — so this walks backward from the block ending in the
    already-captured last line, merging any immediately preceding block that
    doesn't end in terminal punctuation (a wrapped continuation, not a
    genuinely separate block of passage text), then uses the merged text as
    the real prompt and trims the now-duplicated prefix off the end of
    `stem`. A no-op for the (large majority of) single-line prompts, where
    the block *is* just that line already.
    """
    for rec in records:
        page = doc[rec["page"] - 1]
        blocks = sorted(
            (b for b in page.get_text("dict")["blocks"] if b.get("lines")),
            key=lambda b: b["bbox"][1],
        )
        block_texts = []
        for b in blocks:
            line_texts = ["".join(sp["text"] for sp in ln["spans"]) for ln in b["lines"]]
            block_texts.append(normalize(" ".join(t for t in line_texts if t).strip()))

        last_line = normalize(rec["prompt"])
        for i, text in enumerate(block_texts):
            if not text.endswith(last_line):
                continue
            parts = [text]
            j = i - 1
            # A block ending in the fill-in-the-blank placeholder "______"
            # (fill-in-the-blank passages, e.g. Command of Evidence/
            # Inferences) is just as much a genuine stopping point as
            # terminal punctuation — without this, the passage block right
            # before the real prompt gets swallowed into `prompt` wholesale,
            # since a bare "______" doesn't match the punctuation check.
            while (
                j >= 0
                and block_texts[j]
                and not re.search(r"[.?!][”\"'’]?\s*$", block_texts[j])
                and not block_texts[j].endswith("______")
            ):
                parts.insert(0, block_texts[j])
                j -= 1
            full_prompt = " ".join(parts)
            if len(full_prompt) <= len(last_line):
                break
            prefix = full_prompt[: -len(last_line)].strip()
            stripped_stem = rec["stem"].rstrip()
            if prefix and stripped_stem.endswith(prefix):
                idx = stripped_stem.rfind(prefix)
                rec["stem"] = stripped_stem[:idx].rstrip()
                rec["prompt"] = full_prompt
            break
    return records


def annotate_underlines(records, doc):
    """Wrap each record's underlined passage text in <u>...</u> in place.

    Runs are matched into `stem` by plain substring search (after the same
    ligature/nbsp normalization used to build `stem` itself), so a miss is
    always a silent no-op, never a corruption — logged as a warning instead.
    """
    for rec in records:
        page = doc[rec["page"] - 1]
        runs = extract_underline_runs(page)
        if not runs:
            continue
        stem = rec["stem"]
        search_from = 0
        unmatched = []
        for run_text in runs:
            run_norm = normalize(run_text)
            idx = stem.find(run_norm, search_from)
            if idx == -1:
                unmatched.append(run_text)
                continue
            end = idx + len(run_norm)
            stem = stem[:idx] + "<u>" + stem[idx:end] + "</u>" + stem[end:]
            search_from = idx + len("<u>") + len(run_norm) + len("</u>")
        rec["stem"] = stem
        if unmatched:
            rec.setdefault("_warnings", []).append(f"underline not matched: {unmatched}")
    return records


def parse_question_block(block, category, source_pdf):
    errors = []
    m_id = re.match(r"Question ID (\S+)\nID: (\S+)\n", block)
    if not m_id:
        errors.append("no id header")
        return None, errors
    qid = m_id.group(2)
    rest = block[m_id.end():]

    # The Domain/Skill footer (and the "Question Difficulty:" line) can land
    # anywhere in the block depending on page layout — normally after the
    # rationale, but on content-heavy pages (e.g. a chart/table pushes things
    # around) it can land between the last choice and "ID: ... Answer"
    # instead. Strip both out globally, upfront, before any other parsing,
    # so downstream regexes never have to know which case they're in.
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

    prompt_m = re.search(r"\n([^\n]*\?) *\n(?=A\. )", rest)
    if not prompt_m:
        errors.append("no prompt")
        return None, errors
    stem_raw = listify_bullets(rest[: prompt_m.start()])
    prompt = prompt_m.group(1).strip()
    after_prompt = rest[prompt_m.end():]

    ans_hdr_m = re.search(r"\nID: " + re.escape(qid) + r" Answer\n", after_prompt)
    if not ans_hdr_m:
        errors.append("no answer header")
        return None, errors
    choices_block = after_prompt[: ans_hdr_m.start()].strip()
    after_ans_hdr = after_prompt[ans_hdr_m.end():]

    choice_matches = list(re.finditer(r"(?m)^([A-D])\. ", choices_block))
    choices = {}
    for idx, cm in enumerate(choice_matches):
        letter = cm.group(1)
        start = cm.end()
        end = choice_matches[idx + 1].start() if idx + 1 < len(choice_matches) else len(choices_block)
        choices[letter] = collapse_ws(choices_block[start:end])
    if len(choices) != 4:
        errors.append(f"expected 4 choices got {len(choices)}")

    correct_m = re.search(r"Correct Answer:\s*([A-D])\n", after_ans_hdr)
    if not correct_m:
        errors.append("no correct answer")
        return None, errors
    correct = correct_m.group(1)
    after_correct = after_ans_hdr[correct_m.end():]

    rationale_m = re.search(r"Rationale\n(.*)$", after_correct, re.S)
    if not rationale_m:
        errors.append("no rationale")
        return None, errors
    rationale_raw = rationale_m.group(1)

    # Footer/difficulty were already stripped from `rest` upfront, but a
    # question with a very long rationale can still contain the footer's
    # own text if it happened to land inside the rationale span itself —
    # harmless no-op in that case since the substitution is idempotent.
    rationale_raw = FOOTER_RE.sub(footer_sub, rationale_raw)
    rationale_raw = QDIFF_RE.sub(qdiff_sub, rationale_raw)

    if found["domain"] is None or found["skill"] is None:
        errors.append("no domain/skill footer")
    if diff["label"] is None:
        errors.append("no difficulty")

    rationale_full = collapse_ws(rationale_raw)

    rationale_by_choice = {}
    choice_expl_matches = list(re.finditer(r"Choice ([A-D]) is (?:the best answer|correct|incorrect)\.?", rationale_full))
    for idx, cem in enumerate(choice_expl_matches):
        letter = cem.group(1)
        start = cem.start()
        end = choice_expl_matches[idx + 1].start() if idx + 1 < len(choice_expl_matches) else len(rationale_full)
        rationale_by_choice[letter] = rationale_full[start:end].strip()

    domain = found["domain"] or ""
    skill = found["skill"] or ""

    record = {
        "id": qid,
        "category": category,
        "source_pdf": source_pdf,
        "assessment": "SAT",
        "test": "Reading and Writing",
        "domain": domain,
        "domain_code": DOMAIN_CODES.get(domain, ""),
        "skill": skill,
        "skill_code": SKILL_CODES.get(skill, ""),
        "difficulty_label": diff["label"] or "",
        "difficulty": DIFF_MAP.get(diff["label"]),
        "stem": separate_intro_attribution(clean_paragraphs(stem_raw)),
        "prompt": prompt,
        "choices": choices,
        "correct_answer": correct,
        "rationale_full": rationale_full,
        "rationale_by_choice": rationale_by_choice,
        "has_image": False,
        "image_paths": [],
    }
    return record, errors


def parse_pdf(pdf_path, category):
    doc = fitz.open(pdf_path)
    page_texts = [normalize(page.get_text()) for page in doc]
    full_text = "".join(page_texts)

    # cumulative offset -> page index, for citing a starting page per question
    page_offsets = [0]
    for t in page_texts:
        page_offsets.append(page_offsets[-1] + len(t))

    import bisect

    def page_for_offset(off):
        return bisect.bisect_right(page_offsets, off) - 1 + 1  # 1-indexed

    starts = [m.start() for m in re.finditer(r"Question ID \S+\nID: \S+\n", full_text)]
    starts.append(len(full_text))
    blocks = [full_text[starts[i]: starts[i + 1]] for i in range(len(starts) - 1)]
    block_pages = [page_for_offset(starts[i]) for i in range(len(starts) - 1)]

    source_pdf = os.path.basename(pdf_path)
    records, failures = [], []
    for b, page_num in zip(blocks, block_pages):
        rec, errors = parse_question_block(b, category, source_pdf)
        if rec is None:
            failures.append((errors, b[:200]))
            continue
        rec["page"] = page_num
        if errors:
            rec["_warnings"] = errors
        records.append(rec)
    return records, failures, len(doc)


def main():
    if len(sys.argv) != 3:
        print("usage: parse_category.py <pdf_path> <category_name>")
        sys.exit(1)
    pdf_path, category = sys.argv[1], sys.argv[2]

    records, failures, npages = parse_pdf(pdf_path, category)
    print(f"{category}: {npages} pages -> {len(records)} questions parsed, {len(failures)} failed")
    for errors, snippet in failures[:10]:
        print(f"  FAIL {errors}: {snippet!r}")

    doc = fitz.open(pdf_path)
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

    out_dir = "/Users/kyj/Downloads/sat-database/db/categories"
    os.makedirs(out_dir, exist_ok=True)
    slug = category.lower().replace(" ", "_")
    out_path = os.path.join(out_dir, f"{slug}.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
