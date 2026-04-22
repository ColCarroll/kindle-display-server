"""Dashboard route handlers."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.web.auth import get_current_user
from app.web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    user_email = get_current_user(request)
    if not user_email:
        return RedirectResponse(url="/login-page", status_code=302)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user_email": user_email,
            "user_name": request.session.get("user_name"),
        },
    )


@router.get("/login-page", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    """Login page."""
    user_email = get_current_user(request)
    if user_email:
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
        },
    )
