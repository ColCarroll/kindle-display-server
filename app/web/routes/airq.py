"""Air quality route handler."""

import asyncio
import csv
import logging
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.web.auth import require_auth
from app.web.templating import templates

logger = logging.getLogger(__name__)
TZ_BOSTON = ZoneInfo("America/New_York")

router = APIRouter()

INFLUX_URL = "http://koonti:8086"
INFLUX_TOKEN = "airq-local-token"
INFLUX_ORG = "home"

RANGE_OPTIONS = {
    "1h": {"delta": timedelta(hours=1), "agg": "2m", "gap_s": 300, "edge_label": "1h ago"},
    "24h": {"delta": timedelta(hours=24), "agg": "10m", "gap_s": 1800, "edge_label": "24h ago"},
    "7d": {"delta": timedelta(days=7), "agg": "1h", "gap_s": 7200, "edge_label": "7d ago"},
    "30d": {"delta": timedelta(days=30), "agg": "4h", "gap_s": 28800, "edge_label": "30d ago"},
}
DEFAULT_RANGE = "7d"


def _b(lo, hi, bg, label, tc):
    """Shorthand for a band definition with background and text colors."""
    return {"lo": lo, "hi": hi, "color": bg, "label": label, "text_color": tc}


# Single-field metrics
METRICS_CONFIG = [
    {
        "field": "co2",
        "label": "CO₂",
        "unit": "ppm",
        "decimals": 0,
        "min_override": 400,
        "max_cap": 2750,
        "bands": [
            _b(0, 800, "#edf7ed", "Good", "#2a7a2a"),
            _b(800, 1500, "#f7f5e6", "Moderate", "#7a6a10"),
            _b(1500, 2500, "#f7efe6", "High", "#8a4a10"),
            _b(2500, None, "#f7e9e9", "Very high", "#8a1a1a"),
        ],
    },
    {
        "field": "temperature",
        "label": "Temperature",
        "unit": "°F",
        "decimals": 1,
        "transform": lambda v: v * 9 / 5 + 32,
    },
    {"field": "humidity", "label": "Humidity", "unit": "%", "decimals": 1},
    {
        "field": "voc",
        "label": "VOC",
        "unit": "index",
        "decimals": 0,
        "min_override": 0,
        "bands": [
            _b(0, 300, "#edf7ed", "Great", "#2a7a2a"),
            _b(300, 500, "#f7f5e6", "Acceptable", "#7a6a10"),
            _b(500, 1000, "#f7efe6", "High", "#8a4a10"),
            _b(1000, None, "#f7e9e9", "Very high", "#8a1a1a"),
        ],
    },
    {
        "field": "nox",
        "label": "NOx",
        "unit": "index",
        "decimals": 0,
        "min_override": 0,
        "bands": [
            _b(0, 150, "#edf7ed", "Good", "#2a7a2a"),
            _b(150, 250, "#f7f5e6", "Moderate", "#7a6a10"),
            _b(250, 400, "#f7efe6", "High", "#8a4a10"),
            _b(400, None, "#f7e9e9", "Very high", "#8a1a1a"),
        ],
    },
]

# EPA AQI PM2.5 breakpoints used as reference bands on the combined PM chart
PM_BANDS = [
    _b(0, 12, "#edf7ed", "Good", "#2a7a2a"),
    _b(12, 35.4, "#f7f5e6", "Moderate", "#7a6a10"),
    _b(35.4, 55.4, "#f7efe6", "Sensitive", "#8a4a10"),
    _b(55.4, None, "#f7e9e9", "Unhealthy", "#8a1a1a"),
]

# PM sub-fields share a single combined chart
PM_FIELDS = [
    {"field": "pm1_0", "sublabel": "PM1", "dasharray": ""},
    {"field": "pm2_5", "sublabel": "PM2.5", "dasharray": "7,4"},
    {"field": "pm10_0", "sublabel": "PM10", "dasharray": "2,5"},
]

