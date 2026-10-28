"""Fitness route handler: daily Garmin metrics from InfluxDB.

The `garmin` collector on koonti writes one point per day (measurement `daily`,
timestamped at local midnight). Overnight values belong to the morning you woke up.
"""

import asyncio
import logging
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.web.auth import require_auth
from app.web.routes.airq import _parse_ts, _query_influx
from app.web.templating import templates

logger = logging.getLogger(__name__)
TZ_BOSTON = ZoneInfo("America/New_York")

router = APIRouter()

RANGE_OPTIONS = {
    "30d": {"days": 30, "bucket_days": 1, "edge_label": "30d ago"},
    "90d": {"days": 90, "bucket_days": 1, "edge_label": "90d ago"},
    "1y": {"days": 365, "bucket_days": 7, "edge_label": "1y ago"},
    "3y": {"days": 1095, "bucket_days": 7, "edge_label": "3y ago"},
}
DEFAULT_RANGE = "90d"

# Trailing window for the "typical range" band on Health Status charts, like the
# watch's personal baseline. Needs a handful of nights before it means anything.
BASELINE_DAYS = 28
BASELINE_MIN_NIGHTS = 7

MIN_SPAN_FRAC = 0.04


def _b(lo, hi, bg, label, tc):
    """Shorthand for a band definition with background and text colors."""
    return {"lo": lo, "hi": hi, "color": bg, "label": label, "text_color": tc}


GOOD = ("#edf7ed", "#2a7a2a")
OK = ("#f7f5e6", "#7a6a10")
LOW = ("#f7efe6", "#8a4a10")
POOR = ("#f7e9e9", "#8a1a1a")
NORMAL_BAND = {"color": "#eef3f8", "text_color": "#4a6a8a"}

C_MAIN = "#4477aa"
C_ALT = "#aa7733"


def _series(field, display, color=C_MAIN, transform=None):
    return {"field": field, "display": display, "color": color, "transform": transform}


def _m_to_ft(v):
    return v * 3.28084


def _c_delta_to_f(v):
    return v * 9 / 5


# Each section is a list of charts; each chart has one or more series.
# baseline: "garmin_hrv" uses Garmin's own balanced range, "rolling" a trailing mean ± 1 SD.
SECTIONS = [
    {
        "title": "Health Status",
        "note": "Overnight readings; shaded band is your typical range.",
        "charts": [
            {
                "label": "HRV",
                "unit": "ms",
                "decimals": 0,
                "series": [_series("hrv", "Overnight HRV")],
                "baseline": "garmin_hrv",
            },
            {
                "label": "Resting heart rate",
                "unit": "bpm",
                "decimals": 0,
                "series": [_series("rhr", "Resting HR")],
                "baseline": "rolling",
            },
            {
                "label": "Respiration",
                "unit": "brpm",
                "decimals": 1,
                "series": [_series("respiration", "Sleeping respiration")],
                "baseline": "rolling",
            },
            {
                "label": "Skin temp change",
                "unit": "°F",
                "decimals": 1,
                "signed": True,
                "series": [_series("skin_temp_c", "Skin temp", transform=_c_delta_to_f)],
                "baseline": "rolling",
            },
            {
                "label": "Pulse ox",
                "unit": "%",
                "decimals": 0,
                "series": [_series("spo2", "Sleeping SpO₂")],
                "baseline": "rolling",
                "max_cap": 100,
            },
        ],
    },
    {
        "title": "Day",
        "charts": [
            {
                "label": "Steps",
                "unit": "steps",
                "decimals": 0,
                "thousands": True,
                "min_override": 0,
                "series": [_series("steps", "Steps")],
            },
            {
                "label": "Sleep score",
                "unit": "",
                "decimals": 0,
                "series": [_series("sleep_score", "Sleep score")],
                "max_cap": 100,
                "bands": [
                    _b(0, 60, POOR[0], "Poor", POOR[1]),
                    _b(60, 80, LOW[0], "Fair", LOW[1]),
                    _b(80, 90, OK[0], "Good", OK[1]),
                    _b(90, None, GOOD[0], "Excellent", GOOD[1]),
                ],
            },
            {
                "label": "Training readiness",
                "unit": "morning",
                "decimals": 0,
                "series": [_series("readiness", "Readiness")],
                "min_override": 0,
                "max_cap": 100,
                "bands": [
                    _b(0, 25, POOR[0], "Poor", POOR[1]),
                    _b(25, 50, LOW[0], "Low", LOW[1]),
                    _b(50, 75, OK[0], "Moderate", OK[1]),
                    _b(75, None, GOOD[0], "High", GOOD[1]),
                ],
            },
            {
                "label": "Heart rate",
                "unit": "bpm",
                "decimals": 0,
                "series": [
                    _series("max_hr", "Max", color=C_ALT),
                    _series("min_hr", "Min"),
                ],
            },
            {
                "label": "Altitude",
                "unit": "ft · daily avg",
                "decimals": 0,
                "min_override": 0,
                "series": [_series("altitude_m", "Altitude", transform=_m_to_ft)],
            },
        ],
    },
    {
        "title": "Running",
        "charts": [
            {
                "label": "VO₂ max",
                "unit": "ml/kg/min",
                "decimals": 1,
                "series": [_series("vo2max", "VO₂ max")],
            },
            {
                "label": "Hill score",
                "unit": "",
                "decimals": 0,
                "series": [
                    _series("hill_score", "Overall"),
                    _series("hill_strength", "Strength", color=C_ALT),
                    _series("hill_endurance", "Endurance", color="#228833"),
                ],
            },
            {
                "label": "Endurance score",
                "unit": "",
                "decimals": 0,
                "thousands": True,
                "series": [_series("endurance_score", "Endurance score")],
            },
            {
                "label": "5K prediction",
                "unit": "",
                "duration": True,
                "series": [_series("race_5k_s", "5K")],
            },
            {
                "label": "Half marathon prediction",
                "unit": "",
                "duration": True,
                "series": [_series("race_half_s", "Half")],
            },
            {
                "label": "Marathon prediction",
                "unit": "",
                "duration": True,
                "series": [_series("race_marathon_s", "Marathon")],
            },
        ],
    },
]

