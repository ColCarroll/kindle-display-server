"""
Strava renderer using Strava API v3
Displays recent activities and weekly summary
"""

import requests
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
import numpy as np
from datetime import datetime, timedelta
import logging

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


def fetch_recent_activities(limit=30):
    """Fetch recent activities from Strava"""
    access_token = refresh_access_token()
    if not access_token:
        return None

    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": limit}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch Strava activities: {e}")
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
    Shows 7-day running summary and most recent run plot.

    Args:
        ax: matplotlib Axes object to draw on
    """
    activities = fetch_recent_activities(limit=30)

    if not activities:
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

    # Clear the axes
    ax.clear()

    # Filter for running activities only
    now = datetime.now()
    week_start = now - timedelta(days=7)

    weekly_distance = 0  # meters
    weekly_time = 0  # seconds
    weekly_elevation = 0  # meters
    weekly_count = 0
    most_recent_run = None

    for activity in activities:
        # Only process runs
        if activity["type"] != "Run":
            continue

        activity_date = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ")

        # Track most recent run
        if most_recent_run is None:
            most_recent_run = activity

        # Calculate weekly totals
        if activity_date >= week_start:
            weekly_distance += activity["distance"]
            weekly_time += activity["moving_time"]
            weekly_elevation += activity.get("total_elevation_gain", 0)
            weekly_count += 1

    # Convert weekly totals
    weekly_distance_mi = weekly_distance * 0.000621371
    weekly_elevation_ft = weekly_elevation * 3.28084

    # Calculate average pace (min/mile)
    if weekly_distance > 0:
        avg_pace_sec_per_meter = weekly_time / weekly_distance
        avg_pace_min_per_mile = avg_pace_sec_per_meter * 1609.34 / 60
        pace_minutes = int(avg_pace_min_per_mile)
        pace_seconds = int((avg_pace_min_per_mile - pace_minutes) * 60)
        avg_pace_str = f"{pace_minutes}:{pace_seconds:02d}/mi"
    else:
        avg_pace_str = "0:00/mi"

    # Display weekly summary on the left side, each field on its own line
    summary_lines = [
        "Last 7 days:",
        f"{weekly_distance_mi:.1f} mi",
        f"{weekly_elevation_ft:.0f}ft",
        f"{avg_pace_str}",
    ]

    summary_text = "    ".join(summary_lines)
    # Title
    ax.text(
        0.5,
        0.98,
        summary_text,
        ha="center",
        va="top",
        fontsize=config.FONT_SIZE_TITLE,
        weight="bold",
        transform=ax.transAxes,
    )

    square_size = 0.09  # Size of each square plot
    y_start = 0.2

    # Get the last 7 running activities for display
    recent_runs = []
    for activity in activities:
        if activity["type"] == "Run":
            recent_runs.append(activity)
            if len(recent_runs) >= 7:
                break

    # Plot recent runs on the right side - one per row
    if recent_runs:
        x_positions = [0.0, 0.14, 0.28, 0.42, 0.56, 0.7, 0.84]

        for idx, run in enumerate(recent_runs):
            if idx >= len(x_positions):
                break

            x_pos = x_positions[idx]

            # Get day of week
            run_date = datetime.strptime(run["start_date"], "%Y-%m-%dT%H:%M:%SZ")
            day_of_week = run_date.strftime("%A")  # Full day name

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

            # Format stats text
            stats_text = f"{day_of_week}\n\n\n\n{run_distance_mi:.1f}mi\n{run_elevation_ft:.0f}ft\n{run_pace_str}"

            # Add stats text to the right of the square
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

                        # Normalize to square - ensure equal scaling in both dimensions
                        lngs_norm = (lngs - lngs.min()) / max_range * square_size
                        lats_norm = 3 * (lats - lats.min()) / max_range * square_size

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
        ax.vlines(
            np.array(x_positions)[:-1] + 1.2 * square_size,
            0,
            y_start + 2 * square_size,
            colors="black",
            transform=ax.transAxes,
        )

    # Turn off axis frame
    ax.axis("off")
