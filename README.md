# SAT Question Bank

Structured database + browser built from the College Board practice-question PDFs in
this folder. **All 9 source PDFs are fully processed — 1,686 questions, zero parse
failures, zero data-quality issues.** (10 browsing categories: Command of Evidence's
single source PDF is split into two, see below.)

| Category | Questions | Domain | Skill | Images |
|---|---|---|---|---|
| Grammar | 379 | Standard English Conventions | Boundaries / Form, Structure, and Sense | — |
| Textual Evidence | 131 | Information and Ideas | Command of Evidence (Textual) | — |
| Quantitative Evidence | 127 | Information and Ideas | Command of Evidence (Quantitative) | 127 |
| Vocab | 241 | Craft and Structure | Words in Context | — |
| Notes | 192 | Expression of Ideas | Rhetorical Synthesis | — |
| Transitions | 173 | Expression of Ideas | Transitions | — |
| Functions | 138 | Craft and Structure | Text Structure and Purpose | — |
| Main Idea | 125 | Information and Ideas | Central Ideas and Details | — |
| Inferences | 124 | Information and Ideas | Inferences | — |
| Cross-Text | 56 | Craft and Structure | Cross-Text Connections | — |
| **Total** | **1,686** | | | **127** |

(`Functions.pdf` is named for its content — "function of the underlined text" —
not math; it's Reading & Writing like everything else here.)

8 of the 9 source PDFs have no embedded images at all (verified via
`page.get_images()` across every page). `Command of Evidence.pdf` is the
exception: its quantitative-evidence questions reference a chart or data table
rendered as **vector graphics** on the page (not a raster image — that's why
`get_images()` found nothing there either; it needed a different approach,
see below). 127 of its 258 questions have one and are categorized "Quantitative
Evidence"; those get a cropped PNG under `images/command_of_evidence/`. The
remaining 131, which point at a quotation or a described finding instead of a
chart/table, are categorized "Textual Evidence" — this split is a `category`
distinction (short display names), with `skill` correspondingly split into
"Command of Evidence (Quantitative)" / "(Textual)" — matching the naming
already used for the same split in `db/module_composition.json`. One browsing/
practice/test bucket each, not just a filter.

## Layout

```
Grammar.pdf, Vocab.pdf, ...    original source PDFs (untouched)

db/
  schema.sql                   SQLite schema (questions, choices, images, FTS5 search)
  sat.db                       generated SQLite database
  categories/*.json            one file per category, parsed question records (source of truth)
  all_questions.json           all categories concatenated (generated)

images/
  command_of_evidence/*.png    cropped chart/table images, one per question that has one

scripts/
  parse_category.py            PDF -> db/categories/<category>.json (generic text parser, 8 of 9 categories)
  parse_command_of_evidence.py PDF -> db/categories/command_of_evidence.json + images/ (text + chart/table crops)
  build_db.py                  db/categories/*.json -> db/sat.db
  build_site_data.py           db/categories/*.json -> db/all_questions.json + site/data.js
  build_site.py                site/index.template.html + data -> site/dist/index.html (single-file, for Claude Artifacts)
  build_github_pages.py        site/index.template.html + data + images -> site/pages/ (multi-file, for GitHub Pages)

site/
  index.template.html          the web app (single file, vanilla JS, no build tooling)
  dist/index.html               generated, self-contained single file (Claude Artifact build — no images, see below)
  pages/                        generated, multi-file folder: index.html + data.js + images/ (GitHub Pages build)
```

## Question record schema

Each question in `db/categories/*.json` (and the `questions`/`choices` SQLite tables):

```jsonc
{
  "id": "accc2b85",                 // College Board question ID
  "category": "Grammar",            // source bucket (matches the PDF filename)
  "source_pdf": "Grammar.pdf",
  "page": 1,                        // starting page in the source PDF
  "assessment": "SAT",
  "test": "Reading and Writing",
  "domain": "Standard English Conventions",
  "domain_code": "SEC",             // SEC | INI | CAS | EOI
  "skill": "Boundaries",
  "skill_code": "BOUND",
  "difficulty_label": "Hard",       // Easy | Medium | Hard
  "difficulty": 3,                  // 1 / 2 / 3
  "stem": "...",                    // "\n\n"-separated paragraphs; Cross-Text
                                     // questions have "Text 1" / "Text 2" as
                                     // their own paragraph before each passage
  "prompt": "Which choice completes the text so that it conforms to the conventions of Standard English?",
  "choices": { "A": "...", "B": "...", "C": "...", "D": "..." },
  "correct_answer": "B",
  "rationale_full": "...",
  "rationale_by_choice": { "A": "...", "B": "...", "C": "...", "D": "..." },
  "has_image": false,
  "image_paths": []                 // [{ "path": "images/command_of_evidence/<id>.png", "position": "stem" }] when has_image
}
```

## The generic parser (`scripts/parse_category.py`)

One question can span more than one physical PDF page (long rationales
overflow), and the page's Domain/Skill footer gets extracted wherever it
happened to land in reading order — normally after the rationale, but on
content-heavy pages it can land between the last answer choice and the
"ID: ... Answer" marker instead. The parser handles this by concatenating the
whole PDF's text first, splitting on `Question ID <id>` boundaries (not page
boundaries), then stripping the footer + `Question Difficulty:` line out
*wherever* they land in the block — including mid-sentence cases like
`"...listing credits. Choice\n<footer>\nB is incorrect..."`. It also
normalizes PDF ligatures (ﬁ/ﬂ/ﬃ → fi/fl/ffi) and handles both `"the best
answer"` and `"is correct"` phrasings when splitting rationale by choice.

