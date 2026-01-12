"""
Text renderer for displaying custom text, quotes, or notes
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from matplotlib.axes import Axes

from app import config

# Eastern timezone for local display
EASTERN = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def render_text(ax: Axes, text: str = ""):
    """
    Render custom text on the given axes with a timestamp.

    Args:
        ax: matplotlib Axes object to draw on
        text: Text to display (can be multi-line)
    """
    # Clear the axes
    ax.clear()

    # Display the custom text if provided
    if text:
        ax.text(
            0.5,
            0.6,
            text,
            ha="center",
            va="center",
            fontsize=config.FONT_SIZE_SMALL,
            wrap=True,
            transform=ax.transAxes,
        )

    # Add timestamp at the bottom (in Eastern time)
    timestamp = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M")
    ax.text(
        0.98,
        0.05,
        f"Updated: {timestamp}",
        ha="right",
        va="bottom",
        fontsize=config.FONT_SIZE_SMALL - 1,
        color="#666666",
        transform=ax.transAxes,
    )

    # Turn off axis frame
    ax.axis("off")
