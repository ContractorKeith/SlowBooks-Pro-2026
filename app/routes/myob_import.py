# ============================================================================
# MYOB import — upload the export bundle, dry-run, then import.
# Same contract as the Xero route: dry-run mutates NOTHING; import
# re-runs the dry-run internally and refuses when it fails.
# ============================================================================

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.myob_import import classify_filename, dry_run, run_import
from app.services.settings_service import set_setting
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/myob-import", tags=["myob_import"])


async def _bundle_from_uploads(files: list[UploadFile]) -> dict:
    bundle: dict[str, str] = {}
    unrecognized = []
    for file in files:
        kind = classify_filename(file.filename)
        if not kind:
            unrecognized.append(file.filename)
            continue
        content = await read_limited(file, label=f"MYOB export {file.filename}")
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
async def myob_dry_run(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    bundle = await _bundle_from_uploads(files)
    return dry_run(db, bundle)


@router.post("/import")
async def myob_import(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    bundle = await _bundle_from_uploads(files)
    result = run_import(db, bundle)
    if result["ok"]:
        from datetime import date

        set_setting(db, "chart_setup_source", "myob_import")
        set_setting(db, "chart_setup_ready_at", date.today().isoformat())
        db.commit()
    return result
