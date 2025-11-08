"""Tests for configuration module"""

from app import config


def test_kindle_dimensions():
    """Test Kindle display dimensions are correct for Paperwhite 2"""
    assert config.KINDLE_WIDTH == 758
    assert config.KINDLE_HEIGHT == 1024
    assert config.DPI == 100


def test_figure_dimensions():
    """Test figure dimensions are calculated correctly"""
    assert config.FIGURE_WIDTH == 7.58
    assert config.FIGURE_HEIGHT == 10.24


def test_grid_layout():
    """Test grid layout configuration"""
    assert config.GRID_ROWS == 20
    assert "weather1" in config.LAYOUT
    assert "weather2" in config.LAYOUT
    assert "strava" in config.LAYOUT
    assert "calendar" in config.LAYOUT
    assert "text" in config.LAYOUT


def test_layout_row_ranges():
    """Test layout rows don't overlap and are within bounds"""
    all_rows = set()
    for section, (start, end) in config.LAYOUT.items():
        assert 0 <= start < config.GRID_ROWS, f"{section} start row out of bounds"
        assert 0 < end <= config.GRID_ROWS, f"{section} end row out of bounds"
        assert start < end, f"{section} has invalid row range"

        # Check for overlaps
        section_rows = set(range(start, end))
        overlap = all_rows & section_rows
        assert not overlap, f"{section} overlaps with previous sections"
        all_rows.update(section_rows)


def test_weather_coordinates_format(mock_env):
    """Test weather coordinates are properly formatted"""
    # Reload config with mock env
    import importlib

    importlib.reload(config)

    assert isinstance(config.WEATHER_LAT_1, str)
    assert isinstance(config.WEATHER_LON_1, str)
    assert isinstance(config.WEATHER_LAT_2, str)
    assert isinstance(config.WEATHER_LON_2, str)


def test_font_sizes():
    """Test font sizes are reasonable for e-ink display"""
    assert config.FONT_SIZE_TITLE > config.FONT_SIZE_BODY
    assert config.FONT_SIZE_BODY > config.FONT_SIZE_SMALL
    assert config.FONT_SIZE_SMALL >= 6  # Minimum readable size


def test_calendar_ids_parsing(mock_env):
    """Test calendar IDs are parsed from comma-separated string"""
    import importlib

    importlib.reload(config)

    assert isinstance(config.GOOGLE_CALENDAR_IDS, list)
    assert len(config.GOOGLE_CALENDAR_IDS) >= 1
