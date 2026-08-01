# ============================================================================
# Bank rule application — shared by every bank-feed importer (OFX, CSV).
#
# Extracted from ofx_import.import_transactions so the CSV importer doesn't
# carry a drifting copy of the matching logic. Rules are ordered by priority
# (highest first); the first hit wins and marks the transaction "auto".
# ============================================================================

import logging

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction

logger = logging.getLogger(__name__)


def apply_bank_rules(db: Session, bank_account_id: int) -> int:
    """Auto-categorize this account's unmatched transactions by bank rules.

    Returns the number of transactions auto-matched. Commits only when at
    least one transaction matched.
    """
    try:
        from app.models.bank_rules import BankRule
    except ImportError:
        return 0  # bank_rules model not available

    rules = (
        db.query(BankRule)
        .filter(BankRule.is_active)
        .order_by(BankRule.priority.desc())
        .all()
    )
    if not rules:
        return 0

    unmatched = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.match_status == "unmatched",
        )
        .all()
    )

    auto_matched = 0
    for txn in unmatched:
        payee = (txn.payee or "").lower()
        for rule in rules:
            pattern = rule.pattern.lower()
            hit = False
            if rule.rule_type == "contains" and pattern in payee:
                hit = True
            elif rule.rule_type == "starts_with" and payee.startswith(pattern):
                hit = True
            elif rule.rule_type == "exact" and payee == pattern:
                hit = True
            if hit:
                if rule.account_id:
                    txn.category_account_id = rule.account_id
                txn.match_status = "auto"
                auto_matched += 1
                break

    if auto_matched > 0:
        db.commit()
    return auto_matched
