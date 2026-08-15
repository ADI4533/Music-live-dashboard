"""
generate_mock_data.py

Generates fake Last.fm-shaped JSON so we can build/test transform, load,
and the dashboard without waiting on real scrobbles to accumulate.

IMPORTANT: unlike the Spotify version of this project, real data here
starts from ZERO -- there's no bulk import of your past Apple Music
history into Last.fm, only what gets scrobbled from the moment you set
up the bridge app. So this mock generator isn't just a convenience, it's
what you'll actually be developing against for most of the build, with
real data layering in underneath as scrobbles accumulate day by day.

Matches Last.fm's real (slightly awkward) JSON shape: nested "#text" for
display values, "@attr" for metadata like rank/nowplaying. Getting this
exactly right matters -- transform/clean.py parses these specific keys.

Usage:
    python ingestion/generate_mock_data.py --days 14
"""

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# (artist, [track names])
ARTISTS = {
    "Radiohead": ["Karma Police", "No Surprises", "Weird Fishes", "Idioteque", "Paranoid Android"],
    "Drake": ["God's Plan", "Hotline Bling", "One Dance", "In My Feelings", "Passionfruit"],
    "Ed Sheeran": ["Shape of You", "Perfect", "Photograph", "Castle on the Hill", "Bad Habits"],
    "The Weeknd": ["Blinding Lights", "Starboy", "Save Your Tears", "The Hills", "Die For You"],
    "Imagine Dragons": ["Believer", "Radioactive", "Thunder", "Demons", "Natural"],
    "Eminem": ["Lose Yourself", "Not Afraid", "Love The Way You Lie", "Stan", "Rap God"],
    "Taylor Swift": ["Anti-Hero", "Cruel Summer", "Style", "Blank Space", "Cardigan"],
    "Bruno Mars": ["24K Magic", "Uptown Funk", "Just The Way You Are", "Locked Out of Heaven", "Talking to the Moon"],
    "The Beatles": ["Hey Jude", "Let It Be", "Come Together", "Yesterday", "Here Comes the Sun"],
    "Kendrick Lamar": ["HUMBLE.", "Alright", "DNA.", "Money Trees", "King Kunta"],
}


def build_catalog():
    catalog = []
    for artist, tracks in ARTISTS.items():
        for track in tracks:
            catalog.append({"artist": artist, "track": track, "album": f"{artist} Album"})
    return catalog


def make_track_entry(item, played_dt=None, nowplaying=False):
    """Mimics one entry in user.getRecentTracks' track[] array."""
    entry = {
        "artist": {"mbid": "", "#text": item["artist"]},
        "streamable": "0",
        "album": {"mbid": "", "#text": item["album"]},
        "mbid": "",
        "name": item["track"],
        "url": f"https://www.last.fm/music/{item['artist'].replace(' ', '+')}",
    }
    if nowplaying:
        entry["@attr"] = {"nowplaying": "true"}
        # nowplaying entries have no "date" field in the real API --
        # clean.py is expected to skip these, not fake a timestamp
    else:
        entry["date"] = {
            "uts": str(int(played_dt.timestamp())),
            "#text": played_dt.strftime("%d %b %Y, %H:%M"),
        }
    return entry


def generate_recent_tracks(catalog, pull_timestamp, n=50):
    favorite_tracks = random.sample(catalog, k=4)
    binge_today = random.random() < 0.25
    items = []

    for i in range(n):
        if binge_today and i < 15:
            item = favorite_tracks[0]
        elif random.random() < 0.5:
            item = random.choice(favorite_tracks)
        else:
            item = random.choice(catalog)

        played_dt = pull_timestamp - timedelta(minutes=i * random.randint(3, 8))
        items.append(make_track_entry(item, played_dt=played_dt))

    return {
        "recenttracks": {
            "track": items,
            "@attr": {"user": "mockuser", "page": "1", "perPage": "200",
                      "totalPages": "1", "total": str(n)},
        }
    }


def generate_top_artists(catalog, period="7day", limit=10):
    artists = list(ARTISTS.keys())
    weights = [random.randint(5, 40) for _ in artists]
    ranked = sorted(zip(artists, weights), key=lambda x: -x[1])[:limit]

    items = [
        {
            "name": name,
            "playcount": str(count),
            "mbid": "",
            "url": f"https://www.last.fm/music/{name.replace(' ', '+')}",
            "@attr": {"rank": str(i + 1)},
        }
        for i, (name, count) in enumerate(ranked)
    ]
    return {
        "topartists": {
            "artist": items,
            "@attr": {"user": "mockuser", "period": period, "page": "1",
                      "perPage": str(limit), "totalPages": "1", "total": str(limit)},
        }
    }


def generate_top_tracks(catalog, period="7day", limit=10):
    sample = random.sample(catalog, k=min(limit, len(catalog)))
    items = [
        {
            "name": t["track"],
            "duration": str(random.randint(150, 280)),
            "playcount": str(random.randint(3, 25)),
            "artist": {"name": t["artist"], "mbid": "",
                       "url": f"https://www.last.fm/music/{t['artist'].replace(' ', '+')}"},
            "mbid": "",
            "@attr": {"rank": str(i + 1)},
        }
        for i, t in enumerate(sample)
    ]
    return {
        "toptracks": {
            "track": items,
            "@attr": {"user": "mockuser", "period": period, "page": "1",
                      "perPage": str(limit), "totalPages": "1", "total": str(limit)},
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Generate mock Last.fm listening data")
    parser.add_argument("--days", type=int, default=14, help="Number of daily pulls to simulate")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog()

    now = datetime.now(timezone.utc)
    for day_offset in range(args.days):
        pull_time = now - timedelta(days=(args.days - 1 - day_offset))
        stamp = pull_time.strftime("%Y%m%dT%H%M%S")

        recent = generate_recent_tracks(catalog, pull_time)
        (RAW_DIR / f"recent_tracks_{stamp}.json").write_text(json.dumps(recent, indent=2))

        if day_offset % 3 == 0:
            top_a = generate_top_artists(catalog)
            (RAW_DIR / f"top_artists_{stamp}.json").write_text(json.dumps(top_a, indent=2))
            top_t = generate_top_tracks(catalog)
            (RAW_DIR / f"top_tracks_{stamp}.json").write_text(json.dumps(top_t, indent=2))

    print(f"Generated {args.days} days of mock pulls into {RAW_DIR}")
    print(f"Files: {len(list(RAW_DIR.glob('*.json')))}")


if __name__ == "__main__":
    main()
