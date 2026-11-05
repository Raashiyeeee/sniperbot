"""
scripts/clean_test_data.py
──────────────────────────
Wipes test database records and re-initializes fresh, empty tables.
Usage:
    python scripts/clean_test_data.py
"""
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import create_tables, engine


async def main():
    print("🧹 Cleaning test database records...")
    db_file = PROJECT_ROOT / "radar_dev.db"

    # Dispose existing connection pool
    await engine.dispose()

    if db_file.exists():
        os.remove(db_file)
        print("🗑️ Removed radar_dev.db test database.")

    # Recreate empty tables
    await create_tables()
    print("✨ Initialized fresh, empty database schema.")
    print("👉 Start the bot with 'python main.py' to begin collecting real live data!")


if __name__ == "__main__":
    asyncio.run(main())

