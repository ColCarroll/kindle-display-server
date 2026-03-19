"""Strava partial route handlers."""

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.cache import sqlite as cache
from app.fetchers.strava import get_running_summary, polyline_to_svg_path
from app.web.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/partials/strava", response_class=HTMLResponse)
async def strava_partial(request: Request, _user: str = Depends(require_auth)):
    """Strava partial for HTMX loading."""
    try:
        data = await asyncio.to_thread(get_running_summary)

        if not data:
            return templates.TemplateResponse(
                "partials/strava.html",
                {"request": request, "error": "Strava data unavailable"},
            )

        # Convert polylines to SVG paths for each run
        for day in data.get("last_7_days", []):
            if day.get("run") and day["run"].get("polyline"):
                day["run"]["svg_path"] = polyline_to_svg_path(day["run"]["polyline"], 80, 80)

        # Fetch shoe assignments for this week's runs
        activity_ids = [
            day["run"]["id"] for day in data.get("last_7_days", []) if day.get("run")
        ]
        shoe_assignments = cache.get_run_shoes_for_activities(activity_ids)

        return templates.TemplateResponse(
            "partials/strava.html",
            {"request": request, "data": data, "shoe_assignments": shoe_assignments},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/strava.html",
            {"request": request, "error": f"Error loading Strava: {e}"},
        )
