"""Tests for the Garmin fitness page."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.cache import sqlite as cache
from app.web.auth import require_auth
from app.web.routes import fitness


@pytest.fixture(autouse=True)
def tmp_cache_db(monkeypatch, tmp_path):
    """Day tags live in the app's SQLite cache; never touch the real one in tests."""
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "cache.db"))


def _ts(day: date) -> str:
    """InfluxDB-style timestamp for local midnight, as the collector writes it."""
    when = datetime(day.year, day.month, day.day, tzinfo=fitness.TZ_BOSTON)
    return when.isoformat()


def _today() -> date:
    return datetime.now(fitness.TZ_BOSTON).date()


def _rows(field: str, values: list[float], end: date | None = None) -> list[dict]:
    """One row per day ending at `end` (default today), oldest first."""
    end = end or _today()
    start = end - timedelta(days=len(values) - 1)
    return [
        {"_field": field, "_value": str(v), "_time": _ts(start + timedelta(days=i))}
        for i, v in enumerate(values)
    ]


@pytest.fixture
def fake_influx(monkeypatch):
    def _install(rows, exc=None):
        def _fake(_query):
            if exc is not None:
                raise exc
            return rows

        monkeypatch.setattr(fitness, "_query_influx", _fake)

    return _install


def _chart(data: dict, label: str) -> dict:
    for section in data["sections"]:
        for chart in section["charts"]:
            if chart["label"] == label:
                return chart
    raise AssertionError(f"no chart {label!r}")


def test_rows_map_to_their_local_calendar_day(fake_influx):
    fake_influx(_rows("steps", [1000, 2000], end=date(2026, 10, 27)))
    raw = fitness._fetch_daily(30)
    assert raw["steps"] == {date(2026, 10, 26): 1000.0, date(2026, 10, 27): 2000.0}


def test_rolling_band_needs_enough_nights_and_excludes_the_day_itself():
    start = date(2026, 10, 1)
    values = {start + timedelta(days=i): 50.0 + (i % 2) for i in range(10)}

    band = fitness._rolling_band(values)

    assert start + timedelta(days=6) not in band  # only 6 prior nights
    lo, hi = band[start + timedelta(days=7)]
    assert lo < 50.5 < hi


def test_resting_hr_flagged_when_outside_typical_range(fake_influx):
    fake_influx(_rows("rhr", [44, 45] * 10 + [55]))

    rhr = _chart(fitness.fetch_fitness_data("30d"), "Resting heart rate")

    assert rhr["series"][0]["latest_val"] == "55"
    assert rhr["series"][0]["flag"] == "Above typical"
    assert rhr["band_polys"]


def test_hrv_flag_uses_the_same_2_sd_rule_as_the_other_vitals(fake_influx):
    fake_influx(_rows("hrv", [55, 56] * 10 + [53]))  # sd ≈ 0.5: 53 is ~5 SD low
    hrv = _chart(fitness.fetch_fitness_data("30d"), "HRV")
    assert hrv["series"][0]["flag"] == "Below typical"


def test_one_sd_off_is_still_typical(fake_influx):
    fake_influx(_rows("rhr", [44, 46] * 10 + [46.5]))  # mean 45, sd ≈ 1: +1.5 SD
    rhr = _chart(fitness.fetch_fitness_data("30d"), "Resting heart rate")
    assert rhr["series"][0]["flag"] == "Typical"


def test_sleep_score_headline_uses_garmin_bands(fake_influx):
    fake_influx(_rows("sleep_score", [70, 83]))
    chart = _chart(fitness.fetch_fitness_data("30d"), "Sleep score")
    assert chart["series"][0]["flag"] == "Good"


def test_missing_days_break_the_line(fake_influx):
    end = _today()
    rows = _rows("steps", [1, 2], end=end - timedelta(days=10)) + _rows("steps", [3, 4], end=end)
    fake_influx(rows)

    steps = _chart(fitness.fetch_fitness_data("30d"), "Steps")

    assert len(steps["series"][0]["segments"]) == 2


