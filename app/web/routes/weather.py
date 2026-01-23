"""Weather partial route handlers."""

import logging

import requests
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.cache import sqlite as db
from app.fetchers.weather import get_processed_weather
from app.web.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


def geocode_zip(zip_code: str) -> tuple[str, str] | None:
    """Convert a US zip code to lat/lon using zippopotam.us API."""
    try:
        url = f"https://api.zippopotam.us/us/{zip_code}"
        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()

        places = data.get("places", [])
        if places:
            return places[0]["latitude"], places[0]["longitude"]
        return None
    except Exception as e:
        logger.error(f"Geocoding failed for {zip_code}: {e}")
        return None


@router.get("/partials/weather", response_class=HTMLResponse)
async def weather_partial(
    request: Request,
    show_all: bool = False,
    _user: str = Depends(require_auth),
):
    """Weather partial for HTMX loading."""
    try:
        # Start with default locations from config (these can't be deleted)
        location_configs = [
            {"lat": config.WEATHER_LAT_1, "lon": config.WEATHER_LON_1, "id": None, "custom_name": "Home"},
            {"lat": config.WEATHER_LAT_2, "lon": config.WEATHER_LON_2, "id": None, "custom_name": "Wentworth"},
        ]

        # Add saved locations from database only if show_all is true
        saved_locations = db.get_weather_locations()
        if show_all:
            for loc in saved_locations:
                location_configs.append({
                    "lat": loc["lat"],
                    "lon": loc["lon"],
                    "id": loc["id"],
                    "custom_name": loc["name"],
                })

        # Fetch weather for all locations
        locations = []
        for loc_config in location_configs:
            weather = get_processed_weather(loc_config["lat"], loc_config["lon"])
            if weather:
                weather["location_id"] = loc_config.get("id")
                # Use custom name if provided
                if loc_config.get("custom_name"):
                    weather["city"] = loc_config["custom_name"]
                locations.append(weather)

        if not locations:
            return templates.TemplateResponse(
                "partials/weather.html",
                {"request": request, "error": "Weather data unavailable"},
            )

        # Compute shared y-axis limits across all locations
        all_temps = []
        all_precip = []
        for loc in locations:
            all_temps.extend([h["temp"] for h in loc.get("hourly", [])[:120]])
            all_precip.extend([h["precip_amount"] for h in loc.get("hourly", [])[:120]])

        global_min_temp = min(all_temps) if all_temps else 0
        global_max_temp = max(all_temps) if all_temps else 100
        # Precip y-axis max: at least 0.5", or 120% of max precip (like Kindle version)
        max_precip = max(all_precip) if all_precip else 0
        global_max_precip = max(0.5, max_precip * 1.2)

        return templates.TemplateResponse(
            "partials/weather.html",
            {
                "request": request,
                "locations": locations,
                "global_min_temp": global_min_temp,
                "global_max_temp": global_max_temp,
                "global_max_precip": global_max_precip,
                "show_all": show_all,
                "has_saved_locations": len(saved_locations) > 0,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/weather.html",
            {"request": request, "error": f"Error loading weather: {e}"},
        )


@router.post("/weather/locations", response_class=HTMLResponse)
async def add_location(
    request: Request,
    name: str = Form(...),
    zip_code: str = Form(...),
    _user: str = Depends(require_auth),
):
    """Add a new weather location."""
    logger.info(f"Adding location: name={name}, zip_code={zip_code}")
    # Geocode the zip code
    coords = geocode_zip(zip_code)
    if not coords:
        # Return error message that HTMX can display
        return HTMLResponse(
            content=f'<div class="error-message">Could not find location for zip code: {zip_code}</div>',
            status_code=200,
        )

    lat, lon = coords
    location_id = db.add_weather_location(name, zip_code, lat, lon)
    logger.info(f"Added location id={location_id}: {name} at {lat},{lon}")

    # Return a refresh trigger for HTMX
    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": "locationsChanged"},
    )


@router.delete("/weather/locations/{location_id}", response_class=HTMLResponse)
async def delete_location(
    location_id: int,
    _user: str = Depends(require_auth),
):
    """Delete a weather location."""
    db.delete_weather_location(location_id)

    # Return a refresh trigger for HTMX
    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": "locationsChanged"},
    )
