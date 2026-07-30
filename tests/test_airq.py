"""Tests for the air quality stale-sensor alert."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.web.auth import require_auth
from app.web.routes import airq


def _ts(minutes_ago: float) -> str:
    """An InfluxDB-style timestamp the given number of minutes in the past."""
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return when.isoformat().replace("+00:00", "Z")


def _rows(**minutes_ago: float) -> list[dict]:
    """Build query rows keyed by sensor greek letter, e.g. _rows(**{"α": 2})."""
    return [
        {"location": f"AirQ {letter}", "_time": _ts(age)} for letter, age in minutes_ago.items()
    ]


ALL_FRESH = {"α": 1, "β": 1, "γ": 1, "δ": 1, "ε": 1, "ζ": 1}


@pytest.fixture
def fake_influx(monkeypatch):
    """Patch _query_influx with a callable returning canned rows."""

    def _install(rows, exc=None):
        def _fake(_query):
            if exc is not None:
                raise exc
            return rows

        monkeypatch.setattr(airq, "_query_influx", _fake)

    return _install


def test_no_alert_when_all_sensors_fresh(fake_influx):
    fake_influx(_rows(**ALL_FRESH))
    assert airq._stale_sensors() == []


def test_single_stale_sensor_reported_with_age(fake_influx):
    fresh = {**ALL_FRESH, "β": 185}  # Bedroom quiet for ~3h
    fake_influx(_rows(**fresh))

    stale = airq._stale_sensors()

    assert [s["display"] for s in stale] == ["Bedroom"]
    assert stale[0]["site"] == "home"
    assert stale[0]["age"] == "3h 5m"


def test_sensor_just_under_threshold_is_not_stale(fake_influx):
    fake_influx(_rows(**{**ALL_FRESH, "α": 59}))
    assert airq._stale_sensors() == []


def test_sensor_just_over_threshold_is_stale(fake_influx):
    fake_influx(_rows(**{**ALL_FRESH, "α": 61}))
    assert [s["display"] for s in airq._stale_sensors()] == ["Office"]


def test_whole_site_outage_groups_into_one_entry(fake_influx):
    """A cabin power cut silences all four sensors; that should read as one line."""
    fake_influx(_rows(**{"α": 1, "β": 1, "γ": 120, "δ": 120, "ε": 120, "ζ": 120}))

    groups = airq._group_stale(airq._stale_sensors())

    assert len(groups) == 1
    assert groups[0]["site"] == "cabin"
    assert groups[0]["site_label"] == "Wentworth"
    assert len(groups[0]["sensors"]) == 4


def test_missing_sensor_reported_as_no_data(fake_influx):
    """A sensor absent from the lookback window has no age to report."""
    fake_influx(_rows(**{k: v for k, v in ALL_FRESH.items() if k != "ζ"}))

    stale = airq._stale_sensors()

    assert [s["display"] for s in stale] == ["Water Room"]
    assert stale[0]["age"] is None


def test_influx_failure_does_not_raise_a_false_alarm(fake_influx):
    """A dashboard-side outage must not render as a sensor outage."""
    fake_influx(None, exc=RuntimeError("influx down"))
    assert airq._stale_sensors() == []


def test_stale_sensors_spanning_both_sites_group_separately(fake_influx):
    fake_influx(_rows(**{**ALL_FRESH, "α": 90, "γ": 90}))

    groups = airq._group_stale(airq._stale_sensors())

    assert [g["site"] for g in groups] == ["home", "cabin"]


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(minutes=5), "5m"),
        (timedelta(minutes=59), "59m"),
        (timedelta(hours=1), "1h"),
        (timedelta(hours=3, minutes=20), "3h 20m"),
        (timedelta(hours=23, minutes=59), "23h 59m"),
        (timedelta(days=1), "1d"),
        (timedelta(days=2, hours=5), "2d 5h"),
    ],
)
def test_fmt_age(delta, expected):
    assert airq._fmt_age(delta) == expected


# ── Route rendering ──


@pytest.fixture
def client():
    """Test client with auth stubbed out."""
    from app.web.app import app

    app.dependency_overrides[require_auth] = lambda: "test@example.com"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_alert_partial_renders_nothing_when_fresh(client, fake_influx):
    fake_influx(_rows(**ALL_FRESH))

    resp = client.get("/partials/airq-alert")

    assert resp.status_code == 200
    assert resp.text.strip() == ""


def test_alert_partial_renders_warning_when_stale(client, fake_influx):
    fake_influx(_rows(**{**ALL_FRESH, "ε": 200}))

    resp = client.get("/partials/airq-alert")

    assert resp.status_code == 200
    assert "airq-alert" in resp.text
    assert "Basement" in resp.text
    assert "3h 20m" in resp.text
    assert "/air-quality?site=cabin" in resp.text
    assert "1 sensor has not reported" in resp.text


def test_alert_partial_pluralizes_multiple_sensors(client, fake_influx):
    fake_influx(_rows(**{**ALL_FRESH, "γ": 200, "δ": 200}))

    resp = client.get("/partials/airq-alert")

    assert "2 sensors have not reported" in resp.text


def test_alert_partial_requires_auth(fake_influx):
    """Without the dependency override the partial must refuse to render."""
    from app.web.app import app

    fake_influx(_rows(**ALL_FRESH))
    app.dependency_overrides.clear()
    assert TestClient(app).get("/partials/airq-alert").status_code == 401