## Command of Evidence's chart/table extraction (`scripts/parse_command_of_evidence.py`)

Charts and tables in this PDF are drawn as vector graphics (lines, filled
rectangles), not raster images, layered together with the page's own UI
chrome (a background frame, a top header row, navy "ID: ..." / "ID: ...
Answer" badges) — all pulled from the same `page.get_drawings()` call. The
approach:

1. **Filter out known chrome**: drop anything covering >50% of the page
   (background frames), anything spanning >80% of the page width (header
   dividers), anything above y=175 (the header band/ID badge), and navy-filled
   shapes below y=650 (the "Answer" badge).
2. **Bounding-box the rest** and crop+render that region to a PNG at 200 DPI,
   padded generously (55/70/45 pt top/bottom/x) to catch chart titles and
   axis labels, which are real text (not drawings) sitting just outside the
   line-art itself.
3. **Classify** a page as chart/table-bearing only if ≥8 elements remain
   spanning ≥150pt of vertical space — thin isolated strokes (e.g. a single
   underlined phrase in the passage, drawn as an underline) don't qualify on
   their own.
4. **Cap the crop height** at 380pt (raw, pre-padding) as a backstop: on the
   rare page where a stray far-off element (that underline, a badge fragment)
   survives filtering and falls within the bounding box, this stops the crop
   from stretching down through the answer choices and rationale instead of
   just the chart.
5. **Clean the stem text**: a chart page's raw extracted text has the axis
   labels/legend/title jumbled together *before* the real passage (they're
   real text, just laid out beside the chart, so they get pulled into reading
   order ahead of the prose). The parser strips a leading run of short lines
   (≤6 words) up to the first real sentence — the image is the source of
   truth for chart content now, so that noise doesn't need to survive in the
   stem. The original uncleaned text is kept in `stem_raw` for reference.

This is a best-effort heuristic tuned against a sample of the 258 questions,
not a pixel-perfect table/chart detector — expect the occasional crop to
include a bit more or less context than ideal. It never breaks the *text*
parsing (domain/skill/difficulty/choices/rationale are 100% regex-driven, not
dependent on the image heuristic), so a bad crop is a cosmetic issue confined
to one question's image, not a data-quality one.

## Regenerating

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pymupdf

python3 scripts/parse_category.py Grammar.pdf Grammar
python3 scripts/parse_category.py Vocab.pdf Vocab
python3 scripts/parse_category.py Transitions.pdf Transitions
python3 scripts/parse_category.py Notes.pdf Notes
python3 scripts/parse_category.py Functions.pdf Functions
python3 scripts/parse_category.py "Main Idea.pdf" "Main Idea"
python3 scripts/parse_category.py Inferences.pdf Inferences
python3 scripts/parse_category.py "Cross-Text.pdf" "Cross-Text"
python3 scripts/parse_command_of_evidence.py   # text + chart/table images

python3 scripts/build_db.py               # rebuild db/sat.db
python3 scripts/build_site_data.py        # rebuild db/all_questions.json + site/data.js
python3 scripts/build_site.py             # rebuild site/dist/index.html (Claude Artifact)
python3 scripts/build_github_pages.py     # rebuild site/pages/ (GitHub Pages — index.html + data.js + images/)
```

For local preview, serve `site/pages/` with any static file server (needed for
images to load — opening `site/pages/index.html` directly via `file://` won't
fetch `data.js`/images the same way a real HTTP server does).

## Two builds, two purposes

- **`site/dist/index.html`** — single self-contained file, all question data
  inlined, used for the Claude Artifact. Command of Evidence's 16.5MB of chart
  images are **not** embedded here (would blow the artifact's 16MB size
  limit) — those questions still work, just without their image; a broken
  image degrades to a small "not available in this view" note instead of a
  broken-image icon.
- **`site/pages/`** — `index.html` + `data.js` + `images/`, the real deploy
  target (GitHub Pages or any static host). Full image support.

## The web app