def test_long_ranges_average_by_week(fake_influx):
    fake_influx(_rows("steps", [10000.0] * 70))
    steps = _chart(fitness.fetch_fitness_data("1y"), "Steps")
    pts = [p for seg in steps["series"][0]["segments"] for p in seg]
    assert 10 <= len(pts) <= 11
    assert all(v == 10000.0 for _, v in pts)


def test_unit_conversions_and_formatting(fake_influx):
    fake_influx(
        _rows("skin_temp_c", [-0.5]) + _rows("race_marathon_s", [10016]) + _rows("race_5k_s", [989])
    )
    data = fitness.fetch_fitness_data("30d")

    assert _chart(data, "Skin temp change")["series"][0]["latest_val"] == "-0.9"
    pace = {x["display"]: x["latest_val"] for x in _chart(data, "Race pace")["series"]}
    assert pace["Marathon"] == "6:22"  # 2:46:56 / 26.2 mi
    assert pace["5K"] == "5:18"  # 16:29 / 3.1 mi


def test_charts_without_data_are_omitted(fake_influx):
    fake_influx(_rows("steps", [5000]))
    data = fitness.fetch_fitness_data("30d")
    assert [s["title"] for s in data["sections"]] == ["Day"]


def test_x_markers_stay_inside_the_chart():
    for key, opt in fitness.RANGE_OPTIONS.items():
        end = date(2026, 10, 29)
        span = opt["days"]
        start = end - timedelta(days=span)
        markers = fitness._x_markers(start, end, key, lambda d, s=start, n=span: (d - s).days / n)
        assert markers, key
        assert all(0 < m["x_frac"] < 1 for m in markers), key


@pytest.fixture
def client():
    from app.web.app import app

    app.dependency_overrides[require_auth] = lambda: "test@example.com"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_page_renders(client, fake_influx):
    fake_influx(_rows("hrv", [55, 58]) + _rows("steps", [12000, 20394]))
    resp = client.get("/fitness?range=30d")
    assert resp.status_code == 200
    assert "Health Status" in resp.text
    assert "20,394" in resp.text


def test_page_shows_error_when_influx_down(client, fake_influx):
    fake_influx([], exc=RuntimeError("koonti unreachable"))
    resp = client.get("/fitness")
    assert resp.status_code == 200
    assert "koonti unreachable" in resp.text


def test_rejects_unknown_range(client):
    assert client.get("/fitness?range=5y").status_code == 422


def test_trailing_mean_needs_most_of_the_week():
    start = date(2026, 10, 1)
    values = {start + timedelta(days=i): float(i) for i in range(7)}

    avg = fitness._trailing_mean(values, 7)

    assert start + timedelta(days=2) not in avg  # only 3 of 7 nights so far
    assert avg[start + timedelta(days=3)] == 1.5  # nights 0-3
    assert avg[start + timedelta(days=6)] == 3.0  # nights 0-6


def test_sleep_chart_shows_last_night_and_7_day_average(fake_influx):
    hours = [7, 7, 7, 8, 8, 8, 6]  # last night 6h, week averages 7h 17m
    fake_influx(_rows("sleep_s", [h * 3600 for h in hours]))

    sleep = _chart(fitness.fetch_fitness_data("30d"), "Sleep")
    last_night, week = sleep["series"]

    assert last_night["latest_val"] == "6h 00m"
    assert week["latest_val"] == "7h 17m"
    assert week["width"] > last_night["width"]


def test_sleep_stages_are_7_day_averages_in_hours(fake_influx):
    fake_influx(_rows("deep_s", [1800] * 7) + _rows("rem_s", [5400] * 6 + [7200]))

    stages = {
        s["display"]: s for s in _chart(fitness.fetch_fitness_data("30d"), "Sleep stages")["series"]
    }

    assert stages["Deep"]["latest_val"] == "30m"
    assert stages["REM"]["latest_val"] == "1h 34m"
    assert stages["Light"]["latest_val"] is None


def _vitals_rows(nights: int, last: dict[str, float] | None = None) -> list[dict]:
    """`nights` steady nights per vital (with a little wobble for a real SD), last overridable."""
    base = {"hrv": 55, "rhr": 45, "respiration": 12, "skin_temp_c": 0.0, "spo2": 96}
    rows = []
    for field, v in base.items():
        values = [v + (0.5 if i % 2 else -0.5) for i in range(nights)]
        if last and field in last:
            values[-1] = last[field]
        rows += _rows(field, values)
    rows += _rows("hrv_balanced_low", [47] * nights) + _rows("hrv_balanced_high", [63] * nights)
    return rows


