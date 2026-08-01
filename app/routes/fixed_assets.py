# ============================================================================
# Fixed assets — types, register, depreciation runs, disposal, CSV import,
# and the reconciliation report.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.fixed_assets import (
    DepreciationMethod,
    FixedAsset,
    FixedAssetStatus,
    FixedAssetType,
)
from app.services.fixed_assets import (
    book_value,
    dispose_asset,
    import_assets_csv,
    next_asset_number,
    run_depreciation,
)
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/fixed-assets", tags=["fixed_assets"])


# ── Schemas ──────────────────────────────────────────────────────────────


class AssetTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None
    asset_account_id: Optional[int] = None
    accumulated_depreciation_account_id: Optional[int] = None
    depreciation_expense_account_id: Optional[int] = None
    depreciation_method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    effective_life_years: Optional[Decimal] = None
    annual_rate: Optional[Decimal] = None


class AssetTypeUpdate(AssetTypeCreate):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    depreciation_method: Optional[DepreciationMethod] = None


class AssetCreate(BaseModel):
    name: str
    asset_type_id: int
    purchase_date: date
    purchase_price: Decimal
    salvage_value: Decimal = Decimal("0")
    description: Optional[str] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    asset_type_id: Optional[int] = None
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = None
    salvage_value: Optional[Decimal] = None
    description: Optional[str] = None


class DepreciationRunRequest(BaseModel):
    run_date: date


class DisposalRequest(BaseModel):
    disposal_date: date
    proceeds: Decimal = Decimal("0")
    deposit_account_id: int


def _type_payload(t: FixedAssetType) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "asset_account_id": t.asset_account_id,
        "accumulated_depreciation_account_id": t.accumulated_depreciation_account_id,
        "depreciation_expense_account_id": t.depreciation_expense_account_id,
        "depreciation_method": t.depreciation_method.value,
        "effective_life_years": (
            float(t.effective_life_years) if t.effective_life_years else None
        ),
        "annual_rate": float(t.annual_rate) if t.annual_rate else None,
        "is_active": t.is_active,
    }


def _asset_payload(a: FixedAsset) -> dict:
    return {
        "id": a.id,
        "asset_number": a.asset_number,
        "name": a.name,
        "asset_type_id": a.asset_type_id,
        "asset_type_name": a.asset_type.name if a.asset_type else None,
        "status": a.status.value,
        "purchase_date": a.purchase_date.isoformat(),
        "purchase_price": float(a.purchase_price),
        "salvage_value": float(a.salvage_value or 0),
        "accumulated_depreciation": float(a.accumulated_depreciation or 0),
        "book_value": float(book_value(a)),
        "last_depreciation_date": (
            a.last_depreciation_date.isoformat() if a.last_depreciation_date else None
        ),
        "disposal_date": a.disposal_date.isoformat() if a.disposal_date else None,
        "disposal_proceeds": (
            float(a.disposal_proceeds) if a.disposal_proceeds is not None else None
        ),
        "description": a.description,
    }


# ── Asset types ──────────────────────────────────────────────────────────


@router.get("/types")
def list_types(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(FixedAssetType)
    if not include_inactive:
        q = q.filter(FixedAssetType.is_active.is_(True))
    return [_type_payload(t) for t in q.order_by(FixedAssetType.name).all()]


@router.post("/types", status_code=201)
def create_type(data: AssetTypeCreate, db: Session = Depends(get_db)):
    if db.query(FixedAssetType).filter(FixedAssetType.name == data.name).first():
        raise HTTPException(status_code=409, detail="Asset type already exists")
    row = FixedAssetType(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _type_payload(row)


@router.put("/types/{type_id}")
def update_type(type_id: int, data: AssetTypeUpdate, db: Session = Depends(get_db)):
    row = db.get(FixedAssetType, type_id)
    if not row:
        raise HTTPException(status_code=404, detail="Asset type not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _type_payload(row)


# ── Assets ───────────────────────────────────────────────────────────────


@router.get("")
def list_assets(include_disposed: bool = True, db: Session = Depends(get_db)):
    q = db.query(FixedAsset)
    if not include_disposed:
        q = q.filter(FixedAsset.status == FixedAssetStatus.REGISTERED)
    return [_asset_payload(a) for a in q.order_by(FixedAsset.asset_number).all()]


@router.get("/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(FixedAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _asset_payload(asset)


@router.post("", status_code=201)
def create_asset(data: AssetCreate, db: Session = Depends(get_db)):
    if not db.get(FixedAssetType, data.asset_type_id):
        raise HTTPException(status_code=404, detail="Asset type not found")
    asset = FixedAsset(asset_number=next_asset_number(db), **data.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _asset_payload(asset)


@router.put("/{asset_id}")
def update_asset(asset_id: int, data: AssetUpdate, db: Session = Depends(get_db)):
    asset = db.get(FixedAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.status == FixedAssetStatus.DISPOSED:
        raise HTTPException(status_code=400, detail="Disposed assets are read-only")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(asset, key, value)
    db.commit()
    db.refresh(asset)
    return _asset_payload(asset)


# ── Operations ───────────────────────────────────────────────────────────


@router.post("/run-depreciation")
def depreciation_run(data: DepreciationRunRequest, db: Session = Depends(get_db)):
    return run_depreciation(db, data.run_date)


@router.post("/{asset_id}/dispose")
def dispose(asset_id: int, data: DisposalRequest, db: Session = Depends(get_db)):
    asset = db.get(FixedAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return dispose_asset(
        db, asset, data.disposal_date, data.proceeds, data.deposit_account_id
    )


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await read_limited(file, label="Asset CSV")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    return import_assets_csv(db, text)


@router.get("/reports/reconciliation")
def reconciliation(db: Session = Depends(get_db)):
    """Fixed Asset Reconciliation: register totals per type (cost,
    accumulated, book value) — compare against the mapped GL accounts."""
    rows = []
    for t in db.query(FixedAssetType).order_by(FixedAssetType.name).all():
        assets = [a for a in t.assets if a.status == FixedAssetStatus.REGISTERED]
        cost = sum(Decimal(str(a.purchase_price)) for a in assets)
        accum = sum(Decimal(str(a.accumulated_depreciation or 0)) for a in assets)
        rows.append(
            {
                "asset_type": t.name,
                "asset_count": len(assets),
                "cost": float(cost),
                "accumulated_depreciation": float(accum),
                "book_value": float(cost - accum),
            }
        )
    return {
        "types": rows,
        "total_cost": sum(r["cost"] for r in rows),
        "total_accumulated": sum(r["accumulated_depreciation"] for r in rows),
        "total_book_value": sum(r["book_value"] for r in rows),
    }
