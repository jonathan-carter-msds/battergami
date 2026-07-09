"""
battergami pipeline
--------------------
Daily job:
  1. Pull yesterday's completed MLB box scores from the MLB Stats API (free, no key needed).
  2. Insert each batter's game line into the same table the Retrosheet backfill uses.
  3. Run detection_query.sql to find lines that have never occurred before.
  4. Draft tweet text for each hit.
  5. Post to X via the v2 API (tweepy), skipping anything already posted.

This uses sqlite3 for simplicity. Swap `get_conn()` for psycopg2 and change the
two `?` placeholders to `%s` if you move to Postgres for a larger deployment --
the detection query itself is portable SQL either way.

Requirements:
    pip install requests tweepy

Environment variables (set these before running):
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
"""

import os
import sqlite3
import sys
from datetime import date, timedelta

import requests

DB_PATH = os.environ.get("BATTERGAMI_DB", "battergami.db")
MLB_API = "https://statsapi.mlb.com/api/v1"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 1. Ingestion from the MLB Stats API
# ---------------------------------------------------------------------------

# Maps MLB Stats API's short gameType codes to Retrosheet's naming convention,
# so the same game_type value means the same thing regardless of which source
# a row came from. Based on commonly-documented MLB gameType codes; unrecognized
# codes are kept as-is (lowercased) rather than silently dropped, so anything
# unexpected is still visible in the data instead of being masked.
GAME_TYPE_MAP = {
    "R": "regular",
    "A": "allstar",
    "D": "divisionseries",
    "L": "lcs",
    "W": "worldseries",
    "F": "wildcard",
    "S": "preseason",
    "E": "exhibition",
}


def normalize_game_type(raw: str) -> str:
    if not raw:
        return "regular"
    return GAME_TYPE_MAP.get(raw, raw.lower())


