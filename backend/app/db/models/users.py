"""Users, saved routes, alerts, conversations, messages and feedback."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """Anonymous (session cookie) or email-verified user."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    login_token_hash: Mapped[str | None] = mapped_column(String(64))
    login_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedRoute(Base, TimestampMixin):
    __tablename__ = "saved_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    origin_label: Mapped[str] = mapped_column(String(300))
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    dest_label: Mapped[str] = mapped_column(String(300))
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    corridor = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    depart_time: Mapped[time | None] = mapped_column(Time)
    days_of_week: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), default=lambda: [0, 1, 2, 3, 4], server_default="{0,1,2,3,4}"
    )  # 0=Mon
    channels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    digest_time: Mapped[time | None] = mapped_column(Time)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PushSubscription(Base, TimestampMixin):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)


class AlertSent(Base):
    __tablename__ = "alerts_sent"
    __table_args__ = (
        UniqueConstraint("route_id", "event_key", "channel", name="uq_alerts_sent_route_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("saved_routes.id", ondelete="CASCADE"))
    event_key: Mapped[str] = mapped_column(
        String(200)
    )  # chunk:123 | incident:abc | digest:2026-11-15
    kind: Mapped[str] = mapped_column(String(16))  # advisory|incident|congestion|digest
    channel: Mapped[str] = mapped_column(String(16))  # push|email
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(64), index=True)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(12))
    intent: Mapped[str | None] = mapped_column(String(32))
    faithfulness: Mapped[float | None] = mapped_column(Float)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    internals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer)  # +1 / -1
    comment: Mapped[str | None] = mapped_column(Text)
    in_review_queue: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
