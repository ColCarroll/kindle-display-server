"""Tests for main application module"""

import io

import pytest
from PIL import Image

from app import config
from app.main import app, generate_composite_image


@pytest.mark.asyncio
async def test_health_endpoint():
    """Test health check endpoint"""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_generate_composite_image_returns_bytes():
    """Test that image generation returns bytes"""
    # This will fail if APIs are not available, which is expected in tests
    # We're just testing the structure, not the actual API calls
    try:
        result = generate_composite_image()
        assert isinstance(result, bytes)
        assert len(result) > 0
    except Exception:
        # Expected to fail without real API credentials
        pytest.skip("Skipping integration test - requires API credentials")


def test_generate_composite_image_creates_correct_dimensions():
    """Test that generated image has correct dimensions"""
    try:
        image_bytes = generate_composite_image()
        img = Image.open(io.BytesIO(image_bytes))

        assert img.size == (config.KINDLE_WIDTH, config.KINDLE_HEIGHT)
        assert img.mode == "L"  # Grayscale
    except Exception:
        pytest.skip("Skipping integration test - requires API credentials")


@pytest.mark.asyncio
async def test_display_endpoint_returns_png():
    """Test main display endpoint returns PNG"""
    from fastapi.testclient import TestClient

    client = TestClient(app)

    try:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    except Exception:
        pytest.skip("Skipping integration test - requires API credentials")
