"""
Weather renderer using Weather.gov (National Weather Service) API
Displays current conditions and forecast - FREE, no API key required!
"""

import logging
import time
from datetime import UTC, datetime

import requests
from astral import LocationInfo
from astral.sun import sun
from matplotlib.axes import Axes

from app import config

logger = logging.getLogger(__name__)


def _fetch_with_retry(url, headers, max_retries=3, timeout=20):
    """Fetch URL with retry logic and exponential backoff."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Timeout on attempt {attempt + 1}/{max_retries}, retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.warning(
                    f"Request failed on attempt {attempt + 1}/{max_retries}: {e}, retrying..."
                )
                time.sleep(wait_time)
            else:
                raise
    return None


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

        points_response = _fetch_with_retry(points_url, headers)
        points_data = points_response.json()

        # Extract forecast URLs and location info
        forecast_hourly_url = points_data["properties"]["forecastHourly"]
        gridpoint_url = points_data["properties"]["forecastGridData"]
        city = points_data["properties"]["relativeLocation"]["properties"]["city"]
        state = points_data["properties"]["relativeLocation"]["properties"]["state"]

        # Step 2: Get hourly forecast (for temps and descriptive text)
        logger.info(f"Fetching hourly forecast from {forecast_hourly_url}")
        forecast_response = _fetch_with_retry(forecast_hourly_url, headers)
        forecast_data = forecast_response.json()

        # Step 3: Get raw gridpoint data (for quantitative precipitation)
        logger.info(f"Fetching gridpoint data from {gridpoint_url}")
        gridpoint_response = _fetch_with_retry(gridpoint_url, headers)
        gridpoint_data = gridpoint_response.json()

        return {
            "city": f"{city}, {state}",
            "periods": forecast_data["properties"]["periods"][:120],  # 5 days of hourly data
            "gridpoint": gridpoint_data["properties"],  # Raw gridpoint data with QPF
        }

    except Exception as e:
        logger.error(f"Failed to fetch weather data: {e}")
        return None


def _get_sunrise_sunset(lat, lon, date):
    """
    Calculate sunrise and sunset times for a given location and date.

    Args:
        lat: Latitude
        lon: Longitude
        date: datetime.date object

    Returns:
        tuple: (sunrise datetime, sunset datetime) in UTC
    """
    location = LocationInfo(latitude=lat, longitude=lon)
    s = sun(location.observer, date=date, tzinfo=UTC)
    return s["sunrise"], s["sunset"]


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
    # Set default lat/lon if not provided
    if lat is None:
        lat = config.WEATHER_LAT_1
    if lon is None:
        lon = config.WEATHER_LON_1

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
    gridpoint = data.get("gridpoint", {})

    # Get current time and round down to the most recent hour
    now = datetime.now(UTC)
    current_hour = now.replace(minute=0, second=0, microsecond=0)

    # Extract gridpoint precipitation data (time-series format)
    qpf_values = gridpoint.get("quantitativePrecipitation", {}).get("values", [])
    snow_values = gridpoint.get("snowfallAmount", {}).get("values", [])

    # Create lookup dictionaries for QPF and snow by hour
    qpf_by_hour = {}
    snow_by_hour = {}

    # Parse QPF data
    for qpf_entry in qpf_values:
        valid_time = qpf_entry.get("validTime", "")
        value = qpf_entry.get("value")

        if value is not None and "/" in valid_time:
            # Format is like "2024-01-15T12:00:00+00:00/PT1H"
            start_time_str = valid_time.split("/")[0]
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            # Convert from mm to inches
            qpf_by_hour[start_time.replace(minute=0, second=0, microsecond=0)] = value / 25.4

    # Parse snow data
    for snow_entry in snow_values:
        valid_time = snow_entry.get("validTime", "")
        value = snow_entry.get("value")

        if value is not None and "/" in valid_time:
            start_time_str = valid_time.split("/")[0]
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            # Convert from mm to inches
            snow_by_hour[start_time.replace(minute=0, second=0, microsecond=0)] = value / 25.4

    # Extract forecast data (hourly), filtering out past hours
    times = []
    temps = []
    precip_probs = []
    has_snow = []  # Track if snow is in forecast for this period
    precip_amounts = []  # Track quantitative precipitation from gridpoint

    for period in periods:
        # Parse ISO 8601 timestamp for hourly data
        start_time = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))

        # Skip periods that are in the past
        if start_time < current_hour:
            continue

        times.append(start_time)
        temps.append(period["temperature"])

        # Get precipitation probability (may be None)
        precip = period.get("probabilityOfPrecipitation", {})
        if precip and precip.get("value") is not None:
            precip_probs.append(precip["value"])
        else:
            precip_probs.append(0)

        # Get quantitative precipitation from gridpoint data
        hour_key = start_time.replace(minute=0, second=0, microsecond=0)
        qpf_amount = qpf_by_hour.get(hour_key, 0)
        snow_amount = snow_by_hour.get(hour_key, 0)

        # Use snow amount if available, otherwise rain amount
        precip_amounts.append(max(qpf_amount, snow_amount))

        # Check if snow is in the forecast (either from text or from snow data)
        forecast = period.get("shortForecast", "").lower()
        is_snowy = (
            any(
                keyword in forecast
                for keyword in ["snow", "flurries", "sleet", "wintry", "freezing"]
            )
            or snow_amount > 0
        )
        has_snow.append(is_snowy)

    # If all data is stale (past), show error
    if not times:
        ax.text(
            0.5,
            0.5,
            "Weather data is stale",
            ha="center",
            va="center",
            fontsize=config.FONT_SIZE_BODY,
        )
        ax.axis("off")
        return

    # Clear and setup axes
    ax.clear()

    # Remove spines
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Add nighttime shading using axvspan (before plotting data so it's in background)
    # Use actual sunrise/sunset times for the location
    # Build a cache of sunrise/sunset times by date
    sunrise_sunset_cache = {}
    for dt in times:
        date_key = dt.date()
        if date_key not in sunrise_sunset_cache:
            sunrise, sunset = _get_sunrise_sunset(lat, lon, date_key)
            sunrise_sunset_cache[date_key] = (sunrise, sunset)

    # Group consecutive nighttime hours into continuous spans
    in_night = False
    night_start: float | None = None

    for i, dt in enumerate(times):
        sunrise, sunset = sunrise_sunset_cache[dt.date()]
        # It's nighttime if before sunrise or after sunset
        is_night = dt < sunrise or dt >= sunset

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

    # TODO: Add climate normals when we find hourly normal temperature data
    # Currently NOAA only provides daily normals (one high/low per day), not hourly

    # Plot temperature (left y-axis) - use numeric indices for smooth curve
    color_temp = "black"
    ax.plot(range(len(temps)), temps, linewidth=1.5, color=color_temp, zorder=3)
    ax.set_ylabel(
        "°F",
        fontsize=config.FONT_SIZE_BODY,
        color=color_temp,
        rotation=0,
        labelpad=10,
        ha="right",
        va="center",
    )
    ax.tick_params(axis="y", labelcolor=color_temp, labelsize=config.FONT_SIZE_BODY)

    # Create second y-axis for precipitation amounts (in inches)
    ax2 = ax.twinx()

    # Remove spines from second axis
    for spine in ax2.spines.values():
        spine.set_visible(False)

    color_precip = "#666666"

    # Set up right y-axis for precipitation
    max_precip = max(precip_amounts) if precip_amounts else 0.1
    ax2.set_ylim(0, max(0.5, max_precip * 1.2))  # At least 0.5", or 120% of max
    ax2.set_ylabel(
        "in/hr",
        fontsize=config.FONT_SIZE_BODY,
        color=color_precip,
        rotation=0,
        labelpad=15,
        ha="left",
        va="center",
    )

    # Format y-axis tick labels with " for inches
    ax2.tick_params(
        axis="y", labelcolor=color_precip, labelsize=config.FONT_SIZE_BODY, labelright=True
    )
    # Use a formatter to add " suffix
    from matplotlib.ticker import FuncFormatter

    ax2.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.1f}"'))

    # Plot precipitation as vertical dotted lines with markers on top
    for i, amount in enumerate(precip_amounts):
        if amount > 0:
            # Draw dotted vertical line from 0 to amount
            ax2.vlines(
                i,
                0,
                amount,
                colors=color_precip,
                linestyles=":",
                alpha=0.7,
                linewidth=1.5,
                zorder=3,
            )

            # Add marker on top - * for snow, o for rain
            marker = "*" if has_snow[i] else "o"
            marker_size = 40 if has_snow[i] else 20
            ax2.scatter(
                [i],
                [amount],
                marker=marker,
                s=marker_size,
                color=color_precip,
                alpha=0.8,
                zorder=5,
            )

    # Calculate daily precipitation totals and display in daytime columns
    # Group by date
    daily_precip = {}
    daily_is_snow = {}

    for i, dt in enumerate(times):
        date_key = dt.date()
        if date_key not in daily_precip:
            daily_precip[date_key] = 0
            daily_is_snow[date_key] = False

        daily_precip[date_key] += precip_amounts[i]
        # If any hour has snow, mark the day as having snow
        if has_snow[i]:
            daily_is_snow[date_key] = True

    # Find daytime periods (roughly 6am-8pm) for each day and place text
    for date_key, total_precip in daily_precip.items():
        if total_precip < 0.01:  # Skip if less than 0.01"
            continue

        # Find all indices for this day during daytime hours
        day_indices = [
            i for i, dt in enumerate(times) if dt.date() == date_key and 6 <= dt.hour < 20
        ]

        if not day_indices:
            continue

        # Place text in middle of daytime period
        center_idx = day_indices[len(day_indices) // 2]

        # Format precipitation text - use snowflake for snow, nothing for rain
        precip_text = f'{total_precip:.1f}"❄' if daily_is_snow[date_key] else f'{total_precip:.1f}"'

        # Place text on the chart
        ax.text(
            center_idx,
            ax.get_ylim()[1] * 0.95,  # Near top of chart
            precip_text,
            ha="center",
            va="top",
            fontsize=config.FONT_SIZE_SMALL,
            color="black",
            weight="bold",
            zorder=10,
        )

    # Set x-axis labels to show dates at midnight
    # Find indices where date changes (at midnight)
    xtick_positions = []
    xtick_labels = []

    for i, dt in enumerate(times):
        if dt.hour == 0:  # Only at midnight, skip first entry
            xtick_positions.append(i)
            xtick_labels.append(dt.strftime("%a"))

    if show_xlabel:
        ax.set_xticks(xtick_positions)
        ax.set_xticklabels(xtick_labels, fontsize=config.FONT_SIZE_BODY, rotation=45, ha="right")
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
