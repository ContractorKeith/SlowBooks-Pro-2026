# ============================================================================
# Migration engine — shared by every "migrate from X" CSV importer
# (Xero today, MYOB now, whatever comes next).
#
# The engine owns the contract; source modules own only their dialect:
#   parsers = {
#     "coa": text -> ([{code,name,type,description}], errors),
#     "gl":  text -> ([journal_groups], errors)   # rows w/ date/account/
#                                                 # debit/credit/desc/ref
#     "tb":  text -> ({account_name_lower: net_debit_minus_credit}, errors),
#   }
#
# DRY-RUN is the contract: parse everything, require every reconstructed
# journal to balance, flag GL accounts missing from the chart, verify GL
# nets against the trial balance when supplied — and write NOTHING.
# run_import() re-runs the dry-run and refuses when it fails.
# ============================================================================

import csv
import io
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.services.accounting import _q, create_journal_entry

logger = logging.getLogger(__name__)


# ── Dialect-agnostic cell helpers ────────────────────────────────────────


def field(row: dict, *aliases: str) -> str:
    """Case-insensitive, BOM-tolerant header lookup across aliases."""
    for alias in aliases:
        for key, value in row.items():
            if key and key.strip().lstrip("﻿").lower() == alias:
                return (value or "").strip()
    return ""


def parse_amount(raw: str) -> Decimal:
    """Money cell → Decimal. Tolerates $, thousands separators, and
    accounting-style parentheses negatives."""
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


def parse_date(raw: str, formats: tuple[str, ...]):
    raw = (raw or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def sniff_reader(csv_text: str) -> csv.DictReader:
    """DictReader with the delimiter sniffed from the header line —
    MYOB classic exports are tab-separated, cloud exports are commas."""
    first_line = csv_text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first_line else ","
    return csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)


def strip_code_suffix(name: str) -> str:
    """'Name (Code)' → 'name' for cross-file account matching."""
    return name.split("(")[0].strip().lower()


# ── The engine ───────────────────────────────────────────────────────────


def dry_run_bundle(db: Session, bundle: dict, parsers: dict, source_label: str) -> dict:
    """Validate a bundle {kind: csv_text}. Nothing is written."""
    errors: list[str] = []
    warnings: list[str] = []

    if "coa" not in bundle:
        errors.append(
            "Missing chart of accounts file (filename containing 'chart' or 'accounts')"
        )
    if "gl" not in bundle:
        errors.append(
            "Missing general ledger / journal file (filename containing "
            "'general', 'ledger', or 'journal')"
        )
    if errors:
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "accounts": 0,
            "journals": 0,
        }

    accounts, coa_errors = parsers["coa"](bundle["coa"])
    errors.extend(coa_errors)
    journals, gl_errors = parsers["gl"](bundle["gl"])
    errors.extend(gl_errors)

    known = {a["name"].lower() for a in accounts}
    known |= {a.name.lower() for a in db.query(Account).all()}

    simulated: dict[str, Decimal] = {}
    for group in journals:
        total = sum(r["debit"] - r["credit"] for r in group)
        if _q(abs(total)) > Decimal("0.01"):
            ref = (
                group[0].get("journal") or group[0].get("reference") or group[0]["date"]
            )
            errors.append(f"Journal {ref}: unbalanced by {_q(total)}")
        for row in group:
            name = strip_code_suffix(row["account"])
            if name not in known:
                errors.append(
                    f"GL references account {row['account']!r} not present in "
                    f"the chart of accounts"
                )
                known.add(name)  # report once
            simulated[name] = (
                simulated.get(name, Decimal("0")) + row["debit"] - row["credit"]
            )

    if "tb" in bundle and "tb" in parsers:
        tb, tb_errors = parsers["tb"](bundle["tb"])
        errors.extend(tb_errors)
        for name, expected in tb.items():
            got = simulated.get(name, Decimal("0"))
            if _q(got - expected) != 0:
                errors.append(
                    f"Trial balance mismatch for {name!r}: GL nets {_q(got)}, "
                    f"trial balance says {_q(expected)}"
                )
    else:
        warnings.append("No trial balance file supplied — balance verification skipped")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "accounts": len(accounts),
        "journals": len(journals),
    }


def run_import_bundle(
    db: Session, bundle: dict, parsers: dict, source_type: str, source_label: str
) -> dict:
    """Execute the import. Refuses when the dry-run fails."""
    verdict = dry_run_bundle(db, bundle, parsers, source_label)
    if not verdict["ok"]:
        return {**verdict, "imported_accounts": 0, "imported_journals": 0}

    accounts, _ = parsers["coa"](bundle["coa"])
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
        # Source-system codes can collide with the seeded chart's numbers —
        # the name stays authoritative; drop the code rather than fail.
        if spec["code"] and (
            db.query(Account).filter(Account.account_number == spec["code"]).first()
        ):
            acct.account_number = None
        db.add(acct)
        db.flush()
        by_name[key] = acct
        created += 1

    journals, _ = parsers["gl"](bundle["gl"])
    posted = 0
    for group in journals:
        lines = []
        for row in group:
            acct = by_name[strip_code_suffix(row["account"])]
            debit = _q(row["debit"])
            credit = _q(row["credit"])
            # MYOB (and hand-edited files) express contra lines as a
            # NEGATIVE amount in the same column; the journal engine only
            # accepts non-negative sides, so normalize sign to side here.
            if debit < 0:
                credit += -debit
                debit = _q(0)
            if credit < 0:
                debit += -credit
                credit = _q(0)
            if debit == 0 and credit == 0:
                continue
            lines.append(
                {
                    "account_id": acct.id,
                    "debit": debit,
                    "credit": credit,
                    "description": row.get("description") or None,
                }
            )
        if not lines:
            continue
        first = group[0]
        reference = first.get("journal") or first.get("reference") or None
        create_journal_entry(
            db,
            first["date"],
            first.get("description")
            or f"{source_label} import {reference or ''}".strip(),
            lines,
            source_type=source_type,
            reference=reference,
        )
        posted += 1

    db.commit()
    return {**verdict, "imported_accounts": created, "imported_journals": posted}
