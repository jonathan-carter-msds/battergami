-- detection_query.sql
-- Finds batter game lines from a target date that have never occurred
-- in any earlier game in the database.
--
-- Usage: substitute :target_date (e.g. '2026-07-05'). In psycopg2/sqlite3
-- this is passed as a parameter from pipeline.py — see run_detection().
--
-- The "performance vector" is (ab, h, doubles, triples, hr, bb, so, rbi, sb).
-- Two lines are considered the same performance if all nine values match
-- exactly, regardless of player, team, or era.

WITH new_games AS (
    SELECT *
    FROM batter_game_lines
    WHERE game_date = :target_date
),

-- guard against trivial "everything zero" lines (e.g. a pinch-hit walk-off
-- appearance with 0 AB, 0 BB, 0 HBP shouldn't count as a "performance")
candidate_games AS (
    SELECT *
    FROM new_games
    WHERE ab > 0 OR bb > 0 OR hbp > 0
),

first_ever AS (
    SELECT c.*
    FROM candidate_games c
    WHERE NOT EXISTS (
        SELECT 1
        FROM batter_game_lines hist
        WHERE hist.game_date < c.game_date
          AND hist.ab       = c.ab
          AND hist.r        = c.r
          AND hist.h        = c.h
          AND hist.doubles  = c.doubles
          AND hist.triples  = c.triples
          AND hist.hr       = c.hr
          AND hist.bb       = c.bb
          AND hist.so       = c.so
          AND hist.rbi      = c.rbi
          AND hist.sb       = c.sb
    )
)

-- de-duplicate against same-day teammates who happened to post the identical
-- new line — keep every one of them, but tag ties so pipeline.py can decide
-- whether to send one tweet or a thread
SELECT
    game_id,
    game_date,
    player_id,
    player_name,
    team,
    home_team,
    away_team,
    ab, r, h, doubles, triples, hr, rbi, bb, so, sb,
    COUNT(*) OVER (
        PARTITION BY ab, r, h, doubles, triples, hr, bb, so, rbi, sb
    ) AS same_day_ties
FROM first_ever
ORDER BY player_name;