def test_radar_draws_last_night_bold_over_six_fading_nights(fake_influx):
    fake_influx(_vitals_rows(40))

    radar = fitness.fetch_vitals_radar()

    polys = radar["polys"]
    assert len(polys) == 7
    assert polys[-1]["day"] == _today()  # drawn last, on top
    assert polys[-1]["width"] == 2.5 and polys[-1]["opacity"] == 1.0
    widths = [p["width"] for p in polys]
    assert widths == sorted(widths)  # older = thinner
    assert [a["label"] for a in radar["axes"]] == [
        "HRV",
        "Resting heart rate",
        "Respiration",
        "Skin temp change",
        "Pulse ox",
    ]


def test_radar_labels_color_low_blue_high_red_typical_black(fake_influx):
    fake_influx(_vitals_rows(40, last={"rhr": 52, "hrv": 40}))

    axes = {a["label"]: a for a in fitness.fetch_vitals_radar()["axes"]}

    assert axes["HRV"]["value"] == "40"
    assert axes["HRV"]["color"] == fitness.VITAL_COLORS["Below typical"]
    assert axes["Resting heart rate"]["color"] == fitness.VITAL_COLORS["Above typical"]
    assert axes["Respiration"]["color"] == fitness.VITAL_COLORS["Typical"]


def test_radar_labels_stay_inside_the_viewbox(fake_influx):
    fake_influx(_vitals_rows(40, last={"skin_temp_c": -1.5}))
    for a in fitness.fetch_vitals_radar()["axes"]:
        right = a["text_x"] + len(a["value"]) * fitness.LABEL_CHAR_PX
        assert a["icon_x"] >= 0 and right <= fitness.RADAR_W, a["label"]
        assert a["icon_y"] >= 0 and a["text_y"] <= fitness.RADAR_H, a["label"]


def test_radar_point_on_band_edge_lands_on_typical_ring():
    lo_edge = fitness._radar_r(-1)
    hi_edge = fitness._radar_r(1)
    assert 0 < lo_edge < fitness._radar_r(0) < hi_edge < fitness.RADAR_R
    assert fitness._radar_r(99) == fitness.RADAR_R  # clamped


def test_radar_skips_nights_missing_a_vital(fake_influx):
    rows = [
        r for r in _vitals_rows(40) if not (r["_field"] == "spo2" and r["_time"] == _ts(_today()))
    ]
    fake_influx(rows)

    radar = fitness.fetch_vitals_radar()

    assert _today() not in [p["day"] for p in radar["polys"]]
    spo2 = radar["axes"][4]
    assert spo2["value"] == "–"
    assert spo2["color"] == fitness.VITAL_COLORS[None]


def test_vitals_partial_links_to_fitness(client, fake_influx):
    fake_influx(_vitals_rows(40))
    resp = client.get("/partials/fitness-vitals")
    assert resp.status_code == 200
    assert 'href="/fitness"' in resp.text
    assert ">Vitals<" in resp.text


def test_vitals_partial_without_data(client, fake_influx):
    fake_influx([])
    assert "No Garmin vitals yet" in client.get("/partials/fitness-vitals").text


# ── Strain, big days, day tags ──


def test_strain_is_mean_signed_z_and_needs_all_four():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(21)]
    steady = {d: 50.0 + (i % 2) for i, d in enumerate(days)}  # mean 50.5, sd ≈ 0.51
    last = days[-1]
    raw = {
        "rhr": {**steady, last: 50.5 + 2 * 0.5121},  # +2 SD (worse)
        "hrv": {**steady, last: 50.5 - 2 * 0.5121},  # −2 SD → +2 after sign flip (worse)
        "respiration": {**steady, last: 50.5},  # 0
        "skin_temp_c": {**steady, last: 50.5},  # 0
    }

    strain = fitness._strain(raw)

    assert strain[last] == pytest.approx(1.0, abs=0.02)
    del raw["skin_temp_c"][last]
    assert last not in fitness._strain(raw)