# Sensor sets per site. Home sensors are untagged in InfluxDB; cabin sensors carry
# site=cabin. Each set is queried and rendered independently so the locations stay separate.
# Per-sensor temp_offsets (°C) are calibrated against a reference thermometer and applied
# to the raw Celsius reading before the C→F transform.
SENSOR_SETS: dict[str, dict] = {
    "home": {
        "label": "Home",
        "sensors": [
            {"name": "AirQ α", "display": "Office", "color": "#4477aa"},
            {"name": "AirQ β", "display": "Bedroom", "color": "#aa7733"},
        ],
        "temp_offsets": {
            "AirQ α": -0.89,  # Office:  21.45°C raw → 20.56°C → 69°F  (2026-04-27)
            "AirQ β": -3.45,  # Bedroom: 24.01°C raw → 20.56°C → 69°F  (2026-04-27)
        },
    },
    "cabin": {
        "label": "Wentworth",
        "sensors": [
            {"name": "AirQ γ", "display": "Main Room", "color": "#4477aa"},
            {"name": "AirQ δ", "display": "Green Room", "color": "#228833"},
            {"name": "AirQ ε", "display": "Basement", "color": "#aa7733"},
            {"name": "AirQ ζ", "display": "Water Room", "color": "#66ccee"},
        ],
        # Cabin temps are already self-heating corrected in ESPHome; no extra app-side offset.
        "temp_offsets": {},
    },
}
DEFAULT_SITE = "home"

ALL_FIELDS = [m["field"] for m in METRICS_CONFIG] + [p["field"] for p in PM_FIELDS]


def _legend_sensors(site: str) -> list[dict]:
    """Sensor display names + colors for the chart legend."""
    return [{"display": s["display"], "color": s["color"]} for s in SENSOR_SETS[site]["sensors"]]


def _query_influx(flux_query: str) -> list[dict]:
    """Run a Flux query and return parsed rows."""
    resp = requests.post(
        f"{INFLUX_URL}/api/v2/query",
        params={"org": INFLUX_ORG},
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        data=flux_query.encode(),
        timeout=15,
    )
    resp.raise_for_status()

    seen_header = False
    clean_lines = []
    for line in resp.text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line.startswith(",_result,") and not line.startswith(",result,"):
            if not seen_header:
                seen_header = True
                clean_lines.append(line)
        else:
            clean_lines.append(line)

    if not clean_lines:
        return []

    return list(csv.DictReader(clean_lines))


def _parse_ts(time_str: str) -> float:
    # InfluxDB returns nanosecond precision; Python only handles up to microseconds
    ts = time_str.replace("Z", "+00:00")
    if "." in ts:
        dot = ts.index(".")
        plus = ts.index("+", dot)
        ts = ts[:dot] + ts[dot:plus][:7] + ts[plus:]
    return datetime.fromisoformat(ts).timestamp()


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _remove_spikes(pts: list[tuple[str, float]], n_sigma: float = 4.0) -> list[tuple[str, float]]:
    """Drop points that deviate more than n_sigma from the mean of their two neighbors."""
    if len(pts) < 3:
        return pts
    vals = [v for _, v in pts]
    diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
    try:
        scale = statistics.stdev(diffs) if len(diffs) > 1 else diffs[0]
    except statistics.StatisticsError:
        return pts
    if scale == 0:
        return pts
    threshold = n_sigma * scale
    result = [pts[0]]
    for i in range(1, len(pts) - 1):
        neighbor_mean = (vals[i - 1] + vals[i + 1]) / 2
        if abs(vals[i] - neighbor_mean) <= threshold:
            result.append(pts[i])
    result.append(pts[-1])
    return result


