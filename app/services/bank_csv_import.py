# ============================================================================
# CSV Bank Transaction Import — Chase checking, Chase credit card, PayPal
# Extends Feature 18 (bank feed import) to support CSV bank statement exports.
#
# Column mapping & pitfalls documented in the skill. Key rules:
#   - Chase checking: Amount column already signed (neg=debit, pos=credit)
#   - Chase credit:   Amount column already signed (neg=charge, pos=payment)
#   - PayPal:         Gross (NOT Net) = transaction amount;
#                     Fee column goes to Merchant Fee expense (6120)
# ============================================================================

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction

logger = logging.getLogger(__name__)

# ── Column signatures for format auto-detection ──────────────────────────

CHASE_CHECKING_SIG = {"Details", "Posting Date", "Description", "Amount", "Type"}
CHASE_CREDIT_SIG = {
    "Transaction Date",
    "Post Date",
    "Description",
    "Category",
    "Type",
    "Amount",
}
PAYPAL_SIG = {
    "Date",
    "Time",
    "Name",
    "Type",
    "Status",
}
PAYPAL_NEW_SIG = {
    "Date",
    "Time",
    "Description",
    "Gross",
    "Fee",
    "Net",
    "Transaction ID",
    "From Email Address",
    "Name",
}


def detect_format(headers: set[str]) -> str:
    """Detect CSV format by header column signature (not filename)."""
    if CHASE_CHECKING_SIG.issubset(headers):
        return "chase_checking"
    if CHASE_CREDIT_SIG.issubset(headers):
        return "chase_credit"
    if PAYPAL_SIG.issubset(headers):
        return "paypal"
    if PAYPAL_NEW_SIG.issubset(headers):
        return "paypal_new"
    return "unknown"


def parse_date(val: str) -> date:
    """Parse a date string with broad format tolerance."""
    val = val.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    try:
        from dateutil import parser as dateparser

        return dateparser.parse(val).date()
    except ImportError:
        pass
    raise ValueError(f"Cannot parse date: {val}")


# ── Format-specific parsers ──────────────────────────────────────────────


def parse_chase_checking(reader: csv.DictReader) -> list[dict]:
    """Parse Chase personal checking CSV.

    Columns: Details, Posting Date, Description, Amount, Type, Balance, Check or Slip #
    """
    transactions = []
    for row in reader:
        txn_type = (row.get("Details") or "").strip()
        date_str = (row.get("Posting Date") or "").strip()
        description = (row.get("Description") or "").strip()
        amount_str = (row.get("Amount") or "").strip()
        check_slip = (row.get("Check or Slip #") or "").strip()

        if not date_str or not amount_str:
            continue

        try:
            txn_date = parse_date(date_str)
            amount = Decimal(amount_str)
        except (ValueError, Exception) as e:
            logger.warning("Skipping chase checking row: %s", e)
            continue

        transactions.append(
            {
                "date": txn_date,
                "amount": amount,  # already signed: negative=debit, positive=credit
                "payee": description,
                "description": description,
                "check_number": check_slip if check_slip else None,
                "import_id": f"chk_{txn_date.isoformat()}_{amount}",
                "txn_type": txn_type,
            }
        )
    return transactions


def parse_chase_credit(reader: csv.DictReader) -> list[dict]:
    """Parse Chase credit card CSV.

    Columns: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Transaction Date") or "").strip()
        description = (row.get("Description") or "").strip()
        category = (row.get("Category") or "").strip()
        amount_str = (row.get("Amount") or "").strip()
        memo = (row.get("Memo") or "").strip()

        if not date_str or not amount_str:
            continue

        try:
            txn_date = parse_date(date_str)
            amount = Decimal(amount_str)
        except (ValueError, Exception) as e:
            logger.warning("Skipping chase credit row: %s", e)
            continue

        transactions.append(
            {
                "date": txn_date,
                "amount": amount,  # already signed: negative=charge, positive=payment/return
                "payee": description,
                "description": f"{category} - {description}"
                if category
                else description,
                "check_number": None,
                "import_id": f"cc_{txn_date.isoformat()}_{amount}",
                "category": category,
                "memo": memo,
            }
        )
    return transactions


def parse_paypal(reader: csv.DictReader) -> list[dict]:
    """Parse PayPal CSV.

    CRITICAL DIFFERENCE from raw intuition: map *Gross* (not Net) as the
    transaction amount. Net understates revenue because PayPal already
    subtracted the fee. Route the Fee column to Merchant Fee expense (6120)
    in a separate split. See imports/csv-import.md for worked example.

    Additionally, skip paired "Bank Deposit to PP Account" rows — they are
    the mirror-image of Express Checkout payments and net to zero. The real
    cash movement shows up in the Chase checking CSV as an IAT (PAYPAL)
    transfer.
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Date") or "").strip()
        name = (row.get("Name") or "").strip()
        txn_type = (row.get("Type") or "").strip()
        status = (row.get("Status") or "").strip()
        gross_str = (row.get("Gross") or "").strip()
        fee_str = (row.get("Fee") or "").strip()
        item_title = (row.get("Item Title") or "").strip()

        if not date_str or not gross_str:
            continue

        try:
            txn_date = parse_date(date_str)
            gross = Decimal(gross_str)
            fee = Decimal(fee_str) if fee_str else Decimal("0.00")
        except (ValueError, Exception) as e:
            logger.warning("Skipping PayPal row: %s", e)
            continue

        # Skip paired Bank Deposit rows — they're the mirror of payments
        if txn_type == "Bank Deposit to PP Account":
            continue

        # Build description from available fields
        desc_parts = [p for p in [name, item_title, txn_type] if p]
        description = " | ".join(desc_parts)

        transactions.append(
            {
                "date": txn_date,
                "amount": gross,  # Gross, NOT Net
                "payee": name or item_title or "PayPal Transfer",
                "description": description,
                "check_number": None,
                "import_id": f"pp_{txn_date.isoformat()}_{gross}",
                "fee": fee,
                "status": status,
            }
        )
    return transactions


