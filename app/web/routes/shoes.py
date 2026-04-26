"""Shoe tracking route handlers."""

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.cache import sqlite as cache
from app.fetchers.strava import fetch_athlete_gear
from app.web.auth import require_auth
from app.web.templating import templates

router = APIRouter()


@router.get("/shoes", response_class=HTMLResponse)
async def shoes_page(
    request: Request,
    user_email: str = Depends(require_auth),
    synced: int | None = None,
    msg: str | None = None,
):
    shoes = cache.get_shoes(include_retired=False)
    local_miles = cache.get_shoe_local_miles()
    for shoe in shoes:
        shoe["local_miles"] = local_miles.get(shoe["strava_id"], 0.0)
    shoes.sort(key=lambda s: s["local_miles"], reverse=True)
    pending_count = len(cache.get_unsynced_run_shoes())
    if msg == "strava_error":
        sync_status = "error"
    elif synced is not None:
        sync_status = (
            f"Synced {synced} shoe{'s' if synced != 1 else ''} from Strava."
            if synced
            else "No shoes found on Strava — add some at strava.com/settings/gear."
        )
    else:
        sync_status = None
    return templates.TemplateResponse(
        "shoes.html",
        {
            "request": request,
            "shoes": shoes,
            "pending_count": pending_count,
            "sync_status": sync_status,
            "user_email": user_email,
            "user_name": request.session.get("user_name"),
        },
    )


@router.post("/shoes/sync")
async def sync_shoes(_: str = Depends(require_auth)):
    gear = await asyncio.to_thread(fetch_athlete_gear, False)
    if gear is None:
        return RedirectResponse(url="/shoes?msg=strava_error", status_code=303)
    for shoe in gear:
        cache.upsert_shoe(
            strava_id=shoe["id"],
            name=shoe.get("name", "Unknown Shoe"),
            distance_mi=shoe.get("distance", 0) * 0.000621371,
            retired=shoe.get("retired", False),
        )
    cache.retire_absent_shoes([shoe["id"] for shoe in gear])
    return RedirectResponse(url=f"/shoes?synced={len(gear)}", status_code=303)


@router.post("/shoes/{shoe_id}/style", response_class=HTMLResponse)
async def update_shoe_style(
    shoe_id: str,
    request: Request,
    bg_color: str = Form("#e0e0e0"),
    text_color: str = Form("#333333"),
    use_stripe_1: bool = Form(False),
    stripe_1: str = Form("#000000"),
    use_stripe_2: bool = Form(False),
    stripe_2: str = Form("#000000"),
    use_stripe_3: bool = Form(False),
    stripe_3: str = Form("#000000"),
    _: str = Depends(require_auth),
):
    stripes = []
    if use_stripe_1:
        stripes.append(stripe_1)
    if use_stripe_2:
        stripes.append(stripe_2)
    if use_stripe_3:
        stripes.append(stripe_3)

    cache.update_shoe_style(shoe_id, bg_color, text_color, stripes)
    shoe = cache.get_shoe(shoe_id)
    if shoe:
        shoe["local_miles"] = cache.get_shoe_local_miles().get(shoe_id, 0.0)

    return templates.TemplateResponse(
        "partials/shoe_card.html",
        {"request": request, "shoe": shoe},
    )


@router.post("/partials/strava/run/{activity_id}/shoe/cycle", response_class=HTMLResponse)
async def cycle_run_shoe(
    activity_id: int,
    request: Request,
    distance_mi: str = Form(""),
    pace: str = Form(""),
    _: str = Depends(require_auth),
):
    shoes = cache.get_shoes()  # active only, stable name order for consistent cycling
    current_shoe = cache.get_run_shoe(activity_id)

    current_id = current_shoe["strava_id"] if current_shoe else None
    shoe_ids = [s["strava_id"] for s in shoes]

    next_idx = shoe_ids.index(current_id) + 1 if current_id in shoe_ids else 0

    if next_idx >= len(shoes):
        cache.set_run_shoe(activity_id, None)
        new_shoe = None
    else:
        new_shoe_id = shoe_ids[next_idx]
        cache.set_run_shoe(activity_id, new_shoe_id)
        new_shoe = next(s for s in shoes if s["strava_id"] == new_shoe_id)

    run = {"id": activity_id, "distance_mi": distance_mi, "pace": pace}
    return templates.TemplateResponse(
        "partials/shoe_banner.html",
        {"request": request, "shoe": new_shoe, "run": run},
    )