def _to_segments(
    pts: list[tuple[str, float]],
    t_start_s: float,
    t_span: float,
    gap_s: float,
) -> list[list[tuple[float, float]]]:
    """Split (time_str, value) pairs into continuous segments, returning (x_frac, value)."""
    if not pts:
        return []
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    prev_t: float | None = None
    for time_str, value in pts:
        t = _parse_ts(time_str)
        if prev_t is not None and (t - prev_t) > gap_s:
            if current:
                segments.append(current)
            current = []
        current.append(((t - t_start_s) / t_span, value))
        prev_t = t
    if current:
        segments.append(current)
    return segments


def _compute_x_markers(
    t_start: datetime,
    t_end: datetime,
    t_start_s: float,
    t_span: float,
    range_key: str,
) -> list[dict]:
    """Generate x-axis tick marks in Boston local time."""
    markers = []
    # Work in local time for alignment and labeling
    t_start_local = t_start.astimezone(TZ_BOSTON)

    if range_key == "1h":
        interval = timedelta(minutes=15)
        m = (t_start_local.minute // 15 + 1) * 15
        current = t_start_local.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=m)

        def label(dt: datetime) -> str:
            return dt.astimezone(TZ_BOSTON).strftime("%H:%M")
    elif range_key == "24h":
        interval = timedelta(hours=6)
        h = (t_start_local.hour // 6 + 1) * 6
        current = t_start_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            hours=h
        )

        def label(dt: datetime) -> str:
            return dt.astimezone(TZ_BOSTON).strftime("%H:%M")
    elif range_key == "7d":
        interval = timedelta(days=1)
        d = t_start_local.date() + timedelta(days=1)
        current = datetime(d.year, d.month, d.day, tzinfo=TZ_BOSTON)

        def label(dt: datetime) -> str:
            return dt.astimezone(TZ_BOSTON).strftime("%a")
    else:  # 30d
        interval = timedelta(days=5)
        d = t_start_local.date() + timedelta(days=1)
        current = datetime(d.year, d.month, d.day, tzinfo=TZ_BOSTON)

        def label(dt: datetime) -> str:
            return f"{dt.astimezone(TZ_BOSTON).strftime('%b')} {dt.astimezone(TZ_BOSTON).day}"

    while current < t_end:
        x_frac = (current.timestamp() - t_start_s) / t_span
        markers.append({"x_frac": x_frac, "label": label(current)})
        current += interval

    return markers


