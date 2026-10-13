#!/usr/bin/env bash
# Usage: scripts/safe_commit.sh "feat: message"
# Stages everything, refuses to commit if the staged diff contains an API key value, then commits and pushes.
set -euo pipefail
git add -A
if git diff --cached -U0 | grep -E '^\+' | grep -qE '(API_KEY\s*=\s*[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-or-v1-[a-f0-9]{20,}|sk-[A-Za-z0-9]{32,})'; then
  echo "ABORT: staged diff appears to contain an API key" >&2
  git reset -q
  exit 1
fi
git commit -q -m "$1"
git log --oneline -1
if git remote get-url origin >/dev/null 2>&1; then git push -q origin HEAD 2>&1 | tail -1 || true; fi
