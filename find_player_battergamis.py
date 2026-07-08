"""
find_player_battergamis.py
---------------------------
Answers a different question than anything else in this project: not "has
this line ever happened," but "across this player's ENTIRE career, which of
their games (if any) had a stat line that had never happened before, at the
time they did it."

This scans every game the named player appears in, and for each one, checks
whether that exact 10-stat line existed in any earlier game by ANY player.
If not, that game would have been a genuine battergami had the bot existed
back then.

Usage:
    python find_player_battergamis.py "Player_Name" -> example: python find_player_battergamis.py "Jimmy Rollins"

Read-only. Doesn't touch tweeted_performances, doesn't post anything.
"""

import sys
import sqlite3
import os

DB_PATH = os.environ.get("BATTERGAMI_DB", "battergami.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_player_battergamis(player_name: str):
    conn = get_conn()

    # Confirm the player actually exists in the data before doing the expensive scan,
    # and show what name variants matched (helps catch typos or multiple spellings)
    matches = conn.execute(
        "SELECT DISTINCT player_name, player_id FROM batter_game_lines WHERE player_name LIKE ?",
        (f"%{player_name}%",),
    ).fetchall()

    if not matches:
        print(f"No player found matching '{player_name}'. Check spelling, or try a shorter fragment (e.g. last name only).")
        conn.close()
        return

    if len(matches) > 1:
        print(f"Multiple players matched '{player_name}':")
        for m in matches:
            print(f"  - {m['player_name']} ({m['player_id']})")
        print("Narrow your search to match exactly one player.\n")

    for m in matches:
        name = m["player_name"]
        pid = m["player_id"]
        total_games = conn.execute(
            "SELECT COUNT(*) AS n FROM batter_game_lines WHERE player_id = ?", (pid,)
        ).fetchone()["n"]

        print(f"\n=== Scanning {name} ({pid}) -- {total_games} career games on record ===")

        rows = conn.execute(
            """
            SELECT bgl.*
            FROM batter_game_lines bgl
            WHERE bgl.player_id = ?
              AND (bgl.ab > 0 OR bgl.bb > 0 OR bgl.hbp > 0)
              AND NOT EXISTS (
                  SELECT 1 FROM batter_game_lines hist
                  WHERE hist.game_date < bgl.game_date
                    AND hist.ab = bgl.ab AND hist.r = bgl.r AND hist.h = bgl.h
                    AND hist.doubles = bgl.doubles AND hist.triples = bgl.triples
                    AND hist.hr = bgl.hr AND hist.bb = bgl.bb AND hist.so = bgl.so
                    AND hist.rbi = bgl.rbi AND hist.sb = bgl.sb
              )
            ORDER BY bgl.game_date ASC
            """,
            (pid,),
        ).fetchall()

        if not rows:
            print(f"  No battergami-worthy games found for {name} in the data currently loaded.")
            continue

        print(f"  Found {len(rows)} game(s) that would have been a genuine battergami at the time:\n")
        for row in rows:
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
            print(f"  {row['game_date']}  ({row['team']} vs {row['home_team'] if row['team'] != row['home_team'] else row['away_team']})  --  {line}")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python find_player_battergamis.py "Player Name"')
        sys.exit(1)
    find_player_battergamis(" ".join(sys.argv[1:]))