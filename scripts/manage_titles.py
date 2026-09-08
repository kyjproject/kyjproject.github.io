"""Manage leaderboard title badges in db/user_titles.json.

Titles are the entity, not users — each title is a slug id with its own
emoji/color/reason, and a `users` list of everyone who has it. Editing a
title's look updates it everywhere at once; a title can have any number of
users (including zero, or many). This is the only kind of badge that
exists now — badges tied to a computed condition (e.g. "first N signups",
"#1 right now") were removed; add a new one the same way if wanted again,
directly in Code.gs / site/index.template.html since a condition like that
needs live data this file doesn't have.

Whatever this writes only reaches the live site once db/user_titles.json is
pushed to GitHub (Code.gs reads it from there, not this machine) — and
right now LEADERBOARD_BADGES_ENABLED_ (Code.gs) / LB_BADGES_ENABLED
(site/index.template.html) are both off, so no badge shows anywhere until
those two flags flip back on.

Usage:
    python3 scripts/manage_titles.py list
    python3 scripts/manage_titles.py set grinder "Grinder" --preset grinder --users hamin,kyjv9981
    python3 scripts/manage_titles.py set grinder "Grinder" --emoji 🔥 --color "#dc2626" --reason "..." --users hamin
    python3 scripts/manage_titles.py add-user grinder kyjv9981
    python3 scripts/manage_titles.py remove-user grinder kyjv9981
    python3 scripts/manage_titles.py delete grinder
    python3 scripts/manage_titles.py templates
"""
import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLES_PATH = os.path.join(ROOT, "db", "user_titles.json")

DEFAULT_EMOJI = "⭐"
DEFAULT_COLOR = "#1f5f56"
DEFAULT_REASON = "Custom title, set by the site owner"

# Starter looks — a name, an emoji, and a color that's already legible with
# the badge's fixed white text. Pick one with --preset, or ignore these and
# pass --emoji/--color/--reason directly for anything more specific.
PRESETS = {
    "sports":    {"emoji": "🏀", "color": "#16a34a"},
    "gamer":     {"emoji": "🎮", "color": "#7c3aed"},
    "grinder":   {"emoji": "🔥", "color": "#dc2626"},
    "night_owl": {"emoji": "🦉", "color": "#4338ca"},
    "wordsmith": {"emoji": "📚", "color": "#0f766e"},
    "math_wiz":  {"emoji": "🧮", "color": "#1d4ed8"},
    "chaotic":   {"emoji": "🎲", "color": "#db2777"},
    "goofy":     {"emoji": "🤡", "color": "#ea580c"},
}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def load_titles():
    if not os.path.exists(TITLES_PATH):
        return {}
    with open(TITLES_PATH) as f:
        return json.load(f)


