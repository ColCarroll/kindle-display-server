"""SQLite-based cache backend."""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from app import config

DB_PATH = os.path.join(config.CACHE_DIR, "cache.db")


def _get_connection() -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """Initialize cache tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.commit()


def get(key: str) -> dict[str, Any] | None:
    """Get a cached value by key, or None if expired/missing."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT data, expires_at FROM cache WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        if not row:
            return None

        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires_at:
            # Expired, delete it
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None

        return json.loads(row["data"])
    finally:
        conn.close()


def set(key: str, data: Any, ttl_seconds: int) -> None:
    """Set a cached value with TTL in seconds."""
    conn = _get_connection()
    try:
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=ttl_seconds)
        json_data = json.dumps(data)

        conn.execute(
            """
            INSERT OR REPLACE INTO cache (key, data, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, json_data, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def delete(key: str) -> None:
    """Delete a cached value."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def clear() -> None:
    """Clear all cached values."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM cache")
        conn.commit()
    finally:
        conn.close()


def cleanup_expired() -> int:
    """Remove all expired entries. Returns count of deleted entries."""
    conn = _get_connection()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "DELETE FROM cache WHERE expires_at < ?", (now,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
