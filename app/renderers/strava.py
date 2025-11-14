"""
Strava renderer using Strava API v3
Displays recent activities and weekly summary
"""

import logging
from datetime import datetime, timedelta

import numpy as np
import requests
from matplotlib.axes import Axes

from app import config

logger = logging.getLogger(__name__)

# Cache for access token
_access_token_cache = None
_token_expiry = None


def refresh_access_token():
    """Refresh Strava access token using refresh token"""
    global _access_token_cache, _token_expiry

    if not all([config.STRAVA_CLIENT_ID, config.STRAVA_CLIENT_SECRET, config.STRAVA_REFRESH_TOKEN]):
        logger.warning("Strava credentials not fully configured")
        return None

    # Check if cached token is still valid
    if _access_token_cache and _token_expiry and datetime.now() < _token_expiry:
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
        _token_expiry = datetime.now() + timedelta(
            seconds=token_data["expires_in"] - 300
        )  # 5min buffer

        return _access_token_cache
    except Exception as e:
        logger.error(f"Failed to refresh Strava token: {e}")
        return None


def fetch_recent_activities(limit=30, after=None):
    """Fetch recent activities from Strava

    Args:
        limit: Maximum number of activities per page (max 200)
        after: Unix timestamp to fetch activities after this date
    """
    access_token = refresh_access_token()
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
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch Strava activities: {e}")
        return None


def fetch_all_activities_since(start_date):
    """Fetch all activities since a given date using pagination

    Args:
        start_date: datetime object for the start of the period

    Returns:
        List of all activities since start_date
    """
    # Convert to Unix timestamp
    after_timestamp = int(start_date.timestamp())

    all_activities = []
    page = 1

    while True:
        url = "https://www.strava.com/api/v3/athlete/activities"
        access_token = refresh_access_token()
        if not access_token:
            break

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "per_page": 200,  # Max allowed by Strava
            "page": page,
            "after": after_timestamp,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            activities = response.json()

            if not activities:
                break

            all_activities.extend(activities)
            logger.info(
                f"Fetched page {page}: {len(activities)} activities (total: {len(all_activities)})"
            )

            # If we got fewer than 200, we've reached the end
            if len(activities) < 200:
                break

            page += 1

        except Exception as e:
            logger.error(f"Failed to fetch activities page {page}: {e}")
            break

    return all_activities


def fetch_athlete_stats(athlete_id):
    """Fetch athlete statistics including YTD totals

    Args:
        athlete_id: The athlete's ID (use 'me' for authenticated athlete)

    Returns:
        Dictionary with stats including ytd_run_totals, recent_run_totals, all_run_totals
        Note: Only includes activities with Everyone visibility
    """
    access_token = refresh_access_token()
    if not access_token:
        return None

    # First get the athlete ID if not provided
    if athlete_id is None:
        url = "https://www.strava.com/api/v3/athlete"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            athlete_data = response.json()
            athlete_id = athlete_data["id"]
        except Exception as e:
            logger.error(f"Failed to fetch athlete info: {e}")
            return None

    url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch athlete stats: {e}")
        return None


def fetch_activity_streams(activity_id):
    """Fetch detailed activity stream data (lat/lng, altitude, etc)"""
    access_token = refresh_access_token()
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


