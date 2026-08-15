-- Reference schema (Postgres-flavored). The Python load layer creates the
-- SQLite-compatible equivalent of this automatically via SQLAlchemy -- use
-- this file if you spin up real Postgres (Supabase/Neon free tier) for the
-- "production-shaped" version of the project.

CREATE TABLE IF NOT EXISTS dim_artists (
    artist_id   VARCHAR(64) PRIMARY KEY,
    artist_name VARCHAR(255) NOT NULL,
    genres      TEXT  -- JSON-encoded list; left empty in this build --
                       -- Last.fm's getTopArtists doesn't include genre
                       -- tags, that needs a separate artist.getTopTags
                       -- call per artist (easy follow-on extension)
);

CREATE TABLE IF NOT EXISTS dim_tracks (
    track_id    VARCHAR(64) PRIMARY KEY,
    track_name  VARCHAR(255) NOT NULL,
    artist_id   VARCHAR(64) REFERENCES dim_artists(artist_id),
    album_name  VARCHAR(255),
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS fact_plays (
    play_id     BIGSERIAL PRIMARY KEY,
    track_id    VARCHAR(64) REFERENCES dim_tracks(track_id),
    played_at   TIMESTAMP NOT NULL,
    source_file VARCHAR(255),
    ingested_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_track_played UNIQUE (track_id, played_at)
    -- natural key: recently-played pulls overlap on purpose (last ~50
    -- every time), this constraint is what makes re-running ingestion
    -- idempotent instead of creating duplicate plays
);

CREATE TABLE IF NOT EXISTS fact_top_items_snapshot (
    snapshot_id BIGSERIAL PRIMARY KEY,
    item_type   VARCHAR(16) NOT NULL,   -- 'tracks' or 'artists'
    item_key  VARCHAR(64) NOT NULL,
    name        VARCHAR(255) NOT NULL,
    rank        INTEGER NOT NULL,
    time_range  VARCHAR(16) NOT NULL,   -- short_term / medium_term / long_term
    pulled_at   TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_plays_played_at ON fact_plays(played_at);
CREATE INDEX IF NOT EXISTS idx_fact_plays_track ON fact_plays(track_id);
