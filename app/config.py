"""
Configuration for Kindle Display Server
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Display settings - Kindle Paperwhite 2 (6th gen)
KINDLE_WIDTH = 758
KINDLE_HEIGHT = 1024
DPI = 100
FIGURE_WIDTH = KINDLE_WIDTH / DPI  # 7.58 inches
FIGURE_HEIGHT = KINDLE_HEIGHT / DPI  # 10.24 inches

# GridSpec Layout Configuration
# Define the grid as rows. Each section gets [start_row, end_row]
# Total height is divided into 20 rows for fine-grained control
GRID_ROWS = 20
LAYOUT = {
    "weather1": [0, 5],   # 5 rows - First location
    "weather2": [5, 10],  # 5 rows - Second location
    # Gap: row 10 (empty for spacing)
    "strava": [11, 14],   # 3 rows - Running stats
    # Gap: row 14 (empty for spacing)
    "calendar": [15, 19], # 4 rows - Calendar events
    "text": [19, 20],     # 1 row - Custom text
}

# Weather Configuration (Weather.gov - FREE, no API key required!)
# Location 1 (e.g., home/primary location)
WEATHER_LAT_1 = os.getenv("WEATHER_LAT_1", os.getenv("WEATHER_LAT", "40.7128"))
WEATHER_LON_1 = os.getenv("WEATHER_LON_1", os.getenv("WEATHER_LON", "-74.0060"))

# Location 2 (e.g., vacation home, work, etc.)
WEATHER_LAT_2 = os.getenv("WEATHER_LAT_2", "37.7749")
WEATHER_LON_2 = os.getenv("WEATHER_LON_2", "-122.4194")

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
STRAVA_REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN", "")

GOOGLE_CALENDAR_TOKEN_FILE = os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", "")
# Support multiple calendars - comma-separated list
GOOGLE_CALENDAR_IDS = os.getenv("GOOGLE_CALENDAR_IDS", "primary").split(",")

# Display preferences
BACKGROUND_COLOR = "white"
TEXT_COLOR = "black"
FONT_SIZE_TITLE = 12  # Increased for 758x1024
FONT_SIZE_BODY = 10   # Increased for 758x1024
FONT_SIZE_SMALL = 8   # Increased for 758x1024

# Custom text to display (optional)
CUSTOM_TEXT = os.getenv("CUSTOM_TEXT", "")

# Server settings
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
