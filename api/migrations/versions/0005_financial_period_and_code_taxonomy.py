"""financial reporting periods and activity-code taxonomy metadata

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def _period_metadata(start: date, end: date) -> tuple[int, str]:
    days = (end - start).days + 1
    if days <= 0:
        return days, "invalid"
    if days < 365:
        return days, "short"
    if days <= 366:
        return days, "standard_12_month"
    return days, "long"


def upgrade() -> None:
    with op.batch_alter_table("company_financials") as batch_op:
        batch_op.add_column(sa.Column("period_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("period_length_class", sa.String(length=24), nullable=True))

    with op.batch_alter_table("company_facts") as batch_op:
        batch_op.add_column(sa.Column("code_system", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("code_version", sa.String(length=64), nullable=True))

    financials = sa.table(
        "company_financials",
        sa.column("id", sa.String()),
        sa.column("period_start", sa.Date()),
        sa.column("period_end", sa.Date()),
        sa.column("period_days", sa.Integer()),
        sa.column("period_length_class", sa.String()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(financials.c.id, financials.c.period_start, financials.c.period_end).where(
            financials.c.period_start.is_not(None), financials.c.period_end.is_not(None)
        )
    )
    for row in rows:
        days, length_class = _period_metadata(row.period_start, row.period_end)
        connection.execute(
            financials.update()
            .where(financials.c.id == row.id)
            .values(period_days=days, period_length_class=length_class)
        )

    # Existing industry-code facts do not carry the official activity file's emtak_version field.
    # Leave taxonomy metadata NULL rather than guessing a version from fiscal year or import date.


def downgrade() -> None:
    with op.batch_alter_table("company_facts") as batch_op:
        batch_op.drop_column("code_version")
        batch_op.drop_column("code_system")

    with op.batch_alter_table("company_financials") as batch_op:
        batch_op.drop_column("period_length_class")
        batch_op.drop_column("period_days")
