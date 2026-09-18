"""Shared almanac storage for the dashboard and chatbot."""

import threading
import time
from pathlib import Path

import mysql.connector

from config import DB_CONFIG


ALMANAC_CACHE_TTL = 60
_SEED_PATH = Path(__file__).with_name("school_almanac.txt")
_cache = {"content": None, "version": None, "loaded_at": 0.0}
_refresh_lock = threading.Lock()


def _connection():
    return mysql.connector.connect(**DB_CONFIG)


def read_almanac():
    """Read the shared record. Editors must fail visibly if the DB is unavailable."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT content, version FROM school_almanac WHERE id = 1")
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            raise RuntimeError("The school almanac has not been seeded in the database.")
        return row[0], row[1]
    finally:
        conn.close()


def get_almanac_snapshot(force=False):
    """Serve last-good content during DB outages; refresh at most once per TTL."""
    now = time.monotonic()
    if not force and _cache["content"] is not None and now - _cache["loaded_at"] < ALMANAC_CACHE_TTL:
        return _cache["content"], _cache["version"]
    with _refresh_lock:
        now = time.monotonic()
        if force or _cache["content"] is None or now - _cache["loaded_at"] >= ALMANAC_CACHE_TTL:
            try:
                content, version = read_almanac()
                _cache.update(content=content, version=version, loaded_at=now)
            except Exception as exc:
                print(f"[ALMANAC REFRESH ERROR] {exc}")
                if _cache["content"] is None:
                    _cache.update(content=_SEED_PATH.read_text(encoding="utf-8"),
                                  version=None, loaded_at=now)
                else:
                    _cache["loaded_at"] = now
        return _cache["content"], _cache["version"]


def get_almanac():
    return get_almanac_snapshot()[0]


def save_almanac(content, expected_version):
    """Save an editor revision only if no one else has changed it."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE school_almanac SET content=%s, version=version+1 "
                "WHERE id=1 AND version=%s", (content, expected_version)
            )
            saved = cursor.rowcount == 1
            if saved:
                conn.commit()
            else:
                conn.rollback()
            return saved
        finally:
            cursor.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def append_suggested_almanac_entry(question_id, topic, answer):
    """Append once and remove the suggestion in the same transaction."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM unanswered_questions WHERE id=%s FOR UPDATE", (question_id,))
            if cursor.fetchone() is None:
                conn.rollback()
                return False
            cursor.execute("SELECT content FROM school_almanac WHERE id=1 FOR UPDATE")
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("The school almanac has not been seeded in the database.")
            existing = row[0]
            separator = "\n\n" if existing.strip() else ""
            updated = existing.rstrip("\n") + separator + f"{topic.strip()}\n{answer.strip()}\n"
            cursor.execute("UPDATE school_almanac SET content=%s, version=version+1 WHERE id=1", (updated,))
            cursor.execute("DELETE FROM unanswered_questions WHERE id=%s", (question_id,))
            conn.commit()
            return True
        finally:
            cursor.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
