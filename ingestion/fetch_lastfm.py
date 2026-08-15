"""
fetch_lastfm.py

Pulls listening history from Last.fm's AudioScrobbler API. Your Apple
Music plays get INTO Last.fm via a scrobbler app running on your
phone/laptop (see README "Setup" -- this script doesn't touch Apple
Music at all, it only reads from Last.fm, which the scrobbler has
already populated).

No OAuth here, unlike the original Spotify version: user.getRecentTracks
and user.getTopArtists/Tracks are public-profile reads that just need an
API key + your username. Much less to break.

Endpoints used (Web Services 2.0 / AudioScrobbler API):
    user.getRecentTracks  -- your scrobble history, paginated
    user.getTopArtists    -- top artists for a given period
    user.getTopTracks     -- top tracks for a given period

One real wrinkle: Last.fm doesn't reliably provide a stable ID for every
artist/track (mbid -- MusicBrainz ID -- is frequently blank, since not
everything is matched to MusicBrainz's catalog). transform/clean.py
handles this by deriving keys from normalized names instead of trusting
a source ID -- see the comment there for why that's an imperfect but
workable choice.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
API_URL = "https://ws.audioscrobbler.com/2.0/"


def get_credentials():
    api_key = os.environ.get("LASTFM_API_KEY")
    username = os.environ.get("LASTFM_USERNAME")
    if not api_key or not username:
        raise RuntimeError(
            "Missing LASTFM_API_KEY or LASTFM_USERNAME. "
            "Copy .env.example to .env and fill them in -- "
            "get a free key at https://www.last.fm/api/account/create"
        )
    return api_key, username


def call(method, api_key, username, **params):
    resp = requests.get(
        API_URL,
        params={
            "method": method,
            "user": username,
            "api_key": api_key,
            "format": "json",
            **params,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Last.fm API error {data['error']}: {data.get('message')}")
    return data


def fetch_recent_tracks(api_key, username, pages=4, per_page=200):
    """Pulls the last `pages * per_page` scrobbles. Paginated because a
    single call caps at 200 tracks; loop to cover more history on the
    first run, then rely on daily scheduling to keep up after that."""
    all_items = []
    for page in range(1, pages + 1):
        data = call("user.getRecentTracks", api_key, username, page=page, limit=per_page)
        tracks = data.get("recenttracks", {}).get("track", [])
        if not tracks:
            break
        all_items.extend(tracks)
        total_pages = int(data.get("recenttracks", {}).get("@attr", {}).get("totalPages", 1))
        if page >= total_pages:
            break
        time.sleep(0.25)  # be polite to the API between pages
    return {"recenttracks": {"track": all_items}}


def fetch_top_items(api_key, username, item_type="artists", period="7day", limit=20):
    method = "user.getTopArtists" if item_type == "artists" else "user.getTopTracks"
    return call(method, api_key, username, period=period, limit=limit)


def save(data, name, stamp):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{name}_{stamp}.json"
    path.write_text(json.dumps(data, indent=2))
    print(f"  wrote {path.name}")
    return path


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    print(f"Pull started: {stamp}")

    api_key, username = get_credentials()

    print("Fetching recent tracks...")
    recent = fetch_recent_tracks(api_key, username)
    n_tracks = len(recent["recenttracks"]["track"])
    print(f"  {n_tracks} scrobbles fetched")
    save(recent, "recent_tracks", stamp)

    print("Fetching top artists (7day)...")
    top_artists = fetch_top_items(api_key, username, "artists", "7day")
    save(top_artists, "top_artists", stamp)

    print("Fetching top tracks (7day)...")
    top_tracks = fetch_top_items(api_key, username, "tracks", "7day")
    save(top_tracks, "top_tracks", stamp)

    print("Pull complete.")


if __name__ == "__main__":
    main()
