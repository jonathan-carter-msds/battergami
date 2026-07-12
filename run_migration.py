"""
run_migration.py
------------------
Safely applies migrate_add_game_type.sql against your live battergami.db.
Safe to run even if you already applied this migration earlier -- it won't
error out on a column that already exists, it'll just skip that part and
apply anything new (like the closest_call_tweeted table added later).

Usage:
    python run_migration.py
"""

import sqlite3
import os

DB_PATH = os.environ.get("BATTERGAMI_DB", "battergami.db")

STATEMENTS = [
    "ALTER TABLE batter_game_lines ADD COLUMN game_type TEXT NOT NULL DEFAULT 'regular';",
    "CREATE INDEX IF NOT EXISTS idx_bgl_game_type ON batter_game_lines (game_type);",
    """CREATE TABLE IF NOT EXISTS allstar_tweeted_performances (
        game_id        TEXT NOT NULL,
        player_id      TEXT NOT NULL,
        tweeted_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        tweet_id       TEXT,
        tweet_text     TEXT,
        PRIMARY KEY (game_id, player_id)
    );""",
    """CREATE TABLE IF NOT EXISTS closest_call_tweeted (
        game_id        TEXT NOT NULL,
        player_id      TEXT NOT NULL,
        tweeted_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        tweet_id       TEXT,
        tweet_text     TEXT,
        PRIMARY KEY (game_id, player_id)
    );""",
]


def main():
    conn = sqlite3.connect(DB_PATH)
    applied, skipped = 0, 0
    for stmt in STATEMENTS:
        try:
            conn.execute(stmt)
            applied += 1
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"Already applied, skipping: {stmt.strip().splitlines()[0][:60]}...")
                skipped += 1
            else:
                raise
    conn.commit()

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()

    print(f"\nDone. {applied} statement(s) applied, {skipped} already present and skipped.")
    print("Tables now present:", ", ".join(tables))


if __name__ == "__main__":
    main()
    