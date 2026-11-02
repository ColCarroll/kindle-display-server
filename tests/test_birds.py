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


def test_heatmap_month_sums_the_last_30_days():
    hourly = [
        (_at(TODAY, 7), "Blue Jay", 4),
        (_at(_days_ago(3), 7), "Blue Jay", 6),  # inside the week too
        (_at(_days_ago(29), 7), "Blue Jay", 5),  # oldest day of the month
        (_at(_days_ago(30), 7), "Blue Jay", 100),  # 31 days back: outside
    ]

    assert birds.build_heatmap(hourly, TODAY, "month", now_hour=9)["rows"][0]["total"] == 15
    assert birds.build_heatmap(hourly, TODAY, "week", now_hour=9)["rows"][0]["total"] == 10


def test_heatmap_limits_rows_and_reports_the_rest():
    hourly = [(_at(TODAY, 6), f"Bird {i:02d}", 100 - i) for i in range(birds.HEATMAP_ROWS + 3)]

    hm = birds.build_heatmap(hourly, TODAY, "today", now_hour=6)

    assert len(hm["rows"]) == birds.HEATMAP_ROWS
    assert hm["rows"][0]["name"] == "Bird 00"
    assert hm["more"] == 3


def test_heatmap_empty():
    assert birds.build_heatmap([], TODAY, "today", now_hour=6) == {"rows": [], "more": 0}


# ── So-far-today curves ──


def _same_clock(when: datetime) -> datetime:
    """Identity placement: keep tests about counting, not the sun shift."""
    return when.replace(year=TODAY.year, month=TODAY.month, day=TODAY.day)


def _day_windows(day: date, per_hour: dict[int, list[tuple[str, int]]]):
    return [(_at(day, h), name, n) for h, items in per_hour.items() for name, n in items]


def test_running_totals_count_detections_and_new_species():
    windows = _day_windows(TODAY, {6: [("Blue Jay", 3)], 7: [("Blue Jay", 2), ("Owl", 1)]})

    det, sp = birds._running_totals(windows, TODAY, _same_clock)[TODAY]

    six, seven = 6 * 4, 7 * 4  # 15-minute bins
    assert det[six - 1] == 0 and det[six] == 3 and det[seven] == 6 and det[-1] == 6
    assert sp[six] == 1 and sp[seven] == 2 and sp[-1] == 2


def test_cumulative_band_records_and_rank():
    history = [_days_ago(i) for i in range(1, 11)]  # 10 past days, totals 10..100
    windows = []
    for i, d in enumerate(history, start=1):
        windows += _day_windows(d, {7: [("Blue Jay", 10 * i)]})
    windows += _day_windows(TODAY, {7: [("Blue Jay", 75)]})
    now = _at(TODAY, 9)

    trend = birds.build_cumulative(windows, TODAY, now, history, _same_clock)
    det = trend["detections"]

    assert det["today"] == 75
    assert det["rank"] == 70  # beats 7 of the 10 days at 9am
    assert det["high"]["label"] == f"{_days_ago(10):%b} {_days_ago(10).day} · 100"
    assert det["low"]["label"].endswith("· 10")
    assert det["band"] is not None
    assert trend["history_days"] == 10


def test_no_band_or_rank_until_enough_history():
    windows = _day_windows(_days_ago(1), {7: [("Blue Jay", 5)]})
    trend = birds.build_cumulative(windows, TODAY, _at(TODAY, 9), [_days_ago(1)], _same_clock)
    assert trend["detections"]["rank"] is None and trend["detections"]["band"] is None


def test_today_curve_stops_at_now():
    windows = _day_windows(TODAY, {6: [("Blue Jay", 3)]})
    trend = birds.build_cumulative(windows, TODAY, _at(TODAY, 9), [], _same_clock)
    pts = trend["detections"]["today_points"].split()
    assert len(pts) == 1 + 9 * 4 + 1  # origin + bins 0 through the 9:00 bin


def test_comparison_days_skip_first_partial_day_and_relay_outages():
    earliest = _days_ago(10)
    heartbeats = {_days_ago(4): 29, _days_ago(3): 288, _days_ago(2): 100, _days_ago(1): 287}

    days = birds.comparison_days(TODAY, earliest, heartbeats)

    assert days[0] == _days_ago(9)  # not the partial first day
    assert _days_ago(2) not in days  # relay down most of the day
    assert _days_ago(4) in days  # heartbeats started mid-day: not an outage
    assert _days_ago(5) in days  # before heartbeats existed: assume it was up
    assert _days_ago(1) in days and TODAY not in days


