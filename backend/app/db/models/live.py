"""Live sensor data: TomTom flow, incidents, Open-Meteo weather."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrafficFlowObs(Base):
    __tablename__ = "traffic_flow_obs"
    __table_args__ = (
        Index("ix_traffic_flow_obs_observed_brin", "observed_at", postgresql_using="brin"),
        Index("ix_traffic_flow_obs_segment_time", "segment_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_speed: Mapped[float] = mapped_column(Float)
    free_flow_speed: Mapped[float] = mapped_column(Float)
    current_travel_time: Mapped[float | None] = mapped_column(Float)
    free_flow_travel_time: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    road_closure: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(String(8), default="raw")  # raw|hourly


class TrafficIncident(Base):
    __tablename__ = "traffic_incidents"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # TomTom incident id
    category: Mapped[str] = mapped_column(String(32), index=True)
    icon_category: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    from_name: Mapped[str | None] = mapped_column(Text)
    to_name: Mapped[str | None] = mapped_column(Text)
    road_numbers: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
    magnitude: Mapped[int | None] = mapped_column(Integer)
    length_m: Mapped[float | None] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class WeatherObs(Base):
    __tablename__ = "weather_obs"
    __table_args__ = (
        UniqueConstraint("zone", "observed_at", "kind", name="uq_weather_obs_zone_time_kind"),
        Index("ix_weather_obs_observed_brin", "observed_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone: Mapped[str] = mapped_column(String(64), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(12))  # current|forecast|history
    temperature: Mapped[float | None] = mapped_column(Float)
    precipitation: Mapped[float | None] = mapped_column(Float)
    rain: Mapped[float | None] = mapped_column(Float)
    visibility: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
