"""Notification channels: Web Push (VAPID) and email (Resend or SMTP)."""

from __future__ import annotations

import asyncio
import json
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from app.config import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


async def send_push(subscription: dict[str, Any], payload: dict[str, Any]) -> bool:
    settings = get_settings()
    if not settings.push_enabled:
        log.info("push disabled (VAPID keys missing)")
        return False
    from pywebpush import WebPushException, webpush

    def _send() -> bool:
        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=3600,
            )
            return True
        except WebPushException as exc:
            log.warning("web push failed", extra={"err": str(exc)[:200]})
            return False

    return await asyncio.to_thread(_send)


async def send_email(to: str, subject: str, body: str) -> bool:
    settings = get_settings()
    if settings.resend_api_key:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                    json={"from": settings.smtp_from, "to": [to], "subject": subject, "text": body},
                )
                r.raise_for_status()
            return True
        except Exception as exc:
            log.warning("resend failed", extra={"err": str(exc)[:200]})
            return False
    if settings.smtp_host:
        def _smtp() -> bool:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
            msg.set_content(body)
            try:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                    s.starttls()
                    if settings.smtp_user:
                        s.login(settings.smtp_user, settings.smtp_password)
                    s.send_message(msg)
                return True
            except Exception as exc:
                log.warning("smtp failed", extra={"err": str(exc)[:200]})
                return False

        return await asyncio.to_thread(_smtp)
    log.info("email disabled (no RESEND_API_KEY / SMTP_HOST)")
    return False
