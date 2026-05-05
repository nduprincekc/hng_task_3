"""
Stage 4B database migration — add composite indexes to existing profiles table.
Run ONCE against your live Supabase database.

Usage:
    DATABASE_URL=your_supabase_url python migrate_indexes.py
"""

import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_profiles_country_id_gender ON profiles (country_id, gender);",
    "CREATE INDEX IF NOT EXISTS ix_profiles_country_id_gender_age ON profiles (country_id, gender, age);",
    "CREATE INDEX IF NOT EXISTS ix_profiles_country_name_gender ON profiles (country_name, gender);",
    "CREATE INDEX IF NOT EXISTS ix_profiles_gender_age ON profiles (gender, age);",
    "CREATE INDEX IF NOT EXISTS ix_profiles_age_group ON profiles (age_group);",
    "CREATE INDEX IF NOT EXISTS ix_profiles_name ON profiles (name);",
]


def run():
    if not DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL environment variable first")

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    for sql in INDEXES:
        print(f"  → {sql[:70]}...")
        cur.execute(sql)
        print("    ✓ done")

    cur.close()
    conn.close()
    print("\nAll indexes created.")


if __name__ == "__main__":
    run()