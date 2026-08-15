"""
clean.py

Reads every raw JSON file in data/raw/, flattens Last.fm's nested JSON
into tidy pandas DataFrames, and handles the real-world messiness this
data has:

  1. No reliable entity ID: Spotify gives every track/artist a stable ID;
     Last.fm's mbid (MusicBrainz ID) is frequently blank -- lots of
     scrobbles never get matched to MusicBrainz's catalog, especially
     for less mainstream stuff. We can't dedupe or join on mbid, so we
     derive a key by hashing the normalized (lowercased, trimmed) name
     instead. That's imperfect -- "Beatles" and "The Beatles" would get
     different keys -- but it's the standard workaround for source data
     that doesn't guarantee IDs, and it's honest to say so rather than
     pretend mbid was usable.

  2. "Now playing" entries: a track currently playing appears in
     getRecentTracks WITHOUT a "date" field (it hasn't finished yet, so
     Last.fm hasn't finalized a scrobble timestamp). We skip these --
     the track reappears with a real timestamp once it's done playing,
     so counting the nowplaying entry too would double-count it.

  3. Overlapping pulls: consecutive daily pulls of recent tracks overlap
     (same design reason as before -- we paginate back further than one
     day's worth to be safe). Deduped on (track_key, played_at).

Returns DataFrames -- doesn't touch the database, so this is testable
without a DB connection.
"""

import hashlib
import json
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def make_key(*parts: str) -> str:
    """Derives a stable ID from normalized name parts, for entities
    where the source doesn't reliably provide one."""
    raw = "||".join(p.strip().lower() for p in parts if p)
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _load_json_files(pattern):
    files = sorted(RAW_DIR.glob(pattern))
    for f in files:
        try:
            yield f.name, json.loads(f.read_text())
        except json.JSONDecodeError:
            print(f"  [skip] {f.name}: invalid JSON")
            continue


def clean_plays() -> pd.DataFrame:
    """Flatten all recent_tracks_*.json into one plays DataFrame,
    deduped on (track_key, played_at). Skips 'now playing' entries."""
    rows = []
    skipped_nowplaying = 0

    for fname, payload in _load_json_files("recent_tracks_*.json"):
        for item in payload.get("recenttracks", {}).get("track", []):
            if item.get("@attr", {}).get("nowplaying") == "true":
                skipped_nowplaying += 1
                continue

            artist_name = item.get("artist", {}).get("#text")
            track_name = item.get("name")
            album_name = item.get("album", {}).get("#text")
            played_at_uts = item.get("date", {}).get("uts")

            if not artist_name or not track_name or not played_at_uts:
                continue

            artist_key = make_key(artist_name)
            track_key = make_key(artist_name, track_name)

            rows.append(
                {
                    "track_id": track_key,
                    "track_name": track_name,
                    "artist_id": artist_key,
                    "artist_name": artist_name,
                    "album_name": album_name,
                    "duration_ms": None,  # not provided by getRecentTracks
                    "played_at": pd.to_datetime(int(played_at_uts), unit="s", utc=True),
                    "source_file": fname,
                }
            )

    if skipped_nowplaying:
        print(f"  skipped {skipped_nowplaying} 'now playing' entries (no finalized timestamp yet)")

    if not rows:
        return pd.DataFrame(
            columns=["track_id", "track_name", "artist_id", "artist_name",
                     "album_name", "duration_ms", "played_at", "source_file"]
        )

    df = pd.DataFrame(rows)
    before = len(df)
    df = df.drop_duplicates(subset=["track_id", "played_at"], keep="first")
    after = len(df)
    print(f"  plays: {before} raw rows -> {after} after dedup ({before - after} duplicates removed)")
    return df.sort_values("played_at").reset_index(drop=True)


def clean_top_items(item_type: str) -> pd.DataFrame:
    """Flatten all top_{tracks|artists}_*.json snapshots into one DataFrame."""
    rows = []
    for fname, payload in _load_json_files(f"top_{item_type}_*.json"):
        stamp = fname.replace(f"top_{item_type}_", "").replace(".json", "")
        pulled_at = pd.to_datetime(stamp, format="%Y%m%dT%H%M%S", utc=True, errors="coerce")

        root_key = "topartists" if item_type == "artists" else "toptracks"
        payload_root = payload.get(root_key, {})
        time_range = payload_root.get("@attr", {}).get("period", "7day")
        # Last.fm nests the list under "artist" or "track" (singular),
        # not "artists"/"tracks"
        items = payload_root.get("artist" if item_type == "artists" else "track", [])

        for item in items:
            if item_type == "artists":
                name = item.get("name")
                item_key_equiv = make_key(name) if name else None
            else:
                name = item.get("name")
                artist_name = item.get("artist", {}).get("name", "")
                item_key_equiv = make_key(artist_name, name) if name else None

            rank = int(item.get("@attr", {}).get("rank", 0))

            rows.append(
                {
                    "item_key": item_key_equiv,  # kept as this column name for
                    "name": name,                     # continuity with the load layer;
                    "rank": rank,                      # really "item_key" now
                    "time_range": time_range,
                    "pulled_at": pulled_at,
                    "item_type": item_type,
                }
            )

    if not rows:
        return pd.DataFrame(columns=["item_key", "name", "rank", "time_range", "pulled_at", "item_type"])

    return pd.DataFrame(rows)


def clean_dim_artists(plays_df: pd.DataFrame = None) -> pd.DataFrame:
    """Assembles the artist dimension from names seen in top-artist
    snapshots and in plays -- there's no dedicated bulk artist-lookup
    endpoint being used here, so this is built from what we've already
    pulled rather than a separate fetch.

    Accepts an already-cleaned plays_df to avoid re-reading and
    re-parsing every raw file a second time when called from the load
    step, which already has one; computes it itself if called standalone."""
    rows = []

    for fname, payload in _load_json_files("top_artists_*.json"):
        for item in payload.get("topartists", {}).get("artist", []):
            name = item.get("name")
            if name:
                rows.append({"artist_id": make_key(name), "artist_name": name, "genres": json.dumps([])})

    if plays_df is None:
        plays_df = clean_plays()
    if not plays_df.empty:
        for _, row in plays_df[["artist_id", "artist_name"]].drop_duplicates().iterrows():
            rows.append({"artist_id": row["artist_id"], "artist_name": row["artist_name"], "genres": json.dumps([])})

    if not rows:
        return pd.DataFrame(columns=["artist_id", "artist_name", "genres"])

    df = pd.DataFrame(rows).dropna(subset=["artist_id"])
    return df.drop_duplicates(subset=["artist_id"], keep="first")


if __name__ == "__main__":
    print("Cleaning plays...")
    plays_df = clean_plays()
    print(plays_df.head())

    print("\nCleaning top tracks...")
    print(clean_top_items("tracks").head())

    print("\nCleaning top artists...")
    print(clean_top_items("artists").head())

    print("\nCleaning dim_artists...")
    print(clean_dim_artists().head())