Every question also gets a short display id assigned client-side, grouped by
category in source order (e.g. `Transitions #23`, `Evidence #46`) — shown
everywhere a question appears instead of the long College Board hash id
(which remains the internal storage key, so old progress exports still work).

- **Browse**: full-text search over stem/choices/rationale, filter by category,
  skill, difficulty, and status, sort by difficulty, expand a question for the
  interactive detail view (see its chart/table image if it has one). Multi-passage
  questions (Cross-Text) render "Text 1"/"Text 2" as labeled sections.
- **Practice**: pick a scope (status/category/skill/difficulty, optionally
  shuffled) and work through the matching questions one at a time. Clicking a
  choice only selects it — nothing is graded until you hit **Submit**, which
  reveals whether that pick was right or wrong. **Show answer**/**Hide answer**
  independently reveals the correct choice and full per-choice rationale,
  whenever you want it. Browse's expanded card and the Progress/Review inline
  panels all share this same interactive detail.
- **Test**: pick categories/skills/difficulty/question count and a status
  scope (e.g. only "Incomplete", only "To review"), then take a no-feedback
  quiz (chart/table images included where relevant) — choices are just marked
  selected, nothing is graded until you submit the whole test — followed by a
  scored results page with per-skill breakdown and full review.
- **Mock Exam**: a fully configurable digital-SAT-style R&W mock, not just a
  fixed 54-question run. Pick a **mode** — adaptive full exam (Module 1 routes
  into a harder/easier Module 2 based on your Module 1 accuracy, with an
  editable routing threshold), Module 1 only, or Module 2 only (choose the
  harder or easier pool directly, e.g. to drill the hard pool on demand).
  Scope the question pool by status (e.g. only "Incomplete") with a per-skill
  fallback to the full bank if a skill runs thin. The per-skill question-count
  table is directly editable — change any skill's count, or set a target
  total and apply it to proportionally rescale the whole module (defaults to
  the official 27/27 blueprint, one click to reset). Each difficulty mix
  (Module 1, Module 2 harder pool, Module 2 easier pool) is independently a
  preset (Balanced/Harder/Easier) or fully custom Easy/Medium/Hard weights.
  An opt-in per-question timer (strict auto-advance or flexible) layers on
  top of the real 32-minute module clock, same as Test/Practice. Results
  scale to what you actually took — a full adaptive attempt gets the
  estimated composite /1600 score, module-only attempts get a raw
  per-module/per-skill breakdown instead (the composite curve is calibrated
  against the full 54-question exam). Past attempts are saved locally with
  their mode, reviewable read-only from the history list.
- **Progress**: mark any question "🚩 Mark for review", "✓ Mark complete", or
  "○ Mark incomplete" from its detail view (Browse, Practice, Test results, or
  the Progress/Review tiles). Marked questions get a colored card border
  (green = complete, violet = to review) everywhere they appear. The Progress
  tab has:
  - an overall completion bar + stat counts
  - a **per-category breakdown**: click any category to toggle a detail panel
    showing Easy/Medium/Hard complete-counts and a clickable tile grid — one
    small square per question, colored by status — that opens that question
    right there inline (same interactive detail as Practice, with an "✕" to
    close it) instead of navigating away
  - a **Focus** selector (Easy/Medium/Hard toggles) that scopes the
    percentage, stat counts, and per-category breakdown to just the
    difficulties you pick — e.g. "Hard only" to track completion within your
    current focus instead of the whole bank. Persisted across reloads; this
    is also the intended hook for the planned adaptive-practice feature to
    scope its question pool the same way.

  Saved to the browser's `localStorage` automatically — since that isn't
  guaranteed to survive forever, use **Export progress (.json)** to back it up
  and **Import progress** to restore/transfer it.
- **Review**: everything flagged 🚩 for review, scheduled with **spaced
  repetition** — a from-scratch implementation of FSRS-4.5 (the same memory
  model Anki's "FSRS" scheduler uses), so the queue resurfaces each question
  right around when you're about to forget it instead of dumping the whole
  pile on you every time (the intended answer to "what if this pile gets
  insanely large" — you only ever see what's actually due). The tab shows Due
  now / Scheduled / Never studied / Total flagged, plus a category tile grid
  (solid tile = due, dashed = scheduled for later, tooltip shows the date).
  **Study due** launches a session (capped at 40 cards at a time, however big
  the backlog is) through the same interactive detail view as everywhere
  else — pick a choice, Submit or Show answer, then grade how well you knew
  it with **Again / Hard / Good / Easy** (each button previews the resulting
  interval, e.g. "Good 4d"), which reschedules that card and advances to the
  next. A separate **Practice all flagged** button still jumps into a
  no-grading Practice session over the same set, for a plain run-through.
  Flagging/unflagging a question is unchanged and still drives which
  questions are in the FSRS pool; scheduling data is independent of Complete/
  Incomplete status and survives Export/Import progress.
