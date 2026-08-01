# ============================================================================
# Xero import — upload the CSV bundle, dry-run, then import.
# Dry-run mutates NOTHING (the contract the UI leans on); import re-runs
# the dry-run internally and refuses when it fails.
# ============================================================================

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.settings_service import set_setting
from app.services.upload_limits import read_limited
from app.services.xero_import import classify_filename, dry_run, run_import

router = APIRouter(prefix="/api/xero-import", tags=["xero_import"])


async def _bundle_from_uploads(files: list[UploadFile]) -> dict:
    bundle: dict[str, str] = {}
    unrecognized = []
    for file in files:
        kind = classify_filename(file.filename)
        if not kind:
            unrecognized.append(file.filename)
            continue
        content = await read_limited(file, label=f"Xero CSV {file.filename}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        bundle[kind] = text
    if unrecognized:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not classify files by name: {unrecognized}. Expected "
                f"filenames containing 'chart'/'accounts', 'ledger'/'journal', "
                f"or 'trial'."
            ),
        )
    return bundle


@router.post("/dry-run")
async def xero_dry_run(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    bundle = await _bundle_from_uploads(files)
    return dry_run(db, bundle)


@router.post("/import")
async def xero_import(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    bundle = await _bundle_from_uploads(files)
    result = run_import(db, bundle)
    if result["ok"]:
        # Opening-balance wizard readiness metadata (spec: state tracking)
        from datetime import date

        set_setting(db, "chart_setup_source", "xero_import")
        set_setting(db, "chart_setup_ready_at", date.today().isoformat())
        db.commit()
    return result
