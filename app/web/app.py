"""FastAPI web application for Kindle Dashboard."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.web import auth
from app.web.routes import calendar, dashboard, shoes, strava, weather

logger = logging.getLogger(__name__)


async def _periodic_strava_gear_sync() -> None:
    """Background task: push pending shoe assignments to Strava every 10 minutes."""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            from app.fetchers.strava import sync_pending_shoe_assignments

            await asyncio.to_thread(sync_pending_shoe_assignments)
        except Exception as e:
            logger.error(f"Background Strava gear sync failed: {e}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_periodic_strava_gear_sync())
    yield
    task.cancel()


app = FastAPI(title="Kindle Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)

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
app.include_router(shoes.router)


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
