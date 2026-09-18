"""One-time, idempotent almanac migration for the configured database.

Run before deploying the DB-backed almanac code. Existing edits are never replaced.
"""

from pathlib import Path

import mysql.connector

from config import DB_CONFIG


def migrate():
    seed = Path(__file__).with_name("school_almanac.txt").read_text(encoding="utf-8")
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS school_almanac (
                    id TINYINT PRIMARY KEY,
                    content LONGTEXT NOT NULL,
                    version BIGINT UNSIGNED NOT NULL DEFAULT 1
                )
            """)
            cursor.execute(
                "INSERT IGNORE INTO school_almanac (id, content, version) VALUES (1, %s, 1)",
                (seed,)
            )
            conn.commit()
            print("Almanac database record ready. Existing content was preserved.")
        finally:
            cursor.close()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