HRV_BAND_FIELDS = ("hrv_balanced_low", "hrv_balanced_high")


def _all_fields() -> list[str]:
    fields = [s["field"] for sec in SECTIONS for c in sec["charts"] for s in c["series"]]
    return fields + list(HRV_BAND_FIELDS)


def _fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_value(value: float, chart: dict) -> str:
    if chart.get("duration"):
        return _fmt_duration(value)
    decimals = chart.get("decimals", 0)
    if chart.get("thousands"):
        return f"{value:,.{decimals}f}"
    if chart.get("signed"):
        return f"{value:+.{decimals}f}"
    return f"{value:.{decimals}f}"


def _day(time_str: str) -> date:
    """Calendar day (Boston) of an InfluxDB timestamp written at local midnight."""
    return datetime.fromtimestamp(_parse_ts(time_str), TZ_BOSTON).date()


def _fetch_daily(days: int) -> dict[str, dict[date, float]]:
    """{field: {day: value}} for the last `days` days plus baseline lead-in."""
    field_filter = " or ".join(f'r._field == "{f}"' for f in _all_fields())
    query = f"""
from(bucket: "garmin")
  |> range(start: -{days + BASELINE_DAYS + 1}d)
  |> filter(fn: (r) => r._measurement == "daily")
  |> filter(fn: (r) => {field_filter})
"""
    out: dict[str, dict[date, float]] = {}
    for row in _query_influx(query):
        try:
            value = float(row["_value"])
            day = _day(row["_time"])
        except (KeyError, ValueError, IndexError):
            continue
        out.setdefault(row.get("_field", ""), {})[day] = value
    return out


def _rolling_band(values: dict[date, float]) -> dict[date, tuple[float, float]]:
    """Trailing BASELINE_DAYS mean ± 1 SD for each day, excluding that day itself."""
    band = {}
    days = sorted(values)
    for i, d in enumerate(days):
        window = [values[p] for p in days[:i] if (d - p).days <= BASELINE_DAYS]
        if len(window) < BASELINE_MIN_NIGHTS:
            continue
        mu, sd = statistics.fmean(window), statistics.stdev(window)
        band[d] = (mu - sd, mu + sd)
    return band


