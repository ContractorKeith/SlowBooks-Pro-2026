# ============================================================================
# Bank accounts + reconciliation — toggle cleared items, then validate
# their sum matches the statement balance.
# ============================================================================

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.accounts import Account
from app.models.banking import (
    BankAccount,
    BankTransaction,
    Reconciliation,
    ReconciliationStatus,
)
from app.schemas.banking import (
    BankAccountCreate,
    BankAccountUpdate,
    BankAccountResponse,
    BankTransactionCreate,
    BankTransactionResponse,
    LegacyBalancePost,
    ReconciliationCreate,
    ReconciliationResponse,
)
from app.services.bank_posting import post_opening_balance, require_bank_account
from app.services.bank_register import account_register, gl_balance, gl_balances
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/banking", tags=["banking"])


# ---------------------------------------------------------------------------
# Bank accounts. The ledger account (bank_kind set) is the account; a
# BankAccount row is its feed/statement identity. Balances come from the
# ledger (issue #114).
# ---------------------------------------------------------------------------


def _feed_out(ba: BankAccount, balances: dict) -> dict:
    return {
        "id": ba.id,
        "name": ba.name,
        "account_id": ba.account_id,
        "account_name": ba.account.name if ba.account else None,
        "bank_kind": ba.account.bank_kind if ba.account else None,
        "bank_name": ba.bank_name,
        "last_four": ba.last_four,
        "balance": (
            balances.get(ba.account_id, Decimal("0")) if ba.account_id else Decimal("0")
        ),
        "legacy_balance": ba.legacy_balance,
        "is_active": bool(ba.is_active),
        "created_at": ba.created_at,
        "updated_at": ba.updated_at,
    }


def _feeds_out(db: Session, rows: list) -> list[dict]:
    balances = gl_balances(db, [b.account_id for b in rows if b.account_id])
    return [_feed_out(b, balances) for b in rows]


@router.get("/overview")
def banking_overview(db: Session = Depends(get_db)):
    """Every bank and card account with its ledger balance, its feed (if
    any), how many statement lines wait for review, and the last completed
    reconciliation."""
    accounts = (
        db.query(Account)
        .filter(Account.bank_kind.isnot(None), Account.is_active)
        .order_by(Account.account_number, Account.name)
        .all()
    )
    ids = [a.id for a in accounts]
    balances = gl_balances(db, ids)
    feeds = {
        b.account_id: b
        for b in db.query(BankAccount)
        .filter(BankAccount.is_active, BankAccount.account_id.in_(ids))
        .all()
    }
    review = dict(
        db.query(BankTransaction.bank_account_id, func.count(BankTransaction.id))
        .filter(BankTransaction.match_status == "unmatched")
        .group_by(BankTransaction.bank_account_id)
        .all()
    )
    last_recon = {}
    for r in (
        db.query(Reconciliation)
        .filter(Reconciliation.status == ReconciliationStatus.COMPLETED)
        .filter(Reconciliation.account_id.in_(ids))
        .order_by(Reconciliation.statement_date)
        .all()
    ):
        last_recon[r.account_id] = r.statement_date.isoformat()
    out = []
    for a in accounts:
        feed = feeds.get(a.id)
        out.append(
            {
                "account_id": a.id,
                "account_number": a.account_number,
                "name": a.name,
                "bank_kind": a.bank_kind,
                "balance": float(balances.get(a.id, Decimal("0"))),
                "to_review": int(review.get(feed.id, 0)) if feed else 0,
                "last_reconciled": last_recon.get(a.id),
                "feed": (
                    {
                        "bank_account_id": feed.id,
                        "name": feed.name,
                        "bank_name": feed.bank_name,
                        "last_four": feed.last_four,
                        "legacy_balance": (
                            float(feed.legacy_balance)
                            if feed.legacy_balance is not None
                            else None
                        ),
                    }
                    if feed
                    else None
                ),
            }
        )
    return out


@router.get("/accounts", response_model=list[BankAccountResponse])
def list_bank_accounts(db: Session = Depends(get_db)):
    rows = (
        db.query(BankAccount)
        .filter(BankAccount.is_active)
        .order_by(BankAccount.name)
        .all()
    )
    return _feeds_out(db, rows)


