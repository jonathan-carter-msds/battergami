"""
build_site.py
-------------
Regenerates the static data files that power the battergami dashboard site
(the `site/` directory). Reads battergami.db, writes site/data/*.json.

There is no web backend: every distinct stat line in ~125 years of MLB history
fits in one JSON file (~35k rows), so "has this line ever happened" is answered
entirely in the browser. This script just precomputes the aggregates.

Run it after the daily pipeline, then commit `site/data/` and push -- Netlify
(publish dir = `site/`) redeploys automatically. See publish_site.sh.

    python build_site.py                 # uses ./battergami.db
    BATTERGAMI_DB=/path/to.db python build_site.py

Stdlib only. Safe to run any time; it only reads the database.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone

DB_PATH = os.environ.get("BATTERGAMI_DB", "battergami.db")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "data")

# Handle-agnostic status URLs (x.com/i/status/<id> redirects to the real handle),
# so the site keeps working even if the account is ever renamed.
TWEET_URL = "https://x.com/i/status/{tweet_id}"

# The 10 columns that define a "performance vector", in tweet display order.
LINE_COLS = ["ab", "r", "h", "doubles", "triples", "hr", "bb", "so", "rbi", "sb"]
LINE_LABELS = ["AB", "R", "H", "2B", "3B", "HR", "BB", "SO", "RBI", "SB"]


def get_conn() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        sys.exit(f"database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(name: str, payload: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    size = os.path.getsize(path)
    print(f"  wrote {name:20s} {size/1024:8.1f} KB")


# ---------------------------------------------------------------------------
# lines.json -- one row per distinct stat line, with count + first/last context.
# Powers Check a Line, Board, Random, and the historical Calendar.
# ---------------------------------------------------------------------------

def build_lines(conn: sqlite3.Connection) -> list:
    print("building lines.json ...")
    sel = ",".join(LINE_COLS)
    # Same "not a trivial no-op appearance" guard the detection query uses.
    guard = "ab > 0 OR bb > 0 OR hbp > 0"

    agg = conn.execute(
        f"""
        SELECT {sel}, COUNT(*) AS c,
               MIN(game_date) AS first_date, MAX(game_date) AS last_date
        FROM batter_game_lines
        WHERE {guard}
        GROUP BY {sel}
        """
    ).fetchall()

    # Attach the player/matchup for the first occurrence and the player for the
    # most recent occurrence. One indexed lookup per vector (~35k); a few seconds.
    where_vec = " AND ".join(f"{c} = ?" for c in LINE_COLS)
    first_stmt = (
        f"SELECT player_name, away_team, home_team FROM batter_game_lines "
        f"WHERE game_date = ? AND {where_vec} ORDER BY game_id LIMIT 1"
    )
    last_stmt = (
        f"SELECT player_name FROM batter_game_lines "
        f"WHERE game_date = ? AND {where_vec} ORDER BY game_id LIMIT 1"
    )

    rows = []
    for r in agg:
        vec = [r[c] for c in LINE_COLS]
        f = conn.execute(first_stmt, [r["first_date"], *vec]).fetchone()
        l = conn.execute(last_stmt, [r["last_date"], *vec]).fetchone()
        matchup = f"{f['away_team']} @ {f['home_team']}" if f and f["home_team"] else ""
        rows.append([
            *vec,
            r["c"],
            r["first_date"],
            f["player_name"] if f else "",
            matchup,
            r["last_date"],
            l["player_name"] if l else "",
        ])

    rows.sort(key=lambda x: (x[10], x[0:10]))  # by first_date, then vector
    print(f"  {len(rows):,} distinct stat lines")
    return rows


LINES_FIELDS = LINE_COLS + ["count", "first_date", "first_player", "first_matchup",
                            "last_date", "last_player"]


# ---------------------------------------------------------------------------
# leaderboard.json -- batters ranked by how many distinct lines they were the
# FIRST to record (the "most all-time unique lines" stat, era-weighted).
# ---------------------------------------------------------------------------

def build_leaderboard(conn: sqlite3.Connection, top_n: int = 300) -> list:
    print("building leaderboard.json ...")
    sel = ",".join(LINE_COLS)
    on = " AND ".join(f"b.{c} = f.{c}" for c in LINE_COLS)
    rows = conn.execute(
        f"""
        WITH firsts AS (
            SELECT {sel}, MIN(game_date) AS fd
            FROM batter_game_lines
            WHERE ab > 0 OR bb > 0 OR hbp > 0
            GROUP BY {sel}
        )
        SELECT b.player_name AS name, COUNT(*) AS n,
               MIN(f.fd) AS first, MAX(f.fd) AS last
        FROM firsts f
        JOIN batter_game_lines b
          ON b.game_date = f.fd AND {on}
        GROUP BY b.player_name
        ORDER BY n DESC, b.player_name ASC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()
    print(f"  top batter: {rows[0]['name']} ({rows[0]['n']})")
    return [[r["name"], r["n"], r["first"][:4], r["last"][:4]] for r in rows]


# ---------------------------------------------------------------------------
# calendar.json -- how many stat lines entered history on each date.
# ---------------------------------------------------------------------------

