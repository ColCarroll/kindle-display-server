"""Portfolio dashboard route."""

import csv
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.web.auth import require_auth

logger = logging.getLogger(__name__)
TZ_ET = ZoneInfo("America/New_York")

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

INFLUX_URL = "http://koonti:8086"
INFLUX_TOKEN = "airq-local-token"
INFLUX_ORG = "home"
PORTFOLIO_API = "http://koonti:8087"

RANGE_OPTIONS = {
    "1m": {"flux": "-30d", "label": "1M"},
    "3m": {"flux": "-90d", "label": "3M"},
    "6m": {"flux": "-180d", "label": "6M"},
    "1y": {"flux": "-365d", "label": "1Y"},
    "3y": {"flux": "-1095d", "label": "3Y"},
    "all": {"flux": "-20y", "label": "All"},
}
DEFAULT_RANGE = "1y"

# SVG chart layout constants
CL, CT = 72, 16
CW, CH = 490, 200
CR, CB = CL + CW, CT + CH
SVG_W = CR + 10
SVG_H = CB + 28


def _query_influx(flux_query: str) -> list[dict]:
    resp = requests.post(
        f"{INFLUX_URL}/api/v2/query",
        params={"org": INFLUX_ORG},
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        data=flux_query.encode(),
        timeout=20,
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


def _parse_ts(time_str: str) -> datetime:
    ts = time_str.replace("Z", "+00:00")
    if "." in ts:
        dot = ts.index(".")
        plus = ts.index("+", dot)
        ts = ts[:dot] + ts[dot:plus][:7] + ts[plus:]
    return datetime.fromisoformat(ts)


def _fmt_dollars(v: float) -> str:
    """Compact format for axis labels."""
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


def _fmt_dollars_full(v: float) -> str:
    return f"${v:,.0f}"


def _fmt_signed(v: float, full: bool = True) -> str:
    fmt = _fmt_dollars_full(abs(v)) if full else _fmt_dollars(abs(v))
    return f"+{fmt}" if v >= 0 else f"-{fmt}"


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(
    request: Request,
    time_range: str = Query(default=DEFAULT_RANGE, alias="range"),
    start: str = Query(default=None),
    end: str = Query(default=None),
    user=Depends(require_auth),  # noqa: B008
):
    today = date.today()
    custom_range = bool(start and end)

    if custom_range:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            start_date = today - timedelta(days=365)
            end_date = today
            custom_range = False
        flux_range = (
            f"start: {start_date.isoformat()}T00:00:00Z, "
            f"stop: {(end_date + timedelta(days=1)).isoformat()}T00:00:00Z"
        )
    else:
        if time_range not in RANGE_OPTIONS:
            time_range = DEFAULT_RANGE
        flux_range = f"start: {RANGE_OPTIONS[time_range]['flux']}"
        start_date = None
        end_date = None

    # --- Fetch portfolio_value "all" daily time series ---
    query = f"""
from(bucket: "portfolio")
  |> range({flux_range})
  |> filter(fn: (r) => r._measurement == "portfolio_value" and r.owner == "all" and r._field == "value")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)
  |> sort(columns: ["_time"])
"""
    try:
        rows = _query_influx(query)
    except Exception as e:
        logger.error("InfluxDB query failed: %s", e)
        rows = []

    # Parse and deduplicate (keep one value per UTC calendar date)
    by_day: dict[date, tuple[datetime, float]] = {}
    for row in rows:
        try:
            t = _parse_ts(row["_time"])
            v = float(row["_value"])
            if v > 0:
                d = t.date()  # UTC calendar date
                by_day[d] = (t, v)
        except (KeyError, ValueError):
            continue

    points: list[tuple[datetime, float]] = [by_day[d] for d in sorted(by_day)]

    # --- Performance stats ---
    perf_pct = perf_abs = current_value = start_value = None
    if points:
        start_value = points[0][1]
        current_value = points[-1][1]
        if start_value > 0:
            perf_abs = current_value - start_value
            perf_pct = perf_abs / start_value * 100

    perf_positive = (perf_pct or 0) >= 0

    # --- SVG chart geometry ---
    polyline = fill_path = ""
    x_markers: list[dict] = []
    y_markers: list[dict] = []
    perf_line_y: float | None = None

    if len(points) >= 2:
        t0 = points[0][0].timestamp()
        t1 = points[-1][0].timestamp()
        t_span = t1 - t0 or 1.0

        vals = [v for _, v in points]
        v_lo = min(vals) * 0.995
        v_hi = max(vals) * 1.005
        v_span = v_hi - v_lo or 1.0

        def xp(t: datetime) -> float:
            return CL + (t.timestamp() - t0) / t_span * CW

        def yp(v: float) -> float:
            return CT + (1.0 - (v - v_lo) / v_span) * CH

        svg_pts = [(xp(t), yp(v)) for t, v in points]
        polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in svg_pts)
        fill_path = (
            f"M {svg_pts[0][0]:.1f},{CB} "
            + " ".join(f"L {x:.1f},{y:.1f}" for x, y in svg_pts)
            + f" L {svg_pts[-1][0]:.1f},{CB} Z"
        )

        # X-axis date labels
        n_days = (points[-1][0] - points[0][0]).days
        step = (
            7
            if n_days <= 35
            else 14
            if n_days <= 100
            else 30
            if n_days <= 200
            else 60
            if n_days <= 400
            else 90
            if n_days <= 800
            else 365
        )
        seen_years: set[int] = set()
        cur = points[0][0].date() + timedelta(days=step)
        last_d = points[-1][0].date()
        while cur <= last_d:
            tm = datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc)
            xf = (tm.timestamp() - t0) / t_span
            if 0.02 <= xf <= 0.97:
                if step >= 365:
                    label = str(cur.year)
                elif step >= 28:
                    label = (
                        cur.strftime("%b '%y") if cur.year not in seen_years else cur.strftime("%b")
                    )
                else:
                    label = cur.strftime("%-m/%-d")
                seen_years.add(cur.year)
                x_markers.append({"x": CL + xf * CW, "label": label})
            cur += timedelta(days=step)

        # Y-axis value labels (4 gridlines)
        for i in range(4):
            frac = i / 3
            y_markers.append(
                {
                    "y": CT + (1.0 - frac) * CH,
                    "label": _fmt_dollars(v_lo + frac * v_span),
                }
            )

        # Reference line at range-start value
        if start_value:
            py = yp(start_value)
            if CT <= py <= CB:
                perf_line_y = py

    # --- Daily change table ---
    sorted_days = sorted(by_day)
    daily_rows = []
    for i in range(1, len(sorted_days)):
        prev_d, curr_d = sorted_days[i - 1], sorted_days[i]
        pv, cv = by_day[prev_d][1], by_day[curr_d][1]
        chg = cv - pv
        pct = chg / pv * 100 if pv else 0
        daily_rows.append(
            {
                "date": curr_d.strftime("%b %-d, %Y"),
                "value": _fmt_dollars_full(cv),
                "chg_abs": _fmt_signed(chg),
                "chg_pct": f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%",
                "positive": chg >= 0,
            }
        )
    daily_rows = list(reversed(daily_rows))[:20]

    # --- Per-account day changes from InfluxDB ---
    acct_day_changes: dict[str, dict] = {}
    try:
        acct_query = """
from(bucket: "portfolio")
  |> range(start: -3d)
  |> filter(fn: (r) => r._measurement == "account_value" and r._field == "value")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)
  |> sort(columns: ["_time"])
"""
        acct_rows = _query_influx(acct_query)
        by_acct_ts: dict[str, list[tuple[date, float]]] = {}
        for row in acct_rows:
            acct_id = row.get("account_id", "")
            if not acct_id:
                continue
            try:
                t = _parse_ts(row["_time"])
                v = float(row["_value"])
                if acct_id not in by_acct_ts:
                    by_acct_ts[acct_id] = []
                by_acct_ts[acct_id].append((t.date(), v))
            except (KeyError, ValueError):
                continue
        for acct_id, vals in by_acct_ts.items():
            vals.sort()
            if len(vals) >= 2:
                prev_v, curr_v = vals[-2][1], vals[-1][1]
                chg = curr_v - prev_v
                pct = chg / prev_v * 100 if prev_v else 0.0
            elif vals:
                chg, pct = 0.0, 0.0
            else:
                continue
            acct_day_changes[acct_id] = {"chg": chg, "pct": pct}
    except Exception as e:
        logger.warning("Could not fetch account day changes: %s", e)

    # Badge colours and abbreviations by account type
    type_badge: dict[str, tuple[str, str]] = {
        "checking": ("CHK", "#4285f4"),
        "hysa": ("HYSA", "#1a73e8"),
        "savings": ("SAV", "#1a73e8"),
        "401k": ("401k", "#34a853"),
        "403b": ("403b", "#2e7d32"),
        "hsa": ("HSA", "#00897b"),
        "taxable": ("BROK", "#f9ab00"),
        "roth_ira": ("ROTH", "#9c27b0"),
        "ira": ("IRA", "#7b1fa2"),
        "other": ("—", "#9e9e9e"),
    }

    # --- Account breakdown ---
    accounts: list[dict] = []
    total_acct_value = 0.0
    try:
        resp = requests.get(f"{PORTFOLIO_API}/portfolio/accounts", timeout=10)
        resp.raise_for_status()
        raw_accounts = resp.json()
        total_acct_value = sum(a.get("value") or 0 for a in raw_accounts)
        for a in raw_accounts:
            v = a.get("value") or 0.0
            acct_id = str(a.get("id", ""))
            badge_label, badge_color = type_badge.get(
                (a.get("type") or "other").lower(), ("—", "#9e9e9e")
            )
            day = acct_day_changes.get(acct_id, {})
            chg = day.get("chg", None)
            pct = day.get("pct", None)
            accounts.append(
                {
                    "name": a.get("name", ""),
                    "type": a.get("type", ""),
                    "owner": a.get("owner", ""),
                    "value": v,
                    "value_fmt": _fmt_dollars_full(v),
                    "badge_label": badge_label,
                    "badge_color": badge_color,
                    "pct_of_total": v / total_acct_value * 100 if total_acct_value else 0,
                    "pct_fmt": f"{v / total_acct_value * 100:.1f}%" if total_acct_value else "—",
                    "day_chg_abs": _fmt_signed(chg) if chg is not None else "—",
                    "day_chg_pct": (f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%")
                    if pct is not None
                    else "—",
                    "day_positive": (chg or 0) >= 0,
                    "has_day_change": chg is not None,
                }
            )
        accounts.sort(key=lambda x: x["value"], reverse=True)
    except Exception as e:
        logger.warning("Could not fetch accounts: %s", e)

    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "range": time_range,
            "custom_range": custom_range,
            "start": start or "",
            "end": end or today.isoformat(),
            "range_options": RANGE_OPTIONS,
            # Stats
            "current_value": _fmt_dollars_full(current_value) if current_value else "—",
            "perf_pct": (f"+{perf_pct:.2f}%" if perf_pct >= 0 else f"{perf_pct:.2f}%")
            if perf_pct is not None
            else None,
            "perf_abs": _fmt_signed(perf_abs) if perf_abs is not None else None,
            "perf_positive": perf_positive,
            # SVG
            "svg_w": SVG_W,
            "svg_h": SVG_H,
            "polyline": polyline,
            "fill_path": fill_path,
            "x_markers": x_markers,
            "y_markers": y_markers,
            "perf_line_y": perf_line_y,
            "CL": CL,
            "CT": CT,
            "CW": CW,
            "CH": CH,
            "CR": CR,
            "CB": CB,
            # Tables
            "daily_rows": daily_rows,
            "accounts": accounts,
            "today": today.isoformat(),
            "has_data": len(points) > 0,
        },
    )
