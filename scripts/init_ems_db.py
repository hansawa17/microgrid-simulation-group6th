"""Initialize the B EMS SQLite database.

Usage:
    python scripts/init_ems_db.py
    python scripts/init_ems_db.py --db data/runtime/ems.db
"""

from __future__ import annotations

import argparse
from pathlib import Path

from B_dispatch.repository import EMSRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize B EMS SQLite database")
    parser.add_argument("--db", type=Path, default=Path("data/runtime/ems.db"))
    args = parser.parse_args()
    repo = EMSRepository(args.db)
    repo.initialize()
    print(f"Initialized EMS database: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
