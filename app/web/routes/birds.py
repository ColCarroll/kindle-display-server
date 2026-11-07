"""Birds route handler: BirdNET-Go detections from the Wentworth cabin.

A relay on the cabin Pi streams each detection into the InfluxDB `birds` bucket
(measurement `detection`; tags site, species, common_name, class, model; fields
confidence, id, and `review` once a detection is marked correct/false positive).
"""

import asyncio
import logging
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.web.auth import require_auth
from app.web.routes.airq import _fmt_age, _parse_ts, _query_influx
from app.web.templating import templates

logger = logging.getLogger(__name__)
TZ_BOSTON = ZoneInfo("America/New_York")

router = APIRouter()

BUCKET = "birds"
SITE = "cabin"
SITE_LABEL = "Wentworth"
# BirdNET-Go's (deliberately coarse) location for the cabin, used for sunrise/sunset
SUN_LAT, SUN_LON = 43.870, -71.913

# "So far today" curves: today's running totals against the same time of day on the
# previous CUMULATIVE_DAYS complete days (kept to a season, since busyness is seasonal)
CUMULATIVE_DAYS = 90
CUMULATIVE_EVERY = "15m"
CUMULATIVE_BIN_MIN = 15
CUMULATIVE_BINS = 24 * 60 // CUMULATIVE_BIN_MIN
CUMULATIVE_MIN_HISTORY = 5  # fewer comparison days than this: no band or rank yet
HEARTBEAT_EXPECTED = 288  # relay heartbeats per full day (every 5 min)
HEARTBEAT_MIN_COVERAGE = 0.9
# Curve geometry (SVG user units)
CURVE_W, CURVE_H = 320, 120
CURVE_L, CURVE_R, CURVE_T, CURVE_B = 4, 250, 8, 100
WEEK_DAYS = 7  # heatmap "7 days" view
MONTH_DAYS = 30  # heatmap "30 days" view: sun-aligned, so dawn still lines up across the month
HEATMAP_ROWS = 15  # most-detected species shown in the heatmap
HEATMAP_EVERY = "10m"  # fine windows, re-binned by hour after the sun-relative shift
HEATMAP_WINDOW_MID = timedelta(minutes=5)
ARRIVAL_WINDOW_DAYS = 14  # list species that arrived within this many days...
ARRIVAL_ABSENCE_DAYS = 60  # ...after not being heard for at least this long
BASELINE_DAYS = 2  # species first heard right after records began aren't "arrivals"
ARRIVAL_MIN_DETECTIONS = 2  # one-off detections are where most false positives live

VIEWS = {"today": "Today", "week": "7 days", "month": "30 days"}
VIEW_DAYS = {"today": 1, "week": WEEK_DAYS, "month": MONTH_DAYS}
DEFAULT_VIEW = "today"


def _detections_flux(start: datetime) -> str:
    """Flux preamble: bird detections since `start`, minus reviewed false positives.

    `review` is a separate field on the same point, so fields are pivoted into
    columns before filtering. Windows downstream are aligned to Boston local time.
    """
    start_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""import "timezone"
option location = timezone.location(name: "America/New_York")

from(bucket: "{BUCKET}")
  |> range(start: {start_utc})
  |> filter(fn: (r) => r._measurement == "detection" and r.site == "{SITE}" and r.class == "aves")
  |> filter(fn: (r) => r._field == "confidence" or r._field == "review")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => not exists r.review or r.review != "false_positive")
"""


def _counts_query(start: datetime, every: str) -> str:
    """Per-species detection counts in local-time windows (e.g. `10m`, `1d`)."""
    return (
        _detections_flux(start)
        + f"""  |> group(columns: ["common_name"])
  |> aggregateWindow(every: {every}, fn: count, column: "confidence",
                     timeSrc: "_start", createEmpty: false)
