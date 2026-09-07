"""Build db/sat.db (SQLite) from all db/categories/*.json files."""
import json
import os
import sqlite3
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "sat.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
CATEGORIES_DIR = os.path.join(ROOT, "db", "categories")


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    total = 0
    for path in sorted(glob.glob(os.path.join(CATEGORIES_DIR, "*.json"))):
        with open(path) as f:
            records = json.load(f)
        for r in records:
            conn.execute(
                """INSERT INTO questions
                   (id, title, category, source_pdf, page, assessment, test, domain, domain_code,
                    skill, skill_code, difficulty_label, difficulty, tags, stem, prompt,
                    correct_answer, rationale_full, has_image)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["id"], r.get("title", ""), r["category"], r["source_pdf"], r["page"], r["assessment"],
                    r["test"], r["domain"], r.get("domain_code", ""), r["skill"],
                    r.get("skill_code", ""), r["difficulty_label"], r["difficulty"],
                    ",".join(r.get("tags", [])),
                    r["stem"], r["prompt"], r["correct_answer"], r["rationale_full"],
                    1 if r.get("has_image") else 0,
                ),
            )
            choices_text_parts = []
            for letter, text in r["choices"].items():
                conn.execute(
                    """INSERT INTO choices (question_id, letter, text, is_correct, rationale)
                       VALUES (?,?,?,?,?)""",
                    (
                        r["id"], letter, text, 1 if letter == r["correct_answer"] else 0,
                        r.get("rationale_by_choice", {}).get(letter, ""),
                    ),
                )
                choices_text_parts.append(f"{letter}. {text}")
            for img in r.get("image_paths", []):
                conn.execute(
                    "INSERT INTO images (question_id, path, position) VALUES (?,?,?)",
                    (r["id"], img.get("path", img) if isinstance(img, dict) else img,
                     img.get("position", "stem") if isinstance(img, dict) else "stem"),
                )
            conn.execute(
                "INSERT INTO questions_fts (id, stem, choices_text, rationale_full) VALUES (?,?,?,?)",
                (r["id"], r["stem"], " | ".join(choices_text_parts), r["rationale_full"]),
            )
            total += 1

    conn.commit()
    print(f"Inserted {total} questions into {DB_PATH}")

    cur = conn.execute("SELECT category, COUNT(*) FROM questions GROUP BY category")
    for cat, cnt in cur.fetchall():
        print(f"  {cat}: {cnt}")

    conn.close()


if __name__ == "__main__":
    main()
