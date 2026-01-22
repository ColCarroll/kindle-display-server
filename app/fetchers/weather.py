"""Weather data fetcher with caching."""

import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from astral import LocationInfo
from astral.sun import sun

from app import config
from app.cache import sqlite as cache

# Use timezone.utc for Python 3.10 compatibility (datetime.UTC added in 3.11)
UTC = timezone.utc  # noqa: UP017
EASTERN = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def _fetch_with_retry(url: str, headers: dict, max_retries: int = 3, timeout: int = 20):
    """Fetch URL with retry logic and exponential backoff."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait_time = 2**attempt
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


def _get_cache_key(lat: str, lon: str) -> str:
    """Generate cache key for weather data."""
    return f"weather:{lat}:{lon}"


def get_sunrise_sunset(lat: float, lon: float, date) -> tuple[datetime, datetime]:
    """Calculate sunrise and sunset times for a given location and date."""
    location = LocationInfo(latitude=lat, longitude=lon)
    s = sun(location.observer, date=date, tzinfo=EASTERN)
    return s["sunrise"], s["sunset"]


def fetch_weather_data(
    lat: str | None = None, lon: str | None = None, use_cache: bool = True
) -> dict[str, Any] | None:
    """Fetch weather data from Weather.gov API with caching.

    Args:
        lat: Latitude (optional, defaults to config.WEATHER_LAT_1)
        lon: Longitude (optional, defaults to config.WEATHER_LON_1)
        use_cache: Whether to use cached data if available

    Returns:
        Dictionary with city, periods, and gridpoint data, or None on error
    """
    if lat is None:
        lat = config.WEATHER_LAT_1
    if lon is None:
        lon = config.WEATHER_LON_1

    cache_key = _get_cache_key(lat, lon)

    # Check cache
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.info(f"Using cached weather data for {lat},{lon}")
            return cached

    # Fetch fresh data
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

        # Step 2: Get hourly forecast
        logger.info(f"Fetching hourly forecast from {forecast_hourly_url}")
        forecast_response = _fetch_with_retry(forecast_hourly_url, headers)
        forecast_data = forecast_response.json()

        # Step 3: Get raw gridpoint data
        logger.info(f"Fetching gridpoint data from {gridpoint_url}")
        gridpoint_response = _fetch_with_retry(gridpoint_url, headers)
        gridpoint_data = gridpoint_response.json()

        result = {
            "city": f"{city}, {state}",
            "lat": lat,
            "lon": lon,
            "periods": forecast_data["properties"]["periods"][:120],
            "gridpoint": gridpoint_data["properties"],
            "fetched_at": datetime.now(UTC).isoformat(),
        }

        # Cache the result
        cache.set(cache_key, result, config.WEATHER_CACHE_TTL)

        return result

    except Exception as e:
        logger.error(f"Failed to fetch weather data: {e}")
        return None


def get_processed_weather(lat: str | None = None, lon: str | None = None) -> dict[str, Any] | None:
    """Get weather data processed for web display.

    Returns a dictionary with:
        - city: Location name
        - current_temp: Current temperature
        - current_desc: Current weather description
        - hourly: List of hourly forecasts with temp, precip, time
        - daily_precip: Dictionary of daily precipitation totals
    """
    data = fetch_weather_data(lat, lon)
    if not data:
        return None

    periods = data["periods"]
    gridpoint = data.get("gridpoint", {})

    # Get current time
    now = datetime.now(UTC)
    current_hour = now.replace(minute=0, second=0, microsecond=0)

    # Parse gridpoint precipitation data
    qpf_values = gridpoint.get("quantitativePrecipitation", {}).get("values", [])
    snow_values = gridpoint.get("snowfallAmount", {}).get("values", [])

    qpf_by_hour = {}
    snow_by_hour = {}

    for qpf_entry in qpf_values:
        valid_time = qpf_entry.get("validTime", "")
        value = qpf_entry.get("value")
        if value is not None and "/" in valid_time:
            start_time_str = valid_time.split("/")[0]
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            qpf_by_hour[start_time.replace(minute=0, second=0, microsecond=0)] = value / 25.4

    for snow_entry in snow_values:
        valid_time = snow_entry.get("validTime", "")
        value = snow_entry.get("value")
        if value is not None and "/" in valid_time:
            start_time_str = valid_time.split("/")[0]
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            snow_by_hour[start_time.replace(minute=0, second=0, microsecond=0)] = value / 25.4

    # Process hourly data
    hourly = []
    daily_precip = {}
    daily_is_snow = {}

    for period in periods:
        start_time = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
        if start_time < current_hour:
            continue

        hour_key = start_time.replace(minute=0, second=0, microsecond=0)
        qpf_amount = qpf_by_hour.get(hour_key, 0)
        snow_amount = snow_by_hour.get(hour_key, 0)
        precip_amount = max(qpf_amount, snow_amount)

        forecast = period.get("shortForecast", "").lower()
        is_snowy = (
            any(
                keyword in forecast
                for keyword in ["snow", "flurries", "sleet", "wintry", "freezing"]
            )
            or snow_amount > 0
        )

        # Get sunrise/sunset for nighttime detection
        start_eastern = start_time.astimezone(EASTERN)
        sunrise, sunset = get_sunrise_sunset(float(data["lat"]), float(data["lon"]), start_eastern.date())
        is_night = start_eastern < sunrise or start_eastern >= sunset

        hourly.append({
            "time": start_time.isoformat(),
            "time_eastern": start_eastern.isoformat(),
            "hour": start_eastern.hour,
            "temp": period["temperature"],
            "precip_prob": period.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
            "precip_amount": precip_amount,
            "is_snow": is_snowy,
            "is_night": is_night,
            "description": period.get("shortForecast", ""),
        })

        # Aggregate daily precipitation
        date_key = start_time.date().isoformat()
        if date_key not in daily_precip:
            daily_precip[date_key] = 0
            daily_is_snow[date_key] = False
        daily_precip[date_key] += precip_amount
        if is_snowy:
            daily_is_snow[date_key] = True

    # Get current conditions
    current_temp = hourly[0]["temp"] if hourly else None
    current_desc = hourly[0]["description"] if hourly else None

    return {
        "city": data["city"],
        "lat": data["lat"],
        "lon": data["lon"],
        "current_temp": current_temp,
        "current_desc": current_desc,
        "hourly": hourly,
        "daily_precip": {k: {"amount": v, "is_snow": daily_is_snow[k]} for k, v in daily_precip.items()},
        "fetched_at": data.get("fetched_at"),
    }