def fetch_game_pks(target_date: date) -> list[tuple[int, str]]:
    """Get every (gamePk, game_type) pair for a given date.

    game_type is normalized here (see normalize_game_type) since this is the
    only place the MLB API's raw gameType code is available -- the boxscore
    endpoint used later doesn't include it.
    """
    resp = requests.get(
        f"{MLB_API}/schedule",
        params={"sportId": 1, "date": target_date.isoformat()},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") == "Final":
                games.append((g["gamePk"], normalize_game_type(g.get("gameType", "R"))))
    return games


def fetch_and_store_boxscore(conn: sqlite3.Connection, game_pk: int, game_date: date, game_type: str = "regular"):
    resp = requests.get(f"{MLB_API}/game/{game_pk}/boxscore", timeout=30)
    resp.raise_for_status()
    box = resp.json()

    rows = []
    home_abbr = box["teams"]["home"]["team"]["abbreviation"]
    away_abbr = box["teams"]["away"]["team"]["abbreviation"]

    for side in ("home", "away"):
        team_info = box["teams"][side]
        team_name = team_info["team"]["abbreviation"]
        for player_id, player in team_info.get("players", {}).items():
            stats = player.get("stats", {}).get("batting")
            if not stats or stats.get("atBats") is None:
                continue  # didn't bat (e.g. pitcher who didn't hit, DNP)
            rows.append((
                str(game_pk),
                game_date.isoformat(),
                game_date.year,
                str(player["person"]["id"]),
                player["person"]["fullName"],
                team_name,
                home_abbr,
                away_abbr,
                stats.get("atBats", 0),
                stats.get("runs", 0),
                stats.get("hits", 0),
                stats.get("doubles", 0),
                stats.get("triples", 0),
                stats.get("homeRuns", 0),
                stats.get("rbi", 0),
                stats.get("baseOnBalls", 0),
                stats.get("strikeOuts", 0),
                stats.get("stolenBases", 0),
                stats.get("caughtStealing", 0),
                stats.get("hitByPitch", 0),
                stats.get("sacFlies", 0),
                stats.get("sacBunts", 0),
                stats.get("groundIntoDoublePlay", 0),
                "mlb_api",
                game_type,
            ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO batter_game_lines
        (game_id, game_date, season, player_id, player_name, team, home_team, away_team,
         ab, r, h, doubles, triples, hr, rbi, bb, so, sb, cs, hbp, sf, sh, gidp, source, game_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()

    return len(rows)


def date_already_covered_by_retrosheet(conn: sqlite3.Connection, target_date: date) -> bool:
    """True if the historical backfill already has Retrosheet data for this date.

    Guards against double-counting: Retrosheet and the MLB API use different
    game_id formats and sometimes slightly different names/team codes for the
    same real game, so if both sources' rows for the same date end up in the
    table, a game can look "new" twice and get flagged/tweeted twice. Since
    Retrosheet's backfill always lags real time by months, this situation only
    arises if you (re)run a Retrosheet load that catches up to a date the daily
    MLB API job already ingested -- at that point Retrosheet's data should win,
    and the MLB API version for that date should not be (re)inserted.
    """
    row = conn.execute(
        "SELECT 1 FROM batter_game_lines WHERE game_date = ? AND source = 'retrosheet' LIMIT 1",
        (target_date.isoformat(),),
    ).fetchone()
    return row is not None


def ingest_day(target_date: date) -> set[str]:
    """Ingests a day's completed games and returns the set of game_ids
    (as strings, matching batter_game_lines.game_id) that were just
    ingested in THIS call -- used to scope the hourly closest-call check to
    only freshly-completed games, not the whole day's accumulated pool."""
    conn = get_conn()
    if date_already_covered_by_retrosheet(conn, target_date):
        print(
            f"{target_date} is already covered by the Retrosheet backfill -- "
            f"skipping MLB API ingestion for this date to avoid duplicate rows."
        )
        conn.close()
        return set()
    games = fetch_game_pks(target_date)
    total = 0
    for pk, game_type in games:
        total += fetch_and_store_boxscore(conn, pk, target_date, game_type)
    conn.close()
    print(f"Ingested {total} batter lines across {len(games)} games for {target_date}")
    return {str(pk) for pk, _ in games}


# ---------------------------------------------------------------------------
# 2. Detection
# ---------------------------------------------------------------------------

def run_detection(target_date: date) -> list[sqlite3.Row]:
    conn = get_conn()
    with open(os.path.join(os.path.dirname(__file__), "detection_query.sql")) as f:
        query = f.read().replace(":target_date", "?")
    rows = conn.execute(query, (target_date.isoformat(),)).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# 3. Tweet drafting
# ---------------------------------------------------------------------------

def count_unique_lines(conn: sqlite3.Connection) -> int:
    """All-time count of distinct stat-line combinations seen in the database."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT DISTINCT ab, r, h, doubles, triples, hr, bb, so, rbi, sb
            FROM batter_game_lines
        )
        """
    ).fetchone()
    return row["n"]


def count_tweeted_this_year(conn: sqlite3.Connection, year: int) -> int:
    """How many battergami events have been posted so far for games in the given year.

    Joins back to batter_game_lines and filters on game_date's year rather than
    tweeted_at, so reprocessing an older date (e.g. during backfill) still
    tallies correctly instead of counting against today's real-world year.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM tweeted_performances tp
        JOIN batter_game_lines bgl
          ON tp.game_id = bgl.game_id AND tp.player_id = bgl.player_id
        WHERE strftime('%Y', bgl.game_date) = ?
        """,
        (str(year),),
    ).fetchone()
    return row["n"]


def draft_tweet(row: sqlite3.Row, nth_this_year: int, total_unique_lines: int) -> str:
    parts = []
    if row["ab"]:
        parts.append(f"{row['ab']} AB")
    if row["r"]:
        parts.append(f"{row['r']} R")
    parts.append(f"{row['h']} H")
    if row["doubles"]:
        parts.append(f"{row['doubles']} 2B")
    if row["triples"]:
        parts.append(f"{row['triples']} 3B")
    if row["hr"]:
        parts.append(f"{row['hr']} HR")
    parts.append(f"{row['rbi']} RBI")
    if row["bb"]:
        parts.append(f"{row['bb']} BB")
    if row["so"]:
        parts.append(f"{row['so']} SO")
    if row["sb"]:
        parts.append(f"{row['sb']} SB")

    line = " | ".join(parts)
    matchup = f"{row['away_team']} @ {row['home_team']}"
    year = row["game_date"][:4]

    return (
        f"\U0001F6A8 BATTERGAMI \U0001F6A8\n\n"
        f"{row['player_name']} just posted a line that has NEVER happened in MLB history:\n\n"
        f"{line}\n\n"
        f"{matchup}\n"
        f"That's the {ordinal(nth_this_year)} Battergami of {year} and 1 of "
        f"{total_unique_lines:,}+ unique lines on record."
    )


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# 4. Posting to X
# ---------------------------------------------------------------------------

def post_tweet(text: str) -> str:
    import tweepy

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"],
    )
    resp = client.create_tweet(text=text)
    return str(resp.data["id"])


def already_tweeted(conn: sqlite3.Connection, game_id: str, player_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tweeted_performances WHERE game_id = ? AND player_id = ?",
        (game_id, player_id),
    ).fetchone()
    return row is not None


def mark_tweeted(conn, game_id, player_id, tweet_id, tweet_text):
    conn.execute(
        """INSERT OR REPLACE INTO tweeted_performances
           (game_id, player_id, tweet_id, tweet_text) VALUES (?,?,?,?)""",
        (game_id, player_id, tweet_id, tweet_text),
    )
    conn.commit()


CLOSEST_CALLS_PER_DAY = 5  # how many ranked near-misses to post on a day with zero real battergamis


def find_closest_calls(target_date: date, limit: int = CLOSEST_CALLS_PER_DAY):
    """Finds the top N rarest stat lines among a day's games, ranked by
    rarity (fewest prior occurrences, then longest time since it last
    happened). Only meaningful to call on a day that's already been
    confirmed to have zero genuine battergamis.

    Uses the SAME full-day comparison pool as before -- this is not scoped
    down to individual games, which would make every result less rare, not
    more. It's simply not capped at just the single best one anymore.
    """
    conn = get_conn()
    with open(os.path.join(os.path.dirname(__file__), "closest_call_query.sql")) as f:
        query = f.read().replace(":target_date", "?")
    rows = conn.execute(query, (target_date.isoformat(),)).fetchall()
    conn.close()
    return rows[:limit]


def already_tweeted_closest_call(conn: sqlite3.Connection, game_id: str, player_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM closest_call_tweeted WHERE game_id = ? AND player_id = ?",
        (game_id, player_id),
    ).fetchone()
    return row is not None


def mark_tweeted_closest_call(conn, game_id, player_id, tweet_id, tweet_text):
    conn.execute(
        """INSERT OR REPLACE INTO closest_call_tweeted
           (game_id, player_id, tweet_id, tweet_text) VALUES (?,?,?,?)""",
        (game_id, player_id, tweet_id, tweet_text),
    )
    conn.commit()


def draft_no_battergami_tweet(row: sqlite3.Row, rank: int = 1) -> str:
    parts = []
    if row["ab"]:
        parts.append(f"{row['ab']} AB")
    if row["r"]:
        parts.append(f"{row['r']} R")
    parts.append(f"{row['h']} H")
    if row["doubles"]:
        parts.append(f"{row['doubles']} 2B")
    if row["triples"]:
        parts.append(f"{row['triples']} 3B")
    if row["hr"]:
        parts.append(f"{row['hr']} HR")
    parts.append(f"{row['rbi']} RBI")
    if row["bb"]:
        parts.append(f"{row['bb']} BB")
    if row["so"]:
        parts.append(f"{row['so']} SO")
    if row["sb"]:
        parts.append(f"{row['sb']} SB")

    line = " | ".join(parts)
    matchup = f"{row['away_team']} @ {row['home_team']}"
    times = row["occurrence_count"]
    times_word = "time" if times == 1 else "times"

    header = "NO BATTERGAMI." if rank == 1 else f"CLOSEST CALL #{rank}."

    return (
        f"{header}\n\n"
        f"{row['player_name']} | {matchup}\n\n"
        f"{line}\n\n"
        f"This stat line has been posted {times} {times_word} before. "
        f"The last time this happened was on {row['last_occurrence_date']} "
        f"by {row['last_occurrence_player']}."
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_day(
    target_date: date,
    dry_run: bool = False,
    include_closest_call: bool = False,
    batch_only_closest_call: bool = False,
):
    """
    batch_only_closest_call controls WHICH games the closest-call fallback
    can draw from, and only matters when include_closest_call is True:

    - False (used by the once-daily 5 AM safety-net run): scope is the
      WHOLE day, posting up to CLOSEST_CALLS_PER_DAY ranked results -- a
      comprehensive recap of a fully-completed day.
    - True (used by the hourly intraday runs): scope is ONLY the games
      ingested in THIS run, posting just the single best of that fresh
      batch -- more frequent, smaller posts throughout the day, so
      different players surface at different times rather than everything
      landing in one batch at 5 AM. Rarity is still measured against full
      history either way; only which candidates are eligible changes.
    """
    new_game_ids = ingest_day(target_date)
    hits = run_detection(target_date)

    conn = get_conn()
    new_hits = [row for row in hits if not already_tweeted(conn, row["game_id"], row["player_id"])]
    conn.close()

    if new_hits:
        conn = get_conn()
        year_counts = {}  # local tally so dry-run mode still shows realistic numbers
        for row in new_hits:
            year = int(row["game_date"][:4])
            if year not in year_counts:
                year_counts[year] = count_tweeted_this_year(conn, year)
            year_counts[year] += 1
            nth = year_counts[year]
            total_unique = count_unique_lines(conn)
            text = draft_tweet(row, nth, total_unique)
            if row["same_day_ties"] > 1:
                text += "\n\n(Multiple players matched this exact line today.)"
            print("---")
            print(text)
            if dry_run:
                continue
            tweet_id = post_tweet(text)
            mark_tweeted(conn, row["game_id"], row["player_id"], tweet_id, text)
        conn.close()
        return  # a real battergami takes priority -- no closest-call fallback this run

    print(f"No new never-before-seen batting lines found for {target_date} (this run).")
    if not include_closest_call:
        return

    closest_calls = find_closest_calls(target_date)
    if batch_only_closest_call:
        closest_calls = [row for row in closest_calls if row["game_id"] in new_game_ids]
        closest_calls = closest_calls[:1]  # just the single best from this fresh batch
    if not closest_calls:
        print("No candidate games available to report a closest call for either.")
        return

    conn = get_conn()
    posted_count = 0
    for i, row in enumerate(closest_calls, start=1):
        if already_tweeted_closest_call(conn, row["game_id"], row["player_id"]):
            continue
        text = draft_no_battergami_tweet(row, rank=i)
        print("---")
        print(text)
        if dry_run:
            continue
        tweet_id = post_tweet(text)
        mark_tweeted_closest_call(conn, row["game_id"], row["player_id"], tweet_id, text)
        posted_count += 1
    conn.close()
    if not dry_run:
        print(f"\nPosted {posted_count} closest-call tweet(s) for {target_date}.")



if __name__ == "__main__":
    # Run for "yesterday" by default -- box scores are final by the time this
    # would run overnight via cron. Pass a date (YYYY-MM-DD) as an arg to
    # backfill, reprocess a specific day, or check today's games intraday,
    # and --dry-run to print without posting.
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not os.path.exists(DB_PATH):
        init_db()

    if args:
        target = date.fromisoformat(args[0])
        # An explicit date means this is an intraday check (e.g. the hourly
        # cron entry passing today's date as games finish). A real battergami
        # always takes priority; if this specific run's fresh batch of games
        # didn't produce one, post the single closest call from JUST that
        # batch -- keeps posts spread through the day with different players
        # surfacing over time, rather than everything landing in one big
        # batch at the end of the day.
        include_closest_call = True
        batch_only_closest_call = True
    else:
        target = date.today() - timedelta(days=1)
        # No date passed means this is the once-daily safety-net run over a
        # fully completed day (e.g. the 5 AM cron entry) -- a comprehensive
        # recap covering the WHOLE day, up to CLOSEST_CALLS_PER_DAY ranked
        # results, catching anything the hourly runs might have missed.
        include_closest_call = True
        batch_only_closest_call = False

    run_day(
        target,
        dry_run=dry_run,
        include_closest_call=include_closest_call,
        batch_only_closest_call=batch_only_closest_call,
    )