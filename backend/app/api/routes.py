"""Saved routes, alert subscriptions and optional magic-link email login."""

from __future__ import annotations

import hashlib
import secrets
from datetime import time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notify import send_email
from app.api.deps import current_user, rate_limit
from app.config import get_settings
from app.db.models.users import PushSubscription, SavedRoute, User
from app.db.session import get_db, session_scope
from app.ingestion.geocoding import GeoMatch
from app.rag.tools.route_check import check_route, corridor, route_line
from app.timeutil import now_ist, utcnow

router = APIRouter(prefix="/api", tags=["routes"])

Channel = Literal["push", "email", "inapp"]
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class PlaceIn(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    lat: float = Field(ge=28.0, le=29.2)
    lon: float = Field(ge=76.5, le=77.8)


class RouteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    origin: PlaceIn
    destination: PlaceIn
    depart_time: time | None = None
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    channels: list[Channel] = Field(default_factory=lambda: list[Channel](["inapp"]))
    digest_time: time | None = None

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days_of_week must be 0 (Mon) .. 6 (Sun)")
        return sorted(set(v))


def _gm(p: PlaceIn) -> GeoMatch:
    return GeoMatch(
        mention=p.label,
        name=p.label,
        type="area",
        lat=p.lat,
        lon=p.lon,
        confidence=1,
        method="user",
        radius_m=500,
    )


def _route_out(r: SavedRoute) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "origin": {"label": r.origin_label, "lat": r.origin_lat, "lon": r.origin_lon},
        "destination": {"label": r.dest_label, "lat": r.dest_lat, "lon": r.dest_lon},
        "depart_time": r.depart_time.isoformat(timespec="minutes") if r.depart_time else None,
        "days_of_week": r.days_of_week,
        "channels": r.channels,
        "digest_time": r.digest_time.isoformat(timespec="minutes") if r.digest_time else None,
        "active": r.active,
    }


@router.get("/routes")
async def list_routes(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(SavedRoute).where(SavedRoute.user_id == user.id).order_by(SavedRoute.id)
            )
        )
        .scalars()
        .all()
    )
    return [_route_out(r) for r in rows]


@router.post("/routes", dependencies=[Depends(rate_limit)])
async def create_route(
    body: RouteIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    count = (
        await db.execute(
            text("SELECT count(*) FROM saved_routes WHERE user_id = :u"), {"u": user.id}
        )
    ).scalar_one()
    if count >= 10:
        raise HTTPException(400, "maximum 10 saved routes")
    if "email" in body.channels and not user.email_verified:
        raise HTTPException(400, "verify your email first (POST /api/auth/magic-link)")
    line, _ = await route_line(_gm(body.origin), _gm(body.destination))
    route = SavedRoute(
        user_id=user.id,
        name=body.name,
        origin_label=body.origin.label,
        origin_lat=body.origin.lat,
        origin_lon=body.origin.lon,
        dest_label=body.destination.label,
        dest_lat=body.destination.lat,
        dest_lon=body.destination.lon,
        corridor=f"SRID=4326;{corridor(line, get_settings().alert_corridor_meters).wkt}",
        depart_time=body.depart_time,
        days_of_week=body.days_of_week,
        channels=list(body.channels),
        digest_time=body.digest_time,
        active=True,
    )
    db.add(route)
    await db.flush()
    return _route_out(route)


@router.delete("/routes/{route_id}")
async def delete_route(
    route_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    route = await db.get(SavedRoute, route_id)
    if route is None or route.user_id != user.id:
        raise HTTPException(404, "route not found")
    await db.delete(route)
    return {"deleted": True}


@router.get("/routes/{route_id}/status")
async def route_status(route_id: int, user: User = Depends(current_user)) -> dict[str, Any]:
    async with session_scope() as s:
        route = await s.get(SavedRoute, route_id)
        if route is None or route.user_id != user.id:
            raise HTTPException(404, "route not found")
        o = GeoMatch(
            route.origin_label,
            route.origin_label,
            "area",
            route.origin_lat,
            route.origin_lon,
            1,
            "user",
            500,
        )
        d = GeoMatch(
            route.dest_label,
            route.dest_label,
            "area",
            route.dest_lat,
            route.dest_lon,
            1,
            "user",
            500,
        )
        now = now_ist()
        return await check_route(s, o, d, now, now)


@router.get("/alerts")
async def my_alerts(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text(
                    """SELECT a.id, a.kind, a.channel, a.sent_at, a.payload, r.name AS route
           FROM alerts_sent a JOIN saved_routes r ON r.id = a.route_id
           WHERE r.user_id = :u AND a.channel = 'inapp' ORDER BY a.sent_at DESC LIMIT 50"""
                ),
                {"u": user.id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


class PushIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]


@router.get("/push/vapid-public-key")
async def vapid_key() -> dict[str, Any]:
    s = get_settings()
    return {"enabled": s.push_enabled, "key": s.vapid_public_key or None}


@router.post("/push/subscribe")
async def push_subscribe(
    body: PushIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    if not get_settings().push_enabled:
        raise HTTPException(503, "web push is not configured on this server")
    if "p256dh" not in body.keys or "auth" not in body.keys:
        raise HTTPException(400, "invalid subscription")
    existing = (
        await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    ).scalar_one_or_none()
    if existing:
        existing.user_id, existing.p256dh, existing.auth = (
            user.id,
            body.keys["p256dh"],
            body.keys["auth"],
        )
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.keys["p256dh"],
                auth=body.keys["auth"],
            )
        )
    return {"subscribed": True}


class MagicLinkIn(BaseModel):
    email: str = Field(max_length=320, pattern=EMAIL_RE)


@router.post("/auth/magic-link", dependencies=[Depends(rate_limit)])
async def magic_link(
    body: MagicLinkIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.email_enabled:
        raise HTTPException(503, "email is not configured on this server")
    token = secrets.token_urlsafe(32)
    user.login_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.login_token_expires = utcnow() + timedelta(minutes=20)
    pending_email = str(body.email).lower()
    link = f"{settings.frontend_url}/routes?verify={token}&email={pending_email}"
    await send_email(
        pending_email,
        "Verify your email for Delhi Traffic Copilot",
        f"Click to verify (valid 20 minutes): {link}",
    )
    return {"sent": True}


class VerifyIn(BaseModel):
    token: str = Field(max_length=200)
    email: str = Field(max_length=320, pattern=EMAIL_RE)


@router.post("/auth/verify")
async def verify_email(
    body: VerifyIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    if (
        not user.login_token_hash
        or not secrets.compare_digest(user.login_token_hash, digest)
        or not user.login_token_expires
        or user.login_token_expires < utcnow()
    ):
        raise HTTPException(400, "invalid or expired link")
    email = str(body.email).lower()
    other = (
        await db.execute(select(User).where(User.email == email, User.id != user.id))
    ).scalar_one_or_none()
    if other is not None:
        # sign in to the existing account from this browser
        other.session_id, user.session_id = user.session_id, f"merged-{user.id}"
        other.email_verified = True
        return {"verified": True, "email": email, "merged": True}
    user.email, user.email_verified, user.login_token_hash = email, True, None
    return {"verified": True, "email": email}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "email_verified": user.email_verified,
        "features": get_settings().feature_flags(),
    }
