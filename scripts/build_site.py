"""Inject db/all_questions.json into site/index.template.html -> site/dist/index.html.

The resulting file is fully self-contained (data embedded inline) so it can be
opened directly from disk (file://) or published as a single-file artifact.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "site", "index.template.html")
ALL_JSON_PATH = os.path.join(ROOT, "db", "all_questions.json")
OUT_PATH = os.path.join(ROOT, "site", "dist", "index.html")


def main():
    with open(ALL_JSON_PATH) as f:
        questions = json.load(f)
    if not questions:
        raise SystemExit(
            f"Refusing to build: {ALL_JSON_PATH} has 0 questions. "
            f"That looks like a corrupted/incomplete regeneration, not a real "
            f"deploy — not touching {OUT_PATH} so it keeps whatever it had."
        )

    with open(TEMPLATE_PATH) as f:
        template = f.read()

    # Embed as JSON text inside a <script type="application/json"> tag.
    # Escape "</" so a literal "</script>" inside question text can't break out.
    payload = json.dumps(questions, ensure_ascii=False).replace("</", "<\\/")

    html = template.replace("__QUESTIONS_JSON__", payload)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        f.write(html)

    print(f"Wrote {OUT_PATH} ({len(html):,} bytes, {len(questions)} questions)")


if __name__ == "__main__":
    main()
