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
  staging/proposed_questions.json  manually-added questions awaiting review (see below), empty ([]) by default

images/
  command_of_evidence/*.png    cropped chart/table images, one per question that has one

scripts/
  parse_category.py            PDF -> db/categories/<category>.json (generic text parser, 8 of 9 categories)
  parse_command_of_evidence.py PDF -> db/categories/command_of_evidence.json + images/ (text + chart/table crops)
  build_db.py                  db/categories/*.json -> db/sat.db
  build_site_data.py           db/categories/*.json -> db/all_questions.json + site/data.js
  build_site.py                site/index.template.html + data -> site/dist/index.html (single-file, for Claude Artifacts)
  build_github_pages.py        site/index.template.html + data + images -> site/pages/ (multi-file, for GitHub Pages)
  review_staged_questions.py   db/staging/proposed_questions.json -> db/categories/<category>.json, one-by-one approval
  staging_server.py            local HTTP server so tools/add_question.html works in any browser (not just Chrome/Edge)
  apps-script/Code.gs           Google Apps Script backend for kyj-cloud, the app's optional login/sync feature (deployed separately, not part of this build)

tools/
  add_question.html            local form for staging a new hand-written question into db/staging/proposed_questions.json

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
  "tags": [],                       // freeform labels, e.g. ["comma splice", "tricky"] — optional, [] if none
  "stem": "...",                    // "\n\n"-separated paragraphs; Cross-Text
                                     // questions have "Text 1" / "Text 2" as
                                     // their own paragraph before each passage
  "prompt": "Which choice completes the text so that it conforms to the conventions of Standard English?",
  "choices": { "A": "...", "B": "...", "C": "...", "D": "..." },
  "correct_answer": "B",
  "rationale_full": "...",
  "rationale_by_choice": { "A": "...", "B": "...", "C": "...", "D": "..." },
  "has_image": false,
  "image_paths": []                 // [{ "path": "images/command_of_evidence/<id>.png", "position": "stem" }] when has_image — .svg also allowed for hand-added questions
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

## Adding new questions by hand (staging + review)

Every question so far came from parsing a College Board PDF, but the same
record schema can hold a hand-written question too — e.g. one transcribed
from a practice test that isn't in any of the source PDFs. Nothing you enter
this way touches `db/categories/*.json` (the live database) directly; it
always goes through a staging file first, so a bad entry can't reach the site
without a deliberate approval step.

1. Run `python3 scripts/staging_server.py` and open the
   `http://localhost:8765/tools/add_question.html` link it prints — this
   works in **any** browser, including Firefox/Safari, since the page just
   does a plain `fetch()` POST to that local server, which does the actual
   file write. (Chrome/Edge only, alternative: open `tools/add_question.html`
   directly as a `file://` page and click **Select project root folder**
   instead, which grants write access via the File System Access API — the
   same pattern `tools/image_editor.html` uses for images. Firefox/Safari
   don't implement that API at all, which is what the server sidesteps.)
   Fill in the form (category auto-fills domain/skill/codes; the question ID
   is generated for you, checked against every existing ID; tags are
   optional freeform comma-separated labels) and click **Add to staging**.
   This appends the record to `db/staging/proposed_questions.json` and, if
   you attached an image — paste a screenshot, or upload a `.png`/`.svg`
   file directly — saves it under `images/<category file>/<id>.png` (or
   `.svg`). Keep entering questions — the form stays open and clears itself
   after each save. (If your browser doesn't support folder access, it
   downloads the accumulated staging file instead — merge it into
   `db/staging/proposed_questions.json` yourself.)
2. Run `python3 scripts/review_staged_questions.py`. It shows one staged
   question at a time — full stem, choices, correct answer, rationale — and
   validates it (required fields, exactly 4 distinct non-empty choices, a
   real correct answer, a non-empty rationale for every choice A-D,
   domain/skill/skill_code consistent with the chosen category, difficulty
   label matching its numeric value, prompt ending in "?", no ID collision
   with the live bank or elsewhere in staging, and that any referenced image
   file actually exists). A question with validation
   errors can only be rejected or skipped, never approved. For each clean
   question you choose **Approve** (appends it to the right
   `db/categories/<category>.json` and removes it from staging), **Reject**
   (deletes it from staging permanently, optionally deleting its staged
   image too), or **Skip** (leaves it in staging for next time).
3. If anything was approved, rebuild like normal (see Regenerating below):
   `build_db.py`, `build_site_data.py`, `build_site.py`,
   `build_github_pages.py`.

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
- **Test**: Mock Exam's distribution engine (editable per-section/per-skill
  question counts, editable Easy/Medium/Hard mix, bias-toward-weak, status
  scope) as one flat quiz of any length instead of two timed modules — set
  any total question count and the per-skill blueprint scales to it. Built in
  blueprint order, then shuffled, so the questions come in random order
  rather than grouped by skill. No-feedback quiz (chart/table images included
  where relevant) — choices are just marked selected, nothing is graded until
  you submit the whole test — followed by a scored results page with
  per-skill breakdown and full review.
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
- **Bluebook**: a single-module practice mode built to look and behave like
  the actual College Board "Bluebook" testing app, not just another quiz
  screen. Config (question scope, count, module timer) and the final score
  screen stay in the site's normal theme; starting a module switches to a
  full-screen kiosk view — no app topbar/tabs — with Bluebook's chrome: a
  black-ruled header with the module timer (hideable) and a Directions
  link/overlay, a two-pane layout (passage left, question + lettered choices
  right) with a black question-number badge and a **Mark for Review** flag,
  a toolbar with a **Highlights & Notes** pen (drag-select text in the
  passage to highlight it, click a highlight to remove it — persisted per
  question for the rest of the attempt), a **More** menu's **Answer
  Eliminator** (adds a cross-out toggle to each choice that strikes it
  through and blocks selecting it until undone), and an A−/A+ text-zoom
  stepper. The bottom bar's **Question X of N** pill opens a question
  navigator (grid of tiles showing answered/flagged/current at a glance,
  jump to any question or straight to the review page); the last question's
  **Next** also leads to the review page, which lists every question's
  answered/flagged status before a confirm-and-submit. Skill stats from a
  submitted module feed the same weak-skill tracking as Test/Practice/Mock
  Exam.
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

  There's also **kyj-cloud** — a cloud icon in the topbar (and a matching
  card on the Profile tab, next to the name field) for an optional account
  login that syncs the same backup payload to this repo instead of (or
  alongside) downloading it. It's entirely opt-in: local `localStorage`
  progress and Export/Import work exactly as before whether or not anyone
  ever signs up.

  Since a static site can't hold a GitHub token safely, the app POSTs to a
  small Google Apps Script (`scripts/apps-script/Code.gs`, deployed
  separately by whoever runs this site — see the comment at the top of that
  file for setup) which does the actual GitHub commit server-side, using a
  token that never reaches the browser. The Apps Script's own URL is baked
  into the build via the `CLOUD_SAVE_URL` constant near the top of
  `site/index.template.html` (empty by default, which hides the whole
  feature — e.g. for the Claude Artifact build, which has nowhere to commit
  to).

  Accounts are just a username + password, self-service ("Sign up" right in
  the popover) and checked by the script against a `users.json` file it
  keeps in the repo (`db/cloud_save/`, alongside one progress file per user)
  — there's no real security here by design (this is a personal/shared
  progress tracker, not something holding anything sensitive), so use a
  password you're not reusing elsewhere. Logging in on a new browser offers
  to load your existing cloud progress in (merged with whatever's already
  local). There's no autosave or periodic sync — **Save progress now** /
  **Load from cloud** only ever run when you click them. Credentials are
  remembered in `localStorage` across reloads until you hit **Log out**.
- **Propose**: lets a student submit a new question straight from the site,
  no local setup required. Reuses the same account as cloud save — logging
  in there also unlocks this tab — and the form is the same fields as
  `tools/add_question.html` (category/skill/difficulty auto-fill together,
  a rationale box per choice, optional PNG/SVG image). Submitting posts
  through the same Apps Script (`handlePropose_` in
  `scripts/apps-script/Code.gs`), which appends the record straight to
  `db/staging/proposed_questions.json` on GitHub — it never touches
  `db/categories/*.json` directly. The site owner still has to pull the repo
  and run `scripts/review_staged_questions.py` to actually approve or reject
  each one, exactly like a question added locally; nothing a student submits
  reaches the live bank without that step. Hidden when `CLOUD_SAVE_URL`
  isn't set, same as cloud save.
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