@router.get("/accounts/{account_id}", response_model=BankAccountResponse)
def get_bank_account(account_id: int, db: Session = Depends(get_db)):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return _feeds_out(db, [ba])[0]


def _reject_second_feed(db: Session, account_id: int, exclude_id: int | None = None):
    q = db.query(BankAccount).filter(
        BankAccount.account_id == account_id, BankAccount.is_active
    )
    if exclude_id is not None:
        q = q.filter(BankAccount.id != exclude_id)
    other = q.first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"'{other.name}' is already the feed for this ledger account",
        )


@router.post("/accounts", response_model=BankAccountResponse, status_code=201)
def create_bank_account(data: BankAccountCreate, db: Session = Depends(get_db)):
    acct = require_bank_account(db, data.account_id)
    _reject_second_feed(db, acct.id)
    if data.opening_balance:
        opening_date = data.opening_date or date.today()
        check_closing_date(db, opening_date)
        post_opening_balance(db, acct, opening_date, data.opening_balance)
    ba = BankAccount(
        name=data.name,
        account_id=acct.id,
        bank_name=data.bank_name,
        last_four=data.last_four,
        legacy_balance=None,
    )
    db.add(ba)
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


@router.put("/accounts/{account_id}", response_model=BankAccountResponse)
def update_bank_account(
    account_id: int, data: BankAccountUpdate, db: Session = Depends(get_db)
):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    fields = data.model_dump(exclude_unset=True)
    if "account_id" in fields and fields["account_id"] != ba.account_id:
        linked = (
            db.query(BankTransaction.id)
            .filter(
                BankTransaction.bank_account_id == ba.id,
                BankTransaction.transaction_line_id.isnot(None),
            )
            .first()
        )
        if linked:
            raise HTTPException(
                status_code=400,
                detail="This feed already has matched statement lines; its ledger account cannot change",
            )
        acct = require_bank_account(db, fields["account_id"])
        _reject_second_feed(db, acct.id, exclude_id=ba.id)
    for key, val in fields.items():
        setattr(ba, key, val)
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


@router.post(
    "/accounts/{account_id}/post-legacy-balance", response_model=BankAccountResponse
)
def post_legacy_balance(
    account_id: int, data: LegacyBalancePost, db: Session = Depends(get_db)
):
    """The pre-2.10 register balance, posted once as the account's opening
    balance (against 3900), then cleared from the feed."""
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if ba.legacy_balance is None:
        raise HTTPException(status_code=400, detail="No pre-2.10 balance to post")
    if not ba.account_id:
        raise HTTPException(
            status_code=400, detail="Link this feed to a ledger account first"
        )
    acct = require_bank_account(db, ba.account_id)
    check_closing_date(db, data.date)
    if ba.legacy_balance != 0:
        post_opening_balance(db, acct, data.date, ba.legacy_balance)
    ba.legacy_balance = None
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


