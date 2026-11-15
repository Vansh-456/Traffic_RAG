"""Content models: sources, raw downloads, documents, chunks and vectors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

EMBED_DIM = 1024


class Source(Base, TimestampMixin):
    """Registry of ingestion sources; each can be toggled and tracks health."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))  # advisory|news|sensor|weather|gazetteer
    trust_level: Mapped[str] = mapped_column(String(16))  # official|sensor|news
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    schedule: Mapped[str | None] = mapped_column(String(64))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class RawDocument(Base):
    """Cached raw download (bytes live on disk under DATA_DIR/raw)."""

    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("url", "content_hash", name="uq_raw_documents_url_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(100))
    raw_path: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)


class Document(Base, TimestampMixin):
    """A logical document (advisory PDF, news article, incident, ...)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    raw_document_id: Mapped[int | None] = mapped_column(ForeignKey("raw_documents.id"))
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    authority: Mapped[str | None] = mapped_column(String(200))
    publisher: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8), default="en")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    trust_level: Mapped[str] = mapped_column(String(16))
    linked_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    parse_method: Mapped[str | None] = mapped_column(String(16))  # text|ocr|vision|html|feed
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|ready|failed
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base, TimestampMixin):
    """Retrievable unit with contextual header, enrichment metadata and geometry."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv_en", "tsv_en", postgresql_using="gin"),
        Index("ix_chunks_tsv_simple", "tsv_simple", postgresql_using="gin"),
        Index("ix_chunks_metadata", "metadata", postgresql_using="gin"),
        Index(
            "ix_chunks_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
        Index("ix_chunks_validity", "valid_from", "valid_to"),
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    text_with_context: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(8), default="en")
    event_type: Mapped[str | None] = mapped_column(String(32), index=True)
    severity: Mapped[str | None] = mapped_column(String(8))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    trust_level: Mapped[str] = mapped_column(String(16), default="news")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ready
    dedup_cluster_id: Mapped[int | None] = mapped_column(Integer, index=True)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    geom = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    tsv_en = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, text_with_context)", persisted=True)
    )
    tsv_simple = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, text_with_context)", persisted=True)
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(100))
    embedding = mapped_column(Vector(EMBED_DIM), nullable=False)


class QuestionEmbedding(Base):
    """Hypothetical questions a chunk answers (HyDE-style question index)."""

    __tablename__ = "question_embeddings"
    __table_args__ = (
        Index(
            "ix_question_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBED_DIM), nullable=False)
