"""Google OAuth authentication for the web application."""

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse

from app import config

oauth = OAuth()

oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def get_current_user(request: Request) -> str | None:
    """Get the current user's email from session, or None if not logged in."""
    return request.session.get("user_email")


def require_auth(request: Request) -> str:
    """Dependency that requires authentication.

    Returns the user's email if logged in, raises 401 otherwise.
    """
    user_email = get_current_user(request)
    if not user_email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if config.ALLOWED_EMAIL and user_email != config.ALLOWED_EMAIL:
        raise HTTPException(status_code=403, detail="Access denied")
    return user_email


async def login(request: Request) -> RedirectResponse:
    """Redirect to Google OAuth login."""
    redirect_uri = f"{config.WEB_BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


async def auth_callback(request: Request) -> RedirectResponse:
    """Handle Google OAuth callback."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Authentication failed") from e

    user_info = token.get("userinfo")
    if not user_info:
        raise HTTPException(status_code=400, detail="Could not get user info")

    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Could not get email")

    # Check if user is allowed
    if config.ALLOWED_EMAIL and email != config.ALLOWED_EMAIL:
        raise HTTPException(status_code=403, detail="Access denied")

    # Store user in session
    request.session["user_email"] = email
    request.session["user_name"] = user_info.get("name", email)

    return RedirectResponse(url="/", status_code=302)


async def logout(request: Request) -> RedirectResponse:
    """Log out the current user."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