# Bank Transactions
@router.get("/transactions", response_model=list[BankTransactionResponse])
def list_bank_transactions(
    bank_account_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    q = db.query(BankTransaction)
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    return q.order_by(BankTransaction.date.desc()).offset(skip).limit(limit).all()


@router.post("/transactions", response_model=BankTransactionResponse, status_code=201)
def create_bank_transaction(data: BankTransactionCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    ba = db.query(BankAccount).filter(BankAccount.id == data.bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    txn = BankTransaction(**data.model_dump())
    ba.balance += data.amount
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# Reconciliations
@router.get("/reconciliations", response_model=list[ReconciliationResponse])
def list_reconciliations(bank_account_id: int = None, db: Session = Depends(get_db)):
    q = db.query(Reconciliation)
    if bank_account_id:
        q = q.filter(Reconciliation.bank_account_id == bank_account_id)
    return q.order_by(Reconciliation.statement_date.desc()).all()


@router.post("/reconciliations", response_model=ReconciliationResponse, status_code=201)
def create_reconciliation(data: ReconciliationCreate, db: Session = Depends(get_db)):
    """Start a reconciliation."""
    ba = db.query(BankAccount).filter(BankAccount.id == data.bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    recon = Reconciliation(**data.model_dump())
    db.add(recon)
    db.commit()
    db.refresh(recon)
    return recon


@router.get("/reconciliations/{recon_id}/transactions")
def get_reconciliation_transactions(recon_id: int, db: Session = Depends(get_db)):
    """Get unreconciled transactions for this bank account"""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == recon.bank_account_id)
        .filter(BankTransaction.date <= recon.statement_date)
        .order_by(BankTransaction.date)
        .all()
    )

    # Sum and subtract in Decimal so a reconciliation that's actually zero
    # doesn't show $0.00000001 of "difference" from float drift over hundreds
    # of cleared transactions. Convert to float only at the JSON boundary.
    cleared_total = sum(
        (Decimal(str(t.amount)) for t in txns if t.reconciled), Decimal("0")
    )
    uncleared_total = sum(
        (Decimal(str(t.amount)) for t in txns if not t.reconciled), Decimal("0")
    )
    statement_bal = Decimal(str(recon.statement_balance or 0))
    difference = statement_bal - cleared_total

    return {
        "reconciliation_id": recon.id,
        "statement_balance": float(statement_bal),
        "cleared_total": float(cleared_total),
        "uncleared_total": float(uncleared_total),
        "difference": float(difference),
        "transactions": [
            {
                "id": t.id,
                "date": t.date.isoformat(),
                "payee": t.payee or "",
                "description": t.description or "",
                "amount": float(t.amount),
                "check_number": t.check_number,
                "reconciled": t.reconciled,
            }
            for t in txns
        ],
    }


@router.post("/reconciliations/{recon_id}/toggle/{txn_id}")
def toggle_cleared(recon_id: int, txn_id: int, db: Session = Depends(get_db)):
    """Toggle a transaction's cleared status."""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Reconciliation already completed")

    txn = db.query(BankTransaction).filter(BankTransaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn.reconciled = not txn.reconciled
    db.commit()
    return {"id": txn.id, "reconciled": txn.reconciled}


@router.get("/check-register")
def check_register(
    account_id: int = None,
    start_date: date = None,
    end_date: date = None,
    db: Session = Depends(get_db),
):
    """The register: every ledger line on a bank or card account, natural-
    balance running balance (a card shows the amount owed positive), with
    the posting each row came from and whether it has cleared."""
    if not account_id:
        acct = (
            db.query(Account)
            .filter(Account.bank_kind == "bank", Account.is_active)
            .order_by(Account.account_number)
            .first()
        )
        if not acct:
            return {"account_id": None, "account_name": "", "entries": []}
        account_id = acct.id
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    reg = account_register(db, account, start_date, end_date)
    entries = []
    for e in reg["entries"]:
        row = dict(e)
        row["payment"] = e["credit"] if e["credit"] > 0 else 0
        row["deposit"] = e["debit"] if e["debit"] > 0 else 0
        row["balance"] = e["running_balance"]
        row["voidable"] = (
            e["source_type"] in ("bank_entry", "transfer", "cc_charge")
            and not e["voided"]
            and e["reconciliation_id"] is None
        )
        entries.append(row)
    return {
        "account_id": account.id,
        "account_name": account.name,
        "account_number": account.account_number,
        "bank_kind": account.bank_kind,
        "natural_balance": reg["account"]["natural_balance"],
        "opening_balance": reg["opening_balance"],
        "balance": float(gl_balance(db, account.id)),
        "entries": entries,
    }


@router.post("/reconciliations/{recon_id}/complete")
def complete_reconciliation(recon_id: int, db: Session = Depends(get_db)):
    """Finish a reconciliation — validates the difference is 0."""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Already completed")

    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == recon.bank_account_id)
        .filter(BankTransaction.date <= recon.statement_date)
        .filter(BankTransaction.reconciled)
        .all()
    )
    cleared_total = sum(t.amount for t in txns)

    if abs(cleared_total - recon.statement_balance) > Decimal("0.01"):
        raise HTTPException(
            status_code=400,
            detail=f"Difference is ${float(recon.statement_balance - cleared_total):.2f} — must be $0.00 to complete",
        )

    recon.status = ReconciliationStatus.COMPLETED
    recon.completed_at = datetime.utcnow()
    db.commit()
    return {"status": "completed", "reconciliation_id": recon.id}
