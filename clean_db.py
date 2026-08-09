"""
Wipe operational BotCore DB rows (keep table schemas).

Usage (local / Railway shell):
  python clean_db.py
  python clean_db.py --yes

Temporary public wipe (deployed API — remove after use):
  POST /api/admin/wipe-operational-data
  Header: X-Wipe-Key: <WIPE_DB_KEY from env>
  or query: ?key=<WIPE_DB_KEY>

Requires DATABASE_URL in the environment (or .env).
On Railway the internal URL works; this endpoint is meant to run there.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Tables to empty. Schema retained; serial/identity counters reset.
_TABLES_TO_CLEAR = (
    "analysis_notes",
    "market_data_cache",
    "current_positions",
    "trade_events",
    "users",
    "account_snapshots",
    "test_inputs",
)


def wipe_operational_data() -> dict:
    """
    Delete all rows from each operational table, one at a time.
    Schemas stay intact. Returns {table: "cleared", ...}.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set in environment")

    import psycopg2

    cleared = []
    conn = psycopg2.connect(database_url)
    try:
        cursor = conn.cursor()
        for table in _TABLES_TO_CLEAR:
            cursor.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY")
            cleared.append(table)
            print(f"cleared: {table}")
        conn.commit()
        print("done — operational tables emptied")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"cleared": cleared}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Empty BotCore operational DB tables (schemas kept)."
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Run without interactive confirmation",
    )
    args = parser.parse_args()

    print("This will permanently delete all rows from:")
    for table in _TABLES_TO_CLEAR:
        print(f"  - {table}")
    print("Table schemas are kept.")

    if not args.yes:
        answer = input("Type YES to continue: ").strip()
        if answer != "YES":
            print("aborted")
            return 1

    wipe_operational_data()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
