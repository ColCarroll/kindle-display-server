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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shoes (
            strava_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            bg_color TEXT NOT NULL DEFAULT '#e0e0e0',
            text_color TEXT NOT NULL DEFAULT '#333333',
            stripe_colors TEXT NOT NULL DEFAULT '[]',
            distance_mi REAL NOT NULL DEFAULT 0,
            retired INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_shoes (
            activity_id INTEGER PRIMARY KEY,
            shoe_strava_id TEXT,
            updated_at TEXT NOT NULL,
            synced_to_strava INTEGER NOT NULL DEFAULT 0
        )
    """
    )
    # Migration: add synced_to_strava to existing tables
    import contextlib

    with contextlib.suppress(Exception):
        conn.execute(
            "ALTER TABLE run_shoes ADD COLUMN synced_to_strava INTEGER NOT NULL DEFAULT 0"
        )
    with contextlib.suppress(Exception):
        conn.execute("ALTER TABLE run_shoes ADD COLUMN strava_shoe_id TEXT")
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


# Shoe tracking

_DEFAULT_SHOE_COLORS = [
    "#e63946",
    "#457b9d",
    "#2a9d8f",
    "#e9c46a",
    "#f4a261",
    "#6a4c93",
    "#52b788",
    "#264653",
]


def get_shoes(include_retired: bool = False) -> list[dict[str, Any]]:
    """Get all shoes, optionally including retired ones."""
    conn = _get_connection()
    try:
        query = "SELECT * FROM shoes" if include_retired else "SELECT * FROM shoes WHERE retired = 0"
        cursor = conn.execute(query + " ORDER BY name")
        rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["stripe_colors"] = json.loads(row["stripe_colors"])
        return rows
    finally:
        conn.close()


def get_shoe(strava_id: str) -> dict[str, Any] | None:
    """Get a single shoe by Strava ID."""
    conn = _get_connection()
    try:
        cursor = conn.execute("SELECT * FROM shoes WHERE strava_id = ?", (strava_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["stripe_colors"] = json.loads(d["stripe_colors"])
        return d
    finally:
        conn.close()


def upsert_shoe(strava_id: str, name: str, distance_mi: float, retired: bool) -> None:
    """Sync a shoe from Strava. Preserves user-customized colors on update."""
    conn = _get_connection()
    try:
        now = datetime.utcnow().isoformat()
        existing = conn.execute(
            "SELECT strava_id FROM shoes WHERE strava_id = ?", (strava_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE shoes SET name = ?, distance_mi = ?, retired = ?, synced_at = ? WHERE strava_id = ?",
                (name, distance_mi, 1 if retired else 0, now, strava_id),
            )
        else:
            count = conn.execute("SELECT COUNT(*) as cnt FROM shoes").fetchone()["cnt"]
            default_bg = _DEFAULT_SHOE_COLORS[count % len(_DEFAULT_SHOE_COLORS)]
            conn.execute(
                """
                INSERT INTO shoes (strava_id, name, distance_mi, retired, synced_at, bg_color, text_color, stripe_colors)
                VALUES (?, ?, ?, ?, ?, ?, '#333333', '[]')
                """,
                (strava_id, name, distance_mi, 1 if retired else 0, now, default_bg),
            )
        conn.commit()
    finally:
        conn.close()


def update_shoe_style(
    strava_id: str, bg_color: str, text_color: str, stripe_colors: list[str]
) -> bool:
    """Update a shoe's visual settings. Returns True if the shoe was found."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "UPDATE shoes SET bg_color = ?, text_color = ?, stripe_colors = ? WHERE strava_id = ?",
            (bg_color, text_color, json.dumps(stripe_colors), strava_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_run_shoe(activity_id: int) -> dict[str, Any] | None:
    """Get the shoe assigned to a run activity, or None if unassigned."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT s.* FROM run_shoes rs
            JOIN shoes s ON rs.shoe_strava_id = s.strava_id
            WHERE rs.activity_id = ?
            """,
            (activity_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["stripe_colors"] = json.loads(d["stripe_colors"])
        return d
    finally:
        conn.close()


def set_run_shoe(activity_id: int, shoe_strava_id: str | None) -> None:
    """Assign (or clear) a shoe for a run activity.

    Marks as synced if the new value matches what's already in Strava,
    unsynced otherwise.
    """
    conn = _get_connection()
    try:
        now = datetime.utcnow().isoformat()
        row = conn.execute(
            "SELECT strava_shoe_id FROM run_shoes WHERE activity_id = ?", (activity_id,)
        ).fetchone()
        strava_shoe_id = row["strava_shoe_id"] if row else None
        already_synced = 1 if shoe_strava_id == strava_shoe_id else 0
        conn.execute(
            """
            INSERT OR REPLACE INTO run_shoes (activity_id, shoe_strava_id, strava_shoe_id, updated_at, synced_to_strava)
            VALUES (?, ?, ?, ?, ?)
            """,
            (activity_id, shoe_strava_id, strava_shoe_id, now, already_synced),
        )
        conn.commit()
    finally:
        conn.close()


def get_shoe_local_miles() -> dict[str, float]:
    """Return a mapping of shoe_strava_id -> total miles logged locally."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT rs.shoe_strava_id,
                   SUM(json_extract(sa.data, '$.distance') * 0.000621371) AS miles
            FROM run_shoes rs
            JOIN strava_activities sa ON rs.activity_id = sa.activity_id
            WHERE rs.shoe_strava_id IS NOT NULL
            GROUP BY rs.shoe_strava_id
            """
        )
        return {row["shoe_strava_id"]: round(row["miles"] or 0, 1) for row in cursor.fetchall()}
    finally:
        conn.close()


def get_unsynced_run_shoes() -> list[dict[str, Any]]:
    """Get shoe assignments not yet pushed to Strava (excludes explicit 'no shoe' clearances)."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT activity_id, shoe_strava_id FROM run_shoes WHERE synced_to_strava = 0 AND shoe_strava_id IS NOT NULL"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def mark_run_shoe_synced(activity_id: int) -> None:
    """Mark a shoe assignment as successfully pushed to Strava."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE run_shoes SET synced_to_strava = 1, strava_shoe_id = shoe_strava_id WHERE activity_id = ?",
            (activity_id,),
        )
        conn.commit()
    finally:
        conn.close()




def get_run_shoes_for_activities(activity_ids: list[int]) -> dict[int, dict[str, Any] | None]:
    """Batch-fetch shoe assignments for a list of activity IDs."""
    if not activity_ids:
        return {}
    conn = _get_connection()
    try:
        placeholders = ",".join("?" * len(activity_ids))
        cursor = conn.execute(
            f"""
            SELECT rs.activity_id, s.*
            FROM run_shoes rs
            LEFT JOIN shoes s ON rs.shoe_strava_id = s.strava_id
            WHERE rs.activity_id IN ({placeholders})
            """,
            tuple(activity_ids),
        )
        result: dict[int, dict[str, Any] | None] = {}
        for row in cursor.fetchall():
            d = dict(row)
            activity_id = d.pop("activity_id")
            if d.get("strava_id"):
                d["stripe_colors"] = json.loads(d["stripe_colors"])
                result[activity_id] = d
            else:
                result[activity_id] = None
        return result
    finally:
        conn.close()
