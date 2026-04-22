"""Calendar partial route handlers."""

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.fetchers.calendar import get_events_by_day
from app.web.auth import require_auth
from app.web.templating import templates

router = APIRouter()


@router.get("/partials/calendar", response_class=HTMLResponse)
async def calendar_partial(request: Request, _user: str = Depends(require_auth)):
    """Calendar partial for HTMX loading."""
    try:
        data = await asyncio.to_thread(get_events_by_day)

        return templates.TemplateResponse(
            "partials/calendar.html",
            {"request": request, "data": data},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/calendar.html",
            {"request": request, "error": f"Error loading calendar: {e}"},
        )
