# ============================================================================
# Fixed assets service — depreciation math, disposal, CSV import.
#
# Depreciation runs are month-granular: a run covers the FULL months
# between the asset's depreciation start (purchase date, or the day
# after the last run) and the run date. Straight-line spreads
# (cost - salvage) over the effective life; declining-balance applies
# the annual rate to current book value. Both cap so book value never
# drops below salvage.
# ============================================================================

import csv
import io
import logging
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.fixed_assets import (
    DepreciationMethod,
    FixedAsset,
    FixedAssetStatus,
    FixedAssetType,
)
from app.services.accounting import _q, create_journal_entry

logger = logging.getLogger(__name__)

DISPOSAL_ACCOUNT_NUMBER = "7999"
DISPOSAL_ACCOUNT_NAME = "Gain/Loss on Asset Disposal"


def book_value(asset: FixedAsset) -> Decimal:
    return _q(
        Decimal(str(asset.purchase_price))
        - Decimal(str(asset.accumulated_depreciation or 0))
    )


def next_asset_number(db: Session) -> str:
    count = db.query(FixedAsset).count()
    candidate = count + 1
    while True:
        number = f"FA-{candidate:04d}"
        if not db.query(FixedAsset).filter(FixedAsset.asset_number == number).first():
            return number
        candidate += 1


def _require_type_accounts(asset_type: FixedAssetType):
    missing = [
        label
        for label, value in (
            (
                "accumulated depreciation",
                asset_type.accumulated_depreciation_account_id,
            ),
            ("depreciation expense", asset_type.depreciation_expense_account_id),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Asset type '{asset_type.name}' is missing account mappings: "
                f"{', '.join(missing)}"
            ),
        )


