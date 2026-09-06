"""Nonprofit mode — setup and the documents only a nonprofit posts.

Everything here is gated by Settings -> company_type = nonprofit on the
SPA side; the API itself answers for any company (a business that calls
setup-accounts simply gets four extra system accounts)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.accounts import AccountResponse
from app.services.accounting import NONPROFIT_ACCOUNTS, ensure_nonprofit_accounts

router = APIRouter(prefix="/api/nonprofit", tags=["nonprofit"])


@router.post("/setup-accounts", response_model=list[AccountResponse])
def setup_accounts(db: Session = Depends(get_db)):
    """Create the net-asset, in-kind and bad-debt accounts if missing
    (3300, 3400, 4400, 6960; numbers yield to an existing chart). Safe to
    call any number of times — the Settings page calls it when a company
    switches to nonprofit."""
    accounts = ensure_nonprofit_accounts(db)
    db.commit()
    return [accounts[number] for number, _name, _type in NONPROFIT_ACCOUNTS]
