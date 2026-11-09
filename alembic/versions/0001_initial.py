"""Estructura inicial de judiciary (equivalente a docs/db_structure.sql).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "areas",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="areas_pkey"),
    )
    op.create_table(
        "judges",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="judges_pkey"),
    )
    op.create_table(
        "trials",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("crime", sa.Text(), nullable=False),
        sa.Column("judge_id", sa.Text(), nullable=True),
        sa.Column("area_id", sa.BigInteger(), nullable=True),
        sa.Column("date_admission", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_type", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.ForeignKeyConstraint(
            ["judge_id"], ["judges.id"], name="trials_judge_id_fkey", onupdate="CASCADE", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["area_id"], ["areas.id"], name="trials_area_id_fkey", onupdate="CASCADE", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="trials_pkey"),
    )
    op.create_index("ix_trials_judge_id", "trials", ["judge_id"])
    op.create_index("ix_trials_area_id", "trials", ["area_id"])
    op.create_table(
        "incidents_trials",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("trial_id", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("jurisdictional_dependence", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("defendant", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.ForeignKeyConstraint(
            ["trial_id"], ["trials.id"], name="incidents_trials_trial_id_fkey", onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="incidents_trials_pkey"),
    )
    op.create_index("ix_incidents_trials_trial_id", "incidents_trials", ["trial_id"])
    op.create_table(
        "updates_incidents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("incident_trial_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("visible", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_trial_id"],
            ["incidents_trials.id"],
            name="updates_incidents_incident_trial_id_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="updates_incidents_pkey"),
    )
    op.create_index("ix_updates_incidents_incident_trial_id", "updates_incidents", ["incident_trial_id"])


def downgrade() -> None:
    op.drop_table("updates_incidents")
    op.drop_table("incidents_trials")
    op.drop_table("trials")
    op.drop_table("judges")
    op.drop_table("areas")