def save_titles(titles):
    with open(TITLES_PATH, "w") as f:
        json.dump(titles, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def pushed_reminder():
    print(f"Wrote {TITLES_PATH} — commit + push it (Code.gs reads it from GitHub, not this machine) "
          f"for it to reach the live site.")


def cmd_list(args):
    titles = load_titles()
    if not titles:
        print("No titles defined.")
        return
    for title_id, t in sorted(titles.items()):
        users = t.get("users") or []
        print(f"{title_id}: {t.get('emoji', DEFAULT_EMOJI)} {t.get('text', '')!r} "
              f"color={t.get('color', DEFAULT_COLOR)}")
        print(f"  reason: {t.get('reason', DEFAULT_REASON)!r}")
        print(f"  users:  {', '.join(users) if users else '(none)'}")


def cmd_templates(args):
    print(f"{'name':<12} {'emoji':<6} color")
    for name, t in PRESETS.items():
        print(f"{name:<12} {t['emoji']:<6} {t['color']}")


def _validate_id(title_id):
    if not ID_RE.match(title_id):
        raise SystemExit(f"title id must be lowercase letters/digits/-/_ only, got {title_id!r}")


def cmd_set(args):
    _validate_id(args.title_id)
    if args.preset and args.preset not in PRESETS:
        raise SystemExit(f"Unknown preset {args.preset!r}. Run `templates` to see options.")
    preset = PRESETS.get(args.preset, {})

    color = args.color or preset.get("color", DEFAULT_COLOR)
    if not HEX_RE.match(color):
        raise SystemExit(f"--color must be a 6-digit hex like #16a34a, got {color!r}")

    titles = load_titles()
    existing = titles.get(args.title_id, {})
    users = existing.get("users", [])
    if args.users is not None:
        users = [u.strip() for u in args.users.split(",") if u.strip()]

    titles[args.title_id] = {
        "text": args.text,
        "emoji": args.emoji or preset.get("emoji", DEFAULT_EMOJI),
        "color": color,
        "reason": args.reason or DEFAULT_REASON,
        "users": users,
    }
    save_titles(titles)
    t = titles[args.title_id]
    print(f"Set {args.title_id}: {t['emoji']} {t['text']!r} ({t['color']}) — {t['reason']!r}")
    print(f"  users: {', '.join(users) if users else '(none)'}")
    pushed_reminder()


def cmd_add_user(args):
    titles = load_titles()
    if args.title_id not in titles:
        raise SystemExit(f"No title {args.title_id!r} — create it first with `set`.")
    users = titles[args.title_id].setdefault("users", [])
    if args.username in users:
        print(f"{args.username} already has {args.title_id!r}.")
        return
    users.append(args.username)
    save_titles(titles)
    print(f"Added {args.username} to {args.title_id!r}.")
    pushed_reminder()


def cmd_remove_user(args):
    titles = load_titles()
    if args.title_id not in titles:
        raise SystemExit(f"No title {args.title_id!r}.")
    users = titles[args.title_id].setdefault("users", [])
    if args.username not in users:
        print(f"{args.username} doesn't have {args.title_id!r} — nothing to remove.")
        return
    users.remove(args.username)
    save_titles(titles)
    print(f"Removed {args.username} from {args.title_id!r}.")
    pushed_reminder()


def cmd_delete(args):
    titles = load_titles()
    if args.title_id not in titles:
        print(f"No title {args.title_id!r} — nothing to delete.")
        return
    del titles[args.title_id]
    save_titles(titles)
    print(f"Deleted title {args.title_id!r}.")
    pushed_reminder()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every title and who has it").set_defaults(func=cmd_list)
    sub.add_parser("templates", help="list ready-made emoji/color presets").set_defaults(func=cmd_templates)

    p_set = sub.add_parser("set", help="create or update a title (and optionally replace its user list)")
    p_set.add_argument("title_id", help="slug id, e.g. \"grinder\" — lowercase letters/digits/-/_ only")
    p_set.add_argument("text", help="the badge's visible text, e.g. \"Grinder\"")
    p_set.add_argument("--preset", help="use a ready-made emoji/color combo (see `templates`)")
    p_set.add_argument("--emoji", help=f"overrides --preset's emoji (default {DEFAULT_EMOJI})")
    p_set.add_argument("--color", help=f"overrides --preset's color, 6-digit hex (default {DEFAULT_COLOR})")
    p_set.add_argument("--reason", help="hover text explaining the badge (default: generic)")
    p_set.add_argument("--users", help="comma-separated usernames to assign (replaces the existing list; omit to leave unchanged)")
    p_set.set_defaults(func=cmd_set)

    p_add = sub.add_parser("add-user", help="add one user to an existing title")
    p_add.add_argument("title_id")
    p_add.add_argument("username")
    p_add.set_defaults(func=cmd_add_user)

    p_rm_user = sub.add_parser("remove-user", help="remove one user from a title")
    p_rm_user.add_argument("title_id")
    p_rm_user.add_argument("username")
    p_rm_user.set_defaults(func=cmd_remove_user)

    p_del = sub.add_parser("delete", help="delete a title entirely (unassigns everyone)")
    p_del.add_argument("title_id")
    p_del.set_defaults(func=cmd_delete)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