def fetch_airq_data(range_key: str = DEFAULT_RANGE, site: str = DEFAULT_SITE) -> dict:
    """Fetch air quality data from InfluxDB for the given time range and site (home/cabin)."""
    sensor_set = SENSOR_SETS[site]
    sensors = sensor_set["sensors"]
    temp_offsets = sensor_set["temp_offsets"]
    opt = RANGE_OPTIONS[range_key]
    t_end = datetime.now(timezone.utc)
    t_start = t_end - opt["delta"]
    t_start_s = t_start.timestamp()
    t_span = t_end.timestamp() - t_start_s
    gap_s = opt["gap_s"]

    # Build Flux duration string from the timedelta
    total_minutes = int(opt["delta"].total_seconds() / 60)
    flux_range = f"-{total_minutes}m"

    field_filter = " or ".join(f'r._field == "{f}"' for f in ALL_FIELDS)
    location_filter = " or ".join(f'r.location == "{s["name"]}"' for s in sensors)
    query = f"""
from(bucket: "airq")
  |> range(start: {flux_range})
  |> filter(fn: (r) => r._measurement == "airq" and r.source == "esphome")
  |> filter(fn: (r) => {location_filter})
  |> filter(fn: (r) => {field_filter})
  |> filter(fn: (r) => r._value > 0)
  |> aggregateWindow(every: {opt["agg"]}, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
"""
    rows = _query_influx(query)

    # Organize: {field: {location: [(time_str, value)]}}
    raw: dict[str, dict[str, list]] = {}
    for row in rows:
        field = row.get("_field", "")
        location = row.get("location", "")
        val_str = row.get("_value", "")
        time_str = row.get("_time", "")
        if not val_str:
            continue
        try:
            value = float(val_str)
        except ValueError:
            continue
        raw.setdefault(field, {}).setdefault(location, []).append((time_str, value))

    def _build_series(
        field: str,
        sensor: dict,
        sublabel: str,
        dasharray: str,
        decimals: int,
        transform=None,
        offset_c: float = 0.0,
    ) -> tuple[dict, list[float]]:
        raw_pts = sorted(raw.get(field, {}).get(sensor["name"], []), key=lambda tv: tv[0])
        if offset_c:
            raw_pts = [(t, v + offset_c) for t, v in raw_pts]
        pts = [(t, transform(v)) for t, v in raw_pts] if transform else raw_pts
        pts = _remove_spikes(pts)
        vals = [v for _, v in pts]
        series = {
            "sensor": sensor["display"],
            "color": sensor["color"],
            "dasharray": dasharray,
            "sublabel": sublabel,
            "latest_val": _fmt(pts[-1][1], decimals) if pts else None,
            "latest_raw": pts[-1][1] if pts else None,
            "segments": _to_segments(pts, t_start_s, t_span, gap_s),
        }
        return series, vals

    def _build_metric(
        label: str,
        unit: str,
        decimals: int,
        series: list,
        all_values: list[float],
        *,
        min_override: float | None = None,
        max_cap: float | None = None,
        bands: list | None = None,
    ) -> dict | None:
        if not all_values:
            return None

        sv = sorted(all_values)
        data_lo, data_hi = sv[0], sv[-1]

        lo = min_override if min_override is not None else data_lo
        hi = data_hi
        pad = (hi - lo) * 0.08 if hi != lo else 1.0
        if min_override is None:
            lo -= pad
        if max_cap is not None:
            hi = min(hi + pad, max_cap)
        else:
            hi += pad
        val_range = hi - lo or 1.0

        processed_bands = []
        for band in bands or []:
            b_lo = band["lo"]
            b_hi = hi if band["hi"] is None else band["hi"]
            b_lo_c = max(b_lo, lo)
            b_hi_c = min(b_hi, hi)
            if b_hi_c <= b_lo_c:
                continue
            processed_bands.append(
                {
                    "y_top_frac": 1 - (b_hi_c - lo) / val_range,
                    "y_bot_frac": 1 - (b_lo_c - lo) / val_range,
                    "color": band["color"],
                    "label": band["label"],
                    "text_color": band.get("text_color", "#aaa"),
                }
            )

        # Annotate each series with the band its latest reading falls in
        for s in series:
            s["latest_bg"] = None
            s["latest_tc"] = None
            raw = s.get("latest_raw")
            if raw is not None and bands:
                for band in bands:
                    if band["lo"] <= raw and (band["hi"] is None or raw < band["hi"]):
                        s["latest_bg"] = band["color"]
                        s["latest_tc"] = band.get("text_color", "#aaa")
                        break

            # Find x-fractions of points that exceed the axis ceiling (clipped by SVG)
            s["clipped_fracs"] = [xf for seg in s.get("segments", []) for xf, v in seg if v > hi]

        return {
            "label": label,
            "unit": unit,
            "series": series,
            "min_val": lo,
            "val_range": val_range,
            "y_labels": [_fmt(hi, decimals), _fmt((hi + lo) / 2, decimals), _fmt(lo, decimals)],
            "bands": processed_bands,
        }

    metrics = []

    for cfg in METRICS_CONFIG:
        series, all_values = [], []
        for sensor in sensors:
            s, vals = _build_series(
                cfg["field"],
                sensor,
                "",
                "",
                cfg["decimals"],
                transform=cfg.get("transform"),
                offset_c=temp_offsets.get(sensor["name"], 0.0)
                if cfg["field"] == "temperature"
                else 0.0,
            )
            series.append(s)
            all_values.extend(vals)
        m = _build_metric(
            cfg["label"],
            cfg["unit"],
            cfg["decimals"],
            series,
            all_values,
            min_override=cfg.get("min_override"),
            max_cap=cfg.get("max_cap"),
            bands=cfg.get("bands"),
        )
        if m:
            metrics.append(m)

    # Combined PM chart
    pm_series, pm_all_values = [], []
    for sensor in sensors:
        for pm in PM_FIELDS:
            s, vals = _build_series(pm["field"], sensor, pm["sublabel"], pm["dasharray"], 1)
            pm_series.append(s)
            pm_all_values.extend(vals)
    m = _build_metric(
        "Particulate Matter", "μg/m³", 1, pm_series, pm_all_values, min_override=0, bands=PM_BANDS
    )
    if m:
        metrics.append(m)

    x_markers = _compute_x_markers(t_start, t_end, t_start_s, t_span, range_key)

    return {
        "metrics": metrics,
        "x_markers": x_markers,
        "edge_label": opt["edge_label"],
        "current_range": range_key,
        "site": site,
        "site_label": sensor_set["label"],
        "legend_sensors": _legend_sensors(site),
    }


