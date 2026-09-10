"""One-off migration: converts legacy plain category/skill/difficulty-filter
blueprints (advancedDistribution: false) in db/cloud_save/blueprints.json into
the current always-on distribution/weightConfig shape, mirroring
categoryBlueprint()/weightConfigForDifficulties() in site/index.template.html.

Practice/Test no longer show category/skill/difficulty chips, so an old
blueprint saved in that shape only ever "worked" under the hood via a
fallback code path — this makes the stored data match what the UI now
actually displays, instead of relying on that fallback at apply-time.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "db", "cloud_save", "blueprints.json")

MOCK_BLUEPRINT = [
    {"category": "Vocab", "skill": "Words in Context", "m1": 4, "m2": 4},
    {"category": "Functions", "skill": "Text Structure and Purpose", "m1": 2, "m2": 2},
    {"category": "Cross-Text", "skill": "Cross-Text Connections", "m1": 1, "m2": 1},
    {"category": "Main Idea", "skill": "Central Ideas and Details", "m1": 3, "m2": 3},
    {"category": "Quantitative Evidence", "skill": "Command of Evidence (Quantitative)", "m1": 2, "m2": 2},
    {"category": "Textual Evidence", "skill": "Command of Evidence (Textual)", "m1": 2, "m2": 2},
    {"category": "Inferences", "skill": "Inferences", "m1": 2, "m2": 2},
    {"category": "Grammar", "skill": "Boundaries", "m1": 2, "m2": 2},
    {"category": "Grammar", "skill": "Form, Structure, and Sense", "m1": 2, "m2": 2},
    {"category": "Transitions", "skill": "Transitions", "m1": 4, "m2": 4},
    {"category": "Notes", "skill": "Rhetorical Synthesis", "m1": 3, "m2": 3},
]


def distribute_to_target(weights, target):
    target = max(0, round(target))
    total_weight = sum(weights)
    raw = [(w / total_weight) * target if total_weight else target / len(weights) for w in weights]
    floors = [int(v) for v in raw]
    used = sum(floors)
    remainder = target - used
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i]), reverse=True)
    result = floors[:]
    for k in range(min(remainder, len(order))):
        result[order[k]] += 1
    return result


def category_blueprint(categories, skills, total):
    if skills:
        rows = [r for r in MOCK_BLUEPRINT if r["skill"] in skills]
    elif categories:
        rows = [r for r in MOCK_BLUEPRINT if r["category"] in categories]
    else:
        rows = MOCK_BLUEPRINT
    if not rows:
        rows = MOCK_BLUEPRINT
    weights = [r["m1"] + r["m2"] for r in rows]
    counts = distribute_to_target(weights, total)
    by_key = {(r["category"], r["skill"]): c for r, c in zip(rows, counts)}
    return [
        {"category": r["category"], "skill": r["skill"], "count": by_key.get((r["category"], r["skill"]), 0)}
        for r in MOCK_BLUEPRINT
    ]


def weight_config_for_difficulties(difficulties):
    if not difficulties:
        return {"main": {"preset": "balanced", "custom": {"Easy": 25, "Medium": 50, "Hard": 25}}}
    order = ["Easy", "Medium", "Hard"]
    weights = [1 if d in difficulties else 0 for d in order]
    counts = distribute_to_target(weights, 100)
    return {"main": {"preset": "custom", "custom": dict(zip(order, counts))}}


def main():
    with open(PATH) as f:
        entries = json.load(f)

    changed = 0
    for entry in entries:
        data = entry.get("data") or {}
        if data.get("advancedDistribution"):
            continue  # already in the current shape
        if data.get("view") == "mock":
            continue  # mock blueprints never used the old chip shape
        total = data.get("count", 20)
        categories = data.get("categories") or []
        skills = data.get("skills") or []
        difficulties = data.get("difficulties") or []
        data["blueprint"] = category_blueprint(categories, skills, total)
        data["weightConfig"] = weight_config_for_difficulties(difficulties)
        data["advancedDistribution"] = True
        data["advancedDifficulty"] = True
        changed += 1
        print(f"Migrated {entry['code']} ({entry['name']}): categories={categories} difficulties={difficulties} -> blueprint+weightConfig")

    if changed:
        with open(PATH, "w") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
    print(f"Done — {changed} entr{'y' if changed == 1 else 'ies'} migrated, {len(entries) - changed} already current.")


if __name__ == "__main__":
    main()