def test_night_windows_shifted_past_midnight_clamp_to_the_axis():
    late = datetime(TODAY.year, TODAY.month, TODAY.day, 23, 59, tzinfo=birds.TZ_BOSTON)
    assert birds._bin_of(late + timedelta(hours=2), TODAY) == birds.CUMULATIVE_BINS - 1
    assert birds._bin_of(late - timedelta(days=1), TODAY) == 0


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

    def _install(exc=None, heartbeat_minutes=1):
        now = datetime.now(birds.TZ_BOSTON)
        today = now.date()

        def _fake(query):
            if exc is not None:
                raise exc
            if "relay_heartbeat" in query:
                beat = datetime.now(birds.timezone.utc) - timedelta(minutes=heartbeat_minutes)
                return [{"_time": _influx_ts(beat)}]
            if "every: 10m" in query:
                return [
                    {
                        "_time": _influx_ts(_at(today, 0)),
                        "common_name": "Barred Owl",
                        "confidence": "3",
                    }
                ]
            if "every: 1d" in query or "every: 15m" in query:
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


def test_line_shows_todays_counts_and_links_to_page(client, fake_influx):
    fake_influx()

    resp = client.get("/partials/birds")

    assert resp.status_code == 200
    assert "2 species · 10 detections today" in resp.text
    assert 'href="/birds"' in resp.text
    assert "down" not in resp.text


def test_line_warns_when_relay_is_down(client, fake_influx):
    fake_influx(heartbeat_minutes=300)

    resp = client.get("/partials/birds")

    assert "Bird detector down!" in resp.text
    assert "last heard from 5h ago" in resp.text
    assert "detections today" not in resp.text


def test_line_stays_normal_when_relay_is_only_lagging(client, fake_influx):
    fake_influx(heartbeat_minutes=40)

    assert "detections today" in client.get("/partials/birds").text


def test_line_is_empty_when_influx_is_down(client, fake_influx):
    fake_influx(exc=ConnectionError("koonti unreachable"))

    resp = client.get("/partials/birds")

    assert resp.status_code == 200
    assert resp.text.strip() == ""  # no false alarm when koonti is the problem


def test_line_requires_auth(fake_influx):
    from app.web.app import app

    fake_influx()
    app.dependency_overrides.clear()
    assert TestClient(app).get("/partials/birds").status_code == 401


# ── Heatmap partial ──


def test_heatmap_partial_renders_only_the_heatmap_section(client, fake_influx):
    fake_influx()

    resp = client.get("/partials/birds-heatmap?view=week")

    assert resp.status_code == 200
    assert 'id="birds-heatmap-section"' in resp.text
    assert "Last 7 days by hour" in resp.text
    assert "Barred Owl" in resp.text
    assert "Arrivals" not in resp.text
    assert "<html" not in resp.text


def test_heatmap_toggle_swaps_partial_and_keeps_a_real_link(client, fake_influx):
    fake_influx()

    resp = client.get("/birds")

    assert 'hx-get="/partials/birds-heatmap?view=week"' in resp.text
    assert 'hx-push-url="/birds?view=week"' in resp.text
    assert 'href="/birds?view=week"' in resp.text


def test_heatmap_partial_rejects_invalid_view(client, fake_influx):
    fake_influx()

    assert client.get("/partials/birds-heatmap?view=year").status_code == 422


def test_heatmap_partial_shows_error_when_influx_is_down(client, fake_influx):
    fake_influx(exc=ConnectionError("koonti unreachable"))

    resp = client.get("/partials/birds-heatmap")

    assert resp.status_code == 200
    assert "koonti unreachable" in resp.text


def test_heatmap_partial_requires_auth(fake_influx):
    from app.web.app import app

    fake_influx()
    app.dependency_overrides.clear()
    assert TestClient(app).get("/partials/birds-heatmap").status_code == 401


def test_page_draws_both_so_far_curves(client, fake_influx):
    fake_influx()

    resp = client.get("/birds")

    assert resp.text.count('class="birds-curve"') == 2
    assert "not enough history to compare yet" in resp.text  # fake has no past days
