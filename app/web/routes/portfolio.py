"""Portfolio dashboard route."""

import csv
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

from app.web.auth import require_auth
from app.web.templating import templates

logger = logging.getLogger(__name__)
TZ_ET = ZoneInfo("America/New_York")

router = APIRouter()

INFLUX_URL = "http://koonti:8086"
INFLUX_TOKEN = "airq-local-token"
INFLUX_ORG = "home"
PORTFOLIO_API = "http://koonti:8087"

CATEGORY_ORDER = ["brokerage", "retirement", "cash", "529"]
CATEGORY_META: dict[str, dict] = {
    "brokerage": {"label": "Brokerage"},
    "retirement": {"label": "Retirement"},
    "529": {"label": "529"},
    "cash": {"label": "Cash"},
}


def _categorize(acct: dict) -> str:
    t = acct.get("type", "")
    name = acct.get("name", "").lower()
    if t == "taxable":
        return "brokerage"
    if t in ("401k", "roth_ira") or "403b" in name:
        return "retirement"
    if t == "hsa":
        return "retirement"
    if "529" in name:
        return "529"
    if t in ("checking", "hysa", "savings"):
        return "cash"
    return "retirement"


RANGE_OPTIONS = {
    "1d": {"label": "1D", "agg": "10m"},  # flux range overridden to midnight UTC in route
    "1w": {"flux": "-7d", "label": "1W", "agg": "1h"},
    "1m": {"flux": "-30d", "label": "1M", "agg": "1d"},
    "1y": {"flux": "-365d", "label": "1Y", "agg": "1d"},
}
DEFAULT_RANGE = "1m"

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


