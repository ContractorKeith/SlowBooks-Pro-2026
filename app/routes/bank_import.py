# ============================================================================
# Bank Feed Import — OFX/QFX + CSV file upload and import
# Feature 18: Upload → preview → confirm → auto-match by amount/date
# ============================================================================

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.services.ofx_import import parse_ofx, import_transactions
from app.services.bank_csv_import import parse_csv, import_csv_transactions
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


@router.post("/preview")
async def preview_ofx(file: UploadFile = File(...)):
    """Parse OFX/QFX file and return preview of transactions."""
    content = await read_limited(file, label="Bank file")
    try:
        # Try UTF-8 first, fall back to latin-1
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    transactions = parse_ofx(text)
    return {
        "count": len(transactions),
        "transactions": [
            {
                "fitid": t.get("fitid", ""),
                "date": t["date"].isoformat(),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "memo": t.get("memo", ""),
            }
            for t in transactions
        ],
    }


@router.post("/import/{bank_account_id}")
async def import_ofx(
    bank_account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """Import OFX/QFX transactions into a bank account."""
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    content = await read_limited(file, label="Bank file")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    transactions = parse_ofx(text)
    result = import_transactions(db, bank_account_id, transactions)
    return result


@router.post("/preview-csv")
async def preview_csv(file: UploadFile = File(...)):
    """Parse CSV bank statement and return preview of transactions."""
    content = await read_limited(file, label="CSV file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = parse_csv(text)
    if result["error"]:
        return {
            "format": result["format"],
            "error": result["error"],
            "count": 0,
            "transactions": [],
        }

    return {
        "format": result["format"],
        "count": len(result["transactions"]),
        "transactions": [
            {
                "date": (
                    t["date"].isoformat()
                    if hasattr(t["date"], "isoformat")
                    else str(t["date"])
                ),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "description": t.get("description", ""),
                "check_number": t.get("check_number"),
                "fee": float(t["fee"]) if t.get("fee") else None,
            }
            for t in result["transactions"]
        ],
    }


@router.post("/import-csv/{bank_account_id}")
async def import_csv(
    bank_account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import CSV bank transactions into a bank account.

    Auto-detects format (Chase checking, Chase credit, PayPal).
    Deduplicates by content-derived import_id (re-imports and overlapping
    exports skip; legitimate same-day duplicates still import).
    Auto-applies bank rules after import.
    """
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    content = await read_limited(file, label="CSV file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = import_csv_transactions(db, bank_account_id, text)
    return result
