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

        return templates.TemplateResponse(
            "partials/weather.html",
            {"request": request, "locations": locations},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/weather.html",
            {"request": request, "error": f"Error loading weather: {e}"},
        )