@router.get("/air-quality", response_class=HTMLResponse)
async def air_quality(
    request: Request,
    range: str = Query(default=DEFAULT_RANGE, pattern="^(1h|24h|7d|30d)$"),
    site: str = Query(default=DEFAULT_SITE, pattern="^(home|cabin)$"),
    _user: str = Depends(require_auth),
):
    """Air quality dashboard page."""
    try:
        data = await asyncio.to_thread(fetch_airq_data, range, site)
    except Exception as e:
        logger.error(f"Failed to fetch air quality data: {e}")
        data = {
            "metrics": [],
            "x_markers": [],
            "edge_label": "",
            "current_range": range,
            "site": site,
            "site_label": SENSOR_SETS[site]["label"],
            "legend_sensors": _legend_sensors(site),
            "error": str(e),
        }
    return templates.TemplateResponse(
        "airq.html",
        {"request": request, **data},
    )


# Thresholds for the status indicator (worst value across both sensors wins)
_STATUS_THRESHOLDS = {
    "co2": {"yellow": 800, "red": 1500},
    "voc": {"yellow": 300, "red": 300},  # any non-Good = red
    "nox": {"yellow": 150, "red": 150},
    "pm2_5": {"yellow": 12.0, "red": 12.0},
}


def _airq_status(site: str = DEFAULT_SITE) -> str:
    """Return 'green', 'yellow', or 'red' based on most recent sensor readings for a site."""
    location_filter = " or ".join(
        f'r.location == "{s["name"]}"' for s in SENSOR_SETS[site]["sensors"]
    )
    query = f"""
from(bucket: "airq")
  |> range(start: -30m)
  |> filter(fn: (r) => r.source == "esphome")
  |> filter(fn: (r) => {location_filter})
  |> filter(fn: (r) => r._field == "co2" or r._field == "voc" or r._field == "nox" or r._field == "pm2_5")
  |> last()
"""
    try:
        rows = _query_influx(query)
    except Exception:
        return "green"  # don't alarm if InfluxDB is unreachable

    # Collect worst value per field across sensors
    latest: dict[str, float] = {}
    for row in rows:
        field = row.get("_field", "")
        try:
            val = float(row["_value"])
        except (KeyError, ValueError):
            continue
        if field not in latest or val > latest[field]:
            latest[field] = val

    if not latest:
        return "green"

    status = "green"
    for field, thresholds in _STATUS_THRESHOLDS.items():
        val = latest.get(field)
        if val is None:
            continue
        if val >= thresholds["red"]:
            return "red"
        if val >= thresholds["yellow"] and status == "green":
            status = "yellow"

    return status


