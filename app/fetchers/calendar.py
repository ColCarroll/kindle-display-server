"""Calendar data fetcher with caching."""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import config
from app.cache import sqlite as cache

# Use timezone.utc for Python 3.10 compatibility (datetime.UTC added in 3.11)
UTC = timezone.utc  # noqa: UP017
EASTERN = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def _get_cache_key() -> str:
    """Generate cache key for calendar data."""
    calendar_ids = ",".join(sorted(config.GOOGLE_CALENDAR_IDS))
    return f"calendar:{hashlib.md5(calendar_ids.encode()).hexdigest()[:8]}"


def fetch_calendar_events(use_cache: bool = True) -> list[dict[str, Any]]:
    """Fetch calendar events from Google Calendar API with caching.

    Returns a list of events with summary, start, end, calendar_name
    """
    cache_key = _get_cache_key()

    # Check cache
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None and isinstance(cached, list):
            logger.info("Using cached calendar data")
            return cached

    # Check if token file exists
    if not config.GOOGLE_CALENDAR_TOKEN_FILE or not os.path.exists(
        config.GOOGLE_CALENDAR_TOKEN_FILE
    ):
        logger.info("No calendar token file configured, using sample data")
        now = datetime.now(EASTERN)
        return [
            {
                "summary": "Morning workout",
                "start": now.replace(hour=7, minute=0).isoformat(),
                "end": now.replace(hour=8, minute=0).isoformat(),
                "calendar_name": "Sample",
            },
            {
                "summary": "Team meeting",
                "start": now.replace(hour=10, minute=0).isoformat(),
                "end": now.replace(hour=11, minute=0).isoformat(),
                "calendar_name": "Sample",
            },
            {
                "summary": "Lunch with client",
                "start": now.replace(hour=12, minute=30).isoformat(),
                "end": now.replace(hour=13, minute=30).isoformat(),
                "calendar_name": "Sample",
            },
        ]

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # Load credentials from token file
        creds = Credentials.from_authorized_user_file(
            config.GOOGLE_CALENDAR_TOKEN_FILE, ["https://www.googleapis.com/auth/calendar.readonly"]
        )

        # Refresh if expired
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(config.GOOGLE_CALENDAR_TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

        service = build("calendar", "v3", credentials=creds)

        # Get events from now until one month from now
        now_utc = datetime.now(UTC)
        one_month_later = now_utc + timedelta(days=30)

        time_min = now_utc.isoformat()
        time_max = one_month_later.isoformat()

        # Get calendar names
        calendar_names = {}
        try:
            calendar_list = service.calendarList().list().execute()
            for cal in calendar_list.get("items", []):
                calendar_names[cal["id"]] = cal.get("summary", cal["id"])
        except Exception as e:
            logger.error(f"Failed to fetch calendar names: {e}")

        # Fetch events from all configured calendars
        all_events = []
        for calendar_id in config.GOOGLE_CALENDAR_IDS:
            calendar_id = calendar_id.strip()
            if not calendar_id:
                continue

            try:
                logger.info(f"Fetching events from calendar: {calendar_id}")
                events_result = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        maxResults=50,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                events = events_result.get("items", [])
                calendar_name = calendar_names.get(calendar_id, "Unknown")

                for event in events:
                    start = event["start"].get("dateTime", event["start"].get("date"))
                    end = event["end"].get("dateTime", event["end"].get("date"))
                    all_events.append(
                        {
                            "summary": event.get("summary", "Untitled"),
                            "start": start,
                            "end": end,
                            "calendar_name": calendar_name,
                            "is_all_day": "T" not in start,
                        }
                    )

            except Exception as e:
                logger.error(f"Failed to fetch events from calendar {calendar_id}: {e}")
                continue

        # Sort all events by start time
        all_events.sort(key=lambda x: x["start"])

        # Cache the result
        cache.set(cache_key, all_events, config.CALENDAR_CACHE_TTL)

        return all_events

    except Exception as e:
        logger.error(f"Failed to fetch calendar events: {e}")
        return []


def get_events_by_day(use_cache: bool = True) -> dict[str, Any]:
    """Get calendar events grouped by day for web display.

    Returns:
        Dictionary with:
            - today: List of events for today
            - tomorrow: List of events for tomorrow
            - future: Dict of date -> events for next 5 days
    """
    events = fetch_calendar_events(use_cache)

    now = datetime.now(EASTERN)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()

    events_by_day = {}

    for event in events:
        start_str = event.get("start")

        # Parse start time
        if isinstance(start_str, str):
            if "T" in start_str:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            else:
                start_dt = datetime.fromisoformat(start_str)
                start_dt = start_dt.replace(tzinfo=EASTERN)
        else:
            continue

        # Convert to Eastern time for grouping
        if start_dt.tzinfo is not None:
            start_dt_eastern = start_dt.astimezone(EASTERN)
        else:
            start_dt_eastern = start_dt.replace(tzinfo=EASTERN)

        event_date = start_dt_eastern.date()
        date_key = event_date.isoformat()

        if date_key not in events_by_day:
            events_by_day[date_key] = []

        # Format time for display
        if event.get("is_all_day"):
            time_str = "all-day"
        else:
            time_str = start_dt_eastern.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")

        events_by_day[date_key].append(
            {
                "summary": event["summary"],
                "time": time_str,
                "calendar_name": event.get("calendar_name", ""),
                "is_all_day": event.get("is_all_day", False),
            }
        )

    # Organize by today/tomorrow/future
    today_events = events_by_day.get(today.isoformat(), [])
    tomorrow_events = events_by_day.get(tomorrow.isoformat(), [])

    # Get future events (next 5 days after tomorrow)
    future = {}
    for i in range(2, 7):
        future_date = (now + timedelta(days=i)).date()
        date_key = future_date.isoformat()
        if date_key in events_by_day:
            future[date_key] = {
                "label": future_date.strftime("%a %m/%d").replace(" 0", " "),
                "events": events_by_day[date_key],
            }

    return {
        "today": today_events,
        "tomorrow": tomorrow_events,
        "future": future,
        "today_label": today.strftime("%A, %B %d"),
        "tomorrow_label": tomorrow.strftime("%A, %B %d"),
    }
