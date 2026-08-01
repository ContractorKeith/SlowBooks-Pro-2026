# ============================================================================
# Xero CSV import — chart of accounts + general ledger, with dry-run.
#
# Implements the joelmacklow fork's Xero-import spec
# (docs/spec-xero-import-capability.md): file types detected by name,
# Xero column aliases tolerated, and a DRY-RUN that must pass before
# import will execute — parse errors, unbalanced journals, unmapped
# account types, and trial-balance mismatches all block.
#
# CSV-only; no XLSX, no live Xero API (per the spec's constraints).
# ============================================================================

import csv
import io
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.services.accounting import _q, create_journal_entry

logger = logging.getLogger(__name__)

# Xero account "Type"/"Class" values → our AccountType
_XERO_TYPE_MAP = {
    "bank": AccountType.ASSET,
    "current asset": AccountType.ASSET,
    "current assets": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "fixed assets": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "non-current asset": AccountType.ASSET,
    "prepayment": AccountType.ASSET,
    "asset": AccountType.ASSET,
    "current liability": AccountType.LIABILITY,
    "current liabilities": AccountType.LIABILITY,
    "liability": AccountType.LIABILITY,
    "non-current liability": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "revenue": AccountType.INCOME,
    "sales": AccountType.INCOME,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "direct costs": AccountType.COGS,
    "cost of goods sold": AccountType.COGS,
    "expense": AccountType.EXPENSE,
    "expenses": AccountType.EXPENSE,
    "overheads": AccountType.EXPENSE,
    "depreciation": AccountType.EXPENSE,
}

# Filename fragments → bundle slot
_FILE_KINDS = (
    ("chart", "coa"),
    ("account", "coa"),
    ("general", "gl"),
    ("ledger", "gl"),
    ("journal", "gl"),
    ("trial", "tb"),
)


def classify_filename(name: str) -> str | None:
    lowered = (name or "").lower()
    for fragment, kind in _FILE_KINDS:
        if fragment in lowered:
            return kind
    return None


def _field(row: dict, *aliases: str) -> str:
    for alias in aliases:
        for key, value in row.items():
            if key and key.strip().lstrip("﻿").lower() == alias:
                return (value or "").strip()
    return ""


def _decimal(raw: str) -> Decimal:
    raw = (raw or "").strip().replace(",", "").replace("$", "")
    if not raw:
        return Decimal("0")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"unparseable amount: {raw!r}")
    return -value if negative else value


