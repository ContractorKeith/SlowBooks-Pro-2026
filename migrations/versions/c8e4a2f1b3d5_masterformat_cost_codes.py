"""MasterFormat cost codes, line cost types, and trade packages (#550)

Step 1 of the commercial GC layer. Additive and nullable throughout:

- cost_codes.level — "division" / "section" / NULL (a custom code), derived
  from the CSI code shape on save and import.
- cost_type on bill_lines, estimate_lines, purchase_order_lines and
  vendor_credit_lines — the per-line cost type (transaction_lines already
  has one); NULL falls back to the cost code's default, so existing lines
  read exactly as before.
- trade_packages / trade_package_sections — the bid packages a GC awards,
  mapped many-to-many to cost-code sections.

An ordinary (non-construction) company never sees any of it. Nothing is
backfilled. downgrade reverses.

Revision ID: c8e4a2f1b3d5
Revises: b7d3f1e2a9c4
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8e4a2f1b3d5"
down_revision: Union[str, None] = "b7d3f1e2a9c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LINE_TABLES = (
    "bill_lines",
    "estimate_lines",
    "purchase_order_lines",
    "vendor_credit_lines",
)


def upgrade() -> None:
    with op.batch_alter_table("cost_codes") as batch:
        batch.add_column(sa.Column("level", sa.String(length=10), nullable=True))
    for table in _LINE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("cost_type", sa.String(length=20), nullable=True))

    op.create_table(
        "trade_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_trade_package_name"),
    )
    op.create_table(
        "trade_package_sections",
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("trade_packages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "cost_code_id",
            sa.Integer(),
            sa.ForeignKey("cost_codes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("trade_package_sections")
    op.drop_table("trade_packages")
    for table in _LINE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("cost_type")
    with op.batch_alter_table("cost_codes") as batch:
        batch.drop_column("level")