def _bucket(points: dict[date, float], start: date, bucket_days: int) -> list[tuple[date, float]]:
    """Mean per bucket_days-wide bucket (keyed by bucket start); daily passes through."""
    if bucket_days == 1:
        return sorted(points.items())
    groups: dict[date, list[float]] = {}
    for d, v in points.items():
        key = start + timedelta(days=(d - start).days // bucket_days * bucket_days)
        groups.setdefault(key, []).append(v)
    return [(k, statistics.fmean(vs)) for k, vs in sorted(groups.items())]


def _to_segments(
    pts: list[tuple[date, float]], x_of, max_gap_days: int
) -> list[list[tuple[float, float]]]:
    """Split points into continuous runs so missing days show as gaps, not bridges."""
    segments: list[list[tuple[float, float]]] = []
    prev: date | None = None
    for d, v in pts:
        if prev is None or (d - prev).days > max_gap_days:
            segments.append([])
        segments[-1].append((x_of(d), v))
        prev = d
    return segments


def _x_markers(start: date, end: date, range_key: str, x_of) -> list[dict]:
    """Tick marks: weekly for 30d, monthly for 90d/1y, yearly for 3y."""
    markers = []
    if range_key == "30d":
        d = start + timedelta(days=(7 - start.weekday()) % 7 or 7)  # Mondays
        while d < end:
            markers.append({"x_frac": x_of(d), "label": f"{d.strftime('%b')} {d.day}"})
            d += timedelta(days=7)
    elif range_key in ("90d", "1y"):
        step = 1 if range_key == "90d" else 2
        d = date(start.year, start.month, 1)
        while d < end:
            if d > start:
                markers.append({"x_frac": x_of(d), "label": d.strftime("%b")})
            month = d.month - 1 + step
            d = date(d.year + month // 12, month % 12 + 1, 1)
    else:
        for year in range(start.year + 1, end.year + 1):
            markers.append({"x_frac": x_of(date(year, 1, 1)), "label": str(year)})
    return markers


def _latest_status(chart: dict, latest: tuple[date, float] | None, band) -> dict:
    """Color the headline number: band color for banded charts, in/out of typical range
    for Health Status (the watch's green check vs. amber flag)."""
    if latest is None:
        return {"bg": None, "tc": None, "flag": None}
    day, value = latest
    if chart.get("bands"):
        for b in chart["bands"]:
            if b["lo"] <= value and (b["hi"] is None or value < b["hi"]):
                return {"bg": b["color"], "tc": b["text_color"], "flag": b["label"]}
    if band and day in band:
        lo, hi = band[day]
        if value < lo:
            return {"bg": LOW[0], "tc": LOW[1], "flag": "Below typical"}
        if value > hi:
            return {"bg": LOW[0], "tc": LOW[1], "flag": "Above typical"}
        return {"bg": GOOD[0], "tc": GOOD[1], "flag": "Typical"}
    return {"bg": None, "tc": None, "flag": None}


def _build_chart(chart: dict, raw: dict, start: date, end: date, opt: dict, x_of) -> dict | None:
    bucket_days = opt["bucket_days"]
    max_gap = 2 if bucket_days == 1 else bucket_days * 2

    series_out, all_values = [], []
    headline_band = None
    band_pts: list[tuple[date, float, float]] = []

    for s in chart["series"]:
        transform = s["transform"] or (lambda v: v)
        values = {d: transform(v) for d, v in raw.get(s["field"], {}).items()}

        # Baseline band (only ever on single-series Health Status charts)
        band: dict[date, tuple[float, float]] = {}
        if chart.get("baseline") == "garmin_hrv":
            lows, highs = raw.get(HRV_BAND_FIELDS[0], {}), raw.get(HRV_BAND_FIELDS[1], {})
            band = {d: (lows[d], highs[d]) for d in lows if d in highs}
        elif chart.get("baseline") == "rolling":
            band = _rolling_band(values)
        if band:
            headline_band = band
            lo_pts = _bucket(
                {d: lo for d, (lo, _) in band.items() if d >= start}, start, bucket_days
            )
            hi_pts = dict(
                _bucket({d: hi for d, (_, hi) in band.items() if d >= start}, start, bucket_days)
            )
            band_pts = [(d, lo, hi_pts[d]) for d, lo in lo_pts if d in hi_pts]

        in_range = {d: v for d, v in values.items() if d >= start}
        pts = _bucket(in_range, start, bucket_days)
        latest = max(in_range.items()) if in_range else None
        all_values.extend(v for _, v in pts)
        status = _latest_status(chart, latest, band)
        series_out.append(
            {
                "display": s["display"],
                "color": s["color"],
                "latest_val": _fmt_value(latest[1], chart) if latest else None,
                "latest_day": latest[0] if latest else None,
                "latest_bg": status["bg"],
                "latest_tc": status["tc"],
                "flag": status["flag"],
                "segments": _to_segments(pts, x_of, max_gap),
            }
        )

    if not all_values:
        return None

    # Y range covers the data and the band, with a little padding like the air quality charts
    span_vals = all_values + [v for _, lo, hi in band_pts for v in (lo, hi)]
    lo, hi = min(span_vals), max(span_vals)
    # Near-flat series (VO2 max, race predictions) would otherwise get a hairline axis
    # with repeated labels; give every chart at least MIN_SPAN_FRAC of its magnitude.
    min_span = max(abs(lo), abs(hi)) * MIN_SPAN_FRAC
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    pad = (hi - lo) * 0.08 if hi != lo else 1.0
    lo = chart["min_override"] if chart.get("min_override") is not None else lo - pad
    hi = min(hi + pad, chart["max_cap"]) if chart.get("max_cap") is not None else hi + pad
    val_range = hi - lo or 1.0

    def y_frac(v: float) -> float:
        return 1 - (min(max(v, lo), hi) - lo) / val_range

    bands = []
    for b in chart.get("bands", []):
        b_lo, b_hi = max(b["lo"], lo), min(hi if b["hi"] is None else b["hi"], hi)
        if b_hi > b_lo:
            bands.append(
                {
                    "y_top_frac": y_frac(b_hi),
                    "y_bot_frac": y_frac(b_lo),
                    "color": b["color"],
                    "label": b["label"],
                    "text_color": b["text_color"],
                }
            )

    # Baseline as a closed polygon: upper edge left→right, lower edge right→left
    band_polys = []
    for seg in _to_segments([(d, (lo_, hi_)) for d, lo_, hi_ in band_pts], x_of, max_gap):
        if len(seg) < 2:
            continue
        upper = [(x, y_frac(h)) for x, (_, h) in seg]
        lower = [(x, y_frac(lo_)) for x, (lo_, _) in reversed(seg)]
        band_polys.append(upper + lower)

    fmt = {**chart, "decimals": chart.get("decimals", 0)}
    return {
        "label": chart["label"],
        "unit": chart["unit"],
        "series": series_out,
        "min_val": lo,
        "val_range": val_range,
        "y_labels": [_fmt_value(v, fmt) for v in (hi, (hi + lo) / 2, lo)],
        "bands": bands,
        "band_polys": band_polys,
        "band_style": NORMAL_BAND,
        "multi": len(series_out) > 1,
        "has_baseline": headline_band is not None,
    }


def fetch_fitness_data(range_key: str = DEFAULT_RANGE) -> dict:
    """Build every chart for the given range."""
    opt = RANGE_OPTIONS[range_key]
    end = datetime.now(TZ_BOSTON).date() + timedelta(days=1)
    start = end - timedelta(days=opt["days"])
    span = (end - start).days

    def x_of(d: date) -> float:
        return (d - start).days / span

    raw = _fetch_daily(opt["days"])
    sections = []
    for sec in SECTIONS:
        charts = [
            c for c in (_build_chart(ch, raw, start, end, opt, x_of) for ch in sec["charts"]) if c
        ]
        if charts:
            sections.append({"title": sec["title"], "note": sec.get("note"), "charts": charts})

    return {
        "sections": sections,
        "x_markers": _x_markers(start, end, range_key, x_of),
        "edge_label": opt["edge_label"],
        "current_range": range_key,
        "ranges": list(RANGE_OPTIONS),
    }


@router.get("/fitness", response_class=HTMLResponse)
async def fitness(
    request: Request,
    range: str = Query(default=DEFAULT_RANGE, pattern="^(30d|90d|1y|3y)$"),
    _user: str = Depends(require_auth),
):
    """Garmin health and training page."""
    try:
        data = await asyncio.to_thread(fetch_fitness_data, range)
    except Exception as e:
        logger.error(f"Failed to fetch fitness data: {e}")
        data = {
            "sections": [],
            "x_markers": [],
            "edge_label": "",
            "current_range": range,
            "ranges": list(RANGE_OPTIONS),
            "error": str(e),
        }
    return templates.TemplateResponse("fitness.html", {"request": request, **data})
