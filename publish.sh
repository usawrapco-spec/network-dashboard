#!/usr/bin/env bash
# Refresh video-news-network engagement stats and push data.json to GitHub Pages.
# Run on the box, from anywhere:
#   /workspace/marketing/social/video-news-network/dashboard/publish.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v python3 >/dev/null; then
  echo "python3 is required" >&2
  exit 1
fi
if ! command -v yt-dlp >/dev/null && ! command -v /home/box/.local/bin/yt-dlp >/dev/null; then
  echo "yt-dlp is required on PATH" >&2
  exit 1
fi

export PATH="$HOME/.local/bin:$PATH"

python3 "$DIR/collect.py"

if [ ! -d .git ]; then
  echo "This folder is not a git repo yet. Create usawrapco-spec/network-dashboard first." >&2
  exit 1
fi

git status --short
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "No dashboard changes to publish."
  exit 0
fi

# Never add private/ (scheduled drafts, captions, stills).
git add index.html data.json logo.png .nojekyll publish.sh collect.py scheduled.py README.md .gitignore
git add stats/*.json 2>/dev/null || true
if git ls-files --cached -z | grep -z -E '(^|/)private/' >/dev/null; then
  echo "Refusing to publish: private/ files were staged." >&2
  exit 1
fi

if git diff --cached --quiet; then
  echo "No staged changes to publish."
  exit 0
fi

STAMP=$(python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %-I:%M %p PT'))")
git commit -m "Refresh network dashboard stats (${STAMP})"
git push origin main
echo "Published. Live at https://usawrapco-spec.github.io/network-dashboard/"
