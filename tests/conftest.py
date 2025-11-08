"""Pytest fixtures for tests"""

import matplotlib
import pytest

matplotlib.use("Agg")  # Use non-interactive backend for tests


@pytest.fixture
def mock_env(monkeypatch):
    """Set up mock environment variables"""
    monkeypatch.setenv("WEATHER_LAT_1", "40.7128")
    monkeypatch.setenv("WEATHER_LON_1", "-74.0060")
    monkeypatch.setenv("WEATHER_LAT_2", "37.7749")
    monkeypatch.setenv("WEATHER_LON_2", "-122.4194")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "test_id")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("STRAVA_REFRESH_TOKEN", "test_token")
    monkeypatch.setenv("GOOGLE_CALENDAR_IDS", "primary")
    monkeypatch.setenv("CUSTOM_TEXT", "Test display")