def _fmt_dollars(v: float, span: float = 0) -> str:
    """Compact format for axis labels. Pass span to auto-select precision."""
    if abs(v) >= 1_000_000:
        decimals = 3 if span < 100_000 else 2
        return f"${v / 1_000_000:.{decimals}f}M"
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
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    account: str | None = Query(default=None),
    user=Depends(require_auth),  # noqa: B008
):
    # Validate account param — must be a positive integer string
    if account and not account.isdigit():
        account = None
    today = date.today()
    custom_range = bool(start and end)

    if custom_range:
        assert start and end  # guaranteed by custom_range = bool(start and end)
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
        n_days = (end_date - start_date).days if custom_range else 365
        agg_window = "10m" if n_days <= 1 else "1h" if n_days <= 7 else "1d"
    else:
        if time_range not in RANGE_OPTIONS:
            time_range = DEFAULT_RANGE
        opt = RANGE_OPTIONS[time_range]
        agg_window = opt["agg"]
        start_date = None
        end_date = None
        if time_range == "1d":
            now_et = datetime.now(TZ_ET)
            # Fetch 2 days back so we have both today and yesterday for the ghost line
            two_days_ago = (now_et - timedelta(days=2)).replace(
                hour=9, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            flux_range = f"start: {two_days_ago}"
        else:
            flux_range = f"start: {opt['flux']}"

    # --- Fetch time series (per-account or total) ---
    if account:
        query = f"""
from(bucket: "portfolio")
  |> range({flux_range})
  |> filter(fn: (r) => r._measurement == "account_value" and r._field == "value" and r.account_id == "{account}")
  |> aggregateWindow(every: {agg_window}, fn: last, createEmpty: false, timeSrc: "_start")
  |> sort(columns: ["_time"])
"""
    else:
        query = f"""
from(bucket: "portfolio")
  |> range({flux_range})
  |> filter(fn: (r) => r._measurement == "portfolio_value" and r.owner == "all" and r._field == "value")
  |> aggregateWindow(every: {agg_window}, fn: last, createEmpty: false, timeSrc: "_start")
  |> sort(columns: ["_time"])
"""
    try:
        rows = _query_influx(query)
    except Exception as e:
        logger.error("InfluxDB query failed: %s", e)
        rows = []

    # Parse rows; for daily resolution deduplicate by UTC date, otherwise keep all
    by_day: dict[date, tuple[datetime, float]] = {}
    points: list[tuple[datetime, float]] = []
    if agg_window == "1d":
        for row in rows:
            try:
                t = _parse_ts(row["_time"])
                v = float(row["_value"])
                if v > 0:
                    by_day[t.date()] = (t, v)
            except (KeyError, ValueError):
                continue
        points = [by_day[d] for d in sorted(by_day)]
    else:
        seen: set[datetime] = set()
        for row in rows:
            try:
                t = _parse_ts(row["_time"])
                v = float(row["_value"])
                if v > 0 and t not in seen:
                    seen.add(t)
                    points.append((t, v))
            except (KeyError, ValueError):
                continue
        points.sort()

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
    is_intraday = time_range == "1d" and not custom_range
    polyline = fill_path = yesterday_polyline = ""
    x_markers: list[dict] = []
    y_markers: list[dict] = []
    perf_line_y: float | None = None

    if len(points) >= 2:
        now_et = datetime.now(TZ_ET)
        day_et = now_et

        yest_points: list[tuple[datetime, float]] = []
        today_points: list[tuple[datetime, float]] = []
        if is_intraday:
            market_open_ts = now_et.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
            has_market_data = any(t.timestamp() >= market_open_ts for t, _ in points)
            # Whichever day is active gets the 9am–7pm window
            day_et = now_et if has_market_data else now_et - timedelta(days=1)
            t0 = day_et.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
            t1 = day_et.replace(hour=19, minute=0, second=0, microsecond=0).timestamp()
            t_span = t1 - t0

            # Split points into today vs yesterday for ghost line
            today_date = day_et.date()
            today_points = [(t, v) for t, v in points if t.astimezone(TZ_ET).date() == today_date]
            yest_date = today_date - timedelta(days=1)
            yest_points = [(t, v) for t, v in points if t.astimezone(TZ_ET).date() == yest_date]

            # Recompute perf stats using today's data only
            if today_points:
                start_value = today_points[0][1]
                current_value = today_points[-1][1]
                if start_value and start_value > 0:
                    perf_abs = current_value - start_value
                    perf_pct = perf_abs / start_value * 100
                perf_positive = (perf_pct or 0) >= 0
        else:
            has_market_data = False
            t0 = points[0][0].timestamp()
            t1 = points[-1][0].timestamp()
            t_span = t1 - t0 or 1.0
            today_points = points

        vals = [v for _, v in (today_points if is_intraday else points)]
        all_vals = vals + [v for _, v in yest_points]
        if is_intraday:
            mid = (max(all_vals) + min(all_vals)) / 2
            half = (max(all_vals) - min(all_vals)) / 2 + 500
            v_lo = mid - half
            v_hi = mid + half
        else:
            v_lo = min(all_vals) * 0.995
            v_hi = max(all_vals) * 1.005
        v_span = v_hi - v_lo or 1.0

        def xp_ts(ts: float) -> float:
            return CL + (ts - t0) / t_span * CW

        def xp(t: datetime) -> float:
            return xp_ts(t.timestamp())

        def yp(v: float) -> float:
            return CT + (1.0 - (v - v_lo) / v_span) * CH

        plot_pts = today_points if is_intraday else points
        svg_pts = [(xp(t), yp(v)) for t, v in plot_pts]
        polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in svg_pts)
        fill_path = (
            f"M {svg_pts[0][0]:.1f},{CB} "
            + " ".join(f"L {x:.1f},{y:.1f}" for x, y in svg_pts)
            + f" L {svg_pts[-1][0]:.1f},{CB} Z"
        )

        if is_intraday:
            for hour, label in [(9, "9am"), (12, "12pm"), (15, "3pm"), (18, "6pm")]:
                marker_et = day_et.replace(hour=hour, minute=0, second=0, microsecond=0)
                xf = (marker_et.timestamp() - t0) / t_span
                if 0.01 <= xf <= 0.99:
                    x_markers.append({"x": CL + xf * CW, "label": label})
            if yest_points:
                yest_svg_pts = [(xp_ts(t.timestamp() + 86400), yp(v)) for t, v in yest_points]
                yesterday_polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in yest_svg_pts)
        else:
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
                            cur.strftime("%b '%y")
                            if cur.year not in seen_years
                            else cur.strftime("%b")
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
                    "label": _fmt_dollars(v_lo + frac * v_span, span=v_span),
                }
            )

        # Reference line at range-start value
        if start_value:
            py = yp(start_value)
            if CT <= py <= CB:
                perf_line_y = py

    # --- Daily change table — always show trailing 30 days ---
    daily_rows = []
    # Use by_day if already at daily resolution, otherwise fetch a dedicated 30d series
    if agg_window == "1d" and by_day:
        daily_src = by_day
    else:
        daily_src = {}
        try:
            acct_or_total = (
                f'r._measurement == "account_value" and r._field == "value" and r.account_id == "{account}"'
                if account
                else 'r._measurement == "portfolio_value" and r.owner == "all" and r._field == "value"'
            )
            daily_q = f"""
from(bucket: "portfolio")
  |> range(start: -30d)
  |> filter(fn: (r) => {acct_or_total})
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false, timeSrc: "_start")
  |> sort(columns: ["_time"])
"""
            for row in _query_influx(daily_q):
                try:
                    t = _parse_ts(row["_time"])
                    v = float(row["_value"])
                    if v > 0:
                        daily_src[t.date()] = (t, v)
                except (KeyError, ValueError):
                    continue
        except Exception as e:
            logger.warning("Could not fetch daily change data: %s", e)

    if daily_src:
        sorted_days = sorted(daily_src)
        for i in range(1, len(sorted_days)):
            prev_d, curr_d = sorted_days[i - 1], sorted_days[i]
            pv, cv = daily_src[prev_d][1], daily_src[curr_d][1]
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

    # --- Per-account changes over the selected range from InfluxDB ---
    acct_day_changes: dict[str, dict] = {}
    try:
        acct_query = f"""
from(bucket: "portfolio")
  |> range({flux_range})
  |> filter(fn: (r) => r._measurement == "account_value" and r._field == "value")
  |> aggregateWindow(every: {agg_window}, fn: last, createEmpty: false, timeSrc: "_start")
  |> sort(columns: ["_time"])
"""
        acct_rows = _query_influx(acct_query)
        by_acct_ts: dict[str, list[tuple[datetime, float]]] = {}
        for row in acct_rows:
            acct_id = row.get("account_id", "")
            if not acct_id:
                continue
            try:
                t = _parse_ts(row["_time"])
                v = float(row["_value"])
                if acct_id not in by_acct_ts:
                    by_acct_ts[acct_id] = []
                by_acct_ts[acct_id].append((t, v))
            except (KeyError, ValueError):
                continue
        for acct_id, vals in by_acct_ts.items():
            vals.sort()
            if len(vals) >= 2:
                start_v, end_v = vals[0][1], vals[-1][1]
                chg = end_v - start_v
                pct = chg / start_v * 100 if start_v else 0.0
            elif vals:
                chg, pct = 0.0, 0.0
            else:
                continue
            acct_day_changes[acct_id] = {"chg": chg, "pct": pct}
    except Exception as e:
        logger.warning("Could not fetch account changes: %s", e)

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
            rng = acct_day_changes.get(acct_id, {})
            chg = rng.get("chg", None)
            pct = rng.get("pct", None)
            accounts.append(
                {
                    "id": acct_id,
                    "name": a.get("name", ""),
                    "type": a.get("type", ""),
                    "value": v,
                    "value_fmt": _fmt_dollars_full(v),
                    "pct_fmt": f"{v / total_acct_value * 100:.1f}%" if total_acct_value else "—",
                    "chg_raw": chg,
                    "pct_raw": pct,
                    "chg_abs": _fmt_dollars_full(abs(chg)) if chg is not None else "—",
                    "chg_pct": f"{abs(pct):.2f}%" if pct is not None else "—",
                    "chg_positive": (chg or 0) >= 0,
                    "has_change": chg is not None,
                    "selected": acct_id == account,
                }
            )
        accounts.sort(key=lambda x: x["value"], reverse=True)

        # Build grouped view
        by_cat: dict[str, list[dict]] = {k: [] for k in CATEGORY_ORDER}
        for a in accounts:
            by_cat[_categorize(a)].append(a)

        account_groups: list[dict] = []
        for cat in CATEGORY_ORDER:
            cat_accts = by_cat[cat]
            if not cat_accts:
                continue
            meta = CATEGORY_META[cat]
            total_val = sum(a["value"] for a in cat_accts)
            chg_accts = [a for a in cat_accts if a["has_change"]]
            total_chg: float | None = sum(a["chg_raw"] for a in chg_accts) if chg_accts else None
            start_val = (total_val - total_chg) if total_chg is not None else None
            total_pct: float | None = (
                total_chg / start_val * 100 if (start_val is not None and start_val != 0) else None
            )
            account_groups.append(
                {
                    "key": cat,
                    "label": meta["label"],
                    "value": total_val,
                    "value_fmt": _fmt_dollars_full(total_val),
                    "pct_fmt": f"{total_val / total_acct_value * 100:.1f}%"
                    if total_acct_value
                    else "—",
                    "chg_abs": _fmt_dollars_full(abs(total_chg)) if total_chg is not None else "—",
                    "chg_pct": f"{abs(total_pct):.2f}%" if total_pct is not None else "—",
                    "chg_positive": (total_chg or 0) >= 0,
                    "has_change": total_chg is not None,
                    "accounts": cat_accts,
                }
            )
    except Exception as e:
        logger.warning("Could not fetch accounts: %s", e)
        account_groups = []

    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "range": time_range,
            "custom_range": custom_range,
            "start": start or "",
            "end": end or today.isoformat(),
            "range_options": RANGE_OPTIONS,
            "change_label": "Change"
            if custom_range
            else RANGE_OPTIONS.get(time_range, {}).get("label", "") + " change",
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
            "yesterday_polyline": yesterday_polyline,
            "is_intraday": is_intraday,
            "x_markers": x_markers,
            "y_markers": y_markers,
            "perf_line_y": perf_line_y,
            "CL": CL,
            "CT": CT,
            "CW": CW,
            "CH": CH,
            "CR": CR,
            "CB": CB,
            # Filter state
            "account": account or "",
            "account_name": next(
                (a["name"] for g in account_groups for a in g["accounts"] if a["id"] == account),
                "",
            )
            if account
            else "",
            # Tables
            "daily_rows": daily_rows,
            "account_groups": account_groups,
            "today": today.isoformat(),
            "has_data": len(points) > 0,
        },
    )


