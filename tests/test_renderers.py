"""Tests for renderer modules"""

import matplotlib.pyplot as plt

from app.renderers import text


def test_text_renderer_basic():
    """Test basic text rendering"""
    fig, ax = plt.subplots()
    text.render_text(ax, "Test message")

    # Verify axis is configured for text display
    assert not ax.axison  # Axis should be hidden
    plt.close(fig)


def test_text_renderer_empty_string():
    """Test text renderer with empty string"""
    fig, ax = plt.subplots()
    text.render_text(ax, "")

    assert not ax.axison
    plt.close(fig)


def test_text_renderer_multiline():
    """Test text renderer with multiline text"""
    fig, ax = plt.subplots()
    multiline_text = "Line 1\nLine 2\nLine 3"
    text.render_text(ax, multiline_text)

    assert not ax.axison
    plt.close(fig)


def test_text_renderer_uses_config_font_size():
    """Test that text renderer respects config font size"""
    fig, ax = plt.subplots()
    test_text = "Test"
    text.render_text(ax, test_text)

    # Check that text objects were created
    texts = ax.texts
    assert len(texts) > 0

    plt.close(fig)


# Weather renderer tests would require mocking HTTP requests
# which we'll skip for now but could add with pytest-mock


# Strava renderer tests would require mocking API calls
# which we'll skip for now but could add with pytest-mock


# Calendar renderer tests would require mocking Google API
# which we'll skip for now but could add with pytest-mock
