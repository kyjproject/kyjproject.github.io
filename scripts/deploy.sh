#!/usr/bin/env bash
# Rebuild the GitHub Pages site and push it live. Run this from anywhere,
# with an optional commit message:
#   ./scripts/deploy.sh "add new grammar questions"
# (defaults to "update" if you don't pass one)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 scripts/build_github_pages.py

git add site/pages
if git diff --cached --quiet; then
  echo "Nothing changed in site/pages/ — nothing to deploy."
  exit 0
fi

git commit -m "${1:-update}"
git pull origin main --no-edit
git push origin main

echo "Pushed. GitHub Actions will redeploy https://kyjproject.github.io/ in a minute or two."
