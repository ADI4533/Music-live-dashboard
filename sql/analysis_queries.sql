-- Reference queries. The dashboard runs equivalents of these through
-- pandas.read_sql; kept here as plain SQL because that's what you'll be
-- asked to write on a whiteboard, not the pandas wrapper.

-- 1. Top artists by play count
SELECT a.artist_name, COUNT(*) AS play_count
FROM fact_plays p
JOIN dim_tracks t  ON p.track_id = t.track_id
JOIN dim_artists a ON t.artist_id = a.artist_id
GROUP BY a.artist_name
ORDER BY play_count DESC
LIMIT 10;

-- 2. Daily play count -- the input series for binge detection
SELECT DATE(played_at) AS day, COUNT(*) AS plays
FROM fact_plays
GROUP BY DATE(played_at)
ORDER BY day;

-- 3. Binge-day detection via z-score
-- (a day where play count is >2 standard deviations above your own mean)
WITH daily AS (
    SELECT DATE(played_at) AS day, COUNT(*) AS plays
    FROM fact_plays
    GROUP BY DATE(played_at)
),
stats AS (
    SELECT AVG(plays) AS mean_plays,
           -- SQLite has no native STDDEV; computed in the dashboard with
           -- pandas instead. On Postgres, use STDDEV_POP(plays) here directly.
           1 AS placeholder
    FROM daily
)
SELECT day, plays FROM daily ORDER BY day;

-- 4. Week-over-week listening volume shift
SELECT
    STRFTIME('%Y-%W', played_at) AS iso_week,
    COUNT(*) AS plays,
    COUNT(DISTINCT artist_id) AS distinct_artists  -- requires join to dim_tracks in practice
FROM fact_plays p
GROUP BY iso_week
ORDER BY iso_week;

-- 5. How much has your top-5 changed between the earliest and latest snapshot?
WITH ranked AS (
    SELECT item_key, name, rank, pulled_at,
           ROW_NUMBER() OVER (PARTITION BY item_key ORDER BY pulled_at ASC)  AS rn_first,
           ROW_NUMBER() OVER (PARTITION BY item_key ORDER BY pulled_at DESC) AS rn_last
    FROM fact_top_items_snapshot
    WHERE item_type = 'artists'
)
SELECT name,
       MAX(CASE WHEN rn_first = 1 THEN rank END) AS earliest_rank,
       MAX(CASE WHEN rn_last  = 1 THEN rank END) AS latest_rank
FROM ranked
GROUP BY name
ORDER BY latest_rank;