# SVG layout for mini sparkline — 900-wide viewBox, constrained to 680px by CSS.
# Font sizes chosen so they render ~13px at 680px display width (scaled from 900).
_MCL, _MCT = 12, 16
_MCW, _MCH = 876, 200
_MCR, _MCB = _MCL + _MCW, _MCT + _MCH
_MSVG_W, _MSVG_H = _MCR + 12, _MCB + 30


@router.get("/partials/portfolio", response_class=HTMLResponse)
async def portfolio_mini(request: Request):
    """Censored portfolio sparkline for the main dashboard. No dollar values."""
    query_1m = """
from(bucket: "portfolio")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "portfolio_value" and r.owner == "all" and r._field == "value")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false, timeSrc: "_start")
  |> sort(columns: ["_time"])
"""
    now_et = datetime.now(TZ_ET)
    today_midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    today_midnight_str = today_midnight_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query_today = f"""
from(bucket: "portfolio")
  |> range(start: {today_midnight_str})
  |> filter(fn: (r) => r._measurement == "portfolio_value" and r.owner == "all" and r._field == "value")
  |> sort(columns: ["_time"])
"""
    ctx: dict = {"has_data": False}
    try:
        rows_1m = _query_influx(query_1m)
        rows_today = _query_influx(query_today)
    except Exception as e:
        logger.error("Portfolio mini query failed: %s", e)
        return templates.TemplateResponse(request, "partials/portfolio_mini.html", ctx)

    # Parse 1-month daily points
    by_day_1m: dict[date, tuple[datetime, float]] = {}
    for row in rows_1m:
        try:
            t = _parse_ts(row["_time"])
            v = float(row["_value"])
            if v > 0:
                by_day_1m[t.date()] = (t, v)
        except (KeyError, ValueError):
            continue
    points_1m = [by_day_1m[d] for d in sorted(by_day_1m)]

    if len(points_1m) < 2:
        return templates.TemplateResponse(request, "partials/portfolio_mini.html", ctx)

    # Monthly % change
    month_chg = (points_1m[-1][1] - points_1m[0][1]) / points_1m[0][1] * 100

    # Daily % change: today's last vs today's midnight backfill point (matches 1D chart baseline)
    today_points: list[tuple[datetime, float]] = []
    for row in rows_today:
        try:
            t = _parse_ts(row["_time"])
            v = float(row["_value"])
            if v > 0:
                today_points.append((t, v))
        except (KeyError, ValueError):
            continue
    today_points.sort()
    day_chg: float | None = None
    if len(today_points) >= 2:
        p, c = today_points[0][1], today_points[-1][1]
        day_chg = (c - p) / p * 100 if p else None

    # SVG geometry
    t0 = points_1m[0][0].timestamp()
    t1 = points_1m[-1][0].timestamp()
    t_span = t1 - t0 or 1.0
    vals = [v for _, v in points_1m]
    v_lo = min(vals) * 0.995
    v_hi = max(vals) * 1.005
    v_span = v_hi - v_lo or 1.0

    def mxp(t: datetime) -> float:
        return _MCL + (t.timestamp() - t0) / t_span * _MCW

    def myp(v: float) -> float:
        return _MCT + (1.0 - (v - v_lo) / v_span) * _MCH

    svg_pts = [(mxp(t), myp(v)) for t, v in points_1m]
    mini_polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in svg_pts)
    mini_fill = (
        f"M {svg_pts[0][0]:.1f},{_MCB} "
        + " ".join(f"L {x:.1f},{y:.1f}" for x, y in svg_pts)
        + f" L {svg_pts[-1][0]:.1f},{_MCB} Z"
    )

    # Y-axis gridlines (no labels — values are censored)
    y_gridlines = [_MCT + (1.0 - i / 3) * _MCH for i in range(4)]

    # X-axis date markers every 7 days
    x_markers: list[dict] = []
    cur_d = points_1m[0][0].date() + timedelta(days=7)
    last_d = points_1m[-1][0].date()
    while cur_d <= last_d:
        tm = datetime(cur_d.year, cur_d.month, cur_d.day, tzinfo=timezone.utc)
        xf = (tm.timestamp() - t0) / t_span
        if 0.02 <= xf <= 0.97:
            x_markers.append({"x": _MCL + xf * _MCW, "label": cur_d.strftime("%-m/%-d")})
        cur_d += timedelta(days=7)

    perf_positive = month_chg >= 0

    def _fmt_pct(v: float) -> str:
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    return templates.TemplateResponse(
        request,
        "partials/portfolio_mini.html",
        {
            "has_data": True,
            "svg_w": _MSVG_W,
            "svg_h": _MSVG_H,
            "MCL": _MCL,
            "MCT": _MCT,
            "MCR": _MCR,
            "MCB": _MCB,
            "polyline": mini_polyline,
            "fill_path": mini_fill,
            "y_gridlines": y_gridlines,
            "x_markers": x_markers,
            "perf_positive": perf_positive,
            "month_chg_pct": _fmt_pct(month_chg),
            "month_positive": month_chg >= 0,
            "day_chg_pct": _fmt_pct(day_chg) if day_chg is not None else None,
            "day_positive": (day_chg or 0) >= 0,
        },
    )
