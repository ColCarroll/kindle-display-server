"""Air quality route handler."""

import asyncio
import csv
import logging
import statistics
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.web.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

INFLUX_URL = "http://koonti:8086"
INFLUX_TOKEN = "airq-local-token"
INFLUX_ORG = "home"

RANGE_OPTIONS = {
    "1h":  {"delta": timedelta(hours=1),  "agg": "2m",  "gap_s": 300,   "edge_label": "1h ago"},
    "24h": {"delta": timedelta(hours=24), "agg": "10m", "gap_s": 1800,  "edge_label": "24h ago"},
    "7d":  {"delta": timedelta(days=7),   "agg": "1h",  "gap_s": 7200,  "edge_label": "7d ago"},
    "30d": {"delta": timedelta(days=30),  "agg": "4h",  "gap_s": 28800, "edge_label": "30d ago"},
}
DEFAULT_RANGE = "24h"


def _b(lo, hi, bg, label, tc):
    """Shorthand for a band definition with background and text colors."""
    return {"lo": lo, "hi": hi, "color": bg, "label": label, "text_color": tc}


# Single-field metrics
METRICS_CONFIG = [
    {
        "field": "co2", "label": "CO₂", "unit": "ppm", "decimals": 0,
        "min_override": 400, "max_cap": 2750,
        "bands": [
            _b(0,    800,  "#edf7ed", "Good",      "#2a7a2a"),
            _b(800,  1500, "#f7f5e6", "Moderate",  "#7a6a10"),
            _b(1500, 2500, "#f7efe6", "High",      "#8a4a10"),
            _b(2500, None, "#f7e9e9", "Very high", "#8a1a1a"),
        ],
    },
    {
        "field": "temperature", "label": "Temperature", "unit": "°F", "decimals": 1,
        "transform": lambda v: v * 9 / 5 + 32,
    },
    {"field": "humidity", "label": "Humidity", "unit": "%", "decimals": 1},
    {
        "field": "voc", "label": "VOC", "unit": "index", "decimals": 0,
        "min_override": 0, "max_floor": 1100,
        "bands": [
            _b(0,    300,  "#edf7ed", "Great",      "#2a7a2a"),
            _b(300,  500,  "#f7f5e6", "Acceptable", "#7a6a10"),
            _b(500,  1000, "#f7efe6", "High",       "#8a4a10"),
            _b(1000, None, "#f7e9e9", "Very high",  "#8a1a1a"),
        ],
    },
    {
        "field": "nox", "label": "NOx", "unit": "index", "decimals": 0,
        "min_override": 0,
        "bands": [
            _b(0,   20,  "#edf7ed", "Good",      "#2a7a2a"),
            _b(20,  50,  "#f7f5e6", "Moderate",  "#7a6a10"),
            _b(50,  150, "#f7efe6", "High",      "#8a4a10"),
            _b(150, None, "#f7e9e9", "Very high", "#8a1a1a"),
        ],
    },
]

# EPA AQI PM2.5 breakpoints used as reference bands on the combined PM chart
PM_BANDS = [
    _b(0,    12,   "#edf7ed", "Good",      "#2a7a2a"),
    _b(12,   35.4, "#f7f5e6", "Moderate",  "#7a6a10"),
    _b(35.4, 55.4, "#f7efe6", "Sensitive", "#8a4a10"),
    _b(55.4, None, "#f7e9e9", "Unhealthy", "#8a1a1a"),
]

# PM sub-fields share a single combined chart
PM_FIELDS = [
    {"field": "pm1_0",  "sublabel": "PM1",   "dasharray": ""},
    {"field": "pm2_5",  "sublabel": "PM2.5", "dasharray": "7,4"},
    {"field": "pm10_0", "sublabel": "PM10",  "dasharray": "2,5"},
]

SENSORS = [
    {"name": "AirQ α", "display": "Office",  "color": "#4477aa"},
    {"name": "AirQ β", "display": "Bedroom", "color": "#aa7733"},
]

ALL_FIELDS = [m["field"] for m in METRICS_CONFIG] + [p["field"] for p in PM_FIELDS]


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
    t_start: datetime, t_end: datetime, t_start_s: float, t_span: float, range_key: str,
) -> list[dict]:
    """Generate x-axis tick marks appropriate for the selected time range."""
    markers = []

    if range_key == "1h":
        interval = timedelta(minutes=15)
        # Align to 15-min boundary
        m = (t_start.minute // 15 + 1) * 15
        current = t_start.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=m)
        def label(dt: datetime) -> str:
            return dt.strftime("%H:%M")
    elif range_key == "24h":
        interval = timedelta(hours=6)
        # Align to 6h boundary
        h = (t_start.hour // 6 + 1) * 6
        current = t_start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=h)
        def label(dt: datetime) -> str:
            return dt.strftime("%H:%M")
    elif range_key == "7d":
        interval = timedelta(days=1)
        d = t_start.date() + timedelta(days=1)
        current = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        def label(dt: datetime) -> str:
            return dt.strftime("%a")
    else:  # 30d
        interval = timedelta(days=5)
        d = t_start.date() + timedelta(days=1)
        current = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        def label(dt: datetime) -> str:
            return f"{dt.strftime('%b')} {dt.day}"

    while current < t_end:
        x_frac = (current.timestamp() - t_start_s) / t_span
        markers.append({"x_frac": x_frac, "label": label(current)})
        current += interval

    return markers


