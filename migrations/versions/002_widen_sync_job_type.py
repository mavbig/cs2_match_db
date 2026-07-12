"""widen sync_jobs.job_type for enrich_player UUIDs

Revision ID: 002
Revises: 001
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("sync_jobs", "job_type", type_=sa.String(128), existing_type=sa.String(32))


def downgrade() -> None:
    op.alter_column("sync_jobs", "job_type", type_=sa.String(32), existing_type=sa.String(128))
