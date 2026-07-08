"""
sanity_check.py
----------------
Pulls a range of dates from the MLB Stats API, stores each batter's game
line, and runs the detection query one day at a time (in chronological
order) so each day is only compared against days that came before it --
exactly how the real pipeline will behave, just over a small window instead
of full MLB history.

This does NOT post to X. It only prints what it finds, so you can eyeball
whether the model is too strict, too loose, or about right before doing
anything with Retrosheet or Twitter credentials.

Usage:
    python sanity_check.py 2019-04-01 2019-04-14

If you don't pass dates, it defaults to the first two weeks of the 2019
season below. Two weeks is roughly 150-200 games -- enough to see the
pipeline work end-to-end without waiting on hundreds of API calls.
"""

import sys
from datetime import date, timedelta

# reuse the functions we already wrote in pipeline.py -- no need to duplicate them
from pipeline import (
    get_conn, init_db, fetch_game_pks, fetch_and_store_boxscore,
    run_detection, draft_tweet, count_unique_lines, count_tweeted_this_year,
    find_closest_call, draft_no_battergami_tweet, already_tweeted,
)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main(start: date, end: date):
    init_db()  # safe to call even if the tables already exist

    total_hits = 0
    total_games = 0
    total_closest_calls = 0
    year_counts = {}

    for d in daterange(start, end):
        pks = fetch_game_pks(d)
        if not pks:
            print(f"{d}: no completed games (off day or too early)")
            continue

        conn = get_conn()
        day_rows = 0
        for pk in pks:
            day_rows += fetch_and_store_boxscore(conn, pk, d)
        total_games += len(pks)

        hits = run_detection(d)
        # Only preview hits that haven't already been recorded -- either from a
        # real tweet, or from seed_prelaunch.py's pre-launch bookkeeping. Without
        # this filter, re-running sanity_check.py over a date range that overlaps
        # already-processed days would re-count the same events as if new,
        # inflating the "Nth of year" preview number above the true, real count.
        conn_check = get_conn()
        new_hits = [
            row for row in hits
            if not already_tweeted(conn_check, row["game_id"], row["player_id"])
        ]
        conn_check.close()
        already_recorded_count = len(hits) - len(new_hits)

        if new_hits:
            print(f"\n{d}: {len(pks)} games, {day_rows} batter lines -- {len(new_hits)} first-ever line(s) found"
                  + (f" ({already_recorded_count} already recorded, skipped)" if already_recorded_count else ""))
            for row in new_hits:
                year = int(row["game_date"][:4])
                if year not in year_counts:
                    year_counts[year] = count_tweeted_this_year(conn, year)
                year_counts[year] += 1
                total_unique = count_unique_lines(conn)
                print("  " + draft_tweet(row, year_counts[year], total_unique).replace("\n\n", " | ").replace("\n", " "))
            total_hits += len(new_hits)
        elif already_recorded_count:
            print(f"{d}: {len(pks)} games, {day_rows} batter lines -- "
                  f"{already_recorded_count} line(s) found but already recorded (nothing new to preview)")
        else:
            # Mirrors pipeline.py's real behavior on a day with zero real hits:
            # show what the "No Battergami" fallback would have posted.
            closest = find_closest_call(d)
            if closest:
                print(f"{d}: {len(pks)} games, {day_rows} batter lines -- nothing new. Closest call:")
                print("  " + draft_no_battergami_tweet(closest).replace("\n\n", " | ").replace("\n", " "))
                total_closest_calls += 1
            else:
                print(f"{d}: {len(pks)} games, {day_rows} batter lines -- nothing new, no closest call available either")
        conn.close()

    print(f"\nDone. {total_games} games processed, {total_hits} first-ever lines flagged, "
          f"{total_closest_calls} closest-call fallback(s) shown.")
    print("Remember: this window has almost no history behind it yet, so early days")
    print("will over-flag lines that are actually common -- that's expected, not a bug.")
    print("The model gets more accurate as more history (esp. the Retrosheet backfill) is loaded.")
    print("\nNOTHING in this script ever posts to X -- it only prints. Safe to run anytime.")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        start = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])
    else:
        start = date(2019, 4, 1)
        end = date(2019, 4, 14)
    main(start, end)