def render_strava(ax: Axes):
    """
    Render Strava activity summary on the given axes.
    Shows 7-day running summary and year-to-date mileage chart.

    Args:
        ax: matplotlib Axes object to draw on
    """
    # Clear the axes
    ax.clear()

    # Calculate date ranges
    now = datetime.now()
    week_start = now - timedelta(days=7)
    year_start = datetime(now.year, 1, 1)

    # Calculate days elapsed in year (for pace calculation)
    days_elapsed = (now - year_start).days + 1
    days_in_year = (
        366 if now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0) else 365
    )

    # Try to use the stats endpoint first (much faster)
    yearly_distance_mi = None
    stats = fetch_athlete_stats(None)

    if stats and "ytd_run_totals" in stats:
        # Stats endpoint returns distance in meters
        yearly_distance_mi = stats["ytd_run_totals"]["distance"] * 0.000621371
        logger.info(f"Got YTD mileage from stats endpoint: {yearly_distance_mi:.1f} mi")

    # For weekly stats, we still need to fetch recent activities
    # Fetch last 30 activities (should cover most 7-day periods)
    activities = fetch_recent_activities(limit=30)

    if not activities and yearly_distance_mi is None:
        ax.text(
            0.5,
            0.5,
            "Strava data unavailable",
            ha="center",
            va="center",
            fontsize=config.FONT_SIZE_BODY,
        )
        ax.axis("off")
        return

    # Calculate weekly total from recent activities
    weekly_distance = 0  # meters

    if activities:
        for activity in activities:
            # Only process runs
            if activity["type"] != "Run":
                continue

            activity_date = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ")

            # Calculate weekly total (last 7 days)
            if activity_date >= week_start:
                weekly_distance += activity["distance"]

    # If stats endpoint didn't work, fall back to fetching all activities
    if yearly_distance_mi is None:
        logger.info("Stats endpoint unavailable, falling back to fetching all activities")
        all_activities = fetch_all_activities_since(year_start)

        if not all_activities:
            ax.text(
                0.5,
                0.5,
                "Strava data unavailable",
                ha="center",
                va="center",
                fontsize=config.FONT_SIZE_BODY,
            )
            ax.axis("off")
            return

        yearly_distance = 0  # meters
        for activity in all_activities:
            if activity["type"] != "Run":
                continue
            activity_date = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ")
            if activity_date >= year_start:
                yearly_distance += activity["distance"]

        yearly_distance_mi = yearly_distance * 0.000621371

    # Convert weekly distance to miles
    weekly_distance_mi = weekly_distance * 0.000621371

    # Calculate average miles per day and projected yearly total
    avg_miles_per_day = yearly_distance_mi / days_elapsed if days_elapsed > 0 else 0
    projected_yearly_mi = avg_miles_per_day * days_in_year

    # Calculate x-axis bounds (round to nearest 500)
    x_min = int(projected_yearly_mi / 500) * 500
    x_max = (int(projected_yearly_mi / 500) + 1) * 500

    # Layout parameters - everything on one line (moved higher)
    line_y = 0.65  # Higher up in the chart area

    # Calculate days remaining in year
    days_remaining = days_in_year - days_elapsed

    # Left side: 7-day stat (larger text)
    stat_x = 0.05
    ax.text(
        stat_x,
        line_y,
        f"-7d: {weekly_distance_mi:.1f}mi",
        ha="left",
        va="center",
        fontsize=config.FONT_SIZE_TITLE,
        weight="bold",
        transform=ax.transAxes,
    )

    # Chart area (to the right of the stat, shorter to avoid title overlap)
    chart_left = 0.30
    chart_right = 0.90

    # Extend line past the marker points
    line_extension = 0.05
    line_start = chart_left - line_extension
    line_end = chart_right + line_extension

    # Draw main horizontal line (extends past the dots)
    ax.plot(
        [line_start, line_end],
        [line_y, line_y],
        "k-",
        linewidth=2.0,
        transform=ax.transAxes,
    )

    # Calculate positions for the three markers based on actual mileage values
    # Map mileage to x position: x_min maps to chart_left, x_max maps to chart_right
    def mileage_to_x(miles):
        return chart_left + (miles - x_min) / (x_max - x_min) * (chart_right - chart_left)

    left_marker_x = mileage_to_x(x_min)
    right_marker_x = mileage_to_x(x_max)
    current_x = mileage_to_x(projected_yearly_mi)

    # Draw three dots
    # Left dot (x_min milestone)
    ax.plot(
        [left_marker_x],
        [line_y],
        "ko",
        markersize=6,
        transform=ax.transAxes,
    )

    # Current position dot
    ax.plot(
        [current_x],
        [line_y],
        "ko",
        markersize=8,
        transform=ax.transAxes,
    )

    # Right dot (x_max milestone)
    ax.plot(
        [right_marker_x],
        [line_y],
        "ko",
        markersize=6,
        transform=ax.transAxes,
    )

    # Labels for the markers
    # Calculate miles/day needed from TODAY to reach each target
    miles_needed_for_x_min = x_min - yearly_distance_mi
    miles_needed_for_x_max = x_max - yearly_distance_mi

    miles_per_day_for_x_min = miles_needed_for_x_min / days_remaining if days_remaining > 0 else 0
    miles_per_day_for_x_max = miles_needed_for_x_max / days_remaining if days_remaining > 0 else 0

    # Left: x_min with miles/day needed from today
    ax.text(
        left_marker_x,
        line_y + 0.08,
        f"{x_min}mi",
        ha="center",
        va="bottom",
        fontsize=config.FONT_SIZE_BODY,
        transform=ax.transAxes,
    )
    ax.text(
        left_marker_x,
        line_y - 0.08,
        f"{miles_per_day_for_x_min:.2f}/day",
        ha="center",
        va="top",
        fontsize=config.FONT_SIZE_SMALL,
        transform=ax.transAxes,
    )

    # Right: x_max with miles/day needed from today
    ax.text(
        right_marker_x,
        line_y + 0.08,
        f"{x_max}mi",
        ha="center",
        va="bottom",
        fontsize=config.FONT_SIZE_BODY,
        transform=ax.transAxes,
    )
    ax.text(
        right_marker_x,
        line_y - 0.08,
        f"{miles_per_day_for_x_max:.2f}/day",
        ha="center",
        va="top",
        fontsize=config.FONT_SIZE_SMALL,
        transform=ax.transAxes,
    )

    # Current position: just the numbers, no text
    ax.text(
        current_x,
        line_y + 0.08,
        f"{projected_yearly_mi:.0f}mi",
        ha="center",
        va="bottom",
        fontsize=config.FONT_SIZE_BODY,
        transform=ax.transAxes,
    )
    ax.text(
        current_x,
        line_y - 0.08,
        f"{avg_miles_per_day:.2f}/day",
        ha="center",
        va="top",
        fontsize=config.FONT_SIZE_SMALL,
        transform=ax.transAxes,
    )

    # Add GPS route visualizations for last 7 days (Monday-Sunday)
    square_size = 0.09  # Size of each route visualization
    y_start = 0.12  # Position below the line chart

    # Get the most recent run for each day of the week (within last 7 days)
    today = now.date()

    # Group activities by day of week, keeping only the most recent (and longest) for each
    runs_by_day_of_week = {}  # Key: 0-6 (Monday-Sunday), Value: activity

    if activities:
        for activity in activities:
            if activity["type"] != "Run":
                continue

            activity_date = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ").date()

            # Only consider activities within the last 7 days
            days_ago = (today - activity_date).days
            if days_ago < 0 or days_ago >= 7:
                continue

            day_of_week = activity_date.weekday()  # 0=Monday, 6=Sunday

            # Keep the most recent run for this day of week
            # (activities are ordered most recent first, so first one we see is most recent)
            if day_of_week not in runs_by_day_of_week:
                runs_by_day_of_week[day_of_week] = activity
            else:
                # If same day of week, keep the longer one
                if activity["distance"] > runs_by_day_of_week[day_of_week]["distance"]:
                    runs_by_day_of_week[day_of_week] = activity

    # Display 7 columns (Monday through Sunday)
    x_positions = [0.0, 0.14, 0.28, 0.42, 0.56, 0.7, 0.84]
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for day_of_week, day_name in enumerate(day_names):
        x_pos = x_positions[day_of_week]

        # Check if there's a run for this day of the week
        run = runs_by_day_of_week.get(day_of_week)

        if run:
            # Calculate stats
            run_distance_mi = run["distance"] * 0.000621371
            run_elevation_ft = run.get("total_elevation_gain", 0) * 3.28084

            # Calculate pace
            if run["distance"] > 0:
                pace_sec_per_meter = run["moving_time"] / run["distance"]
                pace_min_per_mile = pace_sec_per_meter * 1609.34 / 60
                pace_minutes = int(pace_min_per_mile)
                pace_seconds = int((pace_min_per_mile - pace_minutes) * 60)
                run_pace_str = f"{pace_minutes}:{pace_seconds:02d}/mi"
            else:
                run_pace_str = "0:00/mi"

            # Format stats text - mileage on top, pace/elevation below GPS map
            mileage_str = f"{run_distance_mi:.1f}mi"
            stats_text = f"{mileage_str}\n\n\n\n{run_elevation_ft:.0f}ft\n{run_pace_str}"

            # Add stats text
            ax.text(
                x_pos,
                y_start,
                stats_text,
                ha="left",
                va="center",
                fontsize=config.FONT_SIZE_SMALL,
                transform=ax.transAxes,
            )

            # Fetch and plot route
            streams = fetch_activity_streams(run["id"])

            if streams and "latlng" in streams:
                latlng_data = streams["latlng"]["data"]
                if latlng_data:
                    lats, lngs = zip(*latlng_data)

                    lats = np.array(lats)
                    lngs = np.array(lngs)

                    lat_range = lats.max() - lats.min()
                    lng_range = lngs.max() - lngs.min()

                    if lat_range > 0 and lng_range > 0:
                        # Determine the larger dimension to ensure square aspect
                        max_range = max(lat_range, lng_range)

                        # Normalize to square - make maps taller with 4x vertical scaling
                        lngs_norm = (lngs - lngs.min()) / max_range * square_size
                        lats_norm = 4 * (lats - lats.min()) / max_range * square_size

                        # Center in the square area
                        lng_offset = x_pos + (square_size - lngs_norm.max()) / 2
                        lat_offset = y_start + (square_size - lats_norm.max()) / 2

                        lngs_norm += lng_offset
                        lats_norm += lat_offset

                        # Plot the route
                        ax.plot(
                            lngs_norm,
                            lats_norm,
                            "k-",
                            linewidth=1.0,
                            transform=ax.transAxes,
                            zorder=3,
                        )

    # Add vertical divider lines between columns
    ax.vlines(
        np.array(x_positions)[:-1] + 1.2 * square_size,
        0,
        y_start + 2 * square_size,
        colors="black",
        transform=ax.transAxes,
    )

    # Turn off axis frame
    ax.axis("off")
