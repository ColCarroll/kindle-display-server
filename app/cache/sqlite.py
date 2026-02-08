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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            zip_code TEXT NOT NULL,
            lat TEXT NOT NULL,
            lon TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strava_activities (
            activity_id INTEGER PRIMARY KEY,
            start_date TEXT NOT NULL,
            year INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strava_year ON strava_activities(year)
    """
    )
    conn.commit()


def get(key: str) -> dict[str, Any] | None:
    """Get a cached value by key, or None if expired/missing."""
    conn = _get_connection()
    try:
        cursor = conn.execute("SELECT data, expires_at FROM cache WHERE key = ?", (key,))
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
        cursor = conn.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# Weather locations management


def get_weather_locations() -> list[dict[str, Any]]:
    """Get all weather locations."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, name, zip_code, lat, lon FROM weather_locations ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def add_weather_location(name: str, zip_code: str, lat: str, lon: str) -> int:
    """Add a weather location. Returns the new location ID."""
    conn = _get_connection()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            """
            INSERT INTO weather_locations (name, zip_code, lat, lon, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, zip_code, lat, lon, now),
        )
        conn.commit()
        # lastrowid can be None if no row was inserted, but we just did an INSERT
        return cursor.lastrowid or 0
    finally:
        conn.close()


def delete_weather_location(location_id: int) -> bool:
    """Delete a weather location by ID. Returns True if deleted."""
    conn = _get_connection()
    try:
        cursor = conn.execute("DELETE FROM weather_locations WHERE id = ?", (location_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# Strava activity caching


def get_cached_strava_activities(year: int) -> list[dict[str, Any]]:
    """Get all cached Strava activities for a given year."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT data FROM strava_activities WHERE year = ? ORDER BY start_date", (year,)
        )
        return [json.loads(row["data"]) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_latest_strava_activity_date(year: int) -> str | None:
    """Get the start_date of the most recent cached activity for a year."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT MAX(start_date) as max_date FROM strava_activities WHERE year = ?", (year,)
        )
        row = cursor.fetchone()
        return row["max_date"] if row else None
    finally:
        conn.close()


def cache_strava_activities(activities: list[dict[str, Any]]) -> int:
    """Cache Strava activities. Returns count of new activities added."""
    conn = _get_connection()
    try:
        added = 0
        for activity in activities:
            activity_id = activity.get("id")
            start_date = activity.get("start_date", "")
            if not activity_id or not start_date:
                continue

            # Extract year from start_date (format: 2026-01-15T08:30:00Z)
            year = int(start_date[:4])

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO strava_activities (activity_id, start_date, year, data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (activity_id, start_date, year, json.dumps(activity)),
                )
                if conn.total_changes:
                    added += 1
            except sqlite3.IntegrityError:
                pass  # Activity already exists

        conn.commit()
        return added
    finally:
        conn.close()


def clear_strava_cache(year: int | None = None) -> int:
    """Clear Strava activity cache. If year is None, clears all years."""
    conn = _get_connection()
    try:
        if year:
            cursor = conn.execute("DELETE FROM strava_activities WHERE year = ?", (year,))
        else:
            cursor = conn.execute("DELETE FROM strava_activities")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
