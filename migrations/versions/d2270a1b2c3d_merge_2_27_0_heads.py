"""2.27.0: one head for the release

Four 2.27.0 changes each added a migration on 2.26.0's head: the
reconciliation's high-water statement date (#389, a1f2e3d4c5b6), the
giving-statement message kind on the saved invoice messages (#415,
f9a0b1c2d3e4, itself on #392's e4f5a6b7c8d9), the Possible Duplicates
dismissals (#414, b7d2e9a41c63) and account detail types (#390,
4ac01efbd72a). They touch different tables and nothing orders them, so
this merge point joins them; it changes no schema.

Revision ID: d2270a1b2c3d
Revises: 4ac01efbd72a, a1f2e3d4c5b6, b7d2e9a41c63, f9a0b1c2d3e4
Create Date: 2026-10-23
"""

from typing import Sequence, Union

revision: str = "d2270a1b2c3d"
down_revision: Union[str, Sequence[str], None] = (
    "4ac01efbd72a",
    "a1f2e3d4c5b6",
    "b7d2e9a41c63",
    "f9a0b1c2d3e4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
