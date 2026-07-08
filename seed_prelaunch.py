"""
seed_prelaunch.py
------------------
Run this ONCE, right before you go live, and never again for this launch.

Problem it solves: the "Nth Battergami of {year}" count in draft_tweet() is
based on how many rows exist in tweeted_performances for that year. But the
bot wasn't running from Opening Day through whenever you actually go live --
real battergami events already happened in games earlier this season that
were never tweeted. Without this step, the first real tweet would wrongly
say "1st Battergami of 2026" instead of the true number.

What this does:
1. Ingests every completed game from 2026 Opening Day (March 25) through
   yesterday via the MLB API (safe to re-run -- INSERT OR REPLACE).
2. Runs the same detection query used in production, day by day in
   chronological order.
3. For every genuine hit, records it in tweeted_performances (so the yearly
   counter and already_tweeted() both see it) -- but does NOT call
   post_tweet(). Nothing gets posted to X. This is bookkeeping only.

After this runs, pipeline.py's normal daily job (starting from today) will
report accurate "Nth of year" numbers and will actually post to X.

Usage:
    python seed_prelaunch.py
    python seed_prelaunch.py --start 2026-03-25 --end 2026-07-06   # explicit override
"""

import argparse
from datetime import date, timedelta

from pipeline import (
    get_conn, init_db, ingest_day, run_detection, draft_tweet,
    already_tweeted, mark_tweeted, count_unique_lines, count_tweeted_this_year,
)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main(start: date, end: date):
    init_db()
    print(f"Seeding battergami records from {start} to {end} (recording only, not posting)...\n")

    total_recorded = 0
    total_games = 0

    for d in daterange(start, end):
        ingest_day(d)  # safe / idempotent, prints its own per-day summary

        hits = run_detection(d)
        if not hits:
            continue

        conn = get_conn()
        for row in hits:
            if already_tweeted(conn, row["game_id"], row["player_id"]):
                continue
            year = int(row["game_date"][:4])
            nth = count_tweeted_this_year(conn, year) + 1
            total_unique = count_unique_lines(conn)
            text = draft_tweet(row, nth, total_unique)
            print(f"  [RECORDED, NOT POSTED] {row['player_name']} -- {d} -- {nth}th of {year}")
            # tweet_id is None on purpose: this was never actually posted
            mark_tweeted(conn, row["game_id"], row["player_id"], None, text)
            total_recorded += 1
        conn.close()

    conn = get_conn()
    final_2026_count = count_tweeted_this_year(conn, end.year)
    conn.close()

    print(f"\nDone. {total_recorded} real battergami events recorded for the season so far.")
    print(f"The next actual tweet will correctly read as the {final_2026_count + 1}th Battergami of {end.year}.")
    print("You can now run pipeline.py normally going forward -- it will post real tweets")
    print("starting from today, with the counter already caught up.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed battergami records for the 2026 season-to-date without posting")
    parser.add_argument("--start", type=str, default="2026-03-25", help="Season start date (default: 2026-03-25, Opening Day)")
    parser.add_argument("--end", type=str, default=None, help="Last date to include (default: yesterday)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)

    main(start, end)