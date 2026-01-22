"""Weather partial route handlers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.fetchers.weather import get_processed_weather
from app.web.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/partials/weather", response_class=HTMLResponse)
async def weather_partial(request: Request, _user: str = Depends(require_auth)):
    """Weather partial for HTMX loading."""
    try:
        # Fetch weather for both locations
        locations = []

        # Location 1
        weather1 = get_processed_weather(config.WEATHER_LAT_1, config.WEATHER_LON_1)
        if weather1:
            locations.append(weather1)

        # Location 2
        weather2 = get_processed_weather(config.WEATHER_LAT_2, config.WEATHER_LON_2)
        if weather2:
            locations.append(weather2)

        if not locations:
            return templates.TemplateResponse(
                "partials/weather.html",
                {"request": request, "error": "Weather data unavailable"},
            )

        # Compute shared y-axis limits across all locations
        all_temps = []
        for loc in locations:
            all_temps.extend([h["temp"] for h in loc.get("hourly", [])[:168]])

        global_min_temp = min(all_temps) if all_temps else 0
        global_max_temp = max(all_temps) if all_temps else 100

        return templates.TemplateResponse(
            "partials/weather.html",
            {
                "request": request,
                "locations": locations,
                "global_min_temp": global_min_temp,
                "global_max_temp": global_max_temp,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/weather.html",
            {"request": request, "error": f"Error loading weather: {e}"},
        )
