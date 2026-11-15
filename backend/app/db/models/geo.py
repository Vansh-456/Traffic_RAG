"""Gazetteer and spatial linkage models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Place(Base, TimestampMixin):
    """Named place in Delhi NCR (junction, road, venue, metro station ...)."""

    __tablename__ = "places"
    __table_args__ = (
        Index(
            "ix_places_search_text_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    osm_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    name_hi: Mapped[str | None] = mapped_column(String(300))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    # lower-cased name + aliases joined by ' | ', used for trigram fuzzy match
    search_text: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(32), index=True)  # junction|road|venue|metro|...
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    centroid = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=True))
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed|osm|nominatim
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class ChunkLocation(Base):
    """Resolved place mention inside a chunk."""

    __tablename__ = "chunk_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id", ondelete="SET NULL"))
    mention: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(16))  # alias|trigram|nominatim
    confidence: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))


class Segment(Base):
    """Monitored road segment polled from TomTom flow (seeded from segments.yaml)."""

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    road: Mapped[str] = mapped_column(String(100), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")


class Event(Base, TimestampMixin):
    """Scheduled event (festival, rehearsal, match) affecting traffic."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    venue: Mapped[str | None] = mapped_column(String(300))
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id", ondelete="SET NULL"))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed|extracted
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
