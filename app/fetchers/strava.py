"""Strava data fetcher with caching."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app import config
from app.cache import sqlite as cache


def decode_polyline(polyline_str: str) -> list[tuple[float, float]]:
    """Decode a Google encoded polyline string into a list of (lat, lng) tuples.

    Based on the algorithm at:
    https://developers.google.com/maps/documentation/utilities/polylinealgorithm
    """
    if not polyline_str:
        return []

    index = 0
    lat = 0
    lng = 0
    coordinates = []

    while index < len(polyline_str):
        # Decode latitude
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat

        # Decode longitude
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng

        coordinates.append((lat / 1e5, lng / 1e5))

    return coordinates


def polyline_to_svg_path(polyline_str: str, width: int = 100, height: int = 100) -> str:
    """Convert a polyline to an SVG path string, normalized to fit in the given dimensions."""
    coords = decode_polyline(polyline_str)
    if not coords or len(coords) < 2:
        return ""

    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]

    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    lat_range = max_lat - min_lat or 0.0001
    lng_range = max_lng - min_lng or 0.0001

    # Use the larger range to maintain aspect ratio
    max_range = max(lat_range, lng_range)

    # Normalize coordinates to SVG space with padding
    padding = 5
    usable_width = width - 2 * padding
    usable_height = height - 2 * padding

    svg_points = []
    for lat, lng in coords:
        # Normalize to 0-1 range using the larger dimension for aspect ratio
        x = (lng - min_lng) / max_range
        y = (max_lat - lat) / max_range  # Flip y since SVG y increases downward

        # Scale to usable area and add padding
        svg_x = padding + x * usable_width
        svg_y = padding + y * usable_height
        svg_points.append(f"{svg_x:.1f},{svg_y:.1f}")

    if not svg_points:
        return ""

    return "M" + " L".join(svg_points)

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
    params: dict = {"per_page": min(limit, 200)}

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


def fetch_activities_for_year(year: int, use_cache: bool = True) -> list[dict] | None:
    """Fetch all activities for a given year, using local SQLite cache.

    This is more efficient than fetch_recent_activities for yearly charts
    because it stores activities permanently and only fetches new ones.
    """
    # Get cached activities from SQLite
    cached_activities = cache.get_cached_strava_activities(year)

    # Find the latest cached activity date
    latest_date = cache.get_latest_strava_activity_date(year)

    if use_cache and latest_date:
        # Only fetch activities after the latest cached one
        # Convert ISO date to unix timestamp
        latest_dt = datetime.fromisoformat(latest_date.replace("Z", "+00:00"))
        after_timestamp = int(latest_dt.timestamp())
        logger.info(f"Fetching activities after {latest_date} ({len(cached_activities)} cached)")
    else:
        # Fetch all activities for the year
        year_start = datetime(year, 1, 1, tzinfo=UTC)
        after_timestamp = int(year_start.timestamp())
        logger.info(f"Fetching all activities for {year}")

    # Fetch new activities from API
    new_activities = fetch_recent_activities(limit=200, after=after_timestamp, use_cache=False)

    if new_activities:
        # Filter to only this year and cache them
        year_activities = [
            a for a in new_activities
            if a.get("start_date", "").startswith(str(year))
        ]
        if year_activities:
            added = cache.cache_strava_activities(year_activities)
            logger.info(f"Cached {added} new activities")

        # Combine cached + new (dedup by ID)
        seen_ids = {a["id"] for a in cached_activities}
        for a in year_activities:
            if a["id"] not in seen_ids:
                cached_activities.append(a)
                seen_ids.add(a["id"])

    return cached_activities if cached_activities else None


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
            - last_7_days: List of last 7 days with run data (or None for rest days)
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
    yearly_elevation_ft = None
    stats = fetch_athlete_stats(use_cache)

    if stats and "ytd_run_totals" in stats:
        yearly_distance_mi = stats["ytd_run_totals"]["distance"] * 0.000621371
        yearly_elevation_ft = stats["ytd_run_totals"].get("elevation_gain", 0) * 3.28084
        logger.info(f"Got YTD mileage from stats endpoint: {yearly_distance_mi:.1f} mi")
        logger.info(f"Got YTD elevation from stats endpoint: {yearly_elevation_ft:.0f} ft")

    # Get activities for the current year (uses local SQLite cache)
    activities = fetch_activities_for_year(now_eastern.year, use_cache=use_cache)

    if not activities and yearly_distance_mi is None:
        return None

    # Calculate weekly total and group runs by day
    weekly_distance = 0
    today_eastern = now_eastern.date()

    # Initialize week (Monday-Sunday), with today highlighted
    # Find the Monday of the current week
    days_since_monday = today_eastern.weekday()  # 0=Monday, 6=Sunday
    monday = today_eastern - timedelta(days=days_since_monday)
    last_monday = monday - timedelta(days=7)

    last_7_days = []
    for i in range(7):  # Monday through Sunday
        day_date = monday + timedelta(days=i)
        last_7_days.append({
            "date": day_date.isoformat(),
            "day_name": day_date.strftime("%a"),  # "Mon", "Tue", etc.
            "is_today": day_date == today_eastern,
            "is_future": day_date > today_eastern,
            "run": None,  # Will be filled if there's a run
        })

    # Group runs by date (keep best run per day)
    # Include both this week and last week's runs
    runs_by_date = {}
    last_week_runs_by_weekday = {}  # Key: weekday (0-6), for fallback on future days

    if activities:
        for activity in activities:
            if activity["type"] != "Run":
                continue

            activity_date_utc = datetime.strptime(
                activity["start_date"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            activity_date_eastern = activity_date_utc.astimezone(EASTERN)
            activity_date = activity_date_eastern.date()

            # Weekly total
            if activity_date_eastern >= week_start_eastern:
                weekly_distance += activity["distance"]

            # Check if in this week or last week (for fallback on future days)
            days_ago = (today_eastern - activity_date).days
            if days_ago < 0 or days_ago > 13:  # Only consider last 2 weeks
                continue

            date_key = activity_date.isoformat()
            distance_mi = activity["distance"] * 0.000621371
            elevation_ft = activity.get("total_elevation_gain", 0) * 3.28084

            # Calculate pace
            if activity["distance"] > 0:
                pace_sec_per_meter = activity["moving_time"] / activity["distance"]
                pace_min_per_mile = pace_sec_per_meter * 1609.34 / 60
                pace_minutes = int(pace_min_per_mile)
                pace_seconds = int((pace_min_per_mile - pace_minutes) * 60)
                pace_str = f"{pace_minutes}:{pace_seconds:02d}"
            else:
                pace_str = "0:00"

            run_data = {
                "id": activity["id"],
                "name": activity.get("name", "Run"),
                "distance_mi": round(distance_mi, 1),
                "elevation_ft": round(elevation_ft, 0),
                "pace": pace_str,
                "strava_url": f"https://www.strava.com/activities/{activity['id']}",
                "polyline": activity.get("map", {}).get("summary_polyline", ""),
                "is_last_week": activity_date < monday,  # Flag if from last week
            }

            # Keep the longest run for each date
            if date_key not in runs_by_date or activity["distance"] > runs_by_date[date_key]["distance"]:
                runs_by_date[date_key] = {"distance": activity["distance"], "run": run_data}

            # Also track last week's runs by weekday for fallback
            if last_monday <= activity_date < monday:
                weekday = activity_date.weekday()
                if weekday not in last_week_runs_by_weekday or activity["distance"] > last_week_runs_by_weekday[weekday]["distance"]:
                    last_week_runs_by_weekday[weekday] = {"distance": activity["distance"], "run": run_data}

    # Fill in runs for each day
    for i, day_data in enumerate(last_7_days):
        if day_data["date"] in runs_by_date:
            # This week's run exists
            day_data["run"] = runs_by_date[day_data["date"]]["run"]
        elif day_data["is_future"] and i in last_week_runs_by_weekday:
            # Future day: show last week's run as fallback
            day_data["run"] = last_week_runs_by_weekday[i]["run"]

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

    # Build list of runs with their exact timestamp (fractional day of year)
    # This allows precise plotting of when runs occurred
    runs_this_year = []
    if activities:
        year_start = datetime(now_eastern.year, 1, 1, tzinfo=EASTERN)
        for activity in activities:
            if activity["type"] != "Run":
                continue
            activity_date_utc = datetime.strptime(
                activity["start_date"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            activity_date_eastern = activity_date_utc.astimezone(EASTERN)
            if activity_date_eastern.year == now_eastern.year:
                # Use fractional day for precise timing
                fractional_day = (activity_date_eastern - year_start).total_seconds() / 86400
                distance_mi = activity["distance"] * 0.000621371
                elevation_ft = activity.get("total_elevation_gain", 0) * 3.28084
                runs_this_year.append({
                    "day": fractional_day,
                    "distance_mi": distance_mi,
                    "elevation_ft": elevation_ft,
                })

        # Sort by time
        runs_this_year.sort(key=lambda x: x["day"])

    # Generate detrended chart data with sawtooth pattern
    # - Line slopes DOWN during rest (expected miles accumulate, actual doesn't)
    # - Line jumps UP when you run
    # Detrended value = cumulative - avg_pace * day
    #
    # Using precise timestamps: for each run, show pre-run point then post-run point
    detrended_data = []
    if runs_this_year and avg_miles_per_day > 0:
        # Calculate total from activities to scale to match stats API
        calculated_total = sum(r["distance_mi"] for r in runs_this_year)
        scale = yearly_distance_mi / calculated_total if calculated_total > 0 else 1

        # Start at day 0 with detrended=0
        detrended_data.append({"day": 0, "detrended": 0})

        cumulative = 0
        for run in runs_this_year:
            run_day = run["day"]
            run_distance = run["distance_mi"] * scale

            # Pre-run point: cumulative before this run at this time
            pre_run_detrended = cumulative - avg_miles_per_day * run_day
            detrended_data.append({"day": run_day, "detrended": round(pre_run_detrended, 1)})

            # Post-run point: after adding this run's distance
            cumulative += run_distance
            post_run_detrended = cumulative - avg_miles_per_day * run_day
            detrended_data.append({"day": run_day + 0.001, "detrended": round(post_run_detrended, 1)})

        # Final point at current time (should be ~0 by definition of avg)
        final_detrended = cumulative - avg_miles_per_day * days_elapsed
        detrended_data.append({"day": days_elapsed, "detrended": round(final_detrended, 1)})

    # Calculate pace lines (detrended)
    # For target T miles/year: detrended = (T/days_in_year - avg) * day
    pace_targets = [2500, 2750, 3000, 3250, 3500, 3750, 4000]
    pace_lines = []
    for target in pace_targets:
        target_per_day = target / days_in_year
        slope = target_per_day - avg_miles_per_day
        pace_lines.append({
            "target": target,
            "slope": round(slope, 4),
            "target_per_day": round(target_per_day, 2),
        })

    # Generate detrended elevation chart data (same sawtooth pattern as mileage)
    avg_elevation_per_day = yearly_elevation_ft / days_elapsed if days_elapsed > 0 and yearly_elevation_ft else 0
    detrended_elevation_data = []
    if runs_this_year and avg_elevation_per_day > 0:
        # Calculate total from activities to scale to match stats API
        calculated_total_elev = sum(r["elevation_ft"] for r in runs_this_year)
        scale_elev = yearly_elevation_ft / calculated_total_elev if calculated_total_elev > 0 else 1

        # Start at day 0 with detrended=0
        detrended_elevation_data.append({"day": 0, "detrended": 0})

        cumulative_elev = 0
        for run in runs_this_year:
            run_day = run["day"]
            run_elevation = run["elevation_ft"] * scale_elev

            # Pre-run point
            pre_run_detrended = cumulative_elev - avg_elevation_per_day * run_day
            detrended_elevation_data.append({"day": run_day, "detrended": round(pre_run_detrended, 0)})

            # Post-run point
            cumulative_elev += run_elevation
            post_run_detrended = cumulative_elev - avg_elevation_per_day * run_day
            detrended_elevation_data.append({"day": run_day + 0.001, "detrended": round(post_run_detrended, 0)})

        # Final point at current time
        final_detrended = cumulative_elev - avg_elevation_per_day * days_elapsed
        detrended_elevation_data.append({"day": days_elapsed, "detrended": round(final_detrended, 0)})

    # Elevation pace lines (targets in feet)
    elevation_targets = [100000, 125000, 150000, 175000, 200000, 225000, 250000]
    elevation_pace_lines = []
    for target in elevation_targets:
        target_per_day = target / days_in_year
        slope = target_per_day - avg_elevation_per_day
        elevation_pace_lines.append({
            "target": target,
            "slope": round(slope, 2),
            "target_per_day": round(target_per_day, 0),
        })

    return {
        "weekly_distance_mi": round(weekly_distance_mi, 1),
        "yearly_distance_mi": round(yearly_distance_mi, 1) if yearly_distance_mi else 0,
        "yearly_elevation_ft": round(yearly_elevation_ft, 0) if yearly_elevation_ft else 0,
        "projected_yearly_mi": round(projected_yearly_mi, 0) if projected_yearly_mi else 0,
        "avg_miles_per_day": round(avg_miles_per_day, 2),
        "avg_elevation_per_day": round(avg_elevation_per_day, 0) if avg_elevation_per_day else 0,
        "days_elapsed": round(days_elapsed, 1),
        "days_in_year": days_in_year,
        "days_remaining": round(days_remaining, 1),
        "milestone_low": milestone_low,
        "milestone_high": milestone_high,
        "miles_per_day_low": round(miles_per_day_low, 2),
        "miles_per_day_high": round(miles_per_day_high, 2),
        "last_7_days": last_7_days,
        "progress_percent": round((projected_yearly_mi - milestone_low) / 500 * 100, 1) if projected_yearly_mi else 0,
        "detrended_data": detrended_data,
        "pace_lines": pace_lines,
        "detrended_elevation_data": detrended_elevation_data,
        "elevation_pace_lines": elevation_pace_lines,
    }