def _date(raw: str):
    raw = (raw or "").strip()
    for fmt in ("%d %b %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(csv.DictReader(io.StringIO(csv_text)), start=2):
        name = _field(row, "name", "account name", "account")
        if not name:
            continue
        xero_type = _field(row, "type", "account type", "class").lower()
        acct_type = _XERO_TYPE_MAP.get(xero_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Xero account type {xero_type!r}")
            continue
        accounts.append(
            {
                "code": _field(row, "code", "account code", "*code") or None,
                "name": name,
                "type": acct_type,
                "description": _field(row, "description") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    """Parse GL rows and group them into journals.

    Grouping key: Xero's journal number when present, otherwise
    (date, reference/source) — every group must balance to zero.
    """
    rows, errors = [], []
    for i, row in enumerate(csv.DictReader(io.StringIO(csv_text)), start=2):
        acct = _field(row, "account", "account name")
        date_raw = _field(row, "date", "journal date")
        if not acct and not date_raw:
            continue  # blank/summary row
        try:
            rows.append(
                {
                    "journal": _field(
                        row,
                        "journal number",
                        "journal no",
                        "journal #",
                        "journalnumber",
                    ),
                    "date": _date(date_raw),
                    "account": acct,
                    "code": _field(row, "account code", "code") or None,
                    "description": _field(row, "description", "details", "narration"),
                    "reference": _field(row, "reference", "source"),
                    "debit": _decimal(_field(row, "debit", "debit (source)")),
                    "credit": _decimal(_field(row, "credit", "credit (source)")),
                }
            )
        except ValueError as exc:
            errors.append(f"GL row {i}: {exc}")

    journals: dict = {}
    for row in rows:
        key = row["journal"] or f"{row['date'].isoformat()}|{row['reference']}"
        journals.setdefault(key, []).append(row)
    return list(journals.values()), errors


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    """Trial balance: {account_name_lower: net_debit_minus_credit}."""
    balances, errors = {}, []
    for i, row in enumerate(csv.DictReader(io.StringIO(csv_text)), start=2):
        name = _field(row, "account", "account name", "name")
        if not name:
            continue
        try:
            debit = _decimal(_field(row, "debit", "debit ytd", "ytd debit"))
            credit = _decimal(_field(row, "credit", "credit ytd", "ytd credit"))
        except ValueError as exc:
            errors.append(f"TB row {i}: {exc}")
            continue
        # Strip Xero's "Name (Code)" suffix for matching
        key = name.split("(")[0].strip().lower()
        balances[key] = balances.get(key, Decimal("0")) + debit - credit
    return balances, errors


def dry_run(db: Session, bundle: dict) -> dict:
    """Validate a bundle {kind: csv_text}. Nothing is written.

    Returns {ok, errors, warnings, accounts, journals} — `ok` False on
    anything that would corrupt an import (missing GL/COA, parse errors,
    unbalanced journals, unmapped accounts, TB mismatches).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if "coa" not in bundle:
        errors.append(
            "Missing chart of accounts CSV (filename containing 'chart' or 'accounts')"
        )
    if "gl" not in bundle:
        errors.append(
            "Missing general ledger CSV (filename containing 'general', 'ledger', or 'journal')"
        )
    if errors:
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "accounts": 0,
            "journals": 0,
        }

    accounts, coa_errors = parse_coa(bundle["coa"])
    errors.extend(coa_errors)
    journals, gl_errors = parse_gl(bundle["gl"])
    errors.extend(gl_errors)

    known = {a["name"].lower() for a in accounts}
    known |= {
        a.name.lower() for a in db.query(Account).all()
    }  # existing accounts also satisfy GL references

    simulated: dict[str, Decimal] = {}
    for group in journals:
        total = sum(r["debit"] - r["credit"] for r in group)
        if _q(abs(total)) > Decimal("0.01"):
            ref = group[0]["journal"] or group[0]["reference"] or group[0]["date"]
            errors.append(f"Journal {ref}: unbalanced by {_q(total)}")
        for row in group:
            name = row["account"].split("(")[0].strip().lower()
            if name not in known:
                errors.append(
                    f"GL references account {row['account']!r} not present in the "
                    f"chart of accounts"
                )
                known.add(name)  # report once
            simulated[name] = (
                simulated.get(name, Decimal("0")) + row["debit"] - row["credit"]
            )

    if "tb" in bundle:
        tb, tb_errors = parse_tb(bundle["tb"])
        errors.extend(tb_errors)
        for name, expected in tb.items():
            got = simulated.get(name, Decimal("0"))
            if _q(got - expected) != 0:
                errors.append(
                    f"Trial balance mismatch for {name!r}: GL nets {_q(got)}, "
                    f"trial balance says {_q(expected)}"
                )
    else:
        warnings.append("No trial balance CSV supplied — balance verification skipped")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "accounts": len(accounts),
        "journals": len(journals),
    }


def run_import(db: Session, bundle: dict) -> dict:
    """Execute the import. Refuses when the dry-run fails (spec rule)."""
    verdict = dry_run(db, bundle)
    if not verdict["ok"]:
        return {**verdict, "imported_accounts": 0, "imported_journals": 0}

    accounts, _ = parse_coa(bundle["coa"])
    created = 0
    by_name: dict[str, Account] = {a.name.lower(): a for a in db.query(Account).all()}
    for spec in accounts:
        key = spec["name"].lower()
        if key in by_name:
            continue
        acct = Account(
            name=spec["name"],
            account_number=spec["code"],
            account_type=spec["type"],
            description=spec["description"],
        )
        # Xero codes can collide with the seeded chart's numbers — keep the
        # name authoritative and drop the code rather than fail the row.
        if spec["code"] and (
            db.query(Account).filter(Account.account_number == spec["code"]).first()
        ):
            acct.account_number = None
        db.add(acct)
        db.flush()
        by_name[key] = acct
        created += 1

    journals, _ = parse_gl(bundle["gl"])
    posted = 0
    for group in journals:
        lines = []
        for row in group:
            acct = by_name[row["account"].split("(")[0].strip().lower()]
            debit = _q(row["debit"])
            credit = _q(row["credit"])
            if debit == 0 and credit == 0:
                continue
            lines.append(
                {
                    "account_id": acct.id,
                    "debit": debit,
                    "credit": credit,
                    "description": row["description"] or None,
                }
            )
        if not lines:
            continue
        first = group[0]
        create_journal_entry(
            db,
            first["date"],
            first["description"]
            or f"Xero import {first['journal'] or first['reference']}",
            lines,
            source_type="xero_import",
            reference=(first["journal"] or first["reference"] or None),
        )
        posted += 1

    db.commit()
    return {
        **verdict,
        "imported_accounts": created,
        "imported_journals": posted,
    }
