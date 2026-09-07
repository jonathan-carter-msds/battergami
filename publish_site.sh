#!/bin/zsh
# publish_site.sh
# Regenerates site/data/*.json from battergami.db and pushes it so Netlify
# redeploys. Meant to run from cron right after the daily pipeline.
#
#   crontab entry (a few minutes after the 5 AM pipeline run):
#   20 5 * * * /Users/jonathancarter/battergami/publish_site.sh >> /Users/jonathancarter/battergami/pipeline.log 2>&1
#
# No secrets in here — safe to commit. Assumes `git push` auth is already
# set up (SSH key or a cached credential helper).

set -e
REPO="/Users/jonathancarter/battergami"
PYTHON="/usr/local/Caskroom/miniconda/base/envs/baseball311/bin/python"

cd "$REPO"

echo "=== publish_site: $(date) ==="
"$PYTHON" build_site.py

if ! git diff --quiet -- site/data; then
  git add site/data
  git commit -m "site data: refresh $(date +%Y-%m-%d)"
  git push
  echo "pushed refreshed site data"
else
  echo "no data changes"
fi
