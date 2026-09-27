#!/bin/bash
# publish_site_fast.sh
# Regenerates ONLY site/data/latest.json + summary.json and pushes them, so
# the site's "Latest battergamis" feed and headline tweet count catch up
# within minutes of the bot actually posting -- instead of waiting for the
# once-a-day full rebuild (publish_site.sh), which also recomputes all
# ~35k lines' first/last occurrences and is far too slow to run hourly on a
# memory-constrained host.
#
# Meant to run right after the bot's own hourly check (see run_battergami.sh)
# -- not on its own cron line, so there's never a moment where it's competing
# with that same run for the database.

set -e
REPO="/home/jonathancarter/battergami/repo"
PYTHON="/home/jonathancarter/battergami/venv/bin/python"
export BATTERGAMI_DB="/home/jonathancarter/battergami/battergami.db"

cd "$REPO"

echo "--- publish_site_fast: $(date) ---"
"$PYTHON" build_site.py --fast

if ! git diff --quiet -- site/data/latest.json site/data/summary.json; then
  git add site/data/latest.json site/data/summary.json
  git commit -q -m "site data: fast refresh $(date +"%Y-%m-%d %H:%M")"
  git push -q
  echo "pushed fast refresh"
else
  echo "no changes"
fi