def _full_months_between(start: date, end: date) -> int:
    """Whole calendar months from `start` to `end` (0 when < 1 month)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def period_depreciation(asset: FixedAsset, run_date: date) -> Decimal:
    """Depreciation to post for the period ending run_date."""
    if asset.status != FixedAssetStatus.REGISTERED:
        return Decimal("0")
    start = asset.last_depreciation_date or asset.purchase_date
    months = _full_months_between(start, run_date)
    if months <= 0:
        return Decimal("0")

    cost = Decimal(str(asset.purchase_price))
    salvage = Decimal(str(asset.salvage_value or 0))
    depreciable_remaining = book_value(asset) - salvage
    if depreciable_remaining <= 0:
        return Decimal("0")

    atype = asset.asset_type
    if atype.depreciation_method == DepreciationMethod.STRAIGHT_LINE:
        life = Decimal(str(atype.effective_life_years or 0))
        if life <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Asset type '{atype.name}' has no effective life set",
            )
        monthly = (cost - salvage) / (life * 12)
    else:
        rate = Decimal(str(atype.annual_rate or 0))
        if rate <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Asset type '{atype.name}' has no annual rate set",
            )
        monthly = book_value(asset) * rate / 12

    amount = _q(monthly * months)
    return min(amount, _q(depreciable_remaining))


def run_depreciation(db: Session, run_date: date) -> dict:
    """Post depreciation for every registered asset up to run_date.

    One journal entry per asset (DR depreciation expense / CR accumulated
    depreciation) so drill-down stays per-asset. Assets with nothing to
    post (fully depreciated, < 1 month elapsed) are skipped and counted.
    """
    assets = (
        db.query(FixedAsset)
        .filter(FixedAsset.status == FixedAssetStatus.REGISTERED)
        .all()
    )
    posted = 0
    skipped = 0
    total = Decimal("0")
    for asset in assets:
        amount = period_depreciation(asset, run_date)
        if amount <= 0:
            skipped += 1
            continue
        atype = asset.asset_type
        _require_type_accounts(atype)
        create_journal_entry(
            db,
            run_date,
            f"Depreciation — {asset.asset_number} {asset.name}",
            [
                {
                    "account_id": atype.depreciation_expense_account_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": f"Depreciation {asset.asset_number}",
                },
                {
                    "account_id": atype.accumulated_depreciation_account_id,
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": f"Accumulated depreciation {asset.asset_number}",
                },
            ],
            source_type="depreciation",
            source_id=asset.id,
        )
        asset.accumulated_depreciation = _q(
            Decimal(str(asset.accumulated_depreciation or 0)) + amount
        )
        asset.last_depreciation_date = run_date
        posted += 1
        total += amount
    db.commit()
    return {"posted": posted, "skipped": skipped, "total": float(_q(total))}


def _disposal_account_id(db: Session) -> int:
    acct = (
        db.query(Account)
        .filter(Account.account_number == DISPOSAL_ACCOUNT_NUMBER)
        .first()
    ) or db.query(Account).filter(Account.name == DISPOSAL_ACCOUNT_NAME).first()
    if not acct:
        acct = Account(
            name=DISPOSAL_ACCOUNT_NAME,
            account_number=DISPOSAL_ACCOUNT_NUMBER,
            account_type=AccountType.EXPENSE,
            is_system=True,
        )
        db.add(acct)
        db.flush()
    return acct.id


def dispose_asset(
    db: Session,
    asset: FixedAsset,
    disposal_date: date,
    proceeds: Decimal,
    deposit_account_id: int,
) -> dict:
    """Sell/dispose: derecognize cost + accumulated, book proceeds, and
    post the residual as gain (credit) or loss (debit) on disposal."""
    if asset.status == FixedAssetStatus.DISPOSED:
        raise HTTPException(status_code=400, detail="Asset is already disposed")
    atype = asset.asset_type
    if not atype.asset_account_id:
        raise HTTPException(
            status_code=400,
            detail=f"Asset type '{atype.name}' has no fixed-asset account mapped",
        )
    _require_type_accounts(atype)

    cost = _q(Decimal(str(asset.purchase_price)))
    accumulated = _q(Decimal(str(asset.accumulated_depreciation or 0)))
    proceeds = _q(Decimal(str(proceeds or 0)))

    lines = []
    if proceeds > 0:
        lines.append(
            {
                "account_id": deposit_account_id,
                "debit": proceeds,
                "credit": Decimal("0"),
                "description": f"Disposal proceeds {asset.asset_number}",
            }
        )
    if accumulated > 0:
        lines.append(
            {
                "account_id": atype.accumulated_depreciation_account_id,
                "debit": accumulated,
                "credit": Decimal("0"),
                "description": f"Derecognize accumulated {asset.asset_number}",
            }
        )
    lines.append(
        {
            "account_id": atype.asset_account_id,
            "debit": Decimal("0"),
            "credit": cost,
            "description": f"Derecognize cost {asset.asset_number}",
        }
    )
    residual = proceeds + accumulated - cost  # >0 gain, <0 loss
    if residual != 0:
        lines.append(
            {
                "account_id": _disposal_account_id(db),
                "debit": -residual if residual < 0 else Decimal("0"),
                "credit": residual if residual > 0 else Decimal("0"),
                "description": f"{'Gain' if residual > 0 else 'Loss'} on disposal {asset.asset_number}",
            }
        )
    txn = create_journal_entry(
        db,
        disposal_date,
        f"Disposal — {asset.asset_number} {asset.name}",
        lines,
        source_type="asset_disposal",
        source_id=asset.id,
    )
    asset.status = FixedAssetStatus.DISPOSED
    asset.disposal_date = disposal_date
    asset.disposal_proceeds = proceeds
    db.commit()
    return {
        "transaction_id": txn.id,
        "gain_loss": float(_q(residual)),
    }


def import_assets_csv(db: Session, csv_text: str) -> dict:
    """Import assets from the CSV template:
    name,asset_type,purchase_date,purchase_price,salvage_value,description

    Asset types are matched by exact name and must exist (strict, like
    the IIF importer's vendor handling): a typo'd type surfaces as a row
    error rather than silently creating a half-configured type.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"name", "asset_type", "purchase_date", "purchase_price"}
    headers = set(reader.fieldnames or [])
    if not required.issubset(headers):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must include columns: {sorted(required)}",
        )
    imported = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        try:
            type_name = (row.get("asset_type") or "").strip()
            atype = (
                db.query(FixedAssetType)
                .filter(FixedAssetType.name == type_name)
                .first()
            )
            if not atype:
                raise ValueError(f"asset type '{type_name}' not found")
            purchase_date = date.fromisoformat((row.get("purchase_date") or "").strip())
            asset = FixedAsset(
                asset_number=next_asset_number(db),
                name=(row.get("name") or "").strip(),
                asset_type_id=atype.id,
                purchase_date=purchase_date,
                purchase_price=_q(Decimal(row.get("purchase_price") or "0")),
                salvage_value=_q(Decimal(row.get("salvage_value") or "0")),
                description=(row.get("description") or "").strip() or None,
            )
            if not asset.name:
                raise ValueError("name is required")
            db.add(asset)
            db.flush()
            imported += 1
        except Exception as exc:
            errors.append({"row": i, "message": str(exc)})
    db.commit()
    return {"imported": imported, "errors": errors}
