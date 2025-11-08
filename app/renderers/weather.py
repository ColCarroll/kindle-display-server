"""
Weather renderer using Weather.gov (National Weather Service) API
Displays current conditions and forecast - FREE, no API key required!
"""

import logging
from datetime import datetime

import requests
from matplotlib.axes import Axes

from app import config

logger = logging.getLogger(__name__)


def fetch_weather_data(lat=None, lon=None):
    """Fetch weather data from Weather.gov API (National Weather Service)"""
    if lat is None:
        lat = config.WEATHER_LAT_1
    if lon is None:
        lon = config.WEATHER_LON_1

    # Weather.gov requires a User-Agent header
    headers = {
        "User-Agent": "(Kindle Display Server, contact@example.com)",
        "Accept": "application/json",
    }

    try:
        # Step 1: Get gridpoint info from lat/lon
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        logger.info(f"Fetching weather gridpoint from {points_url}")

        points_response = requests.get(points_url, headers=headers, timeout=10)
        points_response.raise_for_status()
        points_data = points_response.json()

        # Extract forecast URLs and location info
        forecast_hourly_url = points_data["properties"]["forecastHourly"]
        city = points_data["properties"]["relativeLocation"]["properties"]["city"]
        state = points_data["properties"]["relativeLocation"]["properties"]["state"]

        # Step 2: Get hourly forecast (much higher fidelity - ~156 hours available)
        logger.info(f"Fetching hourly forecast from {forecast_hourly_url}")
        forecast_response = requests.get(forecast_hourly_url, headers=headers, timeout=10)
        forecast_response.raise_for_status()
        forecast_data = forecast_response.json()

        return {
            "city": f"{city}, {state}",
            "periods": forecast_data["properties"]["periods"][:120],  # 5 days of hourly data
        }

    except Exception as e:
        logger.error(f"Failed to fetch weather data: {e}")
        return None


def render_weather(ax: Axes, lat=None, lon=None, title=None, show_xlabel=True):
    """
    Render weather information on the given axes.
    Minimal design with temperature and precipitation over 7 days.

    Args:
        ax: matplotlib Axes object to draw on
        lat: Latitude (optional, defaults to config.WEATHER_LAT_1)
        lon: Longitude (optional, defaults to config.WEATHER_LON_1)
        title: Custom title (optional, defaults to city name from API)
        show_xlabel: Whether to show x-axis labels (optional, defaults to True)
    """
    data = fetch_weather_data(lat, lon)

    if not data:
        ax.text(
            0.5,
            0.5,
            "Weather data unavailable",
            ha="center",
            va="center",
            fontsize=config.FONT_SIZE_BODY,
        )
        ax.axis("off")
        return

    periods = data["periods"]
    city = data["city"]

    # Extract forecast data (hourly)
    times = []
    temps = []
    precip_probs = []
    has_snow = []  # Track if snow is in forecast for this period

    for period in periods:
        # Parse ISO 8601 timestamp for hourly data
        start_time = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
        times.append(start_time)
        temps.append(period["temperature"])

        # Get precipitation probability (may be None)
        precip = period.get("probabilityOfPrecipitation", {})
        if precip and precip.get("value") is not None:
            precip_probs.append(precip["value"])
        else:
            precip_probs.append(0)

        # Check if snow is in the forecast
        forecast = period.get("shortForecast", "").lower()
        is_snowy = any(
            keyword in forecast for keyword in ["snow", "flurries", "sleet", "wintry", "freezing"]
        )
        has_snow.append(is_snowy)

    # Clear and setup axes
    ax.clear()

    # Remove spines
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Add nighttime shading using axvspan (before plotting data so it's in background)
    # Consider nighttime as 8pm-6am (20:00-06:00)
    # Group consecutive nighttime hours into continuous spans
    in_night = False
    night_start: float | None = None

    for i, dt in enumerate(times):
        is_night = dt.hour < 6 or dt.hour >= 20

        if is_night and not in_night:
            # Start of night span
            night_start = i - 0.5
            in_night = True
        elif not is_night and in_night and night_start is not None:
            # End of night span
            ax.axvspan(night_start, i - 0.5, color="gray", alpha=0.15, zorder=0)
            in_night = False

    # Handle case where forecast ends during nighttime
    if in_night and night_start is not None:
        ax.axvspan(night_start, len(times) - 0.5, color="gray", alpha=0.15, zorder=0)

    # Plot temperature (left y-axis) - use numeric indices for smooth curve
    color_temp = "black"
    ax.plot(range(len(temps)), temps, linewidth=1.5, color=color_temp, zorder=3)
    ax.set_ylabel(
        "°F",
        fontsize=config.FONT_SIZE_SMALL,
        color=color_temp,
        rotation=0,
        labelpad=10,
        ha="right",
        va="center",
    )
    ax.tick_params(axis="y", labelcolor=color_temp, labelsize=config.FONT_SIZE_SMALL)

    # Create second y-axis for precipitation
    ax2 = ax.twinx()

    # Remove spines from second axis
    for spine in ax2.spines.values():
        spine.set_visible(False)

    color_precip = "#666666"
    ax2.plot(
        range(len(precip_probs)),
        precip_probs,
        linewidth=1.5,
        color=color_precip,
        linestyle="--",
        alpha=0.7,
        zorder=3,
    )
    # Hide precipitation axis labels and ticks completely
    ax2.tick_params(
        axis="y", which="both", left=False, right=False, labelleft=False, labelright=False
    )
    ax2.set_ylim(0, 100)  # Always show full 0-100% range

    # Add snowflake markers where snow is expected
    snow_indices = [i for i, is_snowy in enumerate(has_snow) if is_snowy and precip_probs[i] > 0]
    if snow_indices:
        snow_precip_values = [precip_probs[i] for i in snow_indices]
        ax2.scatter(
            snow_indices,
            snow_precip_values,
            marker="*",
            s=30,
            color=color_precip,
            alpha=0.8,
            zorder=5,
        )

    # Set x-axis labels to show dates at midnight
    # Find indices where date changes (at midnight)
    xtick_positions = []
    xtick_labels = []

    for i, dt in enumerate(times):
        if dt.hour == 0 or i == 0:  # Midnight or first entry
            xtick_positions.append(i)
            if i == 0:
                xtick_labels.append(dt.strftime("%a %I%p"))
            else:
                xtick_labels.append(dt.strftime("%a"))

    if show_xlabel:
        ax.set_xticks(xtick_positions)
        ax.set_xticklabels(xtick_labels, fontsize=config.FONT_SIZE_SMALL, rotation=45, ha="right")
    else:
        ax.set_xticks([])
        ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)

    # Add subtle grid
    ax.grid(True, alpha=0.2, linewidth=0.5)
    ax.set_axisbelow(True)

    # Title with current temp - use custom title if provided
    current_temp = temps[0]
    current_desc = periods[0]["shortForecast"]
    display_title = title if title else city
    ax.set_title(
        f"{display_title} - {current_temp}°F, {current_desc}", fontsize=config.FONT_SIZE_BODY, pad=5
    )
