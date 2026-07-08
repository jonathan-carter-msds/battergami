# battergami

Tweets when a batter posts an exact box-score line (AB, R, H, 2B, 3B, HR, BB,
SO, RBI, SB) that has never occurred before in MLB history — the batter
analog of `pitchergami`.

## How it decides "never happened before"

Every batter-game is reduced to a 10-number vector: `(ab, r, h, 2b, 3b, hr,
bb, so, rbi, sb)`. A new game's vector is flagged only if no earlier game in
the database has that exact combination. This is an anti-join, not a
threshold — `detection_query.sql` is the whole model.

## Two data sources, two jobs

| Source | Role | Why |
|---|---|---|
| **Retrosheet** | One-time (then periodic) historical backfill | Free, complete game-by-game data back to 1901, but not updated daily during the season — usually lags by weeks. |
| **MLB Stats API** | Daily ingestion | Free, official, box scores available within hours of a game ending. This is what lets the bot post same-day. |

Both write into the same `batter_game_lines` table (see `schema.sql`), so the
detection query treats them as one continuous history.

## 1. Set up the database

```bash
sqlite3 battergami.db < schema.sql
```

(Swap for Postgres if you want this to scale past a hobby project — the SQL
in `detection_query.sql` is portable, just change the `?` placeholders in
`pipeline.py` to `%s` and use `psycopg2` instead of `sqlite3`.)

## 2. Backfill history from Retrosheet

Retrosheet distributes **event files** (pitch-by-pitch), not ready-made batter
box scores, so you need the [Chadwick tools](https://github.com/chadwickbureau/chadwick)
to turn them into game lines:

```bash
# get event files for a season, e.g. 2019
wget https://www.retrosheet.org/events/2019eve.zip
unzip 2019eve.zip -d 2019eve

# cwbox emits one line per player per game
cwbox -y 2019 -f 0,2,3,4,5,6,7,8,9,10,13,14 2019eve/*.EVN 2019eve/*.EVA > 2019_box.csv
```

Write a small loader script (or extend `pipeline.py`) that reads that CSV and
inserts rows into `batter_game_lines` with `source = 'retrosheet'`. Repeat for
each season you want in the baseline — the further back you go, the more
"first-ever" claims will actually hold up, since some modern-looking lines
turn out to have happened once in 1911.

You'll also need the [Chadwick people register](https://github.com/chadwickbureau/register)
to map Retrosheet player IDs to MLBAM IDs (`id_map` table) so the Retrosheet
backfill and the MLB API's daily rows refer to the same player consistently.

## 3. Run the daily job

```bash
export X_API_KEY=...
export X_API_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_SECRET=...

python pipeline.py --dry-run          # prints candidate tweets, posts nothing
python pipeline.py                    # ingests yesterday, tweets any first-ever lines
python pipeline.py 2026-06-15         # reprocess a specific date
```

Schedule it with cron to run once daily, a few hours after the last game of
the day ends (Pacific-night getaway games mean waiting until early morning
Eastern is safest):

```
0 9 * * * cd /path/to/battergami && python pipeline.py >> pipeline.log 2>&1
```

## 4. X (Twitter) API access

You need a X Developer account with a project on at least the **Basic** tier
(the free tier can read but cannot post). Generate a Consumer Key/Secret and
an Access Token/Secret with **read and write** permissions, then set the four
environment variables above. `pipeline.py` uses `tweepy.Client.create_tweet`,
which is the v2 endpoint.

## Design decisions worth revisiting

- **Which columns count.** The vector is `(ab, r, h, 2b, 3b, hr, bb, so, rbi,
  sb)` — 10 numbers. Runs scored is included since it captures impact beyond
  the batter's own box score line (getting driven in, scoring from the
  basepaths); note it does depend partly on teammates, which means two
  otherwise-identical lines can diverge on `r` alone and count as separate
  "first-ever" events. That's a reasonable tradeoff if you want runs to count
  toward the story, but it does make "first-ever" slightly easier to achieve
  than a pure hitting-stats-only vector would.
- **Trivial lines.** The query already excludes 0-AB/0-BB/0-HBP entries (bench
  players who didn't appear). You may also want to exclude very common short
  lines (e.g. 0-for-1) that are only "novel" because almost nobody accumulates
  a 0-AB game with a walk in a specific rare way — that's a judgment call
  about what makes a tweet interesting versus technically true.
- **Same-day ties.** If two players post the identical new line on the same
  day, both are flagged (`same_day_ties` column). The pipeline currently
  tweets both separately with a note; you might prefer a combined tweet.
