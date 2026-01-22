"""Strava partial route handlers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.fetchers.strava import get_running_summary
from app.web.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/partials/strava", response_class=HTMLResponse)
async def strava_partial(request: Request, _user: str = Depends(require_auth)):
    """Strava partial for HTMX loading."""
    try:
        data = get_running_summary()

        if not data:
            return templates.TemplateResponse(
                "partials/strava.html",
                {"request": request, "error": "Strava data unavailable"},
            )

        return templates.TemplateResponse(
            "partials/strava.html",
            {"request": request, "data": data},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/strava.html",
            {"request": request, "error": f"Error loading Strava: {e}"},
        )
