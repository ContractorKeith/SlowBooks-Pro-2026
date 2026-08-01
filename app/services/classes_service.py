# ============================================================================
# Class dimension helpers — default-row management and strict lookup.
# ============================================================================

from typing import Optional

from sqlalchemy.orm import Session

from app.models.classes import TxnClass

UNCATEGORIZED_NAME = "Uncategorized"


def uncategorized_class_id(db: Session) -> int:
    """Id of the system-default class, creating it on first use.

    Kept get-or-create (rather than assuming seed order) so fresh
    databases, migrated desktop files, and the test harness all converge
    on one canonical row.
    """
    row = db.query(TxnClass).filter(TxnClass.is_system_default).first()
    if row:
        return row.id
    # A pre-existing user class named "Uncategorized" gets promoted rather
    # than colliding with the unique name constraint.
    row = db.query(TxnClass).filter(TxnClass.name == UNCATEGORIZED_NAME).first()
    if row:
        row.is_system_default = True
    else:
        row = TxnClass(name=UNCATEGORIZED_NAME, is_system_default=True)
        db.add(row)
    db.flush()
    return row.id


def resolve_class_id(db: Session, name: str) -> Optional[int]:
    """Strict class lookup by exact then case-insensitive name.

    Returns the id or None — callers (e.g. the IIF importer) raise when
    None so a missing CLASS surfaces the same way a missing vendor or
    account does. No fuzzy matching: classes are user-defined labels and
    a near-miss silently filed under the wrong class is worse than an
    error.
    """
    if not name:
        return None
    row = db.query(TxnClass).filter(TxnClass.name == name).first()
    if not row:
        row = db.query(TxnClass).filter(TxnClass.name.ilike(name)).first()
    return row.id if row else None
