-- SAT Question Bank schema
-- Designed to hold questions across all categories (Grammar, Vocab, Transitions,
-- Command of Evidence, Cross-Text, Inferences, Main Idea, Notes, Functions, ...)
-- Question text/choices/rationale are English R&W style (four answer choices A-D),
-- but the schema is generic enough to extend to Math categories later.

CREATE TABLE IF NOT EXISTS questions (
    id                TEXT PRIMARY KEY,      -- College Board question ID (e.g. accc2b85)
    category          TEXT NOT NULL,         -- source PDF / topic bucket, e.g. "Grammar"
    source_pdf        TEXT NOT NULL,
    page              INTEGER,
    assessment        TEXT,                  -- e.g. "SAT"
    test              TEXT,                  -- e.g. "Reading and Writing"
    domain            TEXT,                  -- e.g. "Standard English Conventions"
    domain_code       TEXT,                  -- e.g. "SEC"
    skill             TEXT,                  -- e.g. "Boundaries"
    skill_code        TEXT,                  -- e.g. "BOUND"
    difficulty_label  TEXT,                  -- Easy / Medium / Hard
    difficulty        INTEGER,               -- 1 / 2 / 3
    tags              TEXT NOT NULL DEFAULT '', -- comma-separated freeform labels, e.g. "comma splice,tricky"
    stem              TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    correct_answer    TEXT NOT NULL,         -- letter, e.g. "B"
    rationale_full    TEXT,
    has_image         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS choices (
    question_id  TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    letter       TEXT NOT NULL,              -- A / B / C / D
    text         TEXT NOT NULL,
    is_correct   INTEGER NOT NULL DEFAULT 0,
    rationale    TEXT,                       -- per-choice explanation
    PRIMARY KEY (question_id, letter)
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id   TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,             -- relative path under images/
    position      TEXT NOT NULL DEFAULT 'stem'  -- 'stem' | 'choice_A' | 'choice_B' | ...
);

-- full-text search over stem/choices/rationale
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
    id UNINDEXED,
    stem,
    choices_text,
    rationale_full
);

CREATE INDEX IF NOT EXISTS idx_questions_category   ON questions(category);
CREATE INDEX IF NOT EXISTS idx_questions_domain      ON questions(domain);
CREATE INDEX IF NOT EXISTS idx_questions_skill       ON questions(skill);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty  ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_choices_question      ON choices(question_id);
CREATE INDEX IF NOT EXISTS idx_images_question       ON images(question_id);
