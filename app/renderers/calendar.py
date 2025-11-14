"""
Calendar renderer
Displays today's events and upcoming schedule
"""

import logging
import os
from datetime import datetime, timedelta

from matplotlib.axes import Axes

from app import config

logger = logging.getLogger(__name__)


def fetch_calendar_events():
    """
    Fetch calendar events from Google Calendar API

    Returns a list of events with 'summary', 'start', 'end'
    """
    if not config.GOOGLE_CALENDAR_TOKEN_FILE or not os.path.exists(
        config.GOOGLE_CALENDAR_TOKEN_FILE
    ):
        logger.info("No calendar token file configured, using sample data")
        # Return sample events for demonstration
        now = datetime.now()
        return [
            {
                "summary": "Morning workout",
                "start": now.replace(hour=7, minute=0),
                "end": now.replace(hour=8, minute=0),
            },
            {
                "summary": "Team meeting",
                "start": now.replace(hour=10, minute=0),
                "end": now.replace(hour=11, minute=0),
            },
            {
                "summary": "Lunch with client",
                "start": now.replace(hour=12, minute=30),
                "end": now.replace(hour=13, minute=30),
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
            # Save the refreshed token
            with open(config.GOOGLE_CALENDAR_TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

        # Build the service
        service = build("calendar", "v3", credentials=creds)

        # Get events from now until one month from now
        now = datetime.now()
        one_month_later = now + timedelta(days=30)

        time_min = now.isoformat() + "Z"
        time_max = one_month_later.isoformat() + "Z"

        # First, get calendar names
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
            calendar_id = calendar_id.strip()  # Remove any whitespace
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

                # Get calendar name
                calendar_name = calendar_names.get(calendar_id, "Unknown")

                # Convert to our format
                for event in events:
                    start = event["start"].get("dateTime", event["start"].get("date"))
                    end = event["end"].get("dateTime", event["end"].get("date"))
                    all_events.append(
                        {
                            "summary": event.get("summary", "Untitled"),
                            "start": start,
                            "end": end,
                            "calendar_name": calendar_name,
                        }
                    )

            except Exception as e:
                logger.error(f"Failed to fetch events from calendar {calendar_id}: {e}")
                continue

        # Sort all events by start time
        all_events.sort(key=lambda x: x["start"])

        return all_events

    except Exception as e:
        logger.error(f"Failed to fetch calendar events: {e}")
        return []


def render_calendar(ax: Axes):
    """
    Render calendar events on the given axes.
    Two-column layout: Today/Tomorrow on left, future events on right.

    Args:
        ax: matplotlib Axes object to draw on
    """
    events = fetch_calendar_events()

    # Clear the axes
    ax.clear()

    # Group events by day
    now = datetime.now()
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()

    events_by_day = {}
    for event in events:
        start_str = event.get("start")

        # Parse start time
        if isinstance(start_str, str):
            if "T" in start_str:
                start_dt = datetime.fromisoformat(start_str.replace("Z", ""))
            else:
                start_dt = datetime.fromisoformat(start_str)
        elif isinstance(start_str, datetime):
            start_dt = start_str
        else:
            continue

        event_date = start_dt.date()
        if event_date not in events_by_day:
            events_by_day[event_date] = []
        events_by_day[event_date].append(event)

    # Draw vertical divider line between columns (moved left)
    ax.plot([0.40, 0.40], [0.05, 0.95], "k-", linewidth=0.5, transform=ax.transAxes, zorder=1)

    # Helper function to render a day's events with text wrapping
    def render_day_events(day_date, day_label, x_start, y_start, max_width):
        y_pos = y_start
        day_header_height = 0.10
        event_line_height = 0.08  # More spacing between events

        # Draw day header
        ax.text(
            x_start + 0.02,
            y_pos,
            day_label,
            ha="left",
            va="top",
            fontsize=config.FONT_SIZE_BODY,
            weight="bold",
            transform=ax.transAxes,
        )

        y_pos -= day_header_height

        # Get events for this day
        day_events = events_by_day.get(day_date, [])

        if not day_events:
            ax.text(
                x_start + 0.05,
                y_pos,
                "No events",
                ha="left",
                va="top",
                fontsize=config.FONT_SIZE_SMALL,
                style="italic",
                color="#666666",
                transform=ax.transAxes,
            )
            y_pos -= event_line_height
        else:
            for event in day_events:
                if y_pos < 0.02:
                    break

                summary = event.get("summary", "Untitled")
                start_str = event.get("start")
                end_str = event.get("end")

                # Parse times
                if isinstance(start_str, str):
                    if "T" in start_str:
                        start_dt = datetime.fromisoformat(start_str.replace("Z", ""))
                        end_dt = (
                            datetime.fromisoformat(end_str.replace("Z", "")) if end_str else None
                        )

                        start_time = (
                            start_dt.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")
                        )
                        if end_dt:
                            end_time = (
                                end_dt.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")
                            )
                            time_str = f"{start_time}-{end_time}"
                        else:
                            time_str = start_time
                    else:
                        time_str = "all-day"
                elif isinstance(start_str, datetime):
                    start_dt = start_str
                    end_dt = event.get("end")
                    start_time = start_dt.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")
                    if isinstance(end_dt, datetime):
                        end_time = end_dt.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")
                        time_str = f"{start_time}-{end_time}"
                    else:
                        time_str = start_time
                else:
                    time_str = ""

                # Draw time (bold, dark grey, right-aligned)
                time_x = x_start + 0.15  # Position for time column
                ax.text(
                    time_x,
                    y_pos,
                    time_str,
                    ha="right",
                    va="top",
                    fontsize=config.FONT_SIZE_SMALL,
                    weight="bold",
                    color="#555555",
                    transform=ax.transAxes,
                )

                # Draw event title with simple truncation if needed
                event_x = time_x + 0.02  # Small gap after time
                # Calculate max chars based on available width (roughly 30-40 chars per column)
                max_chars = int(max_width * 100)  # Much more generous

                # Truncate if too long
                display_summary = summary if len(summary) <= max_chars else summary[:max_chars-3] + "..."

                ax.text(
                    event_x,
                    y_pos,
                    display_summary,
                    ha="left",
                    va="top",
                    fontsize=config.FONT_SIZE_SMALL,
                    transform=ax.transAxes,
                )

                y_pos -= event_line_height

        return y_pos

    # LEFT COLUMN: Today and Tomorrow
    left_width = 0.38  # Width available for left column
    y_left = 0.95
    y_left = render_day_events(today, "Today", 0.0, y_left, left_width)
    y_left -= 0.03  # Gap between sections
    render_day_events(tomorrow, "Tomorrow", 0.0, y_left, left_width)

    # RIGHT COLUMN: Next 3 days after tomorrow
    right_width = 0.58  # Width available for right column
    y_right = 0.95
    future_dates = sorted([d for d in events_by_day if d > tomorrow])

    for i, date in enumerate(future_dates[:3]):  # Only show next 3 days
        if y_right < 0.02:
            break

        day_label = date.strftime("%a %m/%d").replace(" 0", " ")
        y_right = render_day_events(date, day_label, 0.42, y_right, right_width)
        y_right -= 0.02  # Small gap between days

    # Turn off axis frame
    ax.axis("off")