def test_big_day_is_double_the_trailing_average_or_a_race():
    start = date(2026, 9, 1)
    load = {start + timedelta(days=i): 100.0 for i in range(28)}  # avg 100
    race_day = start + timedelta(days=30)
    raw = {
        "train_load": {
            **load,
            start + timedelta(days=28): 190.0,  # 1.9x: not big
            start + timedelta(days=29): 260.0,  # 2.6x: big
            race_day: 50.0,
        },
        "race": {race_day: 1.0},
    }

    big = fitness._big_days(raw)

    assert start + timedelta(days=28) not in big
    assert big[start + timedelta(days=29)] == 260.0
    assert race_day in big


def test_tagged_and_big_days_become_chart_markers(fake_influx):
    today = _today()
    cache.toggle_day_tag((today - timedelta(days=3)).isoformat(), "sick")
    fake_influx(
        _rows("rhr", [44, 45] * 10)
        + _rows("steps", [9000] * 5)
        + _rows("train_load", [50.0] * 27 + [400.0], end=today - timedelta(days=1))
    )

    data = fitness.fetch_fitness_data("30d")
    rhr = _chart(data, "Resting heart rate")

    assert [e["label"].split(": ")[1] for e in rhr["events"]["sick"]] == ["sick"]
    assert rhr["events"]["big"][0]["label"].endswith("(load 400)")
    assert _chart(data, "Steps")["events"] is None  # markers only on Health Status charts


def test_toggle_day_tag_round_trip():
    assert cache.toggle_day_tag("2026-09-08", "sick") is True
    assert cache.get_day_tags("2026-09-01", "2026-09-30") == {"2026-09-08": {"sick"}}
    assert cache.toggle_day_tag("2026-09-08", "sick") is False
    assert cache.get_day_tags("2026-09-01", "2026-09-30") == {}


def test_radar_shows_strain_in_the_middle(fake_influx):
    fake_influx(_vitals_rows(40, last={"rhr": 52, "hrv": 40}))
    strain = fitness.fetch_vitals_radar()["strain"]
    assert strain["value"].startswith("+")
    assert strain["color"] == fitness.VITAL_COLORS["Above typical"]


def test_week_is_monday_to_sunday_with_last_weeks_days_for_the_future():
    thursday = date(2026, 10, 29)

    week = fitness._week_days(thursday)

    assert [d.strftime("%a") for d in week] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert week[0] == date(2026, 10, 26) and week[3] == thursday
    assert week[4] == date(2026, 10, 23)  # Friday: last week's, still within 7 days
    assert all(thursday - timedelta(days=6) <= d <= thursday for d in week)


def test_week_strip_shows_strain_and_sick_days(fake_influx):
    today = _today()
    cache.toggle_day_tag(today.isoformat(), "sick")
    fake_influx(_vitals_rows(40, last={"rhr": 52, "hrv": 40}))

    week = {d["iso"]: d for d in fitness.fetch_vitals_radar()["week"]}

    cell = week[today.isoformat()]
    assert cell["today"] and cell["sick"] and cell["strain"]["value"].startswith("+")
    assert sum(d["sick"] for d in week.values()) == 1


def test_tapping_a_day_toggles_sick(client, fake_influx):
    fake_influx([])
    day = (_today() - timedelta(days=2)).isoformat()

    on = client.post(f"/partials/fitness-vitals/sick/{day}")
    off = client.post(f"/partials/fitness-vitals/sick/{day}")

    assert on.status_code == 200 and "vitals-day sick" in on.text
    assert 'aria-pressed="true"' in on.text and 'aria-pressed="false"' in off.text
    assert cache.get_day_tags(day, day) == {}


def test_only_the_last_seven_days_can_be_tagged(client):
    old = (_today() - timedelta(days=7)).isoformat()
    future = (_today() + timedelta(days=1)).isoformat()
    assert client.post(f"/partials/fitness-vitals/sick/{old}").status_code == 400
    assert client.post(f"/partials/fitness-vitals/sick/{future}").status_code == 400