def fetch_airq_data(range_key: str = DEFAULT_RANGE) -> dict:
    """Fetch air quality data from InfluxDB for the given time range."""
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
    query = f"""
from(bucket: "airq")
  |> range(start: {flux_range})
  |> filter(fn: (r) => r._measurement == "airq")
  |> filter(fn: (r) => {field_filter})
  |> filter(fn: (r) => r._value > 0)
  |> group(columns: ["_measurement", "_field", "location"])
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
        field: str, sensor: dict, sublabel: str, dasharray: str, decimals: int,
        transform=None,
    ) -> tuple[dict, list[float]]:
        raw_pts = sorted(raw.get(field, {}).get(sensor["name"], []), key=lambda tv: tv[0])
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
        max_floor: float | None = None,
        max_cap: float | None = None,
        bands: list | None = None,
    ) -> dict | None:
        if not all_values:
            return None

        # Ensure the top open-ended band is always visible (25% of the last band step)
        if bands:
            sorted_bands = sorted(bands, key=lambda b: b["lo"])
            if sorted_bands[-1]["hi"] is None and len(sorted_bands) >= 2:
                step = sorted_bands[-1]["lo"] - sorted_bands[-2]["lo"]
                auto_floor = sorted_bands[-1]["lo"] + step * 0.25
                max_floor = max(max_floor, auto_floor) if max_floor is not None else auto_floor

        # Use 2nd/98th percentile for axis bounds so outliers don't blow the scale.
        # Fall back to min/max for small datasets where percentiles aren't meaningful.
        sv = sorted(all_values)
        n = len(sv)
        if n >= 20:
            data_lo = sv[int(n * 0.02)]
            data_hi = sv[int(n * 0.98)]
        else:
            data_lo, data_hi = sv[0], sv[-1]

        lo = min_override if min_override is not None else data_lo
        hi = max(data_hi, max_floor) if max_floor is not None else data_hi
        pad = (hi - lo) * 0.08 if hi != lo else 1.0
        if min_override is None:
            lo -= pad
        if max_cap is not None:
            # Hard ceiling: no upward padding beyond the cap
            hi = min(hi + pad, max_cap)
        elif max_floor is None or data_hi > max_floor:
            hi += pad
        val_range = hi - lo or 1.0

        processed_bands = []
        for band in (bands or []):
            b_lo = band["lo"]
            b_hi = hi if band["hi"] is None else band["hi"]
            b_lo_c = max(b_lo, lo)
            b_hi_c = min(b_hi, hi)
            if b_hi_c <= b_lo_c:
                continue
            processed_bands.append({
                "y_top_frac": 1 - (b_hi_c - lo) / val_range,
                "y_bot_frac": 1 - (b_lo_c - lo) / val_range,
                "color": band["color"],
                "label": band["label"],
                "text_color": band.get("text_color", "#aaa"),
            })

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
            s["clipped_fracs"] = [
                xf for seg in s.get("segments", []) for xf, v in seg if v > hi
            ]

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
        for sensor in SENSORS:
            s, vals = _build_series(
                cfg["field"], sensor, "", "", cfg["decimals"],
                transform=cfg.get("transform"),
            )
            series.append(s)
            all_values.extend(vals)
        m = _build_metric(
            cfg["label"], cfg["unit"], cfg["decimals"], series, all_values,
            min_override=cfg.get("min_override"),
            max_floor=cfg.get("max_floor"),
            max_cap=cfg.get("max_cap"),
            bands=cfg.get("bands"),
        )
        if m:
            metrics.append(m)

    # Combined PM chart
    pm_series, pm_all_values = [], []
    for sensor in SENSORS:
        for pm in PM_FIELDS:
            s, vals = _build_series(pm["field"], sensor, pm["sublabel"], pm["dasharray"], 1)
            pm_series.append(s)
            pm_all_values.extend(vals)
    m = _build_metric("Particulate Matter", "μg/m³", 1, pm_series, pm_all_values,
                      min_override=0, bands=PM_BANDS)
    if m:
        metrics.append(m)

    x_markers = _compute_x_markers(t_start, t_end, t_start_s, t_span, range_key)

    return {
        "metrics": metrics,
        "x_markers": x_markers,
        "edge_label": opt["edge_label"],
        "current_range": range_key,
    }


@router.get("/air-quality", response_class=HTMLResponse)
async def air_quality(
    request: Request,
    range: str = Query(default=DEFAULT_RANGE, pattern="^(1h|24h|7d|30d)$"),
    _user: str = Depends(require_auth),
):
    """Air quality dashboard page."""
    try:
        data = await asyncio.to_thread(fetch_airq_data, range)
    except Exception as e:
        logger.error(f"Failed to fetch air quality data: {e}")
        data = {"metrics": [], "x_markers": [], "edge_label": "", "current_range": range, "error": str(e)}
    return templates.TemplateResponse(
        "airq.html",
        {"request": request, **data},
    )
