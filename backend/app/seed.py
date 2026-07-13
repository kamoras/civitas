"""Seed the database from a senators JSON file (e.g. from the old static pipeline).

Usage:
    python -m app.seed [path/to/senators.json]

If no path is provided, reads from stdin.
"""

import json
import sys

from app.database import SessionLocal, init_db
from app.services.senator_service import upsert_senator


def seed_from_json(data: list[dict]) -> int:
    """Load senator records into the database.

    Args:
        data: List of senator dicts matching the SenatorSchema shape (camelCase).

    Returns:
        Number of senators upserted.
    """
    init_db()
    db = SessionLocal()
    count = 0
    try:
        for senator_data in data:
            try:
                upsert_senator(db, senator_data)
                count += 1
            except Exception as e:
                print(f"  Failed: {senator_data.get('name', '?')}: {e}", file=sys.stderr)
        print(f"Seeded {count}/{len(data)} senators.")
        return count
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    if not isinstance(data, list):
        print("Expected a JSON array of senator objects.", file=sys.stderr)
        sys.exit(1)

    seed_from_json(data)
