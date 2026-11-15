"""Shared API dependencies: sessions, admin auth, rate limiting."""

from __future__ import annotations

import hmac
import secrets
import time
import uuid

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.users import User
from app.db.session import get_db
from app.redis_client import get_redis

SESSION_COOKIE = "dtc_sid"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    """Fixed 60-second window per IP for expensive endpoints."""
    limit = get_settings().rate_limit_per_min
    key = f"ratelimit:ip:{client_ip(request)}:{int(time.time() // 60)}"
    try:
        r = get_redis()
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 70)
    except Exception:
        return  # fail open if Redis is down; LLM limiter still protects providers
    if count > limit:
        raise HTTPException(status_code=429, detail="Too many requests, please wait a minute.", headers={"Retry-After": "60"})


def session_id(request: Request, response: Response) -> str:
    sid = request.cookies.get(SESSION_COOKIE) or request.headers.get("x-session-id")
    if not sid or len(sid) > 64:
        sid = secrets.token_urlsafe(24)
    response.set_cookie(SESSION_COOKIE, sid, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
                        secure=get_settings().app_env == "production")
    return sid


async def current_user(sid: str = Depends(session_id), db: AsyncSession = Depends(get_db)) -> User:
    user = (await db.execute(select(User).where(User.session_id == sid))).scalar_one_or_none()
    if user is None:
        user = User(id=uuid.uuid4(), session_id=sid)
        db.add(user)
        await db.flush()
    return user


def is_admin_token(token: str | None) -> bool:
    expected = get_settings().admin_token
    return bool(token) and hmac.compare_digest(str(token), expected)


async def require_admin(authorization: str | None = Header(default=None), x_admin_token: str | None = Header(default=None)) -> None:
    token = x_admin_token or (authorization.removeprefix("Bearer ").strip() if authorization else None)
    if not is_admin_token(token):
        raise HTTPException(status_code=401, detail="admin token required")
