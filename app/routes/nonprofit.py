"""Nonprofit mode — setup and the documents only a nonprofit posts.

Everything here is gated by Settings -> company_type = nonprofit on the
SPA side; the API itself answers for any company (a business that calls
setup-accounts simply gets four extra system accounts)."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.nonprofit import RestrictionRelease
from app.schemas.accounts import AccountResponse
from app.schemas.nonprofit import ReleaseCreate, ReleaseResponse, ReleaseSuggestion
from app.services.accounting import NONPROFIT_ACCOUNTS, ensure_nonprofit_accounts
from app.services.closing_date import check_closing_date
from app.services.nonprofit import post_release, suggested_release, void_release

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


# ── Release from restriction ─────────────────────────────────────────────


def _release_response(rel: RestrictionRelease) -> ReleaseResponse:
    data = ReleaseResponse.model_validate(rel)
    data.class_name = rel.fund.name if rel.fund else ""
    return data


def _release_get(db: Session, rel_id: int) -> RestrictionRelease:
    rel = (
        db.query(RestrictionRelease)
        .options(joinedload(RestrictionRelease.fund))
        .filter(RestrictionRelease.id == rel_id)
        .first()
    )
    if not rel:
        raise HTTPException(status_code=404, detail="Release not found")
    return rel


@router.get("/releases/suggest", response_model=ReleaseSuggestion)
def suggest_release(
    class_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    """What the fund spent in the period less what was already released
    for it — the amount the release form fills in."""
    try:
        return suggested_release(db, class_id, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/releases", response_model=list[ReleaseResponse])
def list_releases(
    class_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(RestrictionRelease).options(joinedload(RestrictionRelease.fund))
    if class_id is not None:
        q = q.filter(RestrictionRelease.class_id == class_id)
    if status:
        q = q.filter(RestrictionRelease.status == status)
    return [
        _release_response(r)
        for r in q.order_by(
            RestrictionRelease.date.desc(), RestrictionRelease.id.desc()
        ).all()
    ]


@router.get("/releases/{rel_id}", response_model=ReleaseResponse)
def get_release(rel_id: int, db: Session = Depends(get_db)):
    return _release_response(_release_get(db, rel_id))


@router.post("/releases", response_model=ReleaseResponse, status_code=201)
def create_release(data: ReleaseCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    try:
        rel = post_release(
            db,
            txn_date=data.date,
            class_id=data.class_id,
            amount=data.amount,
            period_start=data.period_start,
            period_end=data.period_end,
            memo=data.memo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return _release_response(_release_get(db, rel.id))


@router.post("/releases/{rel_id}/void", response_model=ReleaseResponse)
def void_release_route(rel_id: int, db: Session = Depends(get_db)):
    rel = _release_get(db, rel_id)
    check_closing_date(db, rel.date)
    try:
        void_release(db, rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return _release_response(_release_get(db, rel.id))
