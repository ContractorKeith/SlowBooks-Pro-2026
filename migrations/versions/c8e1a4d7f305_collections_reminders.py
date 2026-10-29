"""collections: reminder rules, the reminder audit, customer flags (#653)

Payment reminders that send once per rule: invoice_reminder_rules holds the
schedule (days from the due date and the wording), invoice_reminders records
every send or failed try, which is also what marks a rule as already sent
for an invoice. customers gains invoice_reminders_enabled (on for everyone,
so existing customers are reminded unless opted out) and monthly_statement
(off for everyone until chosen). No rows are seeded here; the default rules
are created the first time the rules are read.

Revision ID: c8e1a4d7f305
Revises: d2f7a9c4e6b1
Create Date: 2026-10-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8e1a4d7f305"
down_revision: Union[str, None] = "d2f7a9c4e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_reminder_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("days_from_due", sa.Integer(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_invoice_reminder_rules_id", "invoice_reminder_rules", ["id"])
    op.create_table(
        "invoice_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("invoice_reminder_rules.id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False
        ),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("days_from_due", sa.Integer(), nullable=False),
        sa.Column("balance_due", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "email_log_id", sa.Integer(), sa.ForeignKey("email_log.id"), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_reminders_id", "invoice_reminders", ["id"])
    op.create_index(
        "ix_invoice_reminders_invoice_id", "invoice_reminders", ["invoice_id"]
    )
    with op.batch_alter_table("customers") as batch:
        batch.add_column(
            sa.Column(
                "invoice_reminders_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_statement",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.drop_column("monthly_statement")
        batch.drop_column("invoice_reminders_enabled")
    op.drop_index("ix_invoice_reminders_invoice_id", table_name="invoice_reminders")
    op.drop_index("ix_invoice_reminders_id", table_name="invoice_reminders")
    op.drop_table("invoice_reminders")
    op.drop_index("ix_invoice_reminder_rules_id", table_name="invoice_reminder_rules")
    op.drop_table("invoice_reminder_rules")
