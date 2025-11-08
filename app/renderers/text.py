"""
Text renderer for displaying custom text, quotes, or notes
"""

import logging

from matplotlib.axes import Axes

from app import config

logger = logging.getLogger(__name__)


def render_text(ax: Axes, text: str = ""):
    """
    Render custom text on the given axes.

    Args:
        ax: matplotlib Axes object to draw on
        text: Text to display (can be multi-line)
    """
    # Clear the axes
    ax.clear()

    if not text:
        # Display nothing if no text provided
        ax.axis("off")
        return

    # Display the text
    ax.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        fontsize=config.FONT_SIZE_SMALL,
        wrap=True,
        transform=ax.transAxes,
    )

    # Turn off axis frame
    ax.axis("off")
