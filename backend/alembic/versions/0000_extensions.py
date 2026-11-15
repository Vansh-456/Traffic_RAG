"""Enable Postgres extensions.

Revision ID: 0000
Revises:
Create Date: 2026-11-15
"""
from __future__ import annotations

from alembic import op

revision = "0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ext in ("vector", "postgis", "pg_trgm", "unaccent"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")


def downgrade() -> None:
    pass
