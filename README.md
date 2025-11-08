# Kindle Display Generator

[![CI](https://github.com/ColCarroll/kindle-display-generator/workflows/CI/badge.svg)](https://github.com/ColCarroll/kindle-display-generator/actions)
[![codecov](https://codecov.io/gh/ColCarroll/kindle-display-generator/branch/main/graph/badge.svg)](https://codecov.io/gh/ColCarroll/kindle-display-generator)

A Python CLI tool that generates custom grayscale images for Kindle e-ink displays. Uses matplotlib to create composite views with weather, Strava activities, calendar events, and custom text.

**Inspired by [Matt Healy's Kindle display project](https://matthealy.com/kindle)**, reimplemented in Python with matplotlib instead of Node.js/Puppeteer. Designed to run via cron and serve static images rather than running a web server.

## Features

- **Dual Weather**: Current conditions and 5-day forecast for two locations (Weather.gov - FREE, no API key!)
- **Strava Running Stats**: Last 7 days summary with route visualization (running activities only)
- **Multi-Calendar Support**: Events from multiple Google Calendars in two-column layout
- **Custom Text**: Display quotes, notes, or reminders
- **Configurable Layout**: Adjust GridSpec layout to prioritize different sections
- **Grayscale Output**: Optimized 758x1024px images for Kindle Paperwhite 2 e-ink displays

## Architecture

```
Cron (every 30min) → generate_image.py → saves calendar.png to disk
Kindle (every 6hr)  → wget → nginx/static server → calendar.png
```

The tool uses matplotlib's GridSpec to create a flexible layout where each data source renderer draws into its assigned axes. The complete image is generated and saved as a grayscale PNG file.

## Setup

### 1. Clone and Configure

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API credentials
nano .env
```

### 2. API Credentials

#### Weather (Weather.gov - FREE!)
Weather data comes from the National Weather Service API - no API key required!

1. Set coordinates for **two locations** in `.env`
2. Find your coordinates at https://www.latlong.net/
3. **Note**: Weather.gov only works for US locations

Example for two locations:
```bash
# Location 1 - New York City
WEATHER_LAT_1=40.7128
WEATHER_LON_1=-74.0060

# Location 2 - San Francisco
WEATHER_LAT_2=37.7749
WEATHER_LON_2=-122.4194
```

#### Strava
1. Create an app at https://www.strava.com/settings/api
2. Note your Client ID and Client Secret
3. Follow OAuth flow to get refresh token:
   ```bash
   # Visit this URL (replace YOUR_CLIENT_ID):
   https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=activity:read_all

   # After authorizing, you'll be redirected to localhost with a code
   # Exchange the code for tokens:
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=YOUR_CODE \
     -d grant_type=authorization_code
   ```
4. Add credentials to `.env`

#### Google Calendar (Optional)
1. Follow https://developers.google.com/calendar/api/quickstart/python
2. Download credentials and save as `credentials.json`
3. Run `setup_calendar.py` to authenticate and generate `token.json`
4. Add calendar IDs to `.env` (comma-separated for multiple calendars):
   ```bash
   GOOGLE_CALENDAR_IDS=primary,calendar_id_2@group.calendar.google.com,calendar_id_3@group.calendar.google.com
   ```

### 3. Run Locally with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and environment manager.

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or on macOS: brew install uv

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e .

# Generate image
python generate_image.py /tmp/test.png
```

### 4. Run Locally with pip (Alternative)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Generate image
python generate_image.py /tmp/test.png
```

## Usage

Generate the image and save to disk, then serve as a static file:

```bash
# Generate image
python generate_image.py /path/to/output/calendar.png

# Or with verbose logging
python generate_image.py -v /var/www/html/img/calendar.png

# View the generated image
open /path/to/output/calendar.png  # macOS
xdg-open /path/to/output/calendar.png  # Linux

# Set up cron to regenerate every 30 minutes
# Add to crontab:
*/30 * * * * cd /path/to/project && /path/to/venv/bin/python generate_image.py /var/www/html/img/calendar.png
```

**Note:** Serve the static file with your existing web server (nginx, Apache, etc.)

## Kindle Setup

### Prerequisites
- Jailbroken Kindle Paperwhite 2 (or compatible model, tested on FW 5.12.2.2)
- KUAL (Kindle Unified Application Launcher)
- USBNetwork for SSH access
- Kindle connected to Wi-Fi

For detailed jailbreaking instructions, see `KINDLE_SETUP.md`.

### Quick Setup

1. **Set up SSH access**:
   - Install KUAL and USBNetwork on your jailbroken Kindle
   - Configure WiFi SSH in `/usbnet/etc/config`: set `USE_WIFI="true"`
   - Add your SSH public key to `/usbnet/etc/authorized_keys`
   - Toggle USBNetwork in KUAL to start SSH daemon

2. **Create update script on Kindle** (via USB or SSH):
   ```bash
   cat > /mnt/us/update_display.sh << 'EOF'
   #!/bin/sh
   SERVER_URL="https://your-server.com/img/calendar.png"
   DISPLAY_DIR="/mnt/us/display"
   mkdir -p "$DISPLAY_DIR"

   /usr/sbin/eips 10 15 "Fetching calendar..."

   if wget -O "$DISPLAY_DIR/display.png" "$SERVER_URL"; then
       /usr/sbin/eips -c
       /usr/sbin/eips -g "$DISPLAY_DIR/display.png"
       echo "$(date): Success" >> "$DISPLAY_DIR/update.log"
   else
       /usr/sbin/eips 10 15 "Error: Could not fetch"
       echo "$(date): Failed" >> "$DISPLAY_DIR/update.log"
   fi
   EOF
   chmod +x /mnt/us/update_display.sh
   ```

3. **Set up cron for automatic updates**:
   ```bash
   ssh root@YOUR_KINDLE_IP
   mkdir -p /var/spool/cron/crontabs
   echo "0 */6 * * * /mnt/us/update_display.sh" | crontab -
   ```
   This updates every 6 hours (midnight, 6am, noon, 6pm).

4. **Test manually**:
   ```bash
   ssh root@YOUR_KINDLE_IP '/mnt/us/update_display.sh'
   ```

## Configuration

### Adjust Layout

Edit `app/config.py` to change the GridSpec layout:

```python
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
```

Total rows = 20. Adjust the ranges to change proportions. Leave gaps for visual spacing.

### Customize Renderers

Each renderer is in `app/renderers/`:
- `weather.py` - Modify chart style, units, forecast length
- `strava.py` - Change activity display, metrics
- `calendar.py` - Adjust event formatting
- `text.py` - Customize text rendering

## Deployment

1. **Clone and install on your server:**
   ```bash
   git clone https://github.com/ColCarroll/kindle-display-generator.git
   cd kindle-display-generator
   uv venv
   source .venv/bin/activate
   uv pip install -e .
   ```

2. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your API credentials
   ```

3. **Test image generation:**
   ```bash
   python generate_image.py /tmp/test.png
   ```

4. **Set up cron job:**
   ```bash
   crontab -e
   # Add this line (adjust paths):
   */30 * * * * cd /path/to/kindle-display-generator && /path/to/kindle-display-generator/.venv/bin/python generate_image.py /var/www/html/img/calendar.png
   ```

5. **Serve the static file** with your existing web server (nginx, Apache, etc.):
   ```nginx
   # Example nginx configuration
   location /img/calendar.png {
       alias /var/www/html/img/calendar.png;
       expires 5m;
   }
   ```

## Development

### Running Tests

```bash
# Run tests
make test

# Run tests with coverage
make test-cov

# Run linter
make lint

# Auto-fix linting issues
make lint-fix

# Check code formatting
make format-check

# Format code
make format

# Run type checker
make typecheck

# Run all checks (lint + format + typecheck)
make check

# Run everything (all checks + tests)
make all
```

### Installing Development Dependencies

```bash
# With uv (recommended)
uv pip install -e ".[dev]"

# Or with pip
pip install -e ".[dev]"
```

## Troubleshooting

### Server Issues

```bash
# Check logs
docker-compose logs -f

# Test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/ > test.png
```

### Kindle Issues

- **Display not updating**: Check cron is running (`crontab -l`), verify server URL, check logs at `/mnt/us/display/update.log`
- **Image looks wrong**: Ensure 758x1024px grayscale PNG for Kindle Paperwhite 2
- **Wi-Fi disconnects**: Kindle may sleep, make sure it stays connected
- **SSH not working**: Verify USBNetwork is toggled ON in KUAL, check authorized_keys, ensure WiFi is connected
- **Image only fills 75% of screen**: Wrong resolution - use 758x1024 for Paperwhite 2, not 600x800

### API Issues

- **Weather not loading**: Weather.gov only works for US locations, check coordinates
- **Strava errors**: Refresh token may need regeneration, check token expiry
- **Calendar empty**: Check credentials, verify calendar IDs, ensure token.json exists

## License

MIT