def build_calendar(lines_rows: list) -> dict:
    print("building calendar.json ...")
    fd_idx = LINES_FIELDS.index("first_date")
    counts: dict[str, int] = {}
    for row in lines_rows:
        d = row[fd_idx]
        counts[d] = counts.get(d, 0) + 1
    print(f"  {len(counts):,} distinct dates")
    return counts


# ---------------------------------------------------------------------------
# latest.json -- what the bot actually posted, newest first.
# ---------------------------------------------------------------------------

_NTH_RE = re.compile(r"(\d+)(?:st|nd|rd|th) Battergami of (\d{4})", re.I)


def _fetch_posted(conn: sqlite3.Connection, table: str, kind: str) -> list:
    try:
        posted = conn.execute(
            f"SELECT game_id, player_id, tweet_id, tweet_text, tweeted_at FROM {table}"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    events = []
    for p in posted:
        g = conn.execute(
            """SELECT game_date, player_name, team, home_team, away_team,
                      ab, r, h, doubles, triples, hr, bb, so, rbi, sb
               FROM batter_game_lines WHERE game_id = ? AND player_id = ?""",
            (p["game_id"], p["player_id"]),
        ).fetchone()
        if not g:
            continue
        nth = year = None
        m = _NTH_RE.search(p["tweet_text"] or "")
        if m:
            nth, year = int(m.group(1)), int(m.group(2))
        events.append({
            "type": kind,
            "date": g["game_date"],
            "player": g["player_name"],
            "team": g["team"],
            "matchup": f"{g['away_team']} @ {g['home_team']}" if g["home_team"] else "",
            "line": [g[c] for c in LINE_COLS],
            "nth": nth,
            "year": year,
            "tweet_id": p["tweet_id"],
            "tweet_url": TWEET_URL.format(tweet_id=p["tweet_id"]) if p["tweet_id"] else None,
            "posted_at": p["tweeted_at"],
        })
    return events


def build_latest(conn: sqlite3.Connection, per_type: int = 60) -> list:
    print("building latest.json ...")
    bg = _fetch_posted(conn, "tweeted_performances", "battergami") \
        + _fetch_posted(conn, "allstar_tweeted_performances", "battergami")
    cc = _fetch_posted(conn, "closest_call_tweeted", "closest_call")
    key = lambda e: (e["date"], e["posted_at"] or "")
    bg.sort(key=key, reverse=True)
    cc.sort(key=key, reverse=True)
    events = bg[:per_type] + cc[:per_type]
    events.sort(key=key, reverse=True)  # merged, newest game first
    print(f"  {len(bg)} battergami + {len(cc)} closest-call posts")
    return events


# ---------------------------------------------------------------------------
# summary.json -- headline numbers for the landing page.
# ---------------------------------------------------------------------------

def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
    except sqlite3.OperationalError:
        return 0


def build_summary(conn: sqlite3.Connection, lines_rows: list, latest: list) -> dict:
    print("building summary.json ...")
    span = conn.execute(
        "SELECT MIN(game_date) a, MAX(game_date) b FROM batter_game_lines"
    ).fetchone()
    total_games = conn.execute(
        "SELECT COUNT(*) n FROM batter_game_lines"
    ).fetchone()["n"]

    # All-time genuine battergami posts, counted directly (latest[] is capped).
    posted_total = (_count(conn, "tweeted_performances")
                    + _count(conn, "allstar_tweeted_performances"))
    genuine = [e for e in latest if e["type"] == "battergami"]
    this_year = date.today().year
    season_count = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM tweeted_performances tp
        JOIN batter_game_lines bgl
          ON tp.game_id = bgl.game_id AND tp.player_id = bgl.player_id
        WHERE strftime('%Y', bgl.game_date) = ?
        """,
        (str(this_year),),
    ).fetchone()["n"]

    return {
        "generated_at": now_iso(),
        "distinct_lines": len(lines_rows),
        "total_games_scanned": total_games,
        "coverage_start": span["a"],
        "coverage_end": span["b"],
        "season_year": this_year,
        "season_count": season_count,
        "posted_total": posted_total,
        "last_event": genuine[0] if genuine else None,
    }


# ---------------------------------------------------------------------------

def main() -> None:
    print(f"reading {DB_PATH}")
    conn = get_conn()
    try:
        lines_rows = build_lines(conn)
        leaderboard = build_leaderboard(conn)
        calendar = build_calendar(lines_rows)
        latest = build_latest(conn)
        summary = build_summary(conn, lines_rows, latest)
    finally:
        conn.close()

    gen = now_iso()
    write_json("lines.json", {"generated_at": gen, "fields": LINES_FIELDS,
                              "labels": LINE_LABELS, "rows": lines_rows})
    write_json("leaderboard.json", {"generated_at": gen,
                                    "fields": ["name", "count", "first_year", "last_year"],
                                    "rows": leaderboard})
    write_json("calendar.json", {"generated_at": gen, "counts": calendar})
    write_json("latest.json", {"generated_at": gen, "events": latest})
    write_json("summary.json", summary)
    print("done.")


if __name__ == "__main__":
    main()
