"""Calendar partial route handlers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.fetchers.calendar import get_events_by_day
from app.web.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/partials/calendar", response_class=HTMLResponse)
async def calendar_partial(request: Request, _user: str = Depends(require_auth)):
    """Calendar partial for HTMX loading."""
    try:
        data = get_events_by_day()

        return templates.TemplateResponse(
            "partials/calendar.html",
            {"request": request, "data": data},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/calendar.html",
            {"request": request, "error": f"Error loading calendar: {e}"},
        )
