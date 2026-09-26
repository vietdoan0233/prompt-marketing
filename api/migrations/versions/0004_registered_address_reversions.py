"""Allow a registered address to recur after a different address.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("registered_addresses") as batch_op:
        batch_op.drop_constraint("uq_registered_address_version", type_="unique")
        batch_op.create_index("ix_registered_address_hash", ["company_id", "source_id", "content_hash"])


def downgrade() -> None:
    with op.batch_alter_table("registered_addresses") as batch_op:
        batch_op.drop_index("ix_registered_address_hash")
        batch_op.create_unique_constraint(
            "uq_registered_address_version", ["company_id", "source_id", "content_hash"]
        )
