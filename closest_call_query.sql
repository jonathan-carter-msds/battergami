-- closest_call_query.sql
-- Used ONLY when no genuine battergami was found for a completed day.
-- Finds the single rarest stat line among that day's games: the one that has
-- occurred the fewest times in history, tie-broken by the longest gap since
-- it last happened. This powers the "No Battergami" fallback tweet, mirroring
-- pitchergami's "No Pitchergami" posts (e.g. "thrown 1 time before, last on
-- 1940-05-05 by Emery Adams").

WITH new_games AS (
    SELECT * FROM batter_game_lines WHERE game_date = :target_date
),

candidate_games AS (
    SELECT * FROM new_games WHERE ab > 0 OR bb > 0 OR hbp > 0
),

scored AS (
    SELECT
        c.*,
        (
            SELECT COUNT(*) FROM batter_game_lines hist
            WHERE hist.game_date < c.game_date
              AND hist.ab = c.ab AND hist.r = c.r AND hist.h = c.h
              AND hist.doubles = c.doubles AND hist.triples = c.triples
              AND hist.hr = c.hr AND hist.bb = c.bb AND hist.so = c.so
              AND hist.rbi = c.rbi AND hist.sb = c.sb
        ) AS occurrence_count,
        (
            SELECT MAX(hist.game_date) FROM batter_game_lines hist
            WHERE hist.game_date < c.game_date
              AND hist.ab = c.ab AND hist.r = c.r AND hist.h = c.h
              AND hist.doubles = c.doubles AND hist.triples = c.triples
              AND hist.hr = c.hr AND hist.bb = c.bb AND hist.so = c.so
              AND hist.rbi = c.rbi AND hist.sb = c.sb
        ) AS last_occurrence_date,
        (
            SELECT hist.player_name FROM batter_game_lines hist
            WHERE hist.game_date < c.game_date
              AND hist.ab = c.ab AND hist.r = c.r AND hist.h = c.h
              AND hist.doubles = c.doubles AND hist.triples = c.triples
              AND hist.hr = c.hr AND hist.bb = c.bb AND hist.so = c.so
              AND hist.rbi = c.rbi AND hist.sb = c.sb
            ORDER BY hist.game_date DESC LIMIT 1
        ) AS last_occurrence_player
    FROM candidate_games c
)

-- occurrence_count > 0 is the key filter: if it were 0, this would already
-- have been caught as a genuine battergami by detection_query.sql instead.
SELECT *
FROM scored
WHERE occurrence_count > 0
ORDER BY occurrence_count ASC, last_occurrence_date ASC
LIMIT 1;
