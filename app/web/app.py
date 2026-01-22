"""FastAPI web application for Kindle Dashboard."""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.web import auth
from app.web.routes import calendar, dashboard, strava, weather

app = FastAPI(title="Kindle Dashboard", docs_url=None, redoc_url=None)

# Session middleware for authentication
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET_KEY,
    max_age=86400 * 7,  # 1 week
)

# Static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include routers
app.include_router(dashboard.router)
app.include_router(weather.router)
app.include_router(calendar.router)
app.include_router(strava.router)


# Auth routes
@app.get("/login")
async def login(request: Request):
    """Redirect to Google OAuth."""
    return await auth.login(request)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle Google OAuth callback."""
    return await auth.auth_callback(request)


@app.get("/logout")
async def logout(request: Request):
    """Log out the current user."""
    return await auth.logout(request)


# Health check
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
