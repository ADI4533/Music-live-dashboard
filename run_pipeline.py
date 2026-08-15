"""
run_pipeline.py

Convenience wrapper: ingest -> load, in one command. Useful for local
runs and manual testing; the GitHub Action calls the two steps directly
(see .github/workflows/daily_ingest.yml) so failures are visible per-step
in the Actions log instead of buried in one script's output.

Usage:
    python run_pipeline.py --mock         # generate fresh mock data, then load
    python run_pipeline.py --live         # pull real data from Last.fm, then load
    python run_pipeline.py --load-only    # just re-run transform+load on existing data/raw/
"""

import argparse
import subprocess
import sys


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Step failed: {' '.join(cmd)}")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mock", action="store_true", help="Generate mock data, then load")
    group.add_argument("--live", action="store_true", help="Pull real Last.fm data, then load")
    group.add_argument("--load-only", action="store_true", help="Skip ingestion, just transform+load")
    args = parser.parse_args()

    if args.mock:
        run([sys.executable, "ingestion/generate_mock_data.py", "--days", "14"])
    elif args.live:
        run([sys.executable, "ingestion/fetch_lastfm.py"])

    run([sys.executable, "load/load_to_db.py"])
    print("\nPipeline complete. Run: streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
