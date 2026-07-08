"""
load_retrosheet.py
-------------------
Downloads Retrosheet's pre-built CSV data dump (batting.csv + allplayers.csv,
covering 1898-2025) and loads it into the same batter_game_lines table the
daily MLB API pipeline uses. This is the "historical baseline" step -- run it
once (it can take a while), and from then on pipeline.py's daily runs will be
checking new games against genuine MLB history instead of an almost-empty
database.

No Chadwick, no event-file parsing needed -- Retrosheet already publishes a
simplified CSV export (stattype='value' only, i.e. their single best-estimate
line per player per game) at:
    https://www.retrosheet.org/downloads/basiccsvs.zip

Usage:
    python load_retrosheet.py                      # full 1898-2025 history
    python load_retrosheet.py --start-year 2000     # only 2000 onward
    python load_retrosheet.py --start-year 2000 --end-year 2020

Requirements:
    pip install requests   (already installed from the earlier setup)

Notes:
- This can be a sizable download (expect it to take a few minutes; Retrosheet
  doesn't publish an exact file size on the download page). Run it once and
  leave it -- there's a progress counter so you can see it's working.
- Defaults to gametype == 'regular' only, matching how pitchergami-style
  bots typically scope "MLB history." Postseason/all-star/exhibition games are
  skipped; that's easy to change later if you'd rather include them.
"""

import argparse
import csv
import io
import os
import sqlite3
import zipfile

import requests

DB_PATH = os.environ.get("BATTERGAMI_DB", "battergami.db")
CSV_ZIP_URL = "https://www.retrosheet.org/downloads/basiccsvs.zip"
DOWNLOAD_PATH = "retrosheet_basiccsvs.zip"
EXTRACT_DIR = "retrosheet_csvs"


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


def download_and_extract():
    if not os.path.exists(DOWNLOAD_PATH):
        print(f"Downloading {CSV_ZIP_URL} ...")
        print("(This is a one-time download. It may take a few minutes.)")
        resp = requests.get(CSV_ZIP_URL, stream=True, timeout=300)
        resp.raise_for_status()
        downloaded = 0
        with open(DOWNLOAD_PATH, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                print(f"\r  {downloaded / 1_000_000:.0f} MB downloaded", end="", flush=True)
        print("\nDownload complete.")
    else:
        print(f"{DOWNLOAD_PATH} already exists, skipping download.")

    if not os.path.exists(EXTRACT_DIR):
        print(f"Extracting to {EXTRACT_DIR}/ ...")
        with zipfile.ZipFile(DOWNLOAD_PATH) as z:
            z.extractall(EXTRACT_DIR)
        print("Extraction complete.")
    else:
        print(f"{EXTRACT_DIR}/ already exists, skipping extraction.")


def find_csv(name: str) -> str:
    """Locate a CSV file within the extracted directory (Retrosheet sometimes
    nests files a level deep inside the zip)."""
    for root, _, files in os.walk(EXTRACT_DIR):
        if name in files:
            return os.path.join(root, name)
    raise FileNotFoundError(f"Could not find {name} under {EXTRACT_DIR}/")


def load_player_names() -> dict:
    """Build a {retrosheet_id: full_name} lookup from allplayers.csv."""
    path = find_csv("allplayers.csv")
    names = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("id")
            if pid and pid not in names:
                first = (row.get("first") or "").strip()
                last = (row.get("last") or "").strip()
                full = f"{first} {last}".strip()
                names[pid] = full if full else pid
    print(f"Loaded {len(names):,} player names from allplayers.csv")
    return names


def _int(value: str) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def load_batting_rows(player_names: dict, start_year: int, end_year: int, gametype: str):
    """Yields row tuples matching batter_game_lines' insert order."""
    path = find_csv("batting.csv")
    count = 0
    skipped = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", "")
            if not date_str or len(date_str) != 8:
                skipped += 1
                continue
            year = int(date_str[:4])
            if year < start_year or year > end_year:
                continue
            if gametype and row.get("gametype") != gametype:
                continue

            game_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            team = row.get("team", "")
            opp = row.get("opp", "")
            vishome = row.get("vishome", "")
            if vishome == "h":
                home_team, away_team = team, opp
            elif vishome == "v":
                home_team, away_team = opp, team
            else:
                home_team, away_team = team, opp  # fallback, shouldn't normally hit

            player_id = row.get("id", "")
            count += 1
            if count % 200_000 == 0:
                print(f"  ...{count:,} rows processed so far")

            yield (
                row.get("gid", ""),
                game_date,
                year,
                player_id,
                player_names.get(player_id, player_id),
                team,
                home_team,
                away_team,
                _int(row.get("b_ab")),
                _int(row.get("b_r")),
                _int(row.get("b_h")),
                _int(row.get("b_d")),
                _int(row.get("b_t")),
                _int(row.get("b_hr")),
                _int(row.get("b_rbi")),
                _int(row.get("b_w")),
                _int(row.get("b_k")),
                _int(row.get("b_sb")),
                _int(row.get("b_cs")),
                _int(row.get("b_hbp")),
                _int(row.get("b_sf")),
                _int(row.get("b_sh")),
                _int(row.get("b_gdp")),
                "retrosheet",
            )
    print(f"Finished reading batting.csv: {count:,} rows matched filters, {skipped:,} skipped (bad/missing date)")


def main(start_year: int, end_year: int, gametype: str, batch_size: int = 5000):
    init_db()
    download_and_extract()

    player_names = load_player_names()

    conn = get_conn()
    batch = []
    inserted = 0

    for row in load_batting_rows(player_names, start_year, end_year, gametype):
        batch.append(row)
        if len(batch) >= batch_size:
            conn.executemany(
                """
                INSERT OR REPLACE INTO batter_game_lines
                (game_id, game_date, season, player_id, player_name, team, home_team, away_team,
                 ab, r, h, doubles, triples, hr, rbi, bb, so, sb, cs, hbp, sf, sh, gidp, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                batch,
            )
            conn.commit()
            inserted += len(batch)
            print(f"\r  {inserted:,} rows inserted", end="", flush=True)
            batch = []

    if batch:
        conn.executemany(
            """
            INSERT OR REPLACE INTO batter_game_lines
            (game_id, game_date, season, player_id, player_name, team, home_team, away_team,
             ab, r, h, doubles, triples, hr, rbi, bb, so, sb, cs, hbp, sf, sh, gidp, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            batch,
        )
        conn.commit()
        inserted += len(batch)

    print(f"\nDone. {inserted:,} historical batter-game rows loaded into {DB_PATH}.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Retrosheet historical batting data into battergami.db")
    parser.add_argument("--start-year", type=int, default=1898, help="Earliest season to load (default: 1898)")
    parser.add_argument("--end-year", type=int, default=2025, help="Latest season to load (default: 2025)")
    parser.add_argument(
        "--gametype", type=str, default="regular",
        help="Retrosheet gametype to include (default: regular). Pass '' to include all game types.",
    )
    args = parser.parse_args()
    main(args.start_year, args.end_year, args.gametype)