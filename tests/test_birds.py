"""Tests for the birds page."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.web.auth import require_auth
from app.web.routes import birds

TODAY = date(2026, 10, 27)


def _at(day: date, hour: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=birds.TZ_BOSTON)


def _days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


# ── Heatmap ──


def test_heatmap_today_counts_by_hour_and_ranks_species():
    hourly = [
        (_at(TODAY, 7), "Blue Jay", 4),
        (_at(TODAY, 8), "Blue Jay", 1),
        (_at(TODAY, 2), "Barred Owl", 2),
        (_at(_days_ago(1), 7), "Blue Jay", 50),  # yesterday: not in today's view
    ]

    hm = birds.build_heatmap(hourly, TODAY, "today", now_hour=9)

    assert [r["name"] for r in hm["rows"]] == ["Blue Jay", "Barred Owl"]
    jay = hm["rows"][0]
    assert jay["total"] == 5
    assert jay["cells"][7]["alpha"] == 1.0  # the busiest cell is fully shaded
    assert 0 < jay["cells"][8]["alpha"] < 1
    assert jay["cells"][0]["alpha"] == 0
    assert jay["cells"][9]["now"] and not jay["cells"][8]["now"]
    assert jay["cells"][7]["label"] == "Blue Jay · 07:00 · 4 detections"
    assert hm["more"] == 0


def test_heatmap_week_sums_hours_across_days():
    hourly = [
        (_at(TODAY, 7), "Blue Jay", 4),
        (_at(_days_ago(3), 7), "Blue Jay", 6),
        (_at(_days_ago(7), 7), "Blue Jay", 100),  # 8 days back: outside the week
    ]

    hm = birds.build_heatmap(hourly, TODAY, "week", now_hour=9)

    assert hm["rows"][0]["total"] == 10
    assert not any(c["now"] for c in hm["rows"][0]["cells"])


def test_heatmap_limits_rows_and_reports_the_rest():
    hourly = [(_at(TODAY, 6), f"Bird {i:02d}", 100 - i) for i in range(birds.HEATMAP_ROWS + 3)]

    hm = birds.build_heatmap(hourly, TODAY, "today", now_hour=6)

    assert len(hm["rows"]) == birds.HEATMAP_ROWS
    assert hm["rows"][0]["name"] == "Bird 00"
    assert hm["more"] == 3


def test_heatmap_empty():
    assert birds.build_heatmap([], TODAY, "today", now_hour=6) == {"rows": [], "more": 0}


# ── Trends ──


def test_trend_counts_detections_and_unique_species_per_day():
    daily = [
        (_at(TODAY), "Blue Jay", 10),
        (_at(TODAY), "Barred Owl", 2),
        (_at(_days_ago(1)), "Blue Jay", 5),
    ]

    trend = birds.build_trend(daily, TODAY)

    assert trend["detections"]["today"] == 12
    assert trend["species"]["today"] == 2
    assert trend["detections"]["max"] == 12
    # 30 zero-filled days → 30 points, the last at x=100
    assert len(trend["detections"]["points"].split()) == birds.TREND_DAYS
    assert trend["detections"]["last"][0] == 100


def test_trend_starts_at_first_day_of_records():
    daily = [(_at(_days_ago(4)), "Blue Jay", 3), (_at(TODAY), "Blue Jay", 5)]

    trend = birds.build_trend(daily, TODAY, earliest=_days_ago(4))

    assert len(trend["detections"]["points"].split()) == 5  # 4 days ago .. today
    assert trend["first_label"] == _days_ago(4).strftime("%b %-d")


def test_trend_ignores_earliest_older_than_window():
    trend = birds.build_trend([], TODAY, earliest=_days_ago(400))

    assert len(trend["detections"]["points"].split()) == birds.TREND_DAYS


def test_trend_with_one_day_of_records():
    trend = birds.build_trend([(_at(TODAY), "Blue Jay", 5)], TODAY, earliest=TODAY)

    assert trend["detections"]["today"] == 5
    assert len(trend["detections"]["points"].split()) == 1


def test_trend_with_no_data_is_flat():
    trend = birds.build_trend([], TODAY)

    assert trend["detections"]["today"] == 0
    assert trend["species"]["max"] == 1  # avoids dividing by zero


# ── Arrivals ──


EARLY = _days_ago(200)  # records began long ago


def test_arrival_after_long_absence():
    daily = [(_at(_days_ago(90)), "Dark-eyed Junco", 3), (_at(_days_ago(2)), "Dark-eyed Junco", 8)]

    arrivals = birds.find_arrivals(daily, TODAY, EARLY)

    assert [(a["name"], a["date"]) for a in arrivals] == [("Dark-eyed Junco", _days_ago(2))]


def test_resident_species_is_not_an_arrival():
    daily = [(_at(_days_ago(n)), "Black-capped Chickadee", 5) for n in range(70)]

    assert birds.find_arrivals(daily, TODAY, EARLY) == []


def test_brand_new_species_is_an_arrival():
    daily = [(_at(_days_ago(5)), "Red Crossbill", 2), (_at(_days_ago(4)), "Red Crossbill", 1)]

    arrivals = birds.find_arrivals(daily, TODAY, EARLY)

    assert [a["date"] for a in arrivals] == [_days_ago(5)]  # first day of the visit only
    assert arrivals[0]["count"] == 3  # all detections since the arrival


def test_single_detection_is_not_an_arrival():
    daily = [(_at(_days_ago(3)), "Solitary Sandpiper", 1)]

    assert birds.find_arrivals(daily, TODAY, EARLY) == []


def test_arrival_counts_detections_spread_over_days():
    daily = [(_at(_days_ago(3)), "Fox Sparrow", 1), (_at(_days_ago(1)), "Fox Sparrow", 1)]

    arrivals = birds.find_arrivals(daily, TODAY, EARLY)

    assert [(a["name"], a["date"], a["count"]) for a in arrivals] == [
        ("Fox Sparrow", _days_ago(3), 2)
    ]


def test_species_heard_when_records_began_are_baseline_not_arrivals():
    start = _days_ago(10)
    daily = [(_at(start), "Blue Jay", 9), (_at(_days_ago(3)), "Fox Sparrow", 2)]

    arrivals = birds.find_arrivals(daily, TODAY, start)

    assert [a["name"] for a in arrivals] == ["Fox Sparrow"]


def test_arrivals_outside_window_are_ignored_and_sorted_newest_first():
    daily = [
        (_at(_days_ago(birds.ARRIVAL_WINDOW_DAYS + 1)), "Old News", 2),
        (_at(_days_ago(6)), "Fox Sparrow", 2),
        (_at(_days_ago(1)), "Snow Bunting", 2),
    ]

    arrivals = birds.find_arrivals(daily, TODAY, EARLY)

    assert [a["name"] for a in arrivals] == ["Snow Bunting", "Fox Sparrow"]


# ── Queries ──


def test_queries_exclude_false_positives_and_non_birds():
    q = birds._counts_query(_at(TODAY), "1h")

    assert 'r.class == "aves"' in q
    assert 'r.review != "false_positive"' in q
    assert 'r.site == "cabin"' in q
    assert "every: 1h" in q
    assert 'timezone.location(name: "America/New_York")' in q


# ── Route rendering ──


def _influx_ts(dt: datetime) -> str:
    return dt.astimezone(birds.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def fake_influx(monkeypatch):
    """Answer each query type with canned rows, relative to the real today."""

    def _install(exc=None):
        now = datetime.now(birds.TZ_BOSTON)
        today = now.date()

        def _fake(query):
            if exc is not None:
                raise exc
            if "every: 10m" in query:
                return [
                    {
                        "_time": _influx_ts(_at(today, 0)),
                        "common_name": "Barred Owl",
                        "confidence": "3",
                    }
                ]
            if "every: 1d" in query:
                return [
                    {
                        "_time": _influx_ts(_at(today)),
                        "common_name": "Barred Owl",
                        "confidence": "3",
                    },
                    {"_time": _influx_ts(_at(today)), "common_name": "Blue Jay", "confidence": "7"},
                ]
            return [{"_time": _influx_ts(_at(today - timedelta(days=100)))}]

        monkeypatch.setattr(birds, "_query_influx", _fake)

    return _install


@pytest.fixture
def client():
    """Test client with auth stubbed out."""
    from app.web.app import app

    app.dependency_overrides[require_auth] = lambda: "test@example.com"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_page_renders_heatmap_trends_and_arrivals(client, fake_influx):
    fake_influx()

    resp = client.get("/birds")

    assert resp.status_code == 200
    assert "Birds · Wentworth" in resp.text
    assert "Barred Owl" in resp.text
    assert "<strong>2</strong> species" in resp.text
    assert "<strong>10</strong> detections" in resp.text
    assert "birds-arrivals" in resp.text  # both species are new after 100 quiet days


def test_week_view_is_selected(client, fake_influx):
    fake_influx()

    resp = client.get("/birds?view=week")

    assert resp.status_code == 200
    assert "Last 7 days by hour" in resp.text


def test_invalid_view_is_rejected(client, fake_influx):
    fake_influx()

    assert client.get("/birds?view=year").status_code == 422


def test_influx_failure_renders_an_error_not_a_500(client, fake_influx):
    fake_influx(exc=ConnectionError("koonti unreachable"))

    resp = client.get("/birds")

    assert resp.status_code == 200
    assert "koonti unreachable" in resp.text


def test_page_requires_auth(fake_influx):
    from app.web.app import app

    fake_influx()
    app.dependency_overrides.clear()
    assert TestClient(app).get("/birds").status_code == 401


# ── Relay health & dashboard card ──


def _heartbeat(monkeypatch, minutes_ago=None, exc=None):
    def _fake(_query):
        if exc is not None:
            raise exc
        if minutes_ago is None:
            return []
        when = datetime.now(birds.timezone.utc) - timedelta(minutes=minutes_ago)
        return [{"_time": when.strftime("%Y-%m-%dT%H:%M:%SZ")}]

    monkeypatch.setattr(birds, "_query_influx", _fake)


@pytest.mark.parametrize(
    ("minutes_ago", "status"), [(3, "green"), (14, "green"), (40, "yellow"), (200, "red")]
)
def test_relay_health_by_heartbeat_age(monkeypatch, minutes_ago, status):
    _heartbeat(monkeypatch, minutes_ago)

    health = birds.relay_health(datetime.now(birds.timezone.utc))

    assert health is not None
    assert health["status"] == status
    assert health["title"].startswith("Cabin relay last checked in")


def test_relay_health_without_any_heartbeat_is_red(monkeypatch):
    _heartbeat(monkeypatch)

    assert birds.relay_health(datetime.now(birds.timezone.utc))["status"] == "red"


def test_relay_health_hides_dot_when_influx_is_down(monkeypatch):
    _heartbeat(monkeypatch, exc=ConnectionError("koonti down"))

    assert birds.relay_health(datetime.now(birds.timezone.utc)) is None


def test_card_renders_summary_top_species_and_dot(client, fake_influx):
    fake_influx()

    resp = client.get("/partials/birds")

    assert resp.status_code == 200
    assert "Wentworth birds" in resp.text
    assert "<strong>2</strong> species" in resp.text
    assert "Barred Owl" in resp.text
    assert 'href="/birds"' in resp.text
    # fake_influx answers the heartbeat query with a 100-day-old timestamp
    assert "airq-status-red" in resp.text


def test_card_shows_message_when_influx_is_down(client, fake_influx):
    fake_influx(exc=ConnectionError("koonti unreachable"))

    resp = client.get("/partials/birds")

    assert resp.status_code == 200
    assert "Bird data unavailable" in resp.text
    assert "airq-status-dot" not in resp.text  # no false alarm when koonti is the problem


def test_card_requires_auth(fake_influx):
    from app.web.app import app

    fake_influx()
    app.dependency_overrides.clear()
    assert TestClient(app).get("/partials/birds").status_code == 401


# ── Sun-relative time ──

BOSTON = (42.3601, -71.0589)


def _local(t: datetime) -> str:
    return t.astimezone(birds.TZ_BOSTON).strftime("%H:%M")


@pytest.mark.parametrize(
    ("day", "rise", "sunset"),
    [
        (date(2026, 6, 21), "05:07", "20:24"),  # summer solstice, EDT
        (date(2026, 12, 21), "07:10", "16:14"),  # winter solstice, EST
    ],
)
def test_sun_times_match_boston_references(day, rise, sunset):
    r, s = birds.sun_times(day, *BOSTON)

    def minutes(hhmm):
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    assert abs(minutes(_local(r)) - minutes(rise)) <= 2
    assert abs(minutes(_local(s)) - minutes(sunset)) <= 2


def test_today_detections_stay_put():
    t = datetime(2026, 10, 27, 7, 40, tzinfo=birds.TZ_BOSTON)

    moved = birds.to_today_sun(t, t.date())

    assert abs((moved - t).total_seconds()) < 1


def test_dawn_lines_up_across_the_dst_change():
    """30 minutes after sunrise on a pre-DST day lands ~30 minutes after sunrise
    on a post-DST day, an hour earlier on the clock."""
    before, after = date(2026, 10, 30), date(2026, 11, 2)
    t = birds.sun_times(before)[0] + timedelta(minutes=30)

    moved = birds.to_today_sun(t, after)

    assert _local(t)[:2] == "07"  # ~7:50 EDT
    assert _local(moved)[:2] == "06"  # ~6:54 EST
    gap = moved - birds.sun_times(after)[0]
    assert timedelta(minutes=28) < gap < timedelta(minutes=32)


def test_night_detections_map_relative_to_sunset_and_sunrise():
    day, today = date(2026, 10, 20), date(2026, 10, 27)
    evening = birds.sun_times(day)[1] + timedelta(hours=1)  # 1h after sunset
    predawn = birds.sun_times(day)[0] - timedelta(minutes=10)  # 10 min before sunrise

    evening_moved = birds.to_today_sun(evening, today)
    predawn_moved = birds.to_today_sun(predawn, today)

    after_sunset = evening_moved - birds.sun_times(today)[1]
    before_sunrise = birds.sun_times(today)[0] - predawn_moved
    # nights lengthen a little over the week, so the offsets stretch slightly
    assert timedelta(minutes=58) < after_sunset < timedelta(minutes=63)
    assert timedelta(minutes=9) < before_sunrise < timedelta(minutes=11)


def test_heatmap_uses_place_to_bin_hours():
    windows = [(_at(_days_ago(3), 8), "Blue Jay", 4)]

    hm = birds.build_heatmap(
        windows, TODAY, "week", now_hour=9, place=lambda w: w - timedelta(hours=1)
    )

    assert hm["rows"][0]["cells"][7]["alpha"] == 1.0


def test_daylight_marks_night_hours():
    dl = birds.daylight(TODAY)

    assert dl["sunrise"]["label"].startswith("7:")
    assert dl["sunset"]["label"].startswith("5:")
    assert dl["night"][2] and dl["night"][22]
    assert not dl["night"][12]
    assert 0 < dl["sunrise"]["pct"] < dl["sunset"]["pct"] < 100
