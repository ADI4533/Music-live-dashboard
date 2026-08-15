"""
load_to_db.py

Takes the tidy DataFrames from transform/clean.py and loads them into the
warehouse (SQLite by default, real Postgres if DATABASE_URL points at one --
same code either way, that's the point of using SQLAlchemy Core here).

Idempotency strategy:
    Re-running this script (e.g. after another day's ingestion) must NOT
    duplicate rows. For fact_plays we do that by reading back the
    (track_id, played_at) keys already in the DB and anti-joining before
    insert, rather than relying on DB-specific ON CONFLICT syntax --
    keeps this portable between SQLite and Postgres without two code paths.
"""

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (Column, Integer, MetaData, String, Table,
                         create_engine, inspect, select)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transform.clean import clean_dim_artists, clean_plays, clean_top_items  # noqa: E402

load_dotenv()

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///data/processed/lastfm.db")

metadata = MetaData()

dim_artists = Table(
    "dim_artists", metadata,
    Column("artist_id", String(64), primary_key=True),
    Column("artist_name", String(255)),
    Column("genres", String),
)

dim_tracks = Table(
    "dim_tracks", metadata,
    Column("track_id", String(64), primary_key=True),
    Column("track_name", String(255)),
    Column("artist_id", String(64)),
    Column("album_name", String(255)),
    Column("duration_ms", Integer),
)

fact_plays = Table(
    "fact_plays", metadata,
    Column("play_id", Integer, primary_key=True, autoincrement=True),
    Column("track_id", String(64)),
    Column("played_at", String),  # stored as ISO string for SQLite portability
    Column("source_file", String(255)),
)

fact_top_items = Table(
    "fact_top_items_snapshot", metadata,
    Column("snapshot_id", Integer, primary_key=True, autoincrement=True),
    Column("item_type", String(16)),
    Column("item_key", String(64)),
    Column("name", String(255)),
    Column("rank", Integer),
    Column("time_range", String(16)),
    Column("pulled_at", String),
)


def get_engine():
    # ensure the local sqlite folder exists if using the default path
    if DB_URL.startswith("sqlite"):
        Path("data/processed").mkdir(parents=True, exist_ok=True)
    return create_engine(DB_URL)


def upsert_dim(engine, table, df: pd.DataFrame, key_col: str):
    if df.empty:
        return 0
    with engine.begin() as conn:
        existing = {row[0] for row in conn.execute(select(table.c[key_col]))}
        new_rows = df[~df[key_col].isin(existing)]
        if not new_rows.empty:
            conn.execute(table.insert(), new_rows.to_dict(orient="records"))
    return len(new_rows) if not df.empty else 0


def load_plays(engine, plays_df: pd.DataFrame):
    if plays_df.empty:
        print("  no plays to load")
        return

    plays_df = plays_df.copy()
    plays_df["played_at"] = plays_df["played_at"].astype(str)

    with engine.begin() as conn:
        existing = pd.DataFrame(
            conn.execute(select(fact_plays.c.track_id, fact_plays.c.played_at)).fetchall(),
            columns=["track_id", "played_at"],
        )

    if not existing.empty:
        merged = plays_df.merge(existing, on=["track_id", "played_at"], how="left", indicator=True)
        new_plays = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    else:
        new_plays = plays_df

    cols = ["track_id", "played_at", "source_file"]
    if not new_plays.empty:
        with engine.begin() as conn:
            conn.execute(fact_plays.insert(), new_plays[cols].to_dict(orient="records"))

    print(f"  plays: {len(new_plays)} new rows inserted ({len(plays_df) - len(new_plays)} already existed)")


def load_top_items(engine, tracks_df: pd.DataFrame, artists_df: pd.DataFrame):
    combined = pd.concat([tracks_df, artists_df], ignore_index=True)
    if combined.empty:
        print("  no top-item snapshots to load")
        return

    combined = combined.copy()
    combined["pulled_at"] = combined["pulled_at"].astype(str)
    cols = ["item_type", "item_key", "name", "rank", "time_range", "pulled_at"]

    # same anti-join idempotency pattern as load_plays: natural key here is
    # (item_type, item_key, time_range, pulled_at) -- one row per item per
    # snapshot pull, so re-running load on unchanged raw files inserts 0 rows.
    key_cols = ["item_type", "item_key", "time_range", "pulled_at"]
    with engine.begin() as conn:
        existing = pd.DataFrame(
            conn.execute(select(*[fact_top_items.c[c] for c in key_cols])).fetchall(),
            columns=key_cols,
        )

    if not existing.empty:
        merged = combined.merge(existing, on=key_cols, how="left", indicator=True)
        new_rows = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    else:
        new_rows = combined

    if not new_rows.empty:
        with engine.begin() as conn:
            conn.execute(fact_top_items.insert(), new_rows[cols].to_dict(orient="records"))

    print(f"  top items: {len(new_rows)} new snapshot rows inserted ({len(combined) - len(new_rows)} already existed)")


def main():
    print(f"Loading into: {DB_URL}")
    engine = get_engine()
    metadata.create_all(engine)

    print("\nCleaning plays (shared with artist dimension build below)...")
    plays_df = clean_plays()

    print("\nCleaning + loading artists...")
    artists_df = clean_dim_artists(plays_df=plays_df)
    n = upsert_dim(engine, dim_artists, artists_df, "artist_id")
    print(f"  artists: {n} new rows inserted")

    print("\nLoading plays (also backfills dim_tracks)...")
    if not plays_df.empty:
        tracks_df = plays_df[["track_id", "track_name", "artist_id", "album_name", "duration_ms"]].drop_duplicates("track_id")
        n = upsert_dim(engine, dim_tracks, tracks_df, "track_id")
        print(f"  tracks: {n} new rows inserted")
    load_plays(engine, plays_df)

    print("\nCleaning + loading top-item snapshots...")
    top_tracks_df = clean_top_items("tracks")
    top_artists_df = clean_top_items("artists")
    load_top_items(engine, top_tracks_df, top_artists_df)

    print("\nDone.")


if __name__ == "__main__":
    main()
