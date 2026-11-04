"""Tests for Strava data fetching and detrended chart calculations."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def make_activity(start_date: datetime, distance_mi: float, activity_type: str = "Run"):
    """Create a mock Strava activity."""
    return {
        "type": activity_type,
        "start_date": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "distance": distance_mi / 0.000621371,  # Convert to meters
        "moving_time": int(distance_mi * 9 * 60),  # ~9 min/mile
        "total_elevation_gain": distance_mi * 50 / 3.28084,  # ~50ft/mile in meters
        "id": hash(start_date.isoformat()),
        "name": "Test Run",
        "map": {"summary_polyline": ""},
    }


def test_detrended_chart_first_point_at_origin():
    """The detrended chart should start at (0, 0)."""
    from app.fetchers.strava import get_running_summary

    # Create mock activities starting Jan 1
    year = 2026
    jan1 = datetime(year, 1, 1, 8, 0, 0)
    activities = [
        make_activity(jan1, 5.0),
        make_activity(jan1 + timedelta(days=1), 6.0),
        make_activity(jan1 + timedelta(days=2), 5.0),
    ]

    mock_stats = {
        "ytd_run_totals": {
            "distance": 16.0 / 0.000621371,  # 16 miles in meters
            "elevation_gain": 800 / 3.28084,  # 800 ft in meters
        }
    }

    with (
        patch("app.fetchers.strava.fetch_activities_for_year", return_value=activities),
        patch("app.fetchers.strava.fetch_athlete_stats", return_value=mock_stats),
        patch("app.fetchers.strava.datetime") as mock_dt,
    ):
        # Mock "now" to be Jan 4 at noon
        mock_now = datetime(year, 1, 4, 12, 0, 0, tzinfo=EASTERN)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        mock_dt.strptime = datetime.strptime

        result = get_running_summary(use_cache=False)

    assert result is not None
    detrended = result["detrended_data"]
    assert len(detrended) > 0

    # First point should be at day 0 with detrended 0
    assert detrended[0]["day"] == 0
    assert detrended[0]["detrended"] == 0


def test_detrended_chart_final_point_near_zero():
    """The final point should be close to 0 (by definition of avg pace)."""
    from app.fetchers.strava import get_running_summary

    year = 2026
    jan1 = datetime(year, 1, 1, 8, 0, 0)
    activities = [
        make_activity(jan1, 5.0),
        make_activity(jan1 + timedelta(days=1), 6.0),
        make_activity(jan1 + timedelta(days=2), 5.0),
    ]

    mock_stats = {
        "ytd_run_totals": {
            "distance": 16.0 / 0.000621371,
            "elevation_gain": 800 / 3.28084,
        }
    }

    with (
        patch("app.fetchers.strava.fetch_activities_for_year", return_value=activities),
        patch("app.fetchers.strava.fetch_athlete_stats", return_value=mock_stats),
        patch("app.fetchers.strava.datetime") as mock_dt,
    ):
        mock_now = datetime(year, 1, 4, 12, 0, 0, tzinfo=EASTERN)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        mock_dt.strptime = datetime.strptime

        result = get_running_summary(use_cache=False)

    assert result is not None
    detrended = result["detrended_data"]

    # Final point should be close to 0
    final_point = detrended[-1]
    assert abs(final_point["detrended"]) < 1.0, (
        f"Final detrended was {final_point['detrended']}, expected ~0"
    )


def test_detrended_includes_all_runs():
    """All runs should be represented in the detrended data."""
    from app.fetchers.strava import get_running_summary

    year = 2026
    jan1 = datetime(year, 1, 1, 8, 0, 0)
    # Create 5 runs on different days
    activities = [make_activity(jan1 + timedelta(days=i), 5.0 + i) for i in range(5)]

    total_miles = sum(5.0 + i for i in range(5))
    mock_stats = {
        "ytd_run_totals": {
            "distance": total_miles / 0.000621371,
            "elevation_gain": 1000 / 3.28084,
        }
    }

    with (
        patch("app.fetchers.strava.fetch_activities_for_year", return_value=activities),
        patch("app.fetchers.strava.fetch_athlete_stats", return_value=mock_stats),
        patch("app.fetchers.strava.datetime") as mock_dt,
    ):
        mock_now = datetime(year, 1, 6, 12, 0, 0, tzinfo=EASTERN)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        mock_dt.strptime = datetime.strptime

        result = get_running_summary(use_cache=False)

    assert result is not None
    detrended = result["detrended_data"]

    # Should have: origin + (pre + post for each run) + final = 1 + 5*2 + 1 = 12 points
    # But some might collapse if runs are on same fractional day
    assert len(detrended) >= 7, f"Expected at least 7 points, got {len(detrended)}"

    # Check that we have points near day 0.x, 1.x, 2.x, 3.x, 4.x
    days_covered = {int(pt["day"]) for pt in detrended if pt["day"] > 0}
    expected_days = {0, 1, 2, 3, 4}
    assert expected_days <= days_covered, f"Missing days: {expected_days - days_covered}"


def test_detrended_sawtooth_pattern():
    """Each run should create a jump up (pre-run point < post-run point)."""
    from app.fetchers.strava import get_running_summary

    year = 2026
    jan1 = datetime(year, 1, 1, 8, 0, 0)
    activities = [
        make_activity(jan1, 10.0),  # One big run
    ]

    mock_stats = {
        "ytd_run_totals": {
            "distance": 10.0 / 0.000621371,
            "elevation_gain": 500 / 3.28084,
        }
    }

    with (
        patch("app.fetchers.strava.fetch_activities_for_year", return_value=activities),
        patch("app.fetchers.strava.fetch_athlete_stats", return_value=mock_stats),
        patch("app.fetchers.strava.datetime") as mock_dt,
    ):
        mock_now = datetime(year, 1, 2, 12, 0, 0, tzinfo=EASTERN)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        mock_dt.strptime = datetime.strptime

        result = get_running_summary(use_cache=False)

    assert result is not None
    detrended = result["detrended_data"]

    # Find the pre/post run points (should be at very close day values)
    # Origin at 0, pre-run, post-run (day+0.001), final
    assert len(detrended) >= 3

    # Find consecutive points at nearly same day (the jump)
    found_jump = False
    for i in range(len(detrended) - 1):
        if abs(detrended[i + 1]["day"] - detrended[i]["day"]) < 0.01:
            # This is a pre/post run pair
            pre_run = detrended[i]["detrended"]
            post_run = detrended[i + 1]["detrended"]
            assert post_run > pre_run, f"Expected jump UP: pre={pre_run}, post={post_run}"
            found_jump = True

    assert found_jump, "Should have found at least one run jump"


# ── Year-end pace chart range ──

from app.fetchers.strava import build_pace_chart, max_still_possible  # noqa: E402


def test_max_still_possible_uses_daily_cap_for_a_week_then_sustained():
    assert max_still_possible(3, 30, 15) == 90  # three huge days
    assert max_still_possible(10, 30, 15) == 210  # one huge week beats 10 days at 15
    assert max_still_possible(57, 30, 15) == 855  # beyond two weeks: sustained wins
    assert max_still_possible(0, 30, 15) == 0


def _chart(total=3000.0, elapsed=308.0, remaining=57.0, detrended=None):
    return build_pace_chart(
        total, elapsed, 365, remaining, detrended or [{"day": 0, "detrended": 0}], 30, 15
    )


def test_pace_lines_only_cover_reachable_totals():
    chart = _chart()
    targets = [line["target"] for line in chart["lines"]]

    assert min(targets) >= 3000  # never a total already passed (negative pace)
    assert max(targets) <= 3000 + 855  # never faster than 15/day sustained
    assert all(line["needed_per_day"] >= 0 for line in chart["lines"])
    assert all(line["needed_per_day"] <= 15 for line in chart["lines"])


def test_pace_lines_get_smaller_divisions_for_a_narrow_range():
    chart = _chart()
    majors = [line["target"] for line in chart["lines"] if line["major"]]

    assert chart["step"] == 200
    assert majors == [3000, 3200, 3400, 3600, 3800]
    assert len(chart["lines"]) == 9  # half steps in between


def test_y_range_bottom_is_the_stop_today_line():
    chart = _chart()
    stop_today = (3000 / 365 - 3000 / 308) * 308
    assert chart["y_min"] < stop_today < chart["y_min"] + 0.06 * (chart["y_max"] - chart["y_min"])


def test_y_range_still_shows_the_actual_line():
    chart = _chart(detrended=[{"day": 0, "detrended": 0}, {"day": 100, "detrended": -900}])
    assert chart["y_min"] < -900


def test_todays_endpoint_is_centered():
    chart = _chart()
    assert chart["y_min"] == -chart["y_max"]  # the actual line ends at 0, mid-chart


def test_top_mirrors_stopping_when_the_cap_is_far_above_current_pace():
    # Elevation-like: 121,627 ft at ~395 ft/day, cap 2,000 ft/day sustained
    chart = build_pace_chart(121_627, 308, 365, 57, [{"day": 0, "detrended": 0}], 10_000, 2_000)
    top = max(line["target"] for line in chart["lines"])
    pace = 121_627 / 308
    assert top <= 121_627 + 2 * pace * 57  # ≈ 2x current pace, not 2,000 ft/day
    assert top > 121_627 + 1.5 * pace * 57
