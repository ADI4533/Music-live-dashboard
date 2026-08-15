"""
dashboard/app.py

Streamlit dashboard reading from the warehouse built by load/load_to_db.py.

Run with:  streamlit run dashboard/app.py

Answers the question this whole project was built around: "how does my
listening behavior shift over time, and when do I binge?" -- not just
"here's a chart of top artists" (which any tutorial produces).
"""

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()
DB_URL = os.environ.get("DATABASE_URL", "sqlite:///data/processed/lastfm.db")

st.set_page_config(page_title="Listening Pipeline", layout="wide")


@st.cache_resource
def get_engine():
    return create_engine(DB_URL)


@st.cache_data(ttl=300)
def load_data():
    engine = get_engine()
    plays = pd.read_sql(
        """
        SELECT p.played_at, t.track_name, t.artist_id, a.artist_name
        FROM fact_plays p
        JOIN dim_tracks t  ON p.track_id = t.track_id
        LEFT JOIN dim_artists a ON t.artist_id = a.artist_id
        """,
        engine,
    )
    plays["played_at"] = pd.to_datetime(plays["played_at"], utc=True, errors="coerce")
    plays["artist_name"] = plays["artist_name"].fillna("Unknown Artist")

    top_snapshots = pd.read_sql("SELECT * FROM fact_top_items_snapshot", engine)
    return plays, top_snapshots


def detect_binge_days(daily_counts: pd.Series, z_threshold: float = 2.0) -> pd.DataFrame:
    """Flags days where play count is z_threshold+ standard deviations
    above the person's own mean -- adapts to each user's baseline instead
    of a hardcoded 'more than N plays' rule."""
    mean, std = daily_counts.mean(), daily_counts.std()
    if std == 0 or pd.isna(std):
        z = pd.Series([0] * len(daily_counts), index=daily_counts.index)
    else:
        z = (daily_counts - mean) / std
    return pd.DataFrame({"plays": daily_counts, "z_score": z, "is_binge": z >= z_threshold})


def main():
    st.title("Listening History Pipeline")

    try:
        plays, top_snapshots = load_data()
    except Exception as e:
        st.error(
            f"Couldn't read the database at `{DB_URL}`.\n\n"
            f"Run `python ingestion/generate_mock_data.py` then "
            f"`python load/load_to_db.py` first.\n\nError: {e}"
        )
        return

    if plays.empty:
        st.warning("No plays loaded yet. Run the ingestion + load scripts first.")
        return

    days_covered = (plays["played_at"].max() - plays["played_at"].min()).days + 1
    col1, col2, col3 = st.columns(3)
    col1.metric("Total plays ingested", len(plays))
    col2.metric("Days of history", days_covered)
    col3.metric("Distinct artists", plays["artist_name"].nunique())

    st.divider()

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Top artists")
        top_artists = (
            plays["artist_name"].value_counts().head(10).reset_index()
        )
        top_artists.columns = ["artist_name", "play_count"]
        fig = px.bar(top_artists, x="play_count", y="artist_name", orientation="h")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=400)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("Daily plays + binge-day flags")
        daily = plays.set_index("played_at").resample("D").size()
        binge_df = detect_binge_days(daily).reset_index()
        binge_df.columns = ["day", "plays", "z_score", "is_binge"]

        fig = px.bar(binge_df, x="day", y="plays", color="is_binge",
                      color_discrete_map={True: "#e74c3c", False: "#1DB954"})
        fig.update_layout(height=400, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)

        n_binges = int(binge_df["is_binge"].sum())
        if n_binges:
            st.caption(f"{n_binges} binge day(s) detected (z-score >= 2.0 above your daily average)")

    st.divider()
    st.subheader("Raw plays (latest 100)")
    st.dataframe(
        plays.sort_values("played_at", ascending=False).head(100)[
            ["played_at", "track_name", "artist_name"]
        ],
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