def parse_paypal_new(reader: csv.DictReader) -> list[dict]:
    """Parse new-style PayPal CSV (2026 format — simpler columns).

    Columns: Date, Time, Time Zone, Description, Currency, Gross, Fee, Net,
             Balance, Transaction ID, From Email Address, Name, Bank Name,
             Bank Account, Shipping and Handling Amount, Sales Tax, Invoice ID,
             Reference Txn ID

    Key difference from old format: no 'Type' or 'Status' columns.
    Uses Description + Name as payee, Gross as amount.
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Date") or "").strip()
        name = (row.get("Name") or "").strip()
        description = (row.get("Description") or "").strip()
        gross_str = (row.get("Gross") or "").strip()
        fee_str = (row.get("Fee") or "").strip()
        from_email = (row.get("From Email Address") or "").strip()

        if not date_str or not gross_str:
            continue

        try:
            txn_date = parse_date(date_str)
            gross = Decimal(gross_str)
            fee = Decimal(fee_str) if fee_str else Decimal("0.00")
        except (ValueError, Exception) as e:
            logger.warning("Skipping PayPal row: %s", e)
            continue

        # Skip Bank Deposit to PP Account rows (mirror entries)
        if description.startswith("Bank Deposit to PP Account"):
            continue

        payee = name or description or "PayPal Transfer"
        desc = (
            f"{description} | {name}"
            if name and description
            else (description or name or "")
        )

        transactions.append(
            {
                "date": txn_date,
                "amount": gross,
                "payee": payee,
                "description": desc,
                "check_number": None,
                "import_id": f"pp_{txn_date.isoformat()}_{gross}",
                "fee": fee,
            }
        )
    return transactions


# ── Dispatch ─────────────────────────────────────────────────────────────


def parse_csv(csv_text: str) -> dict:
    """Parse CSV text, auto-detect format, return parsed transactions.

    Strips BOM and surrounding quotes from headers for reliable detection.
    Handles PayPal's '\ufeff"Date"' header format.

    Returns:
        {"format": str, "transactions": list[dict], "error": str | None}
    """
    # Strip BOM before handing to csv reader
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return {
            "format": "unknown",
            "transactions": [],
            "error": "Empty CSV or no headers",
        }

    # Normalize headers: strip whitespace, quotes, and BOM residue
    headers = {h.strip().strip('"').strip("'") for h in reader.fieldnames if h}
    fmt = detect_format(headers)

    parsers = {
        "chase_checking": parse_chase_checking,
        "chase_credit": parse_chase_credit,
        "paypal": parse_paypal,
        "paypal_new": parse_paypal_new,
    }

    parser = parsers.get(fmt)
    if not parser:
        return {
            "format": "unknown",
            "transactions": [],
            "error": f"Unknown CSV format. Headers found: {sorted(headers)}",
        }

    transactions = parser(reader)
    return {"format": fmt, "transactions": transactions, "error": None}


# ── Import into DB ───────────────────────────────────────────────────────


def import_csv_transactions(
    db: Session,
    bank_account_id: int,
    csv_text: str,
    format_hint: Optional[str] = None,
) -> dict:
    """Parse CSV and import into BankTransaction records.

    Dedup strategy (CSVs have no FITID): match on (date, amount) within the
    same bank account. This prevents re-import of the same file.
    """
    result = parse_csv(csv_text)
    if result["error"]:
        return {"imported": 0, "skipped": 0, "errors": [result["error"]], "total": 0}

    transactions = result["transactions"]
    imported = 0
    skipped = 0
    errors = []

    for txn in transactions:
        # Dedup by (date, amount) for this account
        existing = (
            db.query(BankTransaction)
            .filter(
                BankTransaction.bank_account_id == bank_account_id,
                BankTransaction.date == txn["date"],
                BankTransaction.amount == txn["amount"],
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        try:
            bt = BankTransaction(
                bank_account_id=bank_account_id,
                date=txn["date"],
                amount=txn["amount"],
                payee=(txn.get("payee", "") or "")[:200],
                description=(txn.get("description", "") or "")[:500],
                check_number=txn.get("check_number"),
                import_id=txn.get("import_id"),
                import_source=f"csv_{result['format']}",
                match_status="unmatched",
            )
            db.add(bt)
            imported += 1
        except Exception as e:
            logger.exception("Failed to import bank CSV transaction")
            errors.append(str(e))

    db.commit()

    # Auto-apply bank rules (same logic as ofx_import.import_transactions)
    if imported > 0:
        _apply_bank_rules(db, bank_account_id)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total": len(transactions),
        "format": result["format"],
    }


def _apply_bank_rules(db: Session, bank_account_id: int) -> None:
    """Auto-categorize unmatched transactions by bank rules."""
    try:
        from app.models.bank_rules import BankRule

        rules = (
            db.query(BankRule)
            .filter(BankRule.is_active)
            .order_by(BankRule.priority.desc())
            .all()
        )
        if not rules:
            return

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
    except ImportError:
        pass  # bank_rules model not available
