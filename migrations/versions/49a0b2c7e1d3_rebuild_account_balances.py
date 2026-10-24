"""rebuild stored account balances from the ledger (#490)

Reclassify moved lines between accounts without moving Account.balance
with them (2.24.0 through 2.28.0), so a book that used it carries the
reclassified amounts on the old account in every reader of the stored
balance (the Chart of Accounts Balance column, the accounts CSV export,
the AI tools) while the statements, which read the lines, were right all
along. The lines are the truth, so every account's stored balance is set
to the sum of its lines in the account's natural direction: debit minus
credit for assets, expenses and cost of goods sold, credit minus debit
for the rest. Idempotent; an account with no lines is 0. account_type is
stored as the enum NAME on every engine ('ASSET').

Revision ID: 49a0b2c7e1d3
Revises: 4ac01efbd72a
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "49a0b2c7e1d3"
down_revision: Union[str, None] = "4ac01efbd72a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEBIT_NORMAL = ("ASSET", "EXPENSE", "COGS")


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE accounts SET balance = COALESCE(("
            "  SELECT CASE WHEN accounts.account_type IN ('ASSET', 'EXPENSE', 'COGS')"
            "    THEN SUM(tl.debit - tl.credit) ELSE SUM(tl.credit - tl.debit) END"
            "  FROM transaction_lines tl WHERE tl.account_id = accounts.id"
            "), 0)"
        )
    )


def downgrade() -> None:
    # The rebuilt balance is the right one on either release; nothing to undo.
    pass
