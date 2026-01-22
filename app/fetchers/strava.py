"""Strava data fetcher with caching."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app import config
from app.cache import sqlite as cache

# Use timezone.utc for Python 3.10 compatibility (datetime.UTC added in 3.11)
UTC = timezone.utc  # noqa: UP017
EASTERN = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# In-memory token cache (short-lived, refreshes frequently)
_access_token_cache = None
_token_expiry = None


def _refresh_access_token() -> str | None:
    """Refresh Strava access token using refresh token."""
    global _access_token_cache, _token_expiry

    if not all([config.STRAVA_CLIENT_ID, config.STRAVA_CLIENT_SECRET, config.STRAVA_REFRESH_TOKEN]):
        logger.warning("Strava credentials not fully configured")
        return None

    # Check if cached token is still valid
    if _access_token_cache and _token_expiry and datetime.now(UTC) < _token_expiry:
        return _access_token_cache

    url = "https://www.strava.com/oauth/token"
    data = {
        "client_id": config.STRAVA_CLIENT_ID,
        "client_secret": config.STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": config.STRAVA_REFRESH_TOKEN,
    }

    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        token_data = response.json()

        _access_token_cache = token_data["access_token"]
        _token_expiry = datetime.now(UTC) + timedelta(seconds=token_data["expires_in"] - 300)

        return _access_token_cache
    except Exception as e:
        logger.error(f"Failed to refresh Strava token: {e}")
        return None


def fetch_recent_activities(limit: int = 30, after: int | None = None, use_cache: bool = True) -> list[dict] | None:
    """Fetch recent activities from Strava with caching.

    Args:
        limit: Maximum number of activities per page (max 200)
        after: Unix timestamp to fetch activities after this date
        use_cache: Whether to use cached data if available
    """
    cache_key = f"strava:activities:{limit}:{after or 'recent'}"

    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.info("Using cached Strava activities")
            return cached

    access_token = _refresh_access_token()
    if not access_token:
        return None

    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": min(limit, 200)}

    if after:
        params["after"] = after

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        activities = response.json()

        # Cache the result
        cache.set(cache_key, activities, config.STRAVA_CACHE_TTL)

        return activities
    except Exception as e:
        logger.error(f"Failed to fetch Strava activities: {e}")
        return None


def fetch_athlete_stats(use_cache: bool = True) -> dict | None:
    """Fetch athlete statistics including YTD totals."""
    cache_key = "strava:stats"

    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.info("Using cached Strava stats")
            return cached

    access_token = _refresh_access_token()
    if not access_token:
        return None

    # First get the athlete ID
    try:
        url = "https://www.strava.com/api/v3/athlete"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        athlete_data = response.json()
        athlete_id = athlete_data["id"]
    except Exception as e:
        logger.error(f"Failed to fetch athlete info: {e}")
        return None

    # Then get stats
    try:
        url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        stats = response.json()

        # Cache the result
        cache.set(cache_key, stats, config.STRAVA_STATS_CACHE_TTL)

        return stats
    except Exception as e:
        logger.error(f"Failed to fetch athlete stats: {e}")
        return None


def fetch_activity_streams(activity_id: int) -> dict | None:
    """Fetch detailed activity stream data (lat/lng, altitude, etc).

    Note: Not cached since individual streams are rarely re-requested.
    """
    access_token = _refresh_access_token()
    if not access_token:
        return None

    url = f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"keys": "latlng,altitude,distance", "key_by_type": "true"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch activity streams: {e}")
        return None


def get_running_summary(use_cache: bool = True) -> dict[str, Any] | None:
    """Get running summary data for web display.

    Returns:
        Dictionary with:
            - weekly_distance_mi: Total miles in last 7 days
            - yearly_distance_mi: Total miles this year
            - projected_yearly_mi: Projected miles for full year
            - avg_miles_per_day: Average miles per day this year
            - days_elapsed: Days elapsed this year
            - days_remaining: Days remaining this year
            - recent_runs: List of recent runs with stats
    """
    now_eastern = datetime.now(EASTERN)
    year_start_eastern = datetime(now_eastern.year, 1, 1, tzinfo=EASTERN)
    week_start_eastern = now_eastern - timedelta(days=7)

    # Calculate time elapsed
    seconds_elapsed = (now_eastern - year_start_eastern).total_seconds()
    days_elapsed = seconds_elapsed / 86400

    days_in_year = (
        366 if now_eastern.year % 4 == 0 and (now_eastern.year % 100 != 0 or now_eastern.year % 400 == 0) else 365
    )

    year_end_eastern = datetime(now_eastern.year + 1, 1, 1, tzinfo=EASTERN)
    seconds_remaining = (year_end_eastern - now_eastern).total_seconds()
    days_remaining = seconds_remaining / 86400

    # Get yearly stats
    yearly_distance_mi = None
    stats = fetch_athlete_stats(use_cache)

    if stats and "ytd_run_totals" in stats:
        yearly_distance_mi = stats["ytd_run_totals"]["distance"] * 0.000621371
        logger.info(f"Got YTD mileage from stats endpoint: {yearly_distance_mi:.1f} mi")

    # Get recent activities for weekly stats and run details
    activities = fetch_recent_activities(limit=30, use_cache=use_cache)

    if not activities and yearly_distance_mi is None:
        return None

    # Calculate weekly total and gather recent runs
    weekly_distance = 0
    recent_runs = []
    today_eastern = now_eastern.date()

    if activities:
        for activity in activities:
            if activity["type"] != "Run":
                continue

            activity_date_utc = datetime.strptime(
                activity["start_date"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            activity_date_eastern = activity_date_utc.astimezone(EASTERN)

            # Weekly total
            if activity_date_eastern >= week_start_eastern:
                weekly_distance += activity["distance"]

            # Recent runs (last 7 days)
            days_ago = (today_eastern - activity_date_eastern.date()).days
            if 0 <= days_ago <= 7:
                distance_mi = activity["distance"] * 0.000621371
                elevation_ft = activity.get("total_elevation_gain", 0) * 3.28084

                # Calculate pace
                if activity["distance"] > 0:
                    pace_sec_per_meter = activity["moving_time"] / activity["distance"]
                    pace_min_per_mile = pace_sec_per_meter * 1609.34 / 60
                    pace_minutes = int(pace_min_per_mile)
                    pace_seconds = int((pace_min_per_mile - pace_minutes) * 60)
                    pace_str = f"{pace_minutes}:{pace_seconds:02d}/mi"
                else:
                    pace_str = "0:00/mi"

                recent_runs.append({
                    "id": activity["id"],
                    "name": activity.get("name", "Run"),
                    "date": activity_date_eastern.date().isoformat(),
                    "day_of_week": activity_date_eastern.strftime("%A"),
                    "distance_mi": round(distance_mi, 1),
                    "elevation_ft": round(elevation_ft, 0),
                    "pace": pace_str,
                    "moving_time_sec": activity["moving_time"],
                    "strava_url": f"https://www.strava.com/activities/{activity['id']}",
                })

    weekly_distance_mi = weekly_distance * 0.000621371

    # Calculate projections
    avg_miles_per_day = yearly_distance_mi / days_elapsed if days_elapsed > 0 else 0
    projected_yearly_mi = avg_miles_per_day * days_in_year

    # Calculate milestone ranges
    if yearly_distance_mi == 0:
        milestone_low = 0
        milestone_high = 500
    else:
        milestone_low = int(projected_yearly_mi / 500) * 500
        milestone_high = milestone_low + 500

    # Calculate miles/day needed for milestones
    miles_needed_low = milestone_low - yearly_distance_mi
    miles_needed_high = milestone_high - yearly_distance_mi
    miles_per_day_low = max(0, miles_needed_low / days_remaining) if days_remaining > 0 else 0
    miles_per_day_high = miles_needed_high / days_remaining if days_remaining > 0 else 0

    return {
        "weekly_distance_mi": round(weekly_distance_mi, 1),
        "yearly_distance_mi": round(yearly_distance_mi, 1) if yearly_distance_mi else 0,
        "projected_yearly_mi": round(projected_yearly_mi, 0) if projected_yearly_mi else 0,
        "avg_miles_per_day": round(avg_miles_per_day, 2),
        "days_elapsed": round(days_elapsed, 1),
        "days_remaining": round(days_remaining, 1),
        "milestone_low": milestone_low,
        "milestone_high": milestone_high,
        "miles_per_day_low": round(miles_per_day_low, 2),
        "miles_per_day_high": round(miles_per_day_high, 2),
        "recent_runs": recent_runs,
        "progress_percent": round((projected_yearly_mi - milestone_low) / 500 * 100, 1) if projected_yearly_mi else 0,
    }
