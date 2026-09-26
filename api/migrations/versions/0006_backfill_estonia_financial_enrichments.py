"""backfill Estonia EUR values and derived EBITDA

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE company_financials
            SET currency = COALESCE(currency, 'EUR'),
                unit = COALESCE(unit, 'EUR')
            WHERE source_id = 'ee-ariregister'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE company_financials
            SET ebitda = operating_profit - depreciation_and_impairment,
                value_type = 'derived',
                calculation_formula = 'operating_profit - depreciation_and_impairment'
            WHERE source_id = 'ee-ariregister'
              AND ebitda IS NULL
              AND operating_profit IS NOT NULL
              AND depreciation_and_impairment IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    # EUR is a source fact for every Estonia filing and remains valid after downgrade.
    # Remove only values that this migration marked as derived.
    op.execute(
        sa.text(
            """
            UPDATE company_financials
            SET ebitda = NULL,
                value_type = 'reported',
                calculation_formula = NULL
            WHERE source_id = 'ee-ariregister'
              AND calculation_formula = 'operating_profit - depreciation_and_impairment'
            """
        )
    )
