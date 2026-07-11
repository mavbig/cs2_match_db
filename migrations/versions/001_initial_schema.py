"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    op.create_table(
        "players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("steam64_id", sa.String(20), nullable=False, unique=True),
        sa.Column("current_name", sa.String(128)),
        sa.Column("avatar_url", sa.String(512)),
        sa.Column("profile_url", sa.String(512)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_players_steam64_id", "players", ["steam64_id"])

    op.create_table(
        "player_name_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_player_name_history_player_id", "player_name_history", ["player_id"])
    op.create_index("ix_player_name_history_name", "player_name_history", ["name"])

    op.create_table(
        "player_platform_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE")),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("nickname", sa.String(128)),
        sa.Column("profile_url", sa.String(512)),
        sa.UniqueConstraint("platform", "external_id", name="uq_platform_external_id"),
    )
    op.create_index("ix_player_platform_accounts_player_id", "player_platform_accounts", ["player_id"])

    op.create_table(
        "player_stat_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("payload", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_player_stat_snapshots_player_id", "player_stat_snapshots", ["player_id"])

    op.create_table(
        "matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("map", sa.String(64)),
        sa.Column("mode", sa.String(64)),
        sa.Column("played_at", sa.DateTime(timezone=True)),
        sa.Column("score_team_a", sa.Integer),
        sa.Column("score_team_b", sa.Integer),
        sa.Column("duration_seconds", sa.Integer),
        sa.Column("share_code", sa.String(64)),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source", "source_match_id", name="uq_match_source_id"),
    )
    op.create_index("ix_matches_played_at", "matches", ["played_at"])

    op.create_table(
        "match_players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("matches.id", ondelete="CASCADE")),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE")),
        sa.Column("team", sa.String(16)),
        sa.Column("kills", sa.Integer),
        sa.Column("deaths", sa.Integer),
        sa.Column("assists", sa.Integer),
        sa.Column("mvps", sa.Integer),
        sa.Column("headshot_pct", sa.Float),
        sa.Column("score", sa.Integer),
        sa.Column("ping", sa.Integer),
        sa.Column("is_me", sa.Boolean, default=False),
        sa.UniqueConstraint("match_id", "player_id", name="uq_match_player"),
    )
    op.create_index("ix_match_players_match_id", "match_players", ["match_id"])
    op.create_index("ix_match_players_player_id", "match_players", ["player_id"])

    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("matches_imported", sa.Integer, default=0),
        sa.Column("error_message", sa.Text),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("sync_jobs")
    op.drop_table("match_players")
    op.drop_table("matches")
    op.drop_table("player_stat_snapshots")
    op.drop_table("player_platform_accounts")
    op.drop_table("player_name_history")
    op.drop_table("players")
