"""Columna judges.total_trials (totalRegistros de la API de juicios).

Revision ID: 0002_judges_total_trials
Revises: 0001_initial
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002_judges_total_trials"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "judges",
        sa.Column("total_trials", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("judges", "total_trials")