"""
    )


EARLIEST_QUERY = f"""
from(bucket: "{BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "detection" and r.site == "{SITE}")
  |> filter(fn: (r) => r._field == "confidence")
  |> group()
  |> first()
  |> keep(columns: ["_time"])
"""


def _local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=TZ_BOSTON)


def _parse_counts(rows: list[dict]) -> list[tuple[datetime, str, int]]:
    """(local window start, common name, count) from aggregateWindow rows."""
    out = []
    for row in rows:
        try:
            when = datetime.fromtimestamp(_parse_ts(row["_time"]), TZ_BOSTON)
            out.append((when, row["common_name"], int(row["confidence"])))
        except (KeyError, ValueError):
            continue
    return out


# ── Sun-relative time ──
#
# Birds keep time by the sun, not the clock: sunrise drifts a minute or two a day
# and jumps an hour at DST changes. Before detections from different days are
# summed into one hour-of-day heatmap, each is moved to the same point relative
# to sunrise/sunset on *today's* schedule (e.g. 40% of the way through daylight),
# so dawn lines up with dawn. The axis still reads as today's clock time.


@lru_cache(maxsize=64)
def sun_times(day: date, lat: float = SUN_LAT, lon: float = SUN_LON) -> tuple[datetime, datetime]:
    """(sunrise, sunset) in UTC for a calendar day, via the NOAA sunrise equation.

    Accurate to about a minute at mid latitudes; includes the standard -0.833°
    correction for refraction and the sun's radius. Longitude is east-positive.
    """
    rad = math.radians
    n = (day - date(2000, 1, 1)).days
    j_star = n - lon / 360
    m = (357.5291 + 0.98560028 * j_star) % 360
    c = 1.9148 * math.sin(rad(m)) + 0.0200 * math.sin(rad(2 * m)) + 0.0003 * math.sin(rad(3 * m))
    ecl = (m + c + 180 + 102.9372) % 360
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(rad(m)) - 0.0069 * math.sin(rad(2 * ecl))
    sin_dec = math.sin(rad(ecl)) * math.sin(rad(23.4397))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_w = (math.sin(rad(-0.833)) - math.sin(rad(lat)) * sin_dec) / (math.cos(rad(lat)) * cos_dec)
    half_day = math.degrees(math.acos(max(-1.0, min(1.0, cos_w)))) / 360

    def _utc(jd: float) -> datetime:
        return datetime.fromtimestamp((jd - 2440587.5) * 86400, timezone.utc)

    return _utc(j_transit - half_day), _utc(j_transit + half_day)


def to_today_sun(when: datetime, today: date) -> datetime:
    """The moment on `today`'s sun schedule matching `when`'s position between
    sunrise and sunset (daytime) or sunset and sunrise (night)."""
    day = when.astimezone(TZ_BOSTON).date()
    rise, sunset = sun_times(day)
    if when < rise:  # the night before `day`'s sunrise
        start, end = sun_times(day - timedelta(days=1))[1], rise
        anchor = (sun_times(today - timedelta(days=1))[1], sun_times(today)[0])
    elif when < sunset:
        start, end = rise, sunset
        anchor = sun_times(today)
    else:  # the night after `day`'s sunset
        start, end = sunset, sun_times(day + timedelta(days=1))[0]
        anchor = (sun_times(today)[1], sun_times(today + timedelta(days=1))[0])
    frac = (when - start) / (end - start)
    return (anchor[0] + frac * (anchor[1] - anchor[0])).astimezone(TZ_BOSTON)


def daylight(today: date) -> dict:
    """Today's sunrise/sunset for the heatmap axis, and which hour cells are night."""
    rise, sunset = (t.astimezone(TZ_BOSTON) for t in sun_times(today))

    def _hours(t: datetime) -> float:
        return t.hour + t.minute / 60

    return {
        "sunrise": {"label": f"{rise:%-I:%M}", "pct": round(_hours(rise) / 24 * 100, 2)},
        "sunset": {"label": f"{sunset:%-I:%M}", "pct": round(_hours(sunset) / 24 * 100, 2)},
        # an hour cell is "night" if its midpoint falls outside daylight
        "night": [not _hours(rise) <= h + 0.5 < _hours(sunset) for h in range(24)],
    }


def _cell_alpha(count: int, max_count: int) -> float:
    """Heatmap shading: sqrt scale so quiet hours stay visible next to the dawn chorus."""
    if count <= 0 or max_count <= 0:
        return 0.0
    return round(0.15 + 0.85 * math.sqrt(count / max_count), 3)


def build_heatmap(
    windows: list[tuple[datetime, str, int]],
    today: date,
    view: str,
    now_hour: int,
    place: Callable[[datetime], datetime] | None = None,
) -> dict:
    """Species × hour-of-day grid for today, or summed over the view's last VIEW_DAYS days.

    `place` maps a window's time to where it belongs on today's axis (e.g.
    `to_today_sun`); by default the window's own clock hour is used.
    """
    first_day = today - timedelta(days=VIEW_DAYS[view] - 1)
    grid: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
    for when, name, count in windows:
        if first_day <= when.date() <= today:
            hour = (place(when) if place else when).hour
            grid[name][hour] += count

    ranked = sorted(grid.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
    shown = ranked[:HEATMAP_ROWS]
    max_count = max((c for _, hours in shown for c in hours), default=0)
    rows = []
    for name, hours in shown:
        cells = []
        for hour, count in enumerate(hours):
            noun = "detection" if count == 1 else "detections"
            cells.append(
                {
                    "alpha": _cell_alpha(count, max_count),
                    "now": view == "today" and hour == now_hour,
                    "label": f"{name} · {hour:02d}:00 · {count} {noun}",
                }
            )
        rows.append({"name": name, "total": sum(hours), "cells": cells})
    return {"rows": rows, "more": max(len(ranked) - HEATMAP_ROWS, 0)}


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted list."""
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _bin_of(placed: datetime, today: date) -> int:
    """15-minute bin on today's axis, clamped to the day (sun-shifted night windows
    can land just before midnight or just after the next one)."""
    minutes = (placed - _local_midnight(today)).total_seconds() / 60
    return max(0, min(CUMULATIVE_BINS - 1, int(minutes // CUMULATIVE_BIN_MIN)))


def _running_totals(
    windows: list[tuple[datetime, str, int]], today: date, place: Callable[[datetime], datetime]
) -> dict[date, tuple[list[int], list[int]]]:
    """{day: (cumulative detections, cumulative distinct species)} per bin of today's axis."""
    counts: dict[date, list[int]] = defaultdict(lambda: [0] * CUMULATIVE_BINS)
    first_heard: dict[date, dict[str, int]] = defaultdict(dict)
    for when, name, count in windows:
        b = _bin_of(place(when), today)
        day = when.date()
        counts[day][b] += count
        first_heard[day][name] = min(b, first_heard[day].get(name, b))
    out = {}
    for day, per_bin in counts.items():
        new_species = [0] * CUMULATIVE_BINS
        for b in first_heard[day].values():
            new_species[b] += 1
        out[day] = (_cumsum(per_bin), _cumsum(new_species))
    return out


def _cumsum(values: list[int]) -> list[int]:
    total, out = 0, []
    for v in values:
        total += v
        out.append(total)
    return out


def _curve_x(b: int) -> float:
    """Right edge of bin b: a running total is "as of the end of" its bin."""
    return round(CURVE_L + (b + 1) / CUMULATIVE_BINS * (CURVE_R - CURVE_L), 1)


def build_cumulative(
    windows: list[tuple[datetime, str, int]],
    today: date,
    now: datetime,
    history: list[date],
    place: Callable[[datetime], datetime],
) -> dict:
    """Running detection / species totals for today vs. the same time on `history` days:
    25th–75th percentile band, the record-high and record-low days, and today's rank."""
    totals = _running_totals(windows, today, place)
    now_bin = _bin_of(now, today)
    past = [d for d in history if d in totals]
    out: dict = {"history_days": len(past)}

    for idx, key in enumerate(("detections", "species")):
        today_curve = totals.get(today, ([0] * CUMULATIVE_BINS, [0] * CUMULATIVE_BINS))[idx]
        curves = {d: totals[d][idx] for d in past}
        current = today_curve[now_bin]
        enough = len(curves) >= CUMULATIVE_MIN_HISTORY

        band_lo = band_hi = env_lo = env_hi = None
        high = low = None
        if enough:
            cols = [sorted(c[b] for c in curves.values()) for b in range(CUMULATIVE_BINS)]
            band_lo = [_quantile(col, 0.25) for col in cols]
            band_hi = [_quantile(col, 0.75) for col in cols]
            # Records at each time of day (may mix days): today above env_hi ⇔ rank 100%
            env_lo = [col[0] for col in cols]
            env_hi = [col[-1] for col in cols]
            # ...labeled with the full-day record holders
            high = max(curves, key=lambda d: (curves[d][-1], d))
            low = min(curves, key=lambda d: (curves[d][-1], d))

        top = max([current, *(env_hi or [])], default=0)
        top = max(top * 1.08, 5)

        def y(v: float, top: float = top) -> float:
            return round(CURVE_B - v / top * (CURVE_B - CURVE_T), 1)

        def path(curve: list, upto: int = CUMULATIVE_BINS - 1) -> str:
            pts = [(CURVE_L, CURVE_B)] + [(_curve_x(b), y(curve[b])) for b in range(upto + 1)]
            return " ".join(f"{px},{py}" for px, py in pts)

        def record(day: date | None, envelope: list | None, curves: dict = curves) -> dict | None:
            if day is None or envelope is None:
                return None
            total = curves[day][-1]
            return {
                "points": path(envelope),
                "label": f"{day.strftime('%b')} {day.day} · {total}",
                "y": y(total),
            }

        band = None
        if band_lo and band_hi:
            upper = [(_curve_x(b), y(band_hi[b])) for b in range(CUMULATIVE_BINS)]
            lower = [(_curve_x(b), y(band_lo[b])) for b in reversed(range(CUMULATIVE_BINS))]
            band = " ".join(f"{px},{py}" for px, py in [(CURVE_L, CURVE_B), *upper, *lower])

        rank = None
        if enough:
            at_now = [c[now_bin] for c in curves.values()]
            below = sum(v < current for v in at_now) + 0.5 * sum(v == current for v in at_now)
            rank = round(100 * below / len(at_now))

        high_rec, low_rec = record(high, env_hi), record(low, env_lo)
        # Keep the two right-edge labels from overprinting when the records are close
        if high_rec and low_rec and low_rec["y"] - high_rec["y"] < 11:
            low_rec["y"] = high_rec["y"] + 11
        out[key] = {
            "today": current,
            "rank": rank,
            "band": band,
            "high": high_rec,
            "low": low_rec,
            "today_points": path(today_curve, now_bin),
            "today_end": (_curve_x(now_bin), y(current)),
            "top_label": int(top),
        }

    rise, sunset = (t.astimezone(TZ_BOSTON) for t in sun_times(today))
    out["ticks"] = [
        {"x": _curve_x(h * 60 // CUMULATIVE_BIN_MIN - 1), "label": label}
        for h, label in ((6, "6am"), (12, "noon"), (18, "6pm"))
    ]
    out["sun"] = [_curve_x(_bin_of(rise, today)), _curve_x(_bin_of(sunset, today))]
    out["geom"] = {
        "w": CURVE_W,
        "h": CURVE_H,
        "l": CURVE_L,
        "r": CURVE_R,
        "t": CURVE_T,
        "b": CURVE_B,
    }
    return out


HEARTBEAT_DAILY_QUERY = f"""import "timezone"
option location = timezone.location(name: "America/New_York")

from(bucket: "{BUCKET}")
  |> range(start: -{CUMULATIVE_DAYS + 1}d)
  |> filter(fn: (r) => r._measurement == "relay_heartbeat" and r.site == "{SITE}")
  |> group()
  |> aggregateWindow(every: 1d, fn: count, timeSrc: "_start", createEmpty: false)
"""


def comparison_days(today: date, earliest: date | None, heartbeats: dict[date, int]) -> list[date]:
    """Complete days to compare today against: after the first (partial) day of records,
    before today, and, once the relay started sending heartbeats, only days it was up
    for at least HEARTBEAT_MIN_COVERAGE of (so an outage doesn't read as a quiet day)."""
    first = today - timedelta(days=CUMULATIVE_DAYS)
    if earliest:
        first = max(first, earliest + timedelta(days=1))
    heartbeat_start = min(heartbeats, default=None)
    days = []
    for i in range((today - first).days):
        d = first + timedelta(days=i)
        relay_down = heartbeats.get(d, 0) < HEARTBEAT_MIN_COVERAGE * HEARTBEAT_EXPECTED
        # the first heartbeat day is partial by definition: the relay started mid-day
        if heartbeat_start and d > heartbeat_start and relay_down:
            continue
        days.append(d)
    return days


def find_arrivals(
    daily: list[tuple[datetime, str, int]], today: date, earliest: date | None
) -> list[dict]:
    """Species heard in the last ARRIVAL_WINDOW_DAYS that hadn't been heard for
    ARRIVAL_ABSENCE_DAYS before that (returning migrants, new visitors).

    Species first heard within BASELINE_DAYS of the start of records are skipped:
    everything is "new" when the detector first starts. An arrival also needs at
    least ARRIVAL_MIN_DETECTIONS detections since it began, to skip one-off misfires.
    """
    seen: dict[str, Counter] = defaultdict(Counter)
    for when, name, count in daily:
        seen[name][when.date()] += count

    window_start = today - timedelta(days=ARRIVAL_WINDOW_DAYS - 1)
    baseline_end = earliest + timedelta(days=BASELINE_DAYS) if earliest else None
    arrivals = []
    for name, by_day in seen.items():
        for day in sorted(d for d in by_day if window_start <= d <= today):
            gap_start = day - timedelta(days=ARRIVAL_ABSENCE_DAYS)
            heard_before = any(gap_start <= d < day for d in by_day)
            if heard_before or (baseline_end and day < baseline_end):
                continue
            since = sum(c for d, c in by_day.items() if day <= d <= today)
            if since >= ARRIVAL_MIN_DETECTIONS:
                label = day.strftime("%b %-d")
                arrivals.append({"name": name, "date": day, "label": label, "count": since})
            break
    return sorted(arrivals, key=lambda a: (a["date"], a["name"]), reverse=True)


def fetch_heatmap(view: str = DEFAULT_VIEW) -> dict:
    """Heatmap for today or the view's last VIEW_DAYS days, with today's sunrise/sunset."""
    now = datetime.now(TZ_BOSTON)
    today = now.date()
    start = _local_midnight(today - timedelta(days=VIEW_DAYS[view] - 1))
    windows = _parse_counts(_query_influx(_counts_query(start, HEATMAP_EVERY)))
    return {
        "view": view,
        "views": VIEWS,
        "heatmap": build_heatmap(
            windows,
            today,
            view,
            now.hour,
            place=lambda when: to_today_sun(when + HEATMAP_WINDOW_MID, today),
        ),
        "daylight": daylight(today),
    }


def fetch_birds_data(view: str = DEFAULT_VIEW) -> dict:
    """Query InfluxDB and assemble everything the birds page shows."""
    now = datetime.now(TZ_BOSTON)
    today = now.date()
    history_days = ARRIVAL_WINDOW_DAYS + ARRIVAL_ABSENCE_DAYS

    daily = _parse_counts(
        _query_influx(_counts_query(_local_midnight(today - timedelta(days=history_days)), "1d"))
    )
    earliest_rows = _query_influx(EARLIEST_QUERY)
    earliest = (
        datetime.fromtimestamp(_parse_ts(earliest_rows[0]["_time"]), TZ_BOSTON).date()
        if earliest_rows
        else None
    )

    heartbeats = {
        datetime.fromtimestamp(_parse_ts(r["_time"]), TZ_BOSTON).date(): int(r["_value"])
        for r in _query_influx(HEARTBEAT_DAILY_QUERY)
        if r.get("_time") and r.get("_value")
    }
    windows = _parse_counts(
        _query_influx(
            _counts_query(
                _local_midnight(today - timedelta(days=CUMULATIVE_DAYS)), CUMULATIVE_EVERY
            )
        )
    )
    trend = build_cumulative(
        windows,
        today,
        now,
        comparison_days(today, earliest, heartbeats),
        place=lambda when: to_today_sun(when + timedelta(minutes=CUMULATIVE_BIN_MIN / 2), today),
    )
    return {
        **fetch_heatmap(view),
        "site_label": SITE_LABEL,
        "today_label": now.strftime("%a %b %-d"),
        "trend": trend,
        "arrivals": find_arrivals(daily, today, earliest),
        "arrival_absence_days": ARRIVAL_ABSENCE_DAYS,
        "arrival_window_days": ARRIVAL_WINDOW_DAYS,
        "arrival_min_detections": ARRIVAL_MIN_DETECTIONS,
    }


# The cabin relay writes a heartbeat every 5 minutes whenever it can reach InfluxDB,
# so silence means the relay, the Pi, or the cabin Wi-Fi is down, not a quiet night.
HEARTBEAT_QUERY = f"""
from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "relay_heartbeat" and r.site == "{SITE}")
  |> last()
  |> keep(columns: ["_time"])
"""
HEALTHY_WITHIN = timedelta(minutes=15)
LAGGING_WITHIN = timedelta(hours=2)


def relay_health(now: datetime) -> dict | None:
    """Status dot for the cabin relay, or None if InfluxDB itself is unreachable."""
    try:
        rows = _query_influx(HEARTBEAT_QUERY)
    except Exception:
        return None  # don't alarm when koonti is the problem
    if not rows:
        return {"status": "red", "title": "No relay heartbeat in 7 days", "age": None}
    age = now - datetime.fromtimestamp(_parse_ts(rows[0]["_time"]), timezone.utc)
    if age <= HEALTHY_WITHIN:
        status = "green"
    elif age <= LAGGING_WITHIN:
        status = "yellow"
    else:
        status = "red"
    age_label = _fmt_age(age)
    return {
        "status": status,
        "title": f"Cabin relay last checked in {age_label} ago",
        "age": age_label,
    }


def today_summary() -> dict:
    """Species and detection counts so far today (one small query)."""
    today = datetime.now(TZ_BOSTON).date()
    rows = _parse_counts(_query_influx(_counts_query(_local_midnight(today), "1d")))
    counts = Counter()
    for when, name, count in rows:
        if when.date() == today:
            counts[name] += count
    return {"species": len(counts), "detections": sum(counts.values())}


@router.get("/partials/birds", response_class=HTMLResponse)
async def birds_line_partial(request: Request, _user: str = Depends(require_auth)):
    """One line for the dashboard's Wentworth weather: today's counts, or a warning
    when the cabin relay has stopped checking in. Renders nothing if InfluxDB is down."""
    health = await asyncio.to_thread(relay_health, datetime.now(timezone.utc))
    context: dict[str, object] = {"health": health}
    if health is None:
        context["hidden"] = True
    elif health["status"] != "red":
        try:
            context["summary"] = await asyncio.to_thread(today_summary)
        except Exception as e:
            logger.error(f"Failed to fetch bird summary: {e}")
            context["hidden"] = True
    return templates.TemplateResponse(request, "partials/birds_line.html", context)


@router.get("/partials/birds-heatmap", response_class=HTMLResponse)
async def birds_heatmap_partial(
    request: Request,
    view: str = Query(default=DEFAULT_VIEW, pattern="^(today|week|month)$"),
    _user: str = Depends(require_auth),
):
    """Just the heatmap section, so the Today / 7 days toggle redraws only that."""
    try:
        data = await asyncio.to_thread(fetch_heatmap, view)
    except Exception as e:
        logger.error(f"Failed to fetch bird heatmap: {e}")
        data = {"view": view, "views": VIEWS, "heatmap_error": str(e)}
    return templates.TemplateResponse(request, "partials/birds_heatmap.html", data)


@router.get("/birds", response_class=HTMLResponse)
async def birds(
    request: Request,
    view: str = Query(default=DEFAULT_VIEW, pattern="^(today|week|month)$"),
    _user: str = Depends(require_auth),
):
    """Bird detections page."""
    try:
        data = await asyncio.to_thread(fetch_birds_data, view)
    except Exception as e:
        logger.error(f"Failed to fetch bird data: {e}")
        data = {"view": view, "views": VIEWS, "site_label": SITE_LABEL, "error": str(e)}
    return templates.TemplateResponse(request, "birds.html", data)