@router.get("/partials/airq-status", response_class=HTMLResponse)
async def airq_status_partial(
    request: Request,
    site: str = Query(default=DEFAULT_SITE, pattern="^(home|cabin)$"),
    _user: str = Depends(require_auth),
):
    status = await asyncio.to_thread(_airq_status, site)
    return templates.TemplateResponse(
        request, "partials/airq_status.html", {"status": status, "site": site}
    )


# A healthy sensor writes every 60s, so an hour of silence means it is unplugged,
# crashed, or its whole site lost power — all worth surfacing on the front page.
STALE_AFTER = timedelta(hours=1)
# How far back to look for each sensor's last reading. Sensors quieter than this
# are reported without an age rather than with a misleadingly precise one.
STALE_LOOKBACK = timedelta(days=7)


def _all_sensors() -> list[dict]:
    """Every configured sensor across all sites, annotated with its site."""
    return [
        {**sensor, "site": site, "site_label": cfg["label"]}
        for site, cfg in SENSOR_SETS.items()
        for sensor in cfg["sensors"]
    ]


def _fmt_age(delta: timedelta) -> str:
    """Coarse human duration: '45m', '3h', '3h 20m', '2d', '2d 5h'."""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs}h" if hrs else f"{days}d"


def _stale_sensors() -> list[dict]:
    """Sensors with no reading in STALE_AFTER, across every site.

    Deliberately queried with no field filter, so a write of *any* field counts as
    alive: this is a device-liveness check, not a per-field one. Returns [] when
    InfluxDB is unreachable, matching _airq_status — a dashboard-side outage
    should not be reported to the user as a sensor outage.
    """
    sensors = _all_sensors()
    location_filter = " or ".join(f'r.location == "{s["name"]}"' for s in sensors)
    lookback_m = int(STALE_LOOKBACK.total_seconds() / 60)
    query = f"""
from(bucket: "airq")
  |> range(start: -{lookback_m}m)
  |> filter(fn: (r) => r._measurement == "airq" and r.source == "esphome")
  |> filter(fn: (r) => {location_filter})
  |> group(columns: ["location"])
  |> last()
  |> keep(columns: ["location", "_time"])
"""
    try:
        rows = _query_influx(query)
    except Exception as e:
        logger.error(f"Failed to check sensor freshness: {e}")
        return []

    last_seen: dict[str, float] = {}
    for row in rows:
        location = row.get("location", "")
        time_str = row.get("_time", "")
        if not location or not time_str:
            continue
        try:
            ts = _parse_ts(time_str)
        except (ValueError, IndexError):
            continue
        last_seen[location] = max(ts, last_seen.get(location, 0.0))

    now = datetime.now(timezone.utc).timestamp()
    stale = []
    for sensor in sensors:
        ts = last_seen.get(sensor["name"])
        age = None
        if ts is not None:
            age = timedelta(seconds=now - ts)
            if age < STALE_AFTER:
                continue
        stale.append(
            {
                "display": sensor["display"],
                "site": sensor["site"],
                "site_label": sensor["site_label"],
                "age": _fmt_age(age) if age is not None else None,
            }
        )
    return stale


def _group_stale(stale: list[dict]) -> list[dict]:
    """Group stale sensors by site so one power cut reads as one line, not four."""
    groups: dict[str, dict] = {}
    for sensor in stale:
        group = groups.setdefault(
            sensor["site"],
            {"site": sensor["site"], "site_label": sensor["site_label"], "sensors": []},
        )
        group["sensors"].append(sensor)
    return list(groups.values())


@router.get("/partials/airq-alert", response_class=HTMLResponse)
async def airq_alert_partial(request: Request, _user: str = Depends(require_auth)):
    """Front-page warning for sensors that have stopped reporting."""
    stale = await asyncio.to_thread(_stale_sensors)
    return templates.TemplateResponse(
        request,
        "partials/airq_alert.html",
        {"groups": _group_stale(stale), "count": len(stale)},
    )
