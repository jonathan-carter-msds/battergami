-- battergami schema
-- One row per player per game. This is the "performance vector" table
-- that both the historical backfill and the daily ingestion write into.

CREATE TABLE IF NOT EXISTS batter_game_lines (
    game_id        TEXT NOT NULL,          -- retrosheet game id or MLB gamePk
    game_date      DATE NOT NULL,
    season         SMALLINT NOT NULL,
    player_id      TEXT NOT NULL,          -- retrosheet id, mapped to MLBAM id (see id_map table)
    player_name    TEXT NOT NULL,
    team           TEXT NOT NULL,

    -- for building the "AWAY @ HOME" matchup line in tweets
    home_team      TEXT NOT NULL DEFAULT '',
    away_team      TEXT NOT NULL DEFAULT '',

    -- the "stat line" columns used for uniqueness matching
    ab             SMALLINT NOT NULL,
    r              SMALLINT NOT NULL,
    h              SMALLINT NOT NULL,
    doubles        SMALLINT NOT NULL,      -- "2b" is not a valid column name in most engines
    triples        SMALLINT NOT NULL,
    hr             SMALLINT NOT NULL,
    rbi            SMALLINT NOT NULL,
    bb             SMALLINT NOT NULL,
    so             SMALLINT NOT NULL,
    sb             SMALLINT NOT NULL,

    -- kept for context / tweet text, not part of the uniqueness key by default
    cs             SMALLINT NOT NULL DEFAULT 0,
    hbp            SMALLINT NOT NULL DEFAULT 0,
    sf             SMALLINT NOT NULL DEFAULT 0,
    sh             SMALLINT NOT NULL DEFAULT 0,
    gidp           SMALLINT NOT NULL DEFAULT 0,

    source         TEXT NOT NULL DEFAULT 'retrosheet',   -- 'retrosheet' | 'mlb_api'
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (game_id, player_id)
);

-- speeds up the anti-join in detection_query.sql, which filters on this exact tuple
CREATE INDEX IF NOT EXISTS idx_bgl_statline
    ON batter_game_lines (ab, r, h, doubles, triples, hr, bb, so, rbi, sb);

CREATE INDEX IF NOT EXISTS idx_bgl_date ON batter_game_lines (game_date);

-- maps retrosheet ids <-> MLBAM ids, since the two sources use different id schemes
-- populate from the Chadwick "people" register (https://github.com/chadwickbureau/register)
CREATE TABLE IF NOT EXISTS id_map (
    retrosheet_id  TEXT PRIMARY KEY,
    mlbam_id       TEXT UNIQUE,
    full_name      TEXT
);

-- tracks which performances have already been tweeted, so a re-run of the
-- pipeline (e.g. after a crash) never double-posts
CREATE TABLE IF NOT EXISTS tweeted_performances (
    game_id        TEXT NOT NULL,
    player_id      TEXT NOT NULL,
    tweeted_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tweet_id       TEXT,
    tweet_text     TEXT,
    PRIMARY KEY (game_id, player_id)
);

