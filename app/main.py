"""
Kindle Display Image Generator
Generates a composite grayscale image for Kindle display
"""

import matplotlib

matplotlib.use("Agg")  # Use non-interactive backend
import io
import logging

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from PIL import Image

from app import config
from app.renderers import calendar, strava, text, weather

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_composite_image() -> bytes:
    """
    Generate the composite image using matplotlib GridSpec.
    Returns PNG bytes.
    """
    # Create figure with Kindle dimensions
    fig = plt.figure(
        figsize=(config.FIGURE_WIDTH, config.FIGURE_HEIGHT),
        dpi=config.DPI,
        facecolor=config.BACKGROUND_COLOR,
    )

    # Create GridSpec layout
    gs = GridSpec(
        config.GRID_ROWS,
        1,
        figure=fig,
        hspace=0.4,  # Spacing between sections (increased for more gap between strava and calendar)
        left=0.15,  # More space from left edge
        right=0.95,
        top=0.92,  # More space at the top
        bottom=0.05,
    )

    # Create axes for each section based on layout config
    # Weather plots share x and y axes for easier comparison
    axes = {}
    for section, (start, end) in config.LAYOUT.items():
        if section == "weather2":
            # Share axes with weather1
            axes[section] = fig.add_subplot(
                gs[start:end, 0], sharex=axes["weather1"], sharey=axes["weather1"]
            )
        else:
            axes[section] = fig.add_subplot(gs[start:end, 0])

    # Render each section
    try:
        logger.info("Rendering weather section 1")
        weather.render_weather(
            axes["weather1"],
            config.WEATHER_LAT_1,
            config.WEATHER_LON_1,
            title="Home",
            show_xlabel=False,
        )
    except Exception as e:
        logger.error(f"Error rendering weather1: {e}")
        axes["weather1"].text(
            0.5, 0.5, "Weather data unavailable", ha="center", va="center", fontsize=10
        )
        axes["weather1"].axis("off")

    try:
        logger.info("Rendering weather section 2")
        weather.render_weather(
            axes["weather2"],
            config.WEATHER_LAT_2,
            config.WEATHER_LON_2,
            title="Munchalm",
            show_xlabel=True,
        )
    except Exception as e:
        logger.error(f"Error rendering weather2: {e}")
        axes["weather2"].text(
            0.5, 0.5, "Weather data unavailable", ha="center", va="center", fontsize=10
        )
        axes["weather2"].axis("off")

    try:
        logger.info("Rendering strava section")
        strava.render_strava(axes["strava"])
    except Exception as e:
        logger.error(f"Error rendering strava: {e}")
        axes["strava"].text(
            0.5, 0.5, "Strava data unavailable", ha="center", va="center", fontsize=10
        )
        axes["strava"].axis("off")

    try:
        logger.info("Rendering calendar section")
        calendar.render_calendar(axes["calendar"])
    except Exception as e:
        logger.error(f"Error rendering calendar: {e}")
        axes["calendar"].text(
            0.5, 0.5, "Calendar data unavailable", ha="center", va="center", fontsize=10
        )
        axes["calendar"].axis("off")

    try:
        logger.info("Rendering text section")
        text.render_text(axes["text"], config.CUSTOM_TEXT)
    except Exception as e:
        logger.error(f"Error rendering text: {e}")
        axes["text"].axis("off")

    # Save to bytes buffer
    buf = io.BytesIO()
    plt.savefig(
        buf, format="png", dpi=config.DPI, facecolor=config.BACKGROUND_COLOR, edgecolor="none"
    )
    plt.close(fig)

    # Convert to grayscale and ensure exact dimensions
    buf.seek(0)
    img = Image.open(buf)

    # Convert to grayscale (L mode)
    img_gray = img.convert("L")

    # Ensure exact dimensions (resize if needed)
    if img_gray.size != (config.KINDLE_WIDTH, config.KINDLE_HEIGHT):
        img_gray = img_gray.resize(
            (config.KINDLE_WIDTH, config.KINDLE_HEIGHT), Image.Resampling.LANCZOS
        )

    # Save as grayscale PNG
    output_buf = io.BytesIO()
    img_gray.save(output_buf, format="PNG", optimize=True)
    output_buf.seek(0)

    return output_buf.read()